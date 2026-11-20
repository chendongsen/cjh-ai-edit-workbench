"""Exercise installed path isolation without models, editors, or live services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1]


class BundleLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.install = Path(self.tmp.name).resolve() / '中文 安装目录'
        self.app = self.install / 'CJH剪辑工作台.app'
        self.code = self.app / 'Contents/Resources/workbench'
        (self.code / 'backend').mkdir(parents=True)
        shutil.copy2(SOURCE / 'backend/common.py', self.code / 'backend/common.py')

    def common(self, code=None, data=None):
        env = dict(os.environ)
        env.pop('CJH_DATA_DIR', None)
        if data is not None:
            env['CJH_DATA_DIR'] = str(data)
        script = '''import json,sys
sys.path.insert(0, sys.argv[1])
import common
print(json.dumps({name:str(getattr(common,name)) for name in
 ('ROOT','DATA','INSTALL_ROOT','BUNDLE_APP','OLLAMA_EXECUTABLE','HANDOFF_HELPER_APP','VENDOR_DIR')}))
'''
        return subprocess.run([sys.executable, '-I', '-B', '-c', script,
                               str((code or self.code) / 'backend')],
                              env=env, capture_output=True, text=True)

    def test_bundle_code_and_helpers_stay_inside_app_but_data_does_not(self):
        before = {p.relative_to(self.app) for p in self.app.rglob('*')}
        result = self.common()
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = json.loads(result.stdout)
        self.assertEqual(paths['ROOT'], str(self.code))
        self.assertEqual(paths['DATA'], str(self.install / '.local'))
        self.assertEqual(paths['INSTALL_ROOT'], str(self.install))
        self.assertEqual(paths['BUNDLE_APP'], str(self.app))
        self.assertEqual(paths['OLLAMA_EXECUTABLE'], str(self.app / 'Contents/Helpers/Ollama.app/Contents/Resources/ollama'))
        self.assertEqual(paths['HANDOFF_HELPER_APP'], str(self.app / 'Contents/Helpers/CJH草稿助手.app'))
        self.assertEqual(paths['VENDOR_DIR'], str(self.code / 'vendor/3D-Speaker'))
        self.assertTrue((self.install / '.local').is_dir())
        self.assertEqual(before, {p.relative_to(self.app) for p in self.app.rglob('*')})

    def test_old_flat_install_keeps_original_paths(self):
        flat = self.install / '旧版本'
        (flat / 'backend').mkdir(parents=True)
        shutil.copy2(SOURCE / 'backend/common.py', flat / 'backend/common.py')
        result = self.common(flat)
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = json.loads(result.stdout)
        self.assertEqual(paths['ROOT'], str(flat))
        self.assertEqual(paths['DATA'], str(flat / '.local'))
        self.assertEqual(paths['BUNDLE_APP'], 'None')
        self.assertEqual(paths['OLLAMA_EXECUTABLE'], str(flat / '.local/ollama-runtime/Ollama.app/Contents/Resources/ollama'))
        self.assertEqual(paths['HANDOFF_HELPER_APP'], str(flat / '.local/CJH草稿助手.app'))

    def test_explicit_external_data_root_is_respected(self):
        data = self.install / '外置数据' / 'records'
        result = self.common(data=data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['DATA'], str(data))
        self.assertTrue(data.is_dir())
        self.assertFalse((self.install / '.local').exists())

    def test_data_inside_bundle_is_refused_before_write(self):
        data = self.code / '.local'
        result = self.common(data=data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('数据目录不能位于 App 内', result.stderr)
        self.assertFalse(data.exists())

    def test_symlink_cannot_redirect_data_back_into_bundle(self):
        link = self.install / 'data-link'
        link.symlink_to(self.code, target_is_directory=True)
        result = self.common(data=link / 'mutable')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('数据目录不能位于 App 内', result.stderr)
        self.assertFalse((self.code / 'mutable').exists())

    def test_shell_entry_uses_external_data_and_disables_bytecode(self):
        runtime = self.app / 'Contents/Frameworks/runtime'
        (runtime / 'bin').mkdir(parents=True)
        python = runtime / 'bin/python3'
        python.write_text('#!/bin/zsh\nprint -r -- "data=$CJH_DATA_DIR"\n'
                          'print -r -- "hf=$HF_HOME"\n'
                          'print -r -- "bytecode=$PYTHONDONTWRITEBYTECODE"\n'
                          'print -r -- "arg=$1"\n')
        python.chmod(0o755)
        (self.code / 'runtime').symlink_to('../../Frameworks/runtime', target_is_directory=True)
        command = self.code / '启动剪辑工作台.command'
        shutil.copy2(SOURCE / 'distribution/启动剪辑工作台.command', command)
        command.chmod(0o755)
        env = dict(os.environ, CJH_BACKGROUND='1')
        env.pop('CJH_DATA_DIR', None)
        result = subprocess.run([str(command)], env=env, capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = dict(line.split('=', 1) for line in result.stdout.splitlines())
        self.assertEqual(rows['data'], str(self.install / '.local'))
        self.assertEqual(rows['hf'], str(self.install / '.local/hf'))
        self.assertEqual(rows['bytecode'], '1')
        self.assertEqual(rows['arg'], 'backend/launch.py')


if __name__ == '__main__':
    unittest.main()
