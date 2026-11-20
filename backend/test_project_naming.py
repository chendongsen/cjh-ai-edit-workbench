import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from project_naming import make_project_naming


class ProjectNamingTests(unittest.TestCase):
    created = '2026-10-23T01:15:30Z'
    job = '11111111-2222-4333-8444-555555555555'

    def name(self, assets, **kwargs):
        return make_project_naming(assets, kwargs.pop('job_id', self.job),
                                   kwargs.pop('created', self.created), **kwargs)

    def test_common_shoot_folder_and_schema(self):
        assets=[{'path':'/Users/editor/Movies/小林爬山/人物/1.mov'},
                {'path':'/Users/editor/Movies/小林爬山/食物/2.mov'}]
        result=self.name(assets)
        self.assertEqual(result['sourceLabel'],'小林爬山')
        self.assertTrue(result['projectName'].startswith('20261023_小林爬山_粗剪_091530_'))
        self.assertEqual(result['timezone'],'Asia/Shanghai')
        self.assertEqual(result['createdAt'],'2026-10-23T09:15:30+08:00')
        self.assertEqual(result['revision'],'project-naming-1')
        self.assertEqual(set(result),{'projectName','sourceLabel','createdAt','timezone','revision'})

    def test_single_direct_file_uses_stem_not_parent(self):
        result=self.name([{'path':'/Users/editor/Movies/小林爬山/人物反应.mov'}])
        self.assertEqual(result['sourceLabel'],'人物反应')
        self.assertEqual(self.name([{'name':'只有文件名.MOV'}])['sourceLabel'],'只有文件名')

    def test_one_explicit_batch_label_takes_priority_and_assets_unchanged(self):
        assets=[{'path':'/tmp/1.mov','batchLabel':'达人示例广场'},
                {'path':'/tmp/2.mov','batchLabel':'达人示例广场'}]
        before=copy.deepcopy(assets)
        self.assertEqual(self.name(assets)['sourceLabel'],'达人示例广场')
        self.assertEqual(assets,before)
        self.assertEqual(self.name(assets[:1])['sourceLabel'],'达人示例广场')

    def test_mixed_or_partial_batch_labels_do_not_rename_other_sources(self):
        for labels in [('A','B'),('A',None)]:
            assets=[{'path':'/Users/a/Downloads/one.mov','batchLabel':labels[0]},
                    {'path':'/Users/a/Documents/two.mov','batchLabel':labels[1]}]
            self.assertEqual(self.name(assets)['sourceLabel'],'one等2个素材')

    def imported(self, folder, paths, metadata=None):
        root=folder/'.local'/'imports'/('a'*32)
        root.mkdir(parents=True,exist_ok=True)
        (root/'manifest.json').write_text(json.dumps({'files':[{'path':p} for p in (metadata or paths)]}),encoding='utf-8')
        return [{'path':str(root/'files'/p)} for p in paths], root/'manifest.json'

    def test_single_copied_source_uses_stem_without_explicit_batch_label(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets,_=self.imported(Path(temporary),['小林爬山/人物/31.mov'])
            self.assertEqual(self.name(assets)['sourceLabel'],'31')

    def test_repeated_clips_of_one_source_are_named_as_one_file(self):
        assets=[{'path':'/Users/editor/Movies/小林爬山/31.mov','start':start} for start in (1,3,5)]
        original=copy.deepcopy(assets)
        self.assertEqual(self.name(assets)['sourceLabel'],'31')
        self.assertEqual(assets,original)
        self.assertEqual(self.name(assets),self.name(assets[:1]))
        named=[dict(asset,batchLabel='小林爬山') for asset in assets]
        self.assertEqual(self.name(named)['sourceLabel'],'小林爬山')

    def test_unique_path_count_used_in_multiroot_fallback(self):
        assets=[{'path':'/Users/e/Downloads/one.mov'}, {'path':'/Users/e/Downloads/one.mov'},
                {'path':'/Users/e/Documents/two.mov'}]
        self.assertEqual(self.name(assets)['sourceLabel'],'one等2个素材')

    def test_conflicting_labels_on_duplicate_path_do_not_depend_on_order(self):
        assets=[{'path':'/tmp/one.mov','batchLabel':'A'},{'path':'/tmp/one.mov','batchLabel':'B'}]
        self.assertEqual(self.name(assets)['sourceLabel'],'one')
        self.assertEqual(self.name(assets),self.name(list(reversed(assets))))

    def test_copy_manifest_only_reads_submitted_batch_and_selected_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets,manifest=self.imported(Path(temporary),['达人/人物/1.mov','达人/食物/2.mov'],
                                          ['达人/人物/1.mov','达人/食物/2.mov','无关批次/3.mov'])
            opened=[]
            original=Path.open
            def recording(path,*args,**kwargs):
                opened.append(path)
                return original(path,*args,**kwargs)
            with patch('project_naming.Path.open',recording):
                result=self.name(assets)
            self.assertEqual(result['sourceLabel'],'达人')
            self.assertEqual(opened,[manifest])

    def test_bare_uploaded_files_never_use_files_or_uuid(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets,_=self.imported(Path(temporary),['2.mov','1.mov'])
            self.assertEqual(self.name(assets)['sourceLabel'],'1等2个素材')
            self.assertEqual(self.name(assets[:1])['sourceLabel'],'2')

    def test_unknown_or_malformed_manifest_cannot_invent_original_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets,manifest=self.imported(Path(temporary),['假的上传根/1.mov'],['另一批/other.mov'])
            self.assertEqual(self.name(assets)['sourceLabel'],'1')
            manifest.write_text('{invalid',encoding='utf-8')
            self.assertEqual(self.name(assets)['sourceLabel'],'1')

    def test_multiple_upload_roots_use_first_filename_not_import_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets,_=self.imported(Path(temporary),['达人/2.mov','小林/1.mov'])
            self.assertEqual(self.name(assets)['sourceLabel'],'1等2个素材')

    def test_generic_ancestors_are_not_used_or_climbed_to_account(self):
        for root in ('/Users/editor','/Users/editor/Downloads','/Users/editor/Documents',
                     '/Users/editor/Documents/cjh','/Volumes/ShootDrive'):
            with self.subTest(root=root):
                assets=[{'path':root+'/A/2.mov'},{'path':root+'/B/1.mov'}]
                self.assertEqual(self.name(assets)['sourceLabel'],'1等2个素材')
        grouped=[{'path':'/Users/editor/Movies/拍摄A/人物/1.mov'},
                 {'path':'/Users/editor/Movies/拍摄A/人物/2.mov'}]
        self.assertEqual(self.name(grouped)['sourceLabel'],'拍摄A')

    def test_placeholder_batch_labels_are_not_names(self):
        for label in ('我的第一个剪辑项目','我的第一个剪辑项目 严格按脚本 初剪',
                      'files','a'*32,'Untitled Project'):
            with self.subTest(label=label):
                result=self.name([{'path':'/tmp/真素材.mov','batchLabel':label}])
                self.assertEqual(result['sourceLabel'],'真素材')

    def test_epoch_aware_iso_and_naive_shanghai_have_same_name(self):
        epoch=datetime(2026,10,23,1,15,30,tzinfo=timezone.utc).timestamp()
        expected=self.name([])
        for value in (epoch,'2026-10-23T01:15:30+00:00','2026-10-22T18:15:30-07:00',
                      '2026-10-23T09:15:30',datetime(2026,10,23,1,15,30,tzinfo=timezone.utc)):
            with self.subTest(created=value):
                self.assertEqual(self.name([],created=value),expected)

    def test_host_timezone_does_not_change_date_or_retry_name(self):
        assets=[{'name':'人物.mp4'}]
        first=self.name(assets,created='2026-10-22T18:01:02Z')
        with patch.dict('os.environ',{'TZ':'America/Los_Angeles'}):
            second=self.name(assets,created='2026-10-22T18:01:02Z')
        self.assertEqual(first,second)
        self.assertTrue(first['projectName'].startswith('20261023_人物_粗剪_020102_'))

    def test_same_second_jobs_with_identical_uuid_prefix_do_not_collide(self):
        a=self.name([],job_id='aaaaaaaa-0000-0000-0000-000000000001')
        b=self.name([],job_id='aaaaaaaa-0000-0000-0000-000000000002')
        self.assertNotEqual(a['projectName'],b['projectName'])
        self.assertEqual(a,self.name([],job_id='aaaaaaaa-0000-0000-0000-000000000001'))

    def test_long_unicode_label_keeps_date_time_and_entire_short_id(self):
        result=self.name([{'name':'1.mov','batchLabel':'超长素材名称'*100}])
        self.assertLessEqual(len(result['projectName']),100)
        self.assertLessEqual(len(result['projectName'].encode('utf-8')),240)
        self.assertTrue(result['projectName'].endswith('_粗剪_091530_'+hashlib.sha256(self.job.encode()).hexdigest()[:12]))
        self.assertIn(result['sourceLabel'],result['projectName'])

    def test_safe_unicode_normalization_and_filename_characters(self):
        result=self.name([{'name':'1.mov','batchLabel':'  Café e\u0301 / 达人\\:*?"<>|\n\x00\u202e  '}])
        self.assertIn('Café_é',result['sourceLabel'])
        self.assertNotRegex(result['projectName'],r'[/\\:*?"<>|\x00-\x1f\u202e]')
        self.assertFalse(result['sourceLabel'].endswith(('.', ' ', '_')))

    def test_asset_order_does_not_change_fallback(self):
        assets=[{'path':'/Users/e/Downloads/z.mov'},{'path':'/Users/e/Documents/a.mov'}]
        self.assertEqual(self.name(assets),self.name(list(reversed(assets))))

    def test_fallback_and_invalid_inputs(self):
        self.assertEqual(self.name([])['sourceLabel'],'素材')
        self.assertEqual(self.name([],fallback='测试素材')['sourceLabel'],'测试素材')
        self.assertEqual(self.name([],fallback='files')['sourceLabel'],'素材')
        for value in ('not-a-date',True,{},float('nan'),float('inf')):
            with self.subTest(created=value),self.assertRaises(ValueError):
                self.name([],created=value)
        for identifier in ('',None):
            with self.subTest(job=identifier),self.assertRaises(ValueError):
                self.name([],job_id=identifier)


if __name__=='__main__':
    unittest.main()
