import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch, Mock
import intake


def docx(path, content):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+content+'</w:body></w:document>')


class Intake(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root/'data'
        self.data.mkdir()
        self.mockdata = patch.object(intake, 'DATA', self.data)
        self.mockdata.start()
        self.register = Mock(side_effect=lambda paths: [{'id': str(i), 'path': p} for i,p in enumerate(paths)])

    def tearDown(self):
        self.mockdata.stop()
        self.temp.cleanup()

    def test_mixed_folder_script_and_media_nested(self):
        folder=self.root/'拍摄';folder.mkdir();(folder/'食物').mkdir()
        (folder/'食物/a.MOV').write_bytes(b'video')
        (folder/'达人.mp4').write_bytes(b'video2')
        (folder/'.hidden.mov').write_bytes(b'ignore')
        (folder/'~$脚本.docx').write_bytes(b'ignore')
        docx(folder/'达人脚本.docx', '<w:p><w:r><w:t>达人：好饱满的虾饺！</w:t></w:r></w:p>')
        (folder/'readme.txt').write_text('拍摄说明')
        result=intake.scan([str(folder)],self.register)
        self.assertEqual(len(result['assets']),2)
        self.assertEqual(len(result['scripts']),2)
        chosen=next(s for s in result['scripts'] if s['id']==result['selectedScriptId'])
        self.assertEqual(chosen['text'],'达人：好饱满的虾饺！')
        self.assertEqual(result['skipped'],2)

    def test_table_and_encodings(self):
        path=self.root/'table.docx'
        docx(path,'<w:tbl><w:tr><w:tc><w:p><w:r><w:t>闺蜜</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>真的好吃</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
        self.assertEqual(intake.script_text(path),'闺蜜：真的好吃')
        for encoding in ('utf-8-sig','utf-16','gb18030'):
            txt=self.root/'script.txt';txt.write_bytes('达人：好吃'.encode(encoding))
            self.assertEqual(intake.script_text(txt),'达人：好吃')

    def test_ambiguous_does_not_combine(self):
        for name in ('脚本旧.txt','脚本新.md'):(self.root/name).write_text('达人：测试')
        result=intake.scan([str(self.root)],self.register)
        self.assertIsNone(result['selectedScriptId'])
        self.assertEqual(len(result['scripts']),2)
        self.register.assert_not_called()

    def test_bad_doc_and_symlink_are_not_registered(self):
        (self.root/'bad.docx').write_bytes(b'invalidzip')
        outside=self.data/'outside.mov';outside.write_bytes(b'media')
        folder=self.root/'folder';folder.mkdir();(folder/'link.mov').symlink_to(outside)
        result=intake.scan([str(folder),str(self.root/'bad.docx')],self.register)
        self.assertFalse(result['assets']);self.assertFalse(result['scripts'])
        self.assertTrue(any('bad.docx' in w for w in result['warnings']))

    def test_doc_entity_and_size_rejected(self):
        p=self.root/'entity.docx';docx(p,'<!DOCTYPE unsafe>')
        with self.assertRaisesRegex(ValueError,'实体'):intake.script_text(p)
        p=self.root/'huge.txt'
        with p.open('wb') as f:f.truncate(intake.SCRIPT_LIMIT+1)
        with self.assertRaisesRegex(ValueError,'10 MB'):intake.script_text(p)

    def test_copy_partial_offset_completion_idempotent(self):
        text='达人：好吃'.encode()
        rows=[{'path':'拍摄/a.mov','size':5},{'path':'拍摄/脚本.txt','size':len(text)}]
        identifier=intake.begin({'files':rows})['id']
        intake.chunk(identifier,0,0,b'vi')
        with self.assertRaises(ValueError):intake.chunk(identifier,0,0,b'xx')
        self.assertEqual(intake.offsets(identifier)['offsets'],[2,0])
        with self.assertRaisesRegex(ValueError,'未传完'):intake.finish(identifier,self.register)
        self.register.assert_not_called()
        intake.chunk(identifier,0,2,b'deo');intake.chunk(identifier,1,0,text)
        result=intake.finish(identifier,self.register)
        self.assertEqual(Path(result['assets'][0]['path']).read_bytes(),b'video')
        self.assertEqual(result['scripts'][0]['text'],'达人：好吃')
        self.assertEqual(intake.finish(identifier,self.register),result)
        self.assertEqual(self.register.call_count,1)
        self.assertEqual(intake.offsets(identifier)['status'],'complete')
        with self.assertRaises(ValueError):intake.chunk(identifier,0,0,b'x')

    def test_untrusted_paths_and_manifest(self):
        for path in ('../outside.mov','/root.mov','a//b.mov','a/./b.mov','a\\b.mov','.hidden.mov','x/../../b.mov','~$a.docx','x:y.mov'):
            with self.subTest(path=path),self.assertRaises(ValueError):intake.begin({'files':[{'path':path,'size':10}]})
        for rows in ([{'path':'a.mov','size':0}],[{'path':'a.exe','size':3}],
                     [{'path':'A.mov','size':1},{'path':'a.MOV','size':2}]):
            with self.assertRaises(ValueError):intake.begin({'files':rows})
        with self.assertRaises(ValueError):intake.offsets('../escape')

    def test_disk_space_guard(self):
        with patch.object(intake.shutil,'disk_usage',return_value=Mock(free=1)):
            with self.assertRaisesRegex(ValueError,'空间不足'):intake.begin({'files':[{'path':'a.mov','size':10}]})


if __name__=='__main__':unittest.main()
