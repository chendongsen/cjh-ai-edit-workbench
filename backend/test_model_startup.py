import tempfile
import unittest
import io
import urllib.error
from pathlib import Path
from unittest.mock import patch, Mock
import model_startup as startup


class Models(unittest.TestCase):
    def test_live_service_never_spawns(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(startup, 'DATA', Path(tmp)), patch.object(startup, 'live_models', return_value=[startup.DEFAULT]), patch.object(startup.subprocess, 'Popen') as spawn:
            self.assertEqual(startup.ensure('stale-setting', lambda s: None), startup.DEFAULT)
            spawn.assert_not_called()

    def test_cold_service_starts_only_bundled_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root/'ollama-runtime/Ollama.app/Contents/Resources/ollama'
            exe.parent.mkdir(parents=True); exe.touch()
            socket = Mock(); socket.__enter__ = Mock(return_value=socket); socket.__exit__ = Mock()
            socket.connect_ex.return_value = 1
            with patch.object(startup, 'DATA', root), patch.object(startup, 'installed_models', return_value=[startup.DEFAULT]), patch.object(startup, 'live_models', side_effect=[None, [startup.DEFAULT]]), patch.object(startup.socket, 'socket', return_value=socket), patch.object(startup.subprocess, 'Popen') as spawn:
                self.assertEqual(startup.ensure(None, lambda s: None), startup.DEFAULT)
                self.assertEqual(spawn.call_args.args[0], [str(exe), 'serve'])
                self.assertEqual(spawn.call_args.kwargs['env']['OLLAMA_NO_CLOUD'], '1')

    def test_missing_files_do_not_download(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(startup, 'DATA', Path(tmp)), patch.object(startup, 'live_models', return_value=None), patch.object(startup.socket, 'socket') as sock, patch.object(startup.subprocess, 'Popen') as spawn:
            sock.return_value.__enter__.return_value.connect_ex.return_value = 1
            with self.assertRaisesRegex(RuntimeError, '确实缺失'):
                startup.ensure(None, lambda s: None)
            spawn.assert_not_called()

    def test_listening_service_recovers_on_recheck_without_spawn(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(startup, 'DATA', Path(tmp)), patch.object(startup, 'live_models', side_effect=[None, [startup.DEFAULT]]), patch.object(startup.socket, 'socket') as sock, patch.object(startup.subprocess, 'Popen') as spawn:
            sock.return_value.__enter__.return_value.connect_ex.return_value = 0
            self.assertEqual(startup.ensure(None, lambda s: None), startup.DEFAULT)
            spawn.assert_not_called()

    def test_http_failure_on_listener_reports_real_cause_without_spawn(self):
        error = urllib.error.HTTPError(startup.OLLAMA_URL, 502, 'Bad Gateway', {}, None)
        with tempfile.TemporaryDirectory() as tmp, patch.object(startup, 'DATA', Path(tmp)), patch.object(startup, 'local_urlopen', side_effect=error), patch.object(startup.socket, 'socket') as sock, patch.object(startup.subprocess, 'Popen') as spawn:
            sock.return_value.__enter__.return_value.connect_ex.return_value = 0
            with self.assertRaisesRegex(RuntimeError, 'HTTP 502') as raised:
                startup.ensure(None, lambda s: None)
            self.assertNotIn('端口已占用', str(raised.exception))
            self.assertNotIn('确实缺失', str(raised.exception))
            spawn.assert_not_called()

    def test_non_model_service_response_is_not_a_ready_model_list(self):
        errors = []
        with patch.object(startup, 'local_urlopen', return_value=io.BytesIO(b'{"status":"ok"}')):
            self.assertIsNone(startup.live_models(errors))
        self.assertEqual(errors, ['模型接口返回内容格式不正确'])

    def test_remote_url_refused(self):
        with patch.object(startup, 'OLLAMA_URL', 'https://example.org'), patch.object(startup, 'live_models') as query:
            with self.assertRaisesRegex(RuntimeError, '回环'):
                startup.ensure()
            query.assert_not_called()

    def test_default_selection_handles_stale_and_missing_preferences(self):
        self.assertEqual(startup.choose('missing', ['base', 'small'], ['small', 'base']), 'small')
        self.assertEqual(startup.choose('base', ['base', 'small'], ['small']), 'base')
        self.assertIsNone(startup.choose(None, [], ['small']))

    def test_empty_speech_cache_is_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(startup, 'DATA', Path(tmp)):
            snapshot=Path(tmp)/'models/models--Systran--faster-whisper-small/snapshots/test'
            snapshot.mkdir(parents=True)
            self.assertEqual(startup.speech_models(), [])
            for name in ('model.bin','config.json','tokenizer.json','vocabulary.txt'):
                (snapshot/name).write_bytes(b'test')
            self.assertEqual(startup.speech_models(), ['small'])
