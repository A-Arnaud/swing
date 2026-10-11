import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from project_profile import configure_checks, inspect_project
from supervisor.creation import new_file_allowed
from supervisor.engine import Supervisor, initialize
from supervisor.executor import CodexExecutor, validate_proposal
from supervisor.promotion import promote
from supervisor.safety import Blocked, git
from dashboard_data import changes


class ProjectCreationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'tests').mkdir()
        (self.source / 'README.md').write_text('# Simple app\nAn answer service.')
        (self.source / 'index.py').write_text('answer = 42\n')
        (self.source / 'tests/test_base.py').write_text('import unittest\nfrom index import answer\nclass Basic(unittest.TestCase):\n    def test_answer(self): self.assertEqual(answer,42)\n')
        self.files = ['README.md', 'index.py', 'tests/test_base.py']
        self.runtime = self.root / 'runtime'
        initialize(self.runtime, self.source, source_files=self.files)
        profile, argv = configure_checks(self.runtime, self.source, self.files)
        self.s = Supervisor(self.runtime, executor=Mock())
        self.s.config.update(live_enabled=True, acceptance_mode='application_checks',
                             persistent_backlog=True, allow_new_files=True, auto_promote_source=True,
                             allowed_files=['README.md', 'index.py'], protected_files=['tests/test_base.py'],
                             validation_argv=argv, benchmark_argv=[], max_disk_mb=1000)
        self.s.store.set('project_profile', profile)
        self.s.control('running')

    def tearDown(self):
        self.s.close()
        self.temp.cleanup()

    def proposal(self):
        return validate_proposal(dict(problem='Create answer endpoint helper', strategy='Add a reusable feature and regression test',
            files=['src/new_feature.py', 'tests/test_feature.py'], impact=8, cost=2, risk='low',
            evidence='README requests an answer service; no reusable helper exists.', success_metric='A tested helper returns 42'),
            git(self.s.repo, 'rev-parse', 'validated'))

    def editor(self, tree, proposal):
        (tree / 'src').mkdir(exist_ok=True)
        (tree / 'src/new_feature.py').write_text('def value():\n    return 42\n')
        (tree / 'tests/test_feature.py').write_text('import unittest\nfrom new_feature import value\nclass Feature(unittest.TestCase):\n    def test_value(self): self.assertEqual(value(),42)\n')

    def test_creation_tests_promotion_resume_and_new_file_diff(self):
        self.s.executor.discover.return_value = [self.proposal()]
        self.s.executor.implement.side_effect = self.editor
        result = self.s.cycle()
        self.assertEqual(result['status'], 'accepted_in_snapshot', result.get('reason'))
        self.assertEqual(result['promotion']['status'], 'applied')
        self.assertTrue((self.source / 'src/new_feature.py').is_file())
        self.assertIn('src/new_feature.py', self.s.config['allowed_files'])
        self.assertIn('tests/test_feature.py', self.s.config['protected_files'])
        self.assertNotIn('tests/test_feature.py', self.s.config['allowed_files'])
        self.assertIsNone(json.loads((Path(result['promotion']['backup']) / 'promotion.json').read_text())['before']['src/new_feature.py'])
        self.s.original_unchanged()
        diff = changes(self.runtime)
        self.assertIn('+def value():', next(f['diff'] for f in diff['files'] if f['name']=='src/new_feature.py'))
        self.s.close()
        self.s = Supervisor(self.runtime, executor=Mock())
        self.s.original_unchanged()
        self.assertIn('tests/test_feature.py', self.s.config['source_files'])

    def test_created_failing_test_prevents_source_publication(self):
        self.s.executor.discover.return_value = [self.proposal()]
        def wrong(tree, proposal):
            self.editor(tree, proposal)
            (tree / 'src/new_feature.py').write_text('def value():\n    return 0\n')
        self.s.executor.implement.side_effect = wrong
        result = self.s.cycle()
        self.assertEqual(result['reason'], 'independent_validation_failed')
        self.assertFalse((self.source / 'src/new_feature.py').exists())

    def test_promotion_failure_removes_created_file_and_restores_existing_file(self):
        candidate=self.root/'candidate'
        candidate.mkdir()
        (candidate/'index.py').write_text('answer = 43\n')
        (candidate/'new.py').write_text('value = 42\n')
        self.s.config['allowed_files'].append('new.py')
        before=self.s.store.get('source_manifest')
        import os
        replace=os.replace
        def fail(src,dst):
            if Path(dst).name=='index.py': raise OSError('simulated disk failure')
            return replace(src,dst)
        with patch('supervisor.promotion.os.replace',side_effect=fail):
            with self.assertRaises(OSError):
                promote(self.s,candidate,['new.py','index.py'],'a'*32)
        self.assertFalse((self.source/'new.py').exists())
        self.assertEqual((self.source/'index.py').read_text(),'answer = 42\n')
        self.assertEqual(self.s.store.get('source_manifest'),before)

    def test_static_assets_are_preserved_in_imported_snapshot(self):
        from supervisor.workspace import collect
        (self.source/'assets').mkdir()
        (self.source/'assets/logo.png').write_bytes(b'\x89PNG\r\n\0asset')
        files=self.files+['assets/logo.png']
        snapshot=collect(self.source,files)
        self.assertEqual(snapshot['assets/logo.png'],b'\x89PNG\r\n\0asset')
        runtime=self.root/'asset-runtime'
        initialize(runtime,self.source,source_files=files)
        self.assertEqual((runtime/'repository/assets/logo.png').read_bytes(),snapshot['assets/logo.png'])

    def test_new_source_collision_never_overwrites_operator_file(self):
        (self.source / 'src').mkdir()
        (self.source / 'src/new_feature.py').write_text('operator = True\n')
        self.s.executor.discover.return_value = [self.proposal()]
        result = self.s.cycle()
        self.assertEqual(result['reason'], 'new_file_conflicts_with_existing_path')
        self.s.executor.implement.assert_not_called()
        self.assertEqual((self.source / 'src/new_feature.py').read_text(), 'operator = True\n')

    def test_creation_cannot_write_configuration_hidden_files_or_traverse(self):
        for path in ['../bad.py', '.github/action.js', 'package.json', '.env', 'setup.py', 'tests/conftest.py', 'node_modules/a.js']:
            self.assertFalse(new_file_allowed(path, self.s.config), path)
        self.assertTrue(new_file_allowed('src/components/Button.tsx', self.s.config))
        self.assertTrue(new_file_allowed('tests/test_button.py', self.s.config))

    def test_baseline_failure_can_be_repaired_but_after_checks_still_required(self):
        (self.source / 'index.py').write_text('answer = 0\n')
        # Deliberately use a fresh import of the broken baseline.
        second = self.root / 'repair'
        initialize(second, self.source, source_files=self.files)
        profile, argv = configure_checks(second, self.source, self.files)
        s = Supervisor(second, executor=Mock())
        try:
            s.config.update(live_enabled=True, allow_repair_baseline=True, acceptance_mode='application_checks',
                            validation_argv=argv, benchmark_argv=[], allowed_files=['index.py'], protected_files=['tests/test_base.py','README.md'])
            s.control('running')
            p = validate_proposal(dict(problem='Wrong answer',strategy='Restore expected result',files=['index.py'],impact=8,cost=1,
                risk='low',evidence='Protected test expects 42, actual 0',success_metric='Existing regression succeeds'),git(s.repo,'rev-parse','validated'))
            s.executor.discover.return_value=[p]
            s.executor.implement.side_effect=lambda tree,p:(tree/'index.py').write_text('answer = 42\n')
            result=s.cycle()
            self.assertNotEqual(result['before_validation']['exit_code'],0)
            self.assertEqual(result['after_validation']['exit_code'],0)
            self.assertEqual(result['status'],'accepted_in_snapshot')
        finally:
            s.close()

    def test_understanding_is_saved_once_and_reused(self):
        self.s.config['project_onboarding'] = True
        self.s.executor.understand_project.return_value={'purpose':'Answer service','architecture':'One module','testing':'Unittest','priorities':['Create helper']}
        self.s.executor.discover.return_value=[self.proposal()]
        self.s.executor.implement.side_effect=self.editor
        self.assertEqual(self.s.cycle()['status'],'accepted_in_snapshot')
        self.s.executor.discover.return_value=[]
        self.s.cycle()
        self.s.executor.understand_project.assert_called_once()
        self.assertEqual(self.s.store.get('project_understanding')['purpose'],'Answer service')

    def test_structural_only_import_without_manual_command_is_honest(self):
        root=self.root/'plain'
        root.mkdir()
        (root/'main.py').write_text('def hello(): return 42\n')
        runtime=self.root/'profile'
        runtime.mkdir()
        profile,argv=configure_checks(runtime,root,['main.py'])
        self.assertEqual(profile['validation_level'],'structural')
        git(root,'init')
        git(root,'add','main.py')
        self.assertEqual(subprocess.run(argv,cwd=root,capture_output=True).returncode,0)
        (root/'main.py').write_text('def broken(\n')
        self.assertNotEqual(subprocess.run(argv,cwd=root,capture_output=True).returncode,0)

    def test_project_analysis_includes_frameworks_and_nested_checks(self):
        (self.source/'web').mkdir()
        (self.source/'web/package.json').write_text(json.dumps({'dependencies':{'react':'x'},'scripts':{'build':'vite build','test':'echo "Error: no test specified" && exit 1'}}))
        p=inspect_project(self.source,self.files+['web/package.json'])
        self.assertIn('react',p['frameworks'])
        self.assertTrue(any(r['cwd']=='web' and r['command']=='npm run build' for r in p['recipes']))
        self.assertFalse(any(r['command']=='npm run test' for r in p['recipes']))
        self.assertIn('Python',p['languages'])

    def test_structural_only_code_changes_need_real_regression_tests(self):
        runtime=self.root/'structural-runtime'
        files=['README.md','index.py']
        initialize(runtime,self.source,source_files=files)
        profile,argv=configure_checks(runtime,self.source,files)
        s=Supervisor(runtime,executor=Mock())
        try:
            s.config.update(live_enabled=True,allow_new_files=True,acceptance_mode='application_checks',
                allowed_files=['index.py'],protected_files=['README.md'],validation_argv=argv,benchmark_argv=[],validation_level='structural')
            s.control('running')
            base=git(s.repo,'rev-parse','validated')
            item=dict(problem='New helper',strategy='Add helper',files=['src/new_feature.py'],impact=8,cost=2,risk='low',
                      evidence='Missing helper in answer service',success_metric='Helper returns 42')
            s.executor.discover.return_value=[validate_proposal(item,base)]
            def code_only(tree,p):
                (tree/'src').mkdir(exist_ok=True)
                (tree/'src/new_feature.py').write_text('def value(): return 42\n')
            s.executor.implement.side_effect=code_only
            self.assertEqual(s.cycle()['reason'],'behavioral_tests_required')
            item['files'].append('tests/test_feature.py')
            s.executor.discover.return_value=[validate_proposal(item,base)]
            def with_test(tree,p):
                (tree/'tests').mkdir(exist_ok=True)
                self.editor(tree,p)
            s.executor.implement.side_effect=with_test
            result=s.cycle()
            self.assertEqual(result['status'],'accepted_in_snapshot',result.get('reason'))
            self.assertEqual(result['after_validation']['check_level'],'project_checks')
        finally:
            s.close()

    def test_passing_build_does_not_hide_failure_in_new_regression_test(self):
        from github_projects import command
        try:
            command('npm run build')
        except ValueError:
            self.skipTest('Node/npm unavailable')
        (self.source/'package.json').write_text(json.dumps({'scripts':{'build':'node -e "process.exit(0)"'}}))
        files=['README.md','index.py','package.json']
        runtime=self.root/'build-runtime'
        initialize(runtime,self.source,source_files=files)
        profile,argv=configure_checks(runtime,self.source,files)
        s=Supervisor(runtime,executor=Mock())
        try:
            s.config.update(live_enabled=True,allow_new_files=True,acceptance_mode='application_checks',
                allowed_files=['index.py'],protected_files=['README.md','package.json'],validation_argv=argv,benchmark_argv=[])
            s.control('running')
            p=validate_proposal(dict(problem='Add helper',strategy='Create helper with regression',
                files=['src/new_feature.py','tests/test_feature.py'],impact=8,cost=2,risk='low',
                evidence='Missing helper',success_metric='Helper returns 42'),git(s.repo,'rev-parse','validated'))
            s.executor.discover.return_value=[p]
            def wrong(tree,p):
                (tree/'tests').mkdir(exist_ok=True)
                self.editor(tree,p)
                (tree/'src/new_feature.py').write_text('def value(): return 0\n')
            s.executor.implement.side_effect=wrong
            result=s.cycle()
            self.assertEqual(result['before_validation']['exit_code'],0)
            self.assertEqual(result['reason'],'independent_validation_failed')
        finally:
            s.close()

    def test_codex_understanding_contract_contains_profile_and_creation_scope(self):
        transport=CodexExecutor(self.s.config,self.s.store,self.s.interrupted)
        response={'purpose':'Answer service','architecture':'Module index.py','testing':'Unittest checks','priorities':['Create helper']}
        with patch.object(transport,'call',return_value=json.dumps(response)) as call:
            result=transport.understand_project(self.s.repo,git(self.s.repo,'rev-parse','validated'))
        self.assertIn('PROJECT_SETUP',call.call_args.args[1])
        self.assertIn('CREATION_SCOPE',call.call_args.args[1])
        self.assertEqual(result['purpose'],'Answer service')


if __name__=='__main__':
    unittest.main()
