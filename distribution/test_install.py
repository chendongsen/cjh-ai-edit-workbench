import importlib.util
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('installer', Path(__file__).with_name('installer.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallSafety(unittest.TestCase):
    def fixture(self, root):
        payload = root/'payload'
        payload.mkdir()
        (payload/'file.txt').write_text('fixture')
        (root/'manifest.json').write_text(json.dumps({'files': {
            'file.txt': {'size': 7, 'sha256': hashlib.sha256(b'fixture').hexdigest()}}}))
        return payload

    def test_tampering_and_unlisted_files_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = self.fixture(root)
            with patch.object(installer, 'BASE', root):
                installer.verify(payload)
                (payload/'unexpected.json').write_text('{}')
                with self.assertRaises(ValueError):
                    installer.verify(payload)
                (payload/'file.txt').write_text('changed')
                with self.assertRaises(ValueError):
                    installer.verify(payload)

    def test_external_link_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root/'payload'
            payload.mkdir()
            (root/'secret').write_text('not for distribution')
            (payload/'escape').symlink_to('../secret')
            (root/'manifest.json').write_text(json.dumps({'files': {'escape': {'link': '../secret'}}}))
            with patch.object(installer, 'BASE', root), self.assertRaises(ValueError):
                installer.verify(payload)

    def test_existing_install_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'user-record.json').write_text('must survive')
            with patch.object(installer.platform, 'machine', return_value='arm64'), patch.object(
                installer.platform, 'system', return_value='Darwin'), patch.object(
                installer.platform, 'mac_ver', return_value=('14.0', (), '')):
                with self.assertRaises(ValueError):
                    installer.install(root)
            self.assertEqual((root/'user-record.json').read_text(), 'must survive')

    def test_desktop_link_is_relocatable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'安装 路径'
            (root/'CJH剪辑工作台.app').mkdir(parents=True)
            desktop = Path(tmp)/'Desktop'
            link = installer.desktop_shortcut(root, desktop)
            self.assertEqual(link.resolve(), (root/'CJH剪辑工作台.app').resolve())
            self.assertEqual(link, installer.desktop_shortcut(root, desktop))

    def test_desktop_preserves_existing_and_broken_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'CJH剪辑工作台.app').mkdir()
            desktop = root/'Desktop'
            desktop.mkdir()
            old = desktop/'CJH剪辑工作台.app'
            old.symlink_to(root/'missing-old-install')
            link = installer.desktop_shortcut(root, desktop)
            self.assertIn(installer.VERSION, link.name)
            self.assertEqual(old.readlink(), root/'missing-old-install')
            other = root/'another'
            (other/'CJH剪辑工作台.app').mkdir(parents=True)
            with self.assertRaises(FileExistsError):
                installer.desktop_shortcut(other, desktop)

    def test_package_versions_consistent(self):
        import ast
        root = Path(__file__).resolve().parents[1]
        for file in (root/'distribution/build.py', root/'backend/beta.py'):
            tree = ast.parse(file.read_text())
            version = next(n.value.value for n in tree.body if isinstance(n, ast.Assign)
                           and any(isinstance(t, ast.Name) and t.id == 'VERSION' for t in n.targets))
            self.assertEqual(installer.VERSION, version)


if __name__ == '__main__':
    unittest.main()
