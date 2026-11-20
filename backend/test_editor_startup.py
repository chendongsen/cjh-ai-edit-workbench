import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import editor_startup as startup
import workflow
from common import write, read


class Startup(unittest.TestCase):
    def test_already_connected_never_opens_or_switches(self):
        with patch.object(startup,'probe',return_value={'connected':True}),patch.object(startup.subprocess,'run') as run:
            self.assertTrue(startup.ensure_resolve(lambda s:None)['connected'])
            run.assert_not_called()

    def test_cold_launch_waits_for_actual_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=Path(tmp)/'Resolve.app';app.mkdir();exe=app/'ResolvePython';exe.touch()
            with patch.object(startup,'RESOLVE_APP',app),patch.object(startup,'RESOLVE',str(exe)),patch.object(startup,'probe',side_effect=[{'connected':False},{'connected':False},{'connected':True}]),patch.object(startup.subprocess,'run') as run,patch.object(startup.time,'sleep'):
                self.assertTrue(startup.ensure_resolve(lambda s:None)['connected'])
                run.assert_called_once_with(['/usr/bin/open','-g','-a',str(app)],check=True,timeout=15)

    def test_missing_install_is_not_open_success(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(startup,'RESOLVE_APP',Path(tmp)/'missing.app'),patch.object(startup,'probe',return_value={'connected':False}),patch.object(startup.subprocess,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'未找到'):startup.ensure_resolve(lambda s:None)
            run.assert_not_called()

    def test_launch_without_connection_reports_manual_requirement(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=Path(tmp);exe=app/'ResolvePython';exe.touch()
            with patch.object(startup,'RESOLVE_APP',app),patch.object(startup,'RESOLVE',str(exe)),patch.object(startup,'probe',return_value={'connected':False}),patch.object(startup.subprocess,'run'):
                with self.assertRaisesRegex(RuntimeError,'Local'):startup.ensure_resolve(lambda s:None,timeout=0)

    def test_initialized_jianying_does_not_launch_before_export(self):
        with patch.object(startup.jianying,'status',return_value={'available':True,'app':True,'draftRootExists':True}),patch.object(startup.jianying,'launch_app') as launch,patch.object(startup,'ensure_resolve',return_value={'connected':True}):
            self.assertTrue(startup.prepare(lambda s:None)['connected']);launch.assert_not_called()

    def test_failed_preflight_stops_analysis_and_marks_nonretryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);write(root/'input.json',{'assets':[],'autoLaunchEditors':True})
            with patch.object(startup,'prepare',side_effect=RuntimeError('设置 Local')),patch.object(workflow,'analyze') as analyze,patch.object(workflow.jianying,'run') as export:
                with self.assertRaisesRegex(RuntimeError,'Local'):workflow.run(root/'input.json')
                analyze.assert_not_called();export.assert_not_called()
                self.assertTrue(read(root/'startup-failure.json')['needsUserAction'])
                self.assertEqual(read(root/'progress.json')['stage'],'startup')

    def test_submit_accepts_installed_but_closed_resolve_without_opening_in_http(self):
        import server
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'jobs').mkdir();exe=root/'ResolvePython';exe.touch()
            media=root/'clip.mov';media.write_bytes(b'fixture')
            caps={'resolve':{'installed':True},'jianying':{'available':True,'app':True,'draftRootExists':False},
                  'localModels':['local-vision'],'modelCache':['small']}
            asset={'id':'a','path':str(media),'name':media.name,'size':7,'modified':0}
            with patch.object(server,'DATA',root),patch.object(server,'JOBS',{}),patch.object(server,'ASSET_MAP',{'a':asset}),patch.object(server,'RESOLVE',str(exe)),patch.object(server,'capabilities',return_value=caps) as capability,patch.object(startup,'prepare') as prepare,patch.object(server.voice_guard,'choose_profile',return_value=None):
                result=server.submit({'kind':'workflow','projectId':'qa','assetIds':['a'],'confirmed':True,
                                      'unattended':True,'editingScript':'达人：这个虾饺真好吃',
                                      'visionModel':'local-vision','model':'small','clientRequestId':'request1'})
                self.assertEqual(result['status'],'queued')
                self.assertEqual(result['analysisTotal'],1)
                self.assertTrue(read(root/'jobs'/result['id']/'input.json')['autoLaunchEditors'])
                capability.assert_called_once_with(check_resolve=False);prepare.assert_not_called()
                # Older projects can retain a removed model or no model at all.
                second=server.submit({'kind':'workflow','projectId':'qa-fallback','assetIds':['a'],
                    'confirmed':True,'visionModel':'removed-model','model':'tiny'})
                saved=read(root/'jobs'/second['id']/'input.json')
                self.assertEqual(saved['visionModel'],'local-vision')
                self.assertEqual(saved['model'],'small')
                prepare.assert_not_called()

if __name__=='__main__':unittest.main()
