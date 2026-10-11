from pathlib import Path
import tempfile
import unittest

from repository_browser import browse
from supervisor.safety import Blocked, git
from supervisor.store import Store


class RepositoryBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        git(self.source, 'init', '-b', 'main')
        git(self.source, 'config', 'user.name', 'Test')
        git(self.source, 'config', 'user.email', 'test@example.invalid')
        (self.source / 'src').mkdir()
        (self.source / 'src/main.py').write_bytes(b'answer = 1\n')
        (self.source / 'README.md').write_bytes(b'# Project\nHello\n')
        git(self.source, 'add', '.')
        git(self.source, 'commit', '-m', 'Initial project')
        git(self.source, 'branch', 'original')
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        store = Store(self.runtime / 'state.sqlite')
        store.set('source', str(self.source))
        store.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_live_tree_readme_and_file_update(self):
        view = browse(self.runtime)
        self.assertEqual([row['name'] for row in view['entries']], ['src', 'README.md'])
        self.assertEqual(view['readme']['content'], '# Project\nHello\n')
        self.assertEqual(view['entries'][0]['message'], 'Initial project')
        (self.source / 'src/main.py').write_bytes(b'answer = 2\n')
        (self.source / 'new.py').write_text('new = True\n')
        view = browse(self.runtime, directory='src', file='src/main.py')
        self.assertEqual(view['file']['content'], 'answer = 2\n')
        self.assertTrue(view['entries'][0]['modified'])
        self.assertIn('new.py', [row['name'] for row in browse(self.runtime)['entries']])

    def test_branch_is_readonly_and_preserves_exact_code(self):
        (self.source / 'src/main.py').write_bytes(b'answer = 2\n')
        view = browse(self.runtime, branch='original', file='src/main.py')
        self.assertFalse(view['local'])
        self.assertEqual(view['file']['content'], 'answer = 1\n')
        self.assertEqual(git(self.source, 'branch', '--show-current'), 'main')
        self.assertEqual((self.source / 'src/main.py').read_text(), 'answer = 2\n')
        with self.assertRaises(ValueError):
            browse(self.runtime, branch='--all')

    def test_secrets_traversal_binary_and_size_limits(self):
        (self.source / '.env').write_text('SECRET=private')
        (self.source / 'note.md').write_text('ghp_' + 'a' * 40)
        (self.source / 'large.txt').write_bytes(b'a' * 300000)
        (self.source / 'image.png').write_bytes(b'\x89PNG\x00\xff')
        self.assertNotIn('.env', [row['name'] for row in browse(self.runtime)['entries']])
        for file in ['.env', '../outside', 'C:/Windows/win.ini', 'src/../../outside']:
            with self.assertRaises((ValueError, Blocked)):
                browse(self.runtime, file=file)
        self.assertIn('unavailable', browse(self.runtime, file='note.md')['file'])
        self.assertIn('unavailable', browse(self.runtime, file='large.txt')['file'])
        self.assertIn('unavailable', browse(self.runtime, file='image.png')['file'])
        git(self.source, 'add', 'image.png')
        git(self.source, 'commit', '-m', 'Image')
        git(self.source, 'branch', 'image')
        self.assertIn('unavailable', browse(self.runtime, branch='image', file='image.png')['file'])

    def test_selected_source_is_independent(self):
        other = self.root / 'other'
        other.mkdir()
        git(other, 'init', '-b', 'main')
        (other / 'README.md').write_text('Other project')
        git(other, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'add', '.')
        git(other, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'Other')
        store = Store(self.runtime / 'state.sqlite')
        store.set('source', str(other))
        store.close()
        self.assertEqual(browse(self.runtime)['readme']['content'], 'Other project')

    def test_linked_directory_cannot_expose_external_files(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'private.txt').write_text('private')
        try:
            (self.source / 'linked').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Symbolic links unavailable')
        self.assertNotIn('linked', [row['name'] for row in browse(self.runtime)['entries']])
        with self.assertRaises((ValueError, Blocked)):
            browse(self.runtime, file='linked/private.txt')


if __name__ == '__main__':
    unittest.main()
