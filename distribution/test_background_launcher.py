"""Exercise the installed shell entry point without starting any real service."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name('启动剪辑工作台.command')
FAKE_RUNTIME = r'''#!/bin/zsh
print -r -- "fake-runtime=1"
print -r -- "cwd=$PWD"
print -r -- "argc=$#"
print -r -- "arg1=${1-<unset>}"
print -r -- "PYTHONNOUSERSITE=${PYTHONNOUSERSITE-<unset>}"
print -r -- "HF_HUB_OFFLINE=${HF_HUB_OFFLINE-<unset>}"
print -r -- "HF_HUB_DISABLE_TELEMETRY=${HF_HUB_DISABLE_TELEMETRY-<unset>}"
print -r -- "DO_NOT_TRACK=${DO_NOT_TRACK-<unset>}"
print -r -- "CJH_OLLAMA_URL=${CJH_OLLAMA_URL-<unset>}"
print -r -- "HF_HOME=${HF_HOME-<unset>}"
print -r -- "PYTHONPATH=${PYTHONPATH-<unset>}"
print -r -- "PYTHONHOME=${PYTHONHOME-<unset>}"
print -r -- "CJH_BACKGROUND=${CJH_BACKGROUND-<unset>}"
print -r -- "CJH_TEST_MARKER=${CJH_TEST_MARKER-<unset>}"
print -ru2 -- "fake-runtime-stderr"
exit "${CJH_TEST_EXIT_CODE:-0}"
'''


class BackgroundLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / '中文 安装目录'
        (self.root / 'runtime/bin').mkdir(parents=True)
        (self.root / 'backend').mkdir()
        self.command = self.root / SCRIPT.name
        shutil.copy2(SCRIPT, self.command)
        self.command.chmod(0o755)
        runtime = self.root / 'runtime/bin/python3'
        runtime.write_text(FAKE_RUNTIME, encoding='utf-8')
        runtime.chmod(0o755)
        # The shell must pass this as an argument; our fake runtime never runs it.
        (self.root / 'backend/launch.py').write_text(
            'raise AssertionError("The real launcher must not run in this test")\n',
            encoding='utf-8')

    def run_launcher(self, exit_code=0, model_url=None, stdin_eof=False):
        env = dict(os.environ)
        env.pop('CJH_OLLAMA_URL', None)
        env.update(CJH_BACKGROUND='1', CJH_TEST_EXIT_CODE=str(exit_code),
                   CJH_TEST_MARKER='inherited marker',
                   PYTHONPATH='/not/the/workbench/python/path',
                   PYTHONHOME='/not/the/workbench/python/home',
                   PYTHONNOUSERSITE='0', HF_HUB_OFFLINE='0',
                   HF_HUB_DISABLE_TELEMETRY='0', DO_NOT_TRACK='0',
                   HF_HOME='/not/the/workbench/hf')
        if model_url is not None:
            env['CJH_OLLAMA_URL'] = model_url
        # Leave stdin open, with no input. A prompt/read in the launch path would
        # hang and fail the timeout instead of succeeding because of an EOF.
        process = subprocess.Popen([str(self.command)], cwd=self.temporary.name,
                                   env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)
        if stdin_eof:
            process.stdin.close()
            process.stdin = None
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            self.fail('Background launch waited for interactive input')
        output, error = process.communicate()
        rows = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
        return process.returncode, output, error, rows

    def test_background_preserves_environment_and_resolves_unicode_path(self):
        code, output, error, rows = self.run_launcher()
        self.assertEqual(code, 0, error)
        self.assertEqual(rows['fake-runtime'], '1')
        self.assertEqual(rows['cwd'], str(self.root))
        self.assertEqual(rows['argc'], '1')
        self.assertEqual(rows['arg1'], 'backend/launch.py')
        for name in ('PYTHONNOUSERSITE', 'HF_HUB_OFFLINE',
                     'HF_HUB_DISABLE_TELEMETRY', 'DO_NOT_TRACK', 'CJH_BACKGROUND'):
            self.assertEqual(rows[name], '1', name)
        self.assertEqual(rows['CJH_OLLAMA_URL'], 'http://127.0.0.1:11435')
        self.assertEqual(rows['HF_HOME'], str(self.root / '.local/hf'))
        self.assertEqual(rows['PYTHONPATH'], '<unset>')
        self.assertEqual(rows['PYTHONHOME'], '<unset>')
        self.assertEqual(rows['CJH_TEST_MARKER'], 'inherited marker')
        self.assertEqual(error, 'fake-runtime-stderr\n')
        self.assertNotIn('按回车', output)

    def test_existing_model_url_is_preserved(self):
        code, _, _, rows = self.run_launcher(model_url='http://127.0.0.1:12435')
        self.assertEqual(code, 0)
        self.assertEqual(rows['CJH_OLLAMA_URL'], 'http://127.0.0.1:12435')

    def test_nonzero_exit_is_not_swallowed_or_followed_by_prompt(self):
        code, output, error, rows = self.run_launcher(exit_code=7)
        self.assertEqual(code, 7)
        self.assertEqual(rows['fake-runtime'], '1')
        self.assertEqual(error, 'fake-runtime-stderr\n')
        self.assertNotIn('按回车', output)
        self.assertNotIn('启动未完成', output)

    def test_eof_stdin_does_not_mask_the_runtime_exit_status(self):
        for expected in (0, 7):
            with self.subTest(expected=expected):
                code, output, error, rows = self.run_launcher(
                    exit_code=expected, stdin_eof=True)
                self.assertEqual(code, expected)
                self.assertEqual(rows['fake-runtime'], '1')
                self.assertEqual(error, 'fake-runtime-stderr\n')
                self.assertNotIn('按回车', output)


if __name__ == '__main__':
    unittest.main()
