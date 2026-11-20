"""A disabled optional engine must not break imports in the next installer."""
import ast
import importlib.util
from pathlib import Path
import unittest


class BackendPayloadTests(unittest.TestCase):
    def test_allowlist_contains_every_local_import_dependency(self):
        folder = Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location('cjh_payload_builder', folder/'build.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        backend = folder.parent/'backend'
        included = set(builder.BACKEND)
        missing = []
        for name in included:
            if not name.endswith('.py'):
                continue
            tree = ast.parse((backend/name).read_text())
            for node in ast.walk(tree):
                modules = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                           else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                for module in modules:
                    filename = module.split('.')[0]+'.py'
                    if (backend/filename).is_file() and filename not in included:
                        missing.append((name, filename))
        self.assertEqual(missing, [])
        self.assertTrue({'precise_alignment_worker.py', 'video_retrieval_worker.py'} <= included)


if __name__ == '__main__':
    unittest.main()
