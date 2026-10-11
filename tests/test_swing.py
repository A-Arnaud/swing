import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard_data import changes, projects, save_project, status
from supervisor.engine import Supervisor, initialize
from supervisor.executor import CodexExecutor, validate_proposal
from supervisor.guidance import snapshot
from supervisor.memory import context
from supervisor.safety import Blocked, git


class SwingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'runtime'
        initialize(self.root)
        self.s = Supervisor(self.root)
        self.s.config.update(persistent_backlog=True)
        self.base = git(self.s.repo, 'rev-parse', 'validated')
        self.p = validate_proposal(dict(problem='Summation issue', strategy='linear sum', files=['calc.py'],
                                       impact=6, cost=1, risk='low', evidence='Known input fixture',
                                       success_metric='Exact sums'), self.base)

    def tearDown(self):
        self.s.close()
        self.temp.cleanup()

    def test_new_guidance_defers_legacy_queue_and_restoring_guidance_restores_task(self):
        self.s.store.add(self.p)
        self.s.store.set('ralph_instructions', 'Focus on another area')
        self.assertIsNone(self.s.choose())
        self.assertEqual(self.s.store.proposals()[0]['status'], 'deferred_instructions')
        self.s.store.set('ralph_instructions', '')
        self.assertEqual(self.s.choose()['id'], self.p['id'])

    def test_guidance_from_discovery_is_bound_to_returned_proposal(self):
        self.s.store.set('ralph_instructions', 'Keep arithmetic exact')
        transport = CodexExecutor(self.s.config, self.s.store, self.s.interrupted)
        item = {k: v for k, v in self.p.items() if k not in {'id', 'base'}}
        with patch.object(transport, 'call', return_value=json.dumps({'proposals': [item]})) as call:
            proposals = transport.discover(self.s.repo, [], self.base)
        self.assertIn('Keep arithmetic exact', call.call_args.args[1])
        self.assertEqual(proposals[0]['instructions_fingerprint'], snapshot(self.s.store)['fingerprint'])
        self.s.store.add(proposals[0])
        self.assertIsNotNone(self.s.choose())
        self.s.store.set('ralph_instructions', 'Work on the interface instead')
        self.assertIsNone(self.s.choose())

    def test_guidance_race_blocks_implementation_before_call(self):
        self.s.store.set('ralph_instructions', 'Old intention')
        self.p['instructions_fingerprint'] = snapshot(self.s.store)['fingerprint']
        self.s.store.set('ralph_instructions', 'New intention')
        transport = CodexExecutor(self.s.config, self.s.store, self.s.interrupted)
        with patch.object(transport, 'call') as call:
            with self.assertRaisesRegex(Blocked, 'instructions_changed_before_task'):
                transport.implement(self.s.repo, self.p)
            call.assert_not_called()

    def test_implementation_context_omits_unrelated_modules(self):
        (self.s.repo / 'src').mkdir()
        for i in range(50):
            (self.s.repo / f'src/unrelated_{i}.py').write_text(f'def unrelated_{i}(): pass')
        self.s.config['allowed_files'] += [f'src/unrelated_{i}.py' for i in range(50)]
        general = context(self.s.repo, self.s.config, self.s.store, self.base)
        focused = context(self.s.repo, self.s.config, self.s.store, self.base, ['calc.py'])
        self.assertNotIn('unrelated_', focused)
        self.assertIn('calc.py', focused)
        self.assertLess(len(focused), len(general) * .6)

    def test_receipt_is_recorded_at_actual_call_not_just_context_preparation(self):
        self.s.control('running')
        self.s.config.update(live_enabled=True, isolated_execution_confirmed=True,
                             codex_executable=sys.executable, max_ai_calls_per_day=2,
                             max_tokens_per_day=300000)
        self.s.store.set('ralph_instructions', 'Operator intent')
        transport = CodexExecutor(self.s.config, self.s.store, self.s.interrupted)
        prompt = transport.project_context(self.s.repo, self.base, ['calc.py'])
        self.assertIsNone(self.s.store.get('instruction_delivery'))
        events = [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'done'}},
                  {'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 80,
                                                     'output_tokens': 20}}]
        with patch('supervisor.executor.run_command', return_value=(0, '\n'.join(map(json.dumps, events)).encode(), b'')):
            transport.call(self.s.repo, prompt)
        receipt = self.s.store.get('instruction_delivery')
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(receipt['fingerprint'], snapshot(self.s.store)['fingerprint'])
        self.assertEqual(receipt['tokens'], 120)  # Cached input is a subset, never added twice.
        self.assertEqual(self.s.store.get('last_model_usage')['cached_input_tokens'], 80)

    def test_brief_creation_update_persistence_and_no_runtime_start(self):
        result = save_project(self.root, {'name': 'Idea', 'idea': 'Useful new app', 'instructions': 'Simple UI'})
        self.assertEqual(result['state'], 'brief')
        self.assertEqual(projects(self.root)[0]['idea'], 'Useful new app')
        save_project(self.root, {**result, 'idea': 'Revised brief'})
        self.assertEqual(len(projects(self.root)), 1)
        self.assertEqual(projects(self.root)[0]['idea'], 'Revised brief')
        self.assertEqual(self.s.store.get('control')['mode'], 'paused')
        with self.assertRaises(ValueError):
            save_project(self.root, {**result, 'id': '../outside'})

    def test_status_is_readonly_and_does_not_invent_usage(self):
        result = status(self.root)
        self.assertEqual(result['token_usage'], {'measured_total': 0, 'unknown_calls': 0})
        self.assertIsNone(result['instruction_delivery'])
        self.assertEqual(changes(self.root)['state'], 'empty')

    def test_live_diff_reads_current_worktree_not_cached_report(self):
        cycle_id = 'a' * 32
        tree = self.root / 'worktrees' / cycle_id
        tree.mkdir()
        (tree / 'calc.py').write_text('def total(values):\n    return sum(values)\n')
        self.s.store.cycle(cycle_id, {'id': cycle_id, 'status': 'running', 'proposal': self.p})
        first = changes(self.root)
        self.assertEqual(first['state'], 'working')
        self.assertIn('+    return sum(values)', first['files'][0]['diff'])
        (tree / 'calc.py').write_text('def total(values):\n    return sum(values, 0)\n')
        self.assertIn('+    return sum(values, 0)', changes(self.root)['files'][0]['diff'])


if __name__ == '__main__':
    unittest.main()
