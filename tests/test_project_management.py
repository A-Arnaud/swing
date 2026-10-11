import json
import os
from pathlib import Path
import tempfile
import unittest

from dashboard_data import save_project
from project_management import delete_project, main_visible, preview, runtime_guard
from supervisor.engine import initialize
from supervisor.safety import Blocked
from supervisor.store import Store


class ProjectManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.default = self.root / 'runtime'
        initialize(self.default)
        self.original = self.root / 'original'
        self.original.mkdir()
        (self.original / 'file.txt').write_text('original stays')
        self.store(self.default, source=str(self.original), control={'mode': 'stopped'})
        self.project = save_project(self.default, {'name': 'Exact Name', 'idea': 'Example', 'instructions': ''})
        self.folder = self.root / 'swing-projects' / self.project['id']

    def tearDown(self):
        self.temp.cleanup()

    def store(self, runtime, **values):
        store = Store(runtime / 'state.sqlite')
        for key, value in values.items():
            store.set(key, value)
        store.close()

    def body(self, identifier=None):
        value = preview(self.default, self.project['id'] if identifier is None else identifier)
        return {'id': value['id'], 'confirmation': value['name'], 'fingerprint': value['fingerprint']}

    def connected(self):
        source = self.folder / 'source'
        source.mkdir()
        (source / 'main.py').write_text('value = 42')
        runtime = self.folder / 'runtime'
        initialize(runtime)
        self.store(runtime, source=str(source), control={'mode': 'stopped'})
        self.project['state'] = 'connected'
        (self.folder / 'project.json').write_text(json.dumps(self.project))
        return runtime

    def test_exact_name_required_and_wrong_name_preserves_files(self):
        for name in ['exact name', 'Exact Name ', '', '../original']:
            with self.assertRaisesRegex(ValueError, 'project_name_mismatch'):
                delete_project(self.default, {**self.body(), 'confirmation': name})
            self.assertTrue(self.folder.exists())
        self.assertEqual(delete_project(self.default, self.body())['status'], 'deleted')
        self.assertFalse(self.folder.exists())
        self.assertEqual((self.original / 'file.txt').read_text(), 'original stays')

    def test_connected_project_copy_and_readonly_files_are_deleted(self):
        self.connected()
        file = self.folder / 'readonly.txt'
        file.write_text('owned file')
        os.chmod(file, 0o444)
        self.assertEqual(delete_project(self.default, self.body())['status'], 'deleted')
        self.assertFalse(self.folder.exists())

    def test_active_work_pending_promotion_and_owned_worker_are_blocked(self):
        runtime = self.connected()
        self.store(runtime, control={'mode': 'running'})
        self.assertTrue(preview(self.default, self.project['id'])['busy'])
        with self.assertRaisesRegex(ValueError, 'project_busy'):
            delete_project(self.default, self.body())
        self.store(runtime, control={'mode': 'stopped'}, pending_promotion='pending')
        with self.assertRaisesRegex(ValueError, 'project_busy'):
            delete_project(self.default, self.body())
        self.store(runtime, pending_promotion=None)
        with self.assertRaisesRegex(ValueError, 'project_busy'):
            delete_project(self.default, self.body(), worker_active=lambda _: True)
        self.assertTrue(self.folder.exists())

    def test_runtime_lifetime_lock_blocks_deletion_even_while_control_stopped(self):
        runtime = self.connected()
        with runtime_guard(runtime):
            with self.assertRaisesRegex(ValueError, 'project_busy'):
                delete_project(self.default, self.body())
        self.assertTrue(self.folder.exists())

    def test_renamed_project_requires_a_new_confirmation(self):
        body = self.body()
        self.project['idea'] = 'Changed since confirmation'
        (self.folder / 'project.json').write_text(json.dumps(self.project))
        with self.assertRaisesRegex(ValueError, 'project_changed_retry'):
            delete_project(self.default, body)
        self.assertTrue(self.folder.exists())

    def test_external_source_is_never_deleted(self):
        runtime = self.connected()
        self.store(runtime, source=str(self.original))
        with self.assertRaisesRegex(ValueError, 'external_project_source'):
            delete_project(self.default, {'id': self.project['id'], 'confirmation': 'Exact Name'})
        self.assertTrue((self.original / 'file.txt').exists())

    def test_linked_directory_and_traversal_are_rejected(self):
        for identifier in ['..', '../original', str(self.original), 'a' * 31, 'x' * 32]:
            with self.assertRaises(ValueError):
                delete_project(self.default, {'id': identifier, 'confirmation': 'Exact Name'})
        try:
            (self.folder / 'outside').symlink_to(self.original, target_is_directory=True)
        except OSError:
            self.skipTest('Symbolic links unavailable')
        with self.assertRaises((Blocked, ValueError)):
            delete_project(self.default, self.body())
        self.assertTrue((self.original / 'file.txt').exists())

    def test_original_project_can_be_removed_from_swing_without_deleting_source(self):
        body = self.body('')
        self.assertEqual(preview(self.default, '')['kind'], 'detach')
        result = delete_project(self.default, body)
        self.assertTrue(result['source_preserved'])
        self.assertFalse(main_visible(self.default))
        self.assertTrue((self.original / 'file.txt').exists())
        self.assertTrue((self.default / 'state.sqlite').exists())


if __name__ == '__main__':
    unittest.main()
