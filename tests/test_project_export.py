import io
import json
import unittest
import zipfile
from unittest.mock import patch

from project_export import archive
from supervisor.safety import Blocked, git
from supervisor.store import Store
from tests import test_repository_browser


class ProjectExportTests(unittest.TestCase):
    def setUp(self):
        test_repository_browser.RepositoryBrowserTests.setUp(self)
        (self.runtime / 'config.json').write_text(json.dumps({'project_name': 'Example App'}))

    def tearDown(self):
        test_repository_browser.RepositoryBrowserTests.tearDown(self)

    def test_zip_contains_modified_and_new_local_code_without_commit(self):
        (self.source / 'src/main.py').write_bytes(b'answer = 42\n')
        (self.source / 'new.py').write_bytes(b'new = True\n')
        before = git(self.source, 'rev-parse', 'HEAD')
        status = git(self.source, 'status', '--porcelain')
        filename, payload = archive(self.runtime)
        self.assertTrue(filename.startswith('Example-App-'))
        with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
            self.assertEqual(zipped.read('Example-App/src/main.py'), b'answer = 42\n')
            self.assertEqual(zipped.read('Example-App/new.py'), b'new = True\n')
            self.assertIn('Example-App/README.md', zipped.namelist())
            self.assertFalse(any('/.git/' in name for name in zipped.namelist()))
        self.assertEqual(git(self.source, 'rev-parse', 'HEAD'), before)
        self.assertEqual(git(self.source, 'status', '--porcelain'), status)

    def test_secrets_dependencies_and_runtime_excluded_workflows_preserved(self):
        (self.source / '.env').write_text('SECRET=private')
        (self.source / '.env.example').write_text('SECRET=your_value')
        (self.source / 'note.txt').write_text('ghp_' + 'a' * 40)
        for folder in ['.local', 'node_modules', '.github/workflows']:
            (self.source / folder).mkdir(parents=True)
            (self.source / folder / 'file.yml').write_text('name: example')
        _, payload = archive(self.runtime)
        with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
            names = zipped.namelist()
            self.assertNotIn('Example-App/.env', names)
            self.assertNotIn('Example-App/note.txt', names)
            self.assertNotIn('Example-App/.local/file.yml', names)
            self.assertNotIn('Example-App/node_modules/file.yml', names)
            self.assertIn('Example-App/.env.example', names)
            self.assertIn('Example-App/.github/workflows/file.yml', names)

    def test_pending_promotion_prevents_mixed_export(self):
        store = Store(self.runtime / 'state.sqlite')
        store.set('pending_promotion', 'in progress')
        store.close()
        with self.assertRaisesRegex(Blocked, 'export_source_busy'):
            archive(self.runtime)

    def test_concurrent_source_change_prevents_inconsistent_zip(self):
        original = zipfile.ZipFile.writestr
        def change(zipped, info, data):
            original(zipped, info, data)
            if info.filename.endswith('src/main.py'):
                (self.source / 'src/main.py').write_bytes(b'changed during export\n')
        with patch.object(zipfile.ZipFile, 'writestr', change):
            with self.assertRaisesRegex(Blocked, 'export_source_busy'):
                archive(self.runtime)


if __name__ == '__main__':
    unittest.main()
