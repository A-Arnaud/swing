import json
import subprocess
import unittest
from unittest.mock import patch, Mock

from github_account import GitHubAccount, repositories, credential


class GitHubAccountTests(unittest.TestCase):
    def test_status_never_returns_credentials(self):
        with patch('github_account.request', return_value={'login': 'operator', 'token': 'never-publish'}):
            account = GitHubAccount()
            result = account.status()
            self.assertTrue(result['connected'])
            self.assertEqual(result['login'], 'operator')
            self.assertNotIn('never-publish', json.dumps(result))

    def test_missing_credentials_are_not_interactive(self):
        with patch('github_account.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', '')) as run:
            with self.assertRaises(ValueError):
                credential()
            self.assertEqual(run.call_args.kwargs['env']['GCM_INTERACTIVE'], 'never')

    def test_login_uses_browser_and_does_not_spawn_twice(self):
        process = Mock()
        process.poll.return_value = None
        with patch('github_account.subprocess.Popen', return_value=process) as spawn:
            account = GitHubAccount()
            account.connect()
            account.connect()
            self.assertEqual(spawn.call_count, 1)
            self.assertIn('--browser', spawn.call_args.args[0])

    def test_repository_projection_and_pagination(self):
        rows = [{'full_name': f'owner/r{i}', 'html_url': f'https://github.com/owner/r{i}',
                 'private': True, 'permissions': {'pull': True}} for i in range(100)]
        with patch('github_account.request', return_value=rows):
            result = repositories(2)
            self.assertEqual(result['next_page'], 3)
            self.assertEqual(len(result['repositories']), 100)
            self.assertTrue(result['repositories'][0]['private'])
        with self.assertRaises(ValueError):
            repositories(-1)
