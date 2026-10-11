import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from supervisor.engine import Supervisor, initialize
from supervisor.executor import CodexExecutor, validate_proposal
from supervisor.memory import context, refresh, summarize
from supervisor.ralph import RalphExecutor
from supervisor.safety import git
from supervisor.store import Store


def candidate(base, strategy='first', risk='low'):
    return validate_proposal({'problem': 'Concrete summation issue ' + strategy,
                              'strategy': strategy, 'files': ['calc.py'],
                              'impact': 6 if strategy == 'first' else 4, 'cost': 1,
                              'risk': risk, 'evidence': 'Specific reproduced boundary fixture',
                              'success_metric': 'Existing exact sums remain identical'}, base)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.worktree = self.root / 'snapshot'
        self.worktree.mkdir()
        (self.worktree / 'src').mkdir()
        (self.worktree / 'src/a.py').write_text('def old_function(): pass\n')
        (self.worktree / 'README.md').write_text('# Purpose\n')
        (self.root / 'project-brief.md').write_text('Reliable project mission')
        self.store = Store(self.root / 'state.sqlite')
        self.config = {'allowed_files': ['src/a.py'], 'protected_files': ['README.md']}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_persistent_cache_only_rebuilds_changed_files_and_removes_deleted(self):
        with patch('supervisor.memory.summarize', wraps=summarize) as build:
            refresh(self.worktree, self.config, self.store, 'base1')
            self.assertEqual(build.call_count, 2)
        self.store.close()
        self.store = Store(self.root / 'state.sqlite')
        with patch('supervisor.memory.summarize', wraps=summarize) as build:
            refresh(self.worktree, self.config, self.store, 'base1')
            self.assertEqual(build.call_count, 0)
            (self.worktree / 'src/a.py').write_text('def new_function(): pass\n')
            result = refresh(self.worktree, self.config, self.store, 'base2')
            self.assertEqual(build.call_count, 1)
        self.assertEqual(result['changed_files'], ['src/a.py'])
        self.assertIn('new_function', result['files']['src/a.py']['summary'])
        self.assertEqual(self.store.get('memory_status')['reused_files'], 1)
        (self.worktree / 'src/a.py').unlink()
        result = refresh(self.worktree, self.config, self.store, 'base3')
        self.assertNotIn('src/a.py', result['files'])
        self.assertIn('src/a.py', result['changed_files'])
        saved = json.loads((self.root / 'memory/project.json').read_text())
        self.assertEqual(saved, result)

    def test_does_not_index_unconfigured_or_secret_files(self):
        (self.worktree / '.env').write_text('SECRET=do-not-include')
        (self.worktree / 'src/private.py').write_text('def secret_function(): pass')
        self.config['protected_files'].append('.env')
        prompt = context(self.worktree, self.config, self.store, 'base')
        self.assertNotIn('do-not-include', prompt)
        self.assertNotIn('secret_function', prompt)
        self.assertEqual(set(self.store.get('project_memory')['files']), {'README.md', 'src/a.py'})

    def test_guidance_and_verified_results_refresh_without_reindexing(self):
        refresh(self.worktree, self.config, self.store, 'base')
        self.store.set('ralph_instructions', 'Prioritize missing values')
        self.store.cycle('done', {'status': 'accepted_in_snapshot', 'commit': 'abc',
                                'after_validation': {'exit_code': 0},
                                'proposal': {'problem': 'Fixed null', 'evidence': 'HUGE RAW CLAIM'}},
                         'accepted_in_snapshot')
        with patch('supervisor.memory.summarize', wraps=summarize) as build:
            prompt = context(self.worktree, self.config, self.store, 'base')
            self.assertEqual(build.call_count, 0)
        self.assertIn('Prioritize missing values', prompt)
        self.assertIn('Reliable project mission', prompt)
        self.assertIn('Fixed null', prompt)
        self.assertNotIn('HUGE RAW CLAIM', prompt)
        self.assertIn('not proof of predictive lift', prompt)


class BacklogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'runtime'
        initialize(self.root)
        self.s = Supervisor(self.root)
        self.s.config.update(persistent_backlog=True, active_interval_seconds=5, interval_seconds=120,
                             acceptance_mode='application_checks', benchmark_argv=[])
        self.s.control('running')
        self.base = git(self.s.repo, 'rev-parse', 'validated')

    def tearDown(self):
        self.s.close()
        self.temp.cleanup()

    def advance(self, filename='README.md'):
        path = self.s.repo / filename
        path.write_text(path.read_text() + '\n# Other change\n')
        git(self.s.repo, 'add', filename)
        git(self.s.repo, 'commit', '-m', 'Test validated baseline advancement')
        return git(self.s.repo, 'rev-parse', 'validated')

    def test_same_file_and_dependency_changes_keep_hypothesis_but_require_recheck(self):
        p = candidate(self.base)
        self.s.store.add(p)
        self.advance('calc.py')
        updated = self.advance('README.md')
        picked = self.s.choose()
        self.assertEqual(picked['base'], updated)
        self.assertEqual(picked['evidence_base'], self.base)
        self.assertEqual(picked['id'], p['id'])
        self.assertTrue(picked['recheck_required'])
        self.assertEqual(set(picked['changes_since_discovery']), {'calc.py', 'README.md'})
        self.assertEqual(self.s.store.proposals()[0]['attempts'], 0)
        self.assertEqual(self.s.store.proposals()[0]['payload'], picked)

    def test_prior_approval_is_not_carried_to_new_baseline(self):
        p = candidate(self.base, risk='medium')
        self.s.store.add(p)
        self.s.store.set('approval:' + p['id'], True)
        self.advance()
        self.assertIsNone(self.s.choose())
        self.assertFalse(self.s.store.get('approval:' + p['id']))
        self.assertEqual(self.s.store.proposals()[0]['status'], 'awaiting_approval')
        self.s.approval(p['id'])
        self.assertEqual(self.s.choose()['id'], p['id'])

    def test_scope_still_blocks_old_hypotheses(self):
        p = candidate(self.base)
        self.s.store.add(p)
        self.s.config['allowed_files'] = []
        self.advance()
        self.assertIsNone(self.s.choose())
        self.assertEqual(self.s.store.proposals()[0]['status'], 'outside_trusted_scope')

    def test_unrelated_baseline_does_not_revive_hypothesis(self):
        p = candidate(self.advance())
        self.s.store.add(p)
        git(self.s.repo, 'reset', '--hard', self.base)
        self.advance('calc.py')
        self.assertIsNone(self.s.choose())
        self.assertEqual(self.s.store.proposals()[0]['status'], 'obsolete')

    def test_two_accepted_cycles_use_only_one_discovery(self):
        class Executor:
            searches = 0

            def discover(inner, worktree, history, base):
                inner.searches += 1
                return [candidate(base, 'first'), candidate(base, 'second')]

            def implement(inner, worktree, proposal):
                suffix = '' if proposal['strategy'] == 'first' else ', 0'
                (worktree / 'calc.py').write_text(f'def total(values):\n    return sum(values{suffix})\n')

        executor = Executor()
        self.s.executor = executor
        first, second = self.s.cycle(), self.s.cycle()
        self.assertEqual(first['status'], 'accepted_in_snapshot')
        self.assertEqual(second['status'], 'accepted_in_snapshot')
        self.assertEqual(second['proposal_source'], 'backlog')
        self.assertEqual(second['proposal']['evidence_base'], self.base)
        self.assertEqual(executor.searches, 1)
        self.assertIn('implementation_seconds', second)
        self.assertNotIn('discovery_seconds', second)

    def test_active_pacing_does_not_remove_failure_cooldown(self):
        self.assertEqual(self.s.next_delay({'status': 'rejected'}), 120)
        self.assertEqual(self.s.next_delay({'status': 'accepted_in_snapshot'}), 5)
        self.s.store.add(candidate(self.base))
        self.assertEqual(self.s.next_delay({'status': 'rejected'}), 5)
        self.s.control('paused')
        self.assertEqual(self.s.next_delay({'status': 'accepted_in_snapshot'}), 120)

    def test_actual_ralph_prompt_receives_guidance_brief_and_evidence(self):
        self.s.store.set('ralph_instructions', 'Focus on time-correct selection')
        (self.root / 'project-brief.md').write_text('Mission: reliable detection')
        transport = CodexExecutor(self.s.config, self.s.store, self.s.interrupted)
        transport.call = Mock(return_value='<promise>COMPLETE</promise>')
        worker = RalphExecutor(self.root, transport, self.s.store, self.s.interrupted)
        worktree = self.root / 'worktrees' / uuid.uuid4().hex
        git(self.s.repo, 'worktree', 'add', '-b', 'cycle/' + worktree.name, str(worktree), self.base)
        proposal = candidate(self.base)
        proposal['changes_since_discovery'] = ['README.md']
        worker.implement(worktree, proposal)
        prompt = transport.call.call_args.args[1]
        self.assertIn('Focus on time-correct selection', prompt)
        self.assertIn('Mission: reliable detection', prompt)
        self.assertIn(proposal['evidence'], prompt)
        self.assertIn('First reproduce or verify', prompt)
        prd = json.loads((self.root / 'ralph' / worktree.name / 'prd.json').read_text())
        self.assertEqual(prd['userStories'][0]['changes_since_discovery'], ['README.md'])
        self.assertFalse(prd['userStories'][0]['passes'])

    def test_discovery_uses_readonly_compact_memory(self):
        transport = CodexExecutor(self.s.config, self.s.store, self.s.interrupted)
        transport.call = Mock(return_value='{"proposals": []}')
        transport.discover(self.s.repo, [{'huge': 'unfiltered-history'}], self.base)
        prompt = transport.call.call_args.args[1]
        self.assertTrue(transport.call.call_args.kwargs['readonly'])
        self.assertIn('persistent project brief', prompt)
        self.assertIn('Stop exploring once one useful feature gap or defect', prompt)
        self.assertNotIn('unfiltered-history', prompt)
