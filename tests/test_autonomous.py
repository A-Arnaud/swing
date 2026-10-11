import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from supervisor.engine import Supervisor, exclusive, initialize
from supervisor.promotion import promote
from supervisor.safety import Blocked, digest
from supervisor.store import Store
from supervisor.workspace import collect


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.source, self.worktree, self.runtime = root / 'source', root / 'candidate', root / 'runtime'
        self.runtime.mkdir()
        for folder in (self.source, self.worktree):
            (folder / 'src').mkdir(parents=True)
            (folder / 'src/a.py').write_text('old a\n')
            (folder / 'src/b.py').write_text('old b\n')
        self.store = Store(self.runtime / 'state.sqlite')
        self.store.set('source', str(self.source))
        self.store.set('source_manifest', {n: digest(v) for n, v in collect(self.source).items()})
        self.supervisor = SimpleNamespace(root=self.runtime, store=self.store,
                                          config={'allowed_files': ['src/a.py', 'src/b.py']},
                                          interrupted=lambda: False)
        (self.worktree / 'src/a.py').write_text('new a\n')
        (self.worktree / 'src/b.py').write_text('new b\n')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_applies_all_files_and_keeps_exact_backup_and_manifest(self):
        result = promote(self.supervisor, self.worktree, ['src/a.py', 'src/b.py'], '1' * 32)
        self.assertEqual(result['status'], 'applied')
        self.assertEqual((self.source / 'src/a.py').read_text(), 'new a\n')
        self.assertEqual((Path(result['backup']) / 'src/a.py').read_text(), 'old a\n')
        expected = {n: digest(v) for n, v in collect(self.source).items()}
        self.assertEqual(self.store.get('source_manifest'), expected)
        self.assertIsNone(self.store.get('pending_promotion'))

    def test_user_edit_is_never_overwritten(self):
        (self.source / 'src/a.py').write_text('user edit\n')
        with self.assertRaisesRegex(Blocked, 'source_changed_before_promotion'):
            promote(self.supervisor, self.worktree, ['src/a.py'], '2' * 32)
        self.assertEqual((self.source / 'src/a.py').read_text(), 'user edit\n')

    def test_second_file_write_failure_rolls_back_first_file(self):
        import os
        replace = os.replace
        def fail_second(src, dst):
            if Path(dst).name == 'b.py':
                raise OSError('simulated disk failure')
            return replace(src, dst)
        before = self.store.get('source_manifest')
        with patch('supervisor.promotion.os.replace', side_effect=fail_second):
            with self.assertRaises(OSError):
                promote(self.supervisor, self.worktree, ['src/a.py', 'src/b.py'], '3' * 32)
        self.assertEqual((self.source / 'src/a.py').read_text(), 'old a\n')
        self.assertEqual((self.source / 'src/b.py').read_text(), 'old b\n')
        self.assertEqual(self.store.get('source_manifest'), before)
        self.assertIsNone(self.store.get('pending_promotion'))

    def test_interrupted_promotion_journal_blocks_next_cycle(self):
        self.store.set('pending_promotion', 'unfinished.json')
        with self.assertRaisesRegex(Blocked, 'incomplete_promotion'):
            Supervisor.original_unchanged(self.supervisor)


class ApplicationAcceptanceTests(unittest.TestCase):
    def test_independent_checks_reject_wrong_result_without_benchmark(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'runtime'
            initialize(root)
            config = json.loads((root / 'config.json').read_text())
            config.update(acceptance_mode='application_checks', benchmark_argv=[])
            (root / 'config.json').write_text(json.dumps(config))
            s = Supervisor(root)
            try:
                s.control('running')
                self.assertEqual(s.cycle()['reason'], 'independent_validation_failed')
                accepted = s.cycle()
                self.assertEqual(accepted['status'], 'accepted_in_snapshot')
                self.assertEqual(accepted['acceptance'], 'independent_application_tests_and_lint')
            finally:
                s.close()

    def test_launcher_lock_prevents_duplicate_process_for_entire_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with exclusive(root, 'launcher.lock'):
                with self.assertRaisesRegex(Blocked, 'supervisor_already_running'):
                    with exclusive(root, 'launcher.lock'):
                        self.fail('second launcher entered')


if __name__ == '__main__':
    unittest.main()
