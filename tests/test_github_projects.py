import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from dashboard_data import save_project, status
from github_projects import github_url, import_project, resolve_runtime, detect_validation, command
from supervisor.engine import Supervisor


class GitHubProjectTests(unittest.TestCase):
    def test_python_validation_loads_candidate_source_and_detects_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / 'src/token_radar'
            package.mkdir(parents=True)
            (package / '__init__.py').write_text('')
            module = package / 'candidate.py'
            module.write_text('value = 42\n')
            tests = root / 'tests'
            tests.mkdir()
            (tests / 'test_value.py').write_text(
                'import unittest\nfrom token_radar.candidate import value\n'
                'class TestValue(unittest.TestCase):\n'
                '    def test_value(self): self.assertEqual(value, 42)\n')
            specification = root / 'command.json'
            specification.write_text(json.dumps([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests']))
            harness = Path(__file__).resolve().parents[1] / 'imported_checks.py'
            argv = [sys.executable, '-I', '-B', str(harness), str(specification)]
            self.assertEqual(subprocess.run(argv, cwd=root, capture_output=True).returncode, 0)
            module.write_text('value = 43\n')
            self.assertNotEqual(subprocess.run(argv, cwd=root, capture_output=True).returncode, 0)

    def test_only_normal_github_https_urls(self):
        self.assertEqual(github_url('https://github.com/A-Arnaud/swing.git'),
                         ('https://github.com/A-Arnaud/swing.git', 'swing'))
        for url in ['file:///tmp/project', 'https://user:token@github.com/u/r',
                    'https://github.com/u/../r', 'https://example.com/u/r',
                    'https://github.com/u/r?token=secret']:
            with self.assertRaises(ValueError):
                github_url(url)

    def test_import_has_separate_runtime_and_existing_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            default = Path(tmp) / 'runtime-default'
            default.mkdir()
            (default / 'config.json').write_text(json.dumps(dict(codex_executable='codex',
                max_ai_calls_per_day=256, max_tokens_per_day=25000000, reserve_tokens_per_call=100000,
                max_call_seconds=600, validation_timeout_seconds=300)))
            actual_run = subprocess.run
            actual_output = subprocess.check_output

            def run(argv, **kwargs):
                if argv[:2] == ['git', 'clone']:
                    source = Path(argv[-1])
                    (source / 'tests').mkdir(parents=True)
                    (source / 'index.py').write_text('answer = 42\n')
                    (source / 'tests/test_main.py').write_text('assert True\n')
                    (source / 'README.md').write_text('# Sample\n')
                    (source / '.env').write_text('SECRET=private\n')
                    return subprocess.CompletedProcess(argv, 0)
                return actual_run(argv, **kwargs)

            def output(argv, **kwargs):
                if 'ls-files' in argv:
                    return b'index.py\0tests/test_main.py\0README.md\0.env\0'
                return actual_output(argv, **kwargs)

            with patch('github_projects.subprocess.run', side_effect=run), \
                 patch('github_projects.subprocess.check_output', side_effect=output):
                project = import_project(default, dict(url='https://github.com/u/sample',
                    validation='python -m unittest discover -s tests', instructions='Correct demonstrated defects.'))
            self.assertEqual(project['name'], 'sample')
            runtime = resolve_runtime(default, project['id'])
            supervisor = Supervisor(runtime)
            try:
                self.assertEqual(supervisor.config['allowed_files'], ['index.py', 'README.md'])
                self.assertIn('tests/test_main.py', supervisor.config['protected_files'])
                self.assertTrue(supervisor.config['chatgpt_quota_guard'])
                self.assertTrue(supervisor.config['auto_promote_source'])
                self.assertFalse((runtime / 'repository/.env').exists())
                self.assertEqual(supervisor.config['validation_argv'][0], sys.executable)
                supervisor.original_unchanged()
                self.assertEqual(supervisor.store.get('control')['mode'], 'stopped')
                self.assertEqual(status(runtime)['project']['name'], 'sample')
                from supervisor.memory import refresh
                memory = refresh(supervisor.repo, supervisor.config, supervisor.store, 'test-baseline')
                self.assertIn('index.py', memory['files'])
                (runtime.parent / 'source/index.py').write_text('answer = 43\n')
                with self.assertRaises(RuntimeError):
                    supervisor.original_unchanged()
            finally:
                supervisor.close()
            with self.assertRaises(ValueError):
                save_project(default, dict(id=project['id'], name='X', idea='Y', instructions=''))
            with self.assertRaises(ValueError):
                resolve_runtime(default, '../../etc')

    def test_detects_project_validation_without_running_repo_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'package.json').write_text(json.dumps({'scripts': {'test': 'node --test'}}))
            self.assertEqual(detect_validation(root, ['package.json']), 'npm test')
            self.assertEqual(detect_validation(root, ['go.mod']), 'go test ./...')
            self.assertEqual(detect_validation(root, ['Cargo.toml']), 'cargo test')
            self.assertEqual(detect_validation(root, ['App.csproj']), 'dotnet test')
            with self.assertRaises(ValueError):
                detect_validation(root, ['README.md'])

    def test_npm_is_resolved_to_node_cli_without_a_shell(self):
        try:
            argv = command('npm test')
        except ValueError:
            self.skipTest('npm runtime unavailable')
        self.assertTrue(argv[1].endswith('npm-cli.js'))
        self.assertEqual(argv[-1], 'test')
