import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from supervisor.engine import Supervisor, initialize
from supervisor.executor import CodexExecutor, validate_proposal
from supervisor.guidance import snapshot
from supervisor.objectives import complete, current, sync, validate_assessment
from supervisor.safety import Blocked, git


def criterion(status='unmet'):
    return {'criterion': 'Exact summation in one traversal', 'status': status,
            'evidence': 'calc.py total and contract.py exact-value fixtures',
            'files': ['calc.py', 'contract.py']}


class ObjectiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'runtime'
        initialize(self.root)
        self.s = Supervisor(self.root)
        self.s.config.update(persistent_backlog=True, acceptance_mode='application_checks', benchmark_argv=[])
        self.s.store.set('ralph_instructions', 'Make summation exact and linear')
        self.s.control('running')
        self.base = git(self.s.repo, 'rev-parse', 'validated')

    def tearDown(self):
        self.s.close()
        self.temp.cleanup()

    def task(self, base=None, order=0, problem='Linear summation'):
        p = validate_proposal(dict(problem=problem, strategy='linear-sum', files=['calc.py'],
                                   impact=1, cost=10, risk='low', evidence='Repeated prefixes in calc.py',
                                   success_metric='Exact summation in one traversal'), base or self.base)
        fingerprint = snapshot(self.s.store)['fingerprint']
        p.update(instructions_fingerprint=fingerprint, objective_fingerprint=fingerprint, objective_order=order)
        return p

    def test_objective_queue_precedes_high_scoring_general_work_and_keeps_order(self):
        sync(self.s.store)
        first = self.task(order=0)
        second = self.task(order=1, problem='Second step')
        general = self.task(problem='Unrelated optimization')
        general.pop('objective_fingerprint')
        general.update(impact=10, cost=1)
        for p in [general, second, first]:
            self.s.store.add(p)
        self.assertEqual(self.s.choose()['id'], first['id'])
        self.s.store.proposal_state(first['id'], 'accepted')
        self.assertEqual(self.s.choose()['id'], second['id'])

    def test_completed_goal_requires_evidence_checks_and_current_instructions(self):
        objective = sync(self.s.store)
        with self.assertRaisesRegex(Blocked, 'objective_not_verified'):
            complete(self.s.store, objective, self.base, {'exit_code': 0})
        objective['criteria'] = [criterion('met')]
        with self.assertRaisesRegex(Blocked, 'objective_not_verified'):
            complete(self.s.store, objective, self.base, {'exit_code': 1})
        self.s.store.set('ralph_instructions', 'Changed')
        with self.assertRaisesRegex(Blocked, 'instructions_changed'):
            complete(self.s.store, objective, self.base, {'exit_code': 0})
        self.assertEqual(current(self.s.store)['state'], 'planning')

    def test_criteria_cannot_be_dropped_or_evidence_invented(self):
        objective = sync(self.s.store)
        objective['criteria'] = [criterion()]
        altered = {**criterion(), 'criterion': 'Just pass existing tests'}
        with self.assertRaisesRegex(Blocked, 'objective_criteria_changed'):
            validate_assessment({'criteria': [altered], 'proposals': []}, objective, self.s.repo, self.s.config)
        for value in [[], [{**criterion('met'), 'files': []}], [{**criterion('met'), 'files': ['absent.py']}]]:
            with self.assertRaises(Blocked):
                validate_assessment({'criteria': value, 'proposals': []}, objective, self.s.repo, self.s.config)

    def test_checkpoint_completion_then_general_discovery_only_afterwards(self):
        transport = Mock()
        transport.pursue_objective.side_effect = [([criterion()], [self.task()]), ([criterion('met')], [])]
        transport.implement.side_effect = lambda tree, p: (tree / 'calc.py').write_text('def total(values):\n    return sum(values)\n')
        transport.discover.return_value = []
        self.s.executor = transport
        self.assertEqual(self.s.cycle()['status'], 'accepted_in_snapshot')
        self.assertEqual(current(self.s.store)['state'], 'active')
        transport.discover.assert_not_called()
        self.assertEqual(self.s.cycle()['status'], 'objective_verified')
        self.assertEqual(current(self.s.store)['state'], 'completed')
        self.assertEqual(current(self.s.store)['validation']['exit_code'], 0)
        transport.discover.assert_not_called()
        self.s.cycle()
        transport.discover.assert_called_once()

    def test_empty_plan_stops_instead_of_searching_unrelated_work(self):
        transport = Mock()
        transport.pursue_objective.return_value = ([criterion('unknown')], [])
        self.s.executor = transport
        result = self.s.cycle()
        self.assertEqual(result['reason'], 'objective_needs_evidence_or_scope')
        self.assertEqual(current(self.s.store)['state'], 'blocked')
        self.assertEqual(self.s.store.get('control')['mode'], 'stopped')
        self.assertEqual(self.s.cycle()['status'], 'idle')
        transport.pursue_objective.assert_called_once()
        transport.discover.assert_not_called()

    def test_two_tasks_use_one_plan_and_failed_dependency_never_runs_next_step(self):
        transport = Mock()
        first, second = self.task(), self.task(order=1, problem='Follow-up step')
        transport.pursue_objective.return_value = ([criterion()], [first, second])
        edits = iter(['return sum(values)', 'return sum(values, 0)'])
        transport.implement.side_effect = lambda tree, p: (tree / 'calc.py').write_text('def total(values):\n    '+next(edits)+'\n')
        self.s.executor = transport
        self.assertEqual(self.s.cycle()['status'], 'accepted_in_snapshot')
        self.assertEqual(self.s.cycle()['status'], 'accepted_in_snapshot')
        transport.pursue_objective.assert_called_once()
        transport.discover.assert_not_called()
        first = self.task(git(self.s.repo, 'rev-parse', 'validated'), problem='Broken prerequisite')
        second = self.task(first['base'], order=1, problem='Dependent task')
        transport.pursue_objective.side_effect = [([criterion()], [first, second]), ([criterion('unknown')], [])]
        transport.implement.side_effect = lambda tree, p: (tree / 'calc.py').write_text('def total(values):\n    return len(values)\n')
        self.assertEqual(self.s.cycle()['reason'], 'independent_validation_failed')
        self.assertEqual(self.s.cycle()['reason'], 'objective_needs_evidence_or_scope')
        self.assertEqual(transport.implement.call_count, 3)
        blocked = next(p for p in self.s.store.proposals() if p['id'] == second['id'])
        self.assertEqual(blocked['status'], 'blocked_dependency')

    def test_completion_claim_cannot_bypass_final_independent_checks(self):
        transport = Mock()
        transport.pursue_objective.return_value = ([criterion('met')], [])
        self.s.executor = transport
        with patch.object(self.s, 'verify', side_effect=[{'exit_code': 0}, Blocked('independent_validation_failed')]):
            self.assertEqual(self.s.cycle()['reason'], 'independent_validation_failed')
        self.assertEqual(current(self.s.store)['state'], 'blocked')
        self.assertNotIn('verified_base', current(self.s.store))
        transport.discover.assert_not_called()

    def test_approval_blocks_general_search(self):
        sync(self.s.store)
        p = self.task()
        p['risk'] = 'medium'
        self.s.store.add(p)
        transport = Mock()
        self.s.executor = transport
        result = self.s.cycle()
        self.assertEqual(result['reason'], 'objective_waiting_for_approval_or_scope')
        transport.pursue_objective.assert_not_called()
        transport.discover.assert_not_called()

    def test_persisted_queue_resumes_without_planning_each_cycle(self):
        sync(self.s.store)
        p = self.task()
        self.s.store.add(p)
        self.s.close()
        self.s = Supervisor(self.root)
        self.s.config.update(persistent_backlog=True, acceptance_mode='application_checks', benchmark_argv=[])
        transport = Mock()
        transport.implement.side_effect = lambda tree, p: (tree / 'calc.py').write_text('def total(values):\n    return sum(values)\n')
        self.s.executor = transport
        self.assertEqual(self.s.cycle()['status'], 'accepted_in_snapshot')
        transport.pursue_objective.assert_not_called()
        transport.discover.assert_not_called()

    def test_transport_keeps_criteria_and_binds_tasks_and_rejects_guidance_race(self):
        objective = sync(self.s.store)
        transport = CodexExecutor(self.s.config, self.s.store, self.s.interrupted)
        p = self.task()
        fields = ('problem', 'strategy', 'files', 'impact', 'cost', 'risk', 'evidence', 'success_metric')
        response = json.dumps({'criteria': [criterion()], 'proposals': [{k: p[k] for k in fields}]})
        with patch.object(transport, 'call', return_value=response) as call:
            criteria, tasks = transport.pursue_objective(self.s.repo, [], self.base, objective)
        self.assertEqual(tasks[0]['objective_fingerprint'], objective['fingerprint'])
        self.assertIn('ONLY toward', call.call_args.args[1])
        self.assertEqual(call.call_args.args[2].name, 'objective.json')
        def race(*args, **kwargs):
            self.s.store.set('ralph_instructions', 'New request')
            return response
        with patch.object(transport, 'call', side_effect=race):
            with self.assertRaisesRegex(Blocked, 'instructions_changed'):
                transport.pursue_objective(self.s.repo, [], self.base, objective)

    def test_instruction_edit_during_implementation_cannot_publish(self):
        sync(self.s.store)
        self.s.store.add(self.task())
        transport = Mock()
        def edit(tree, p):
            (tree / 'calc.py').write_text('def total(values):\n    return sum(values)\n')
            self.s.store.set('ralph_instructions', 'Different goal')
        transport.implement.side_effect = edit
        self.s.executor = transport
        result = self.s.cycle()
        self.assertEqual(result['reason'], 'instructions_changed_before_task')
        self.assertEqual(git(self.s.repo, 'rev-parse', 'validated'), self.base)


if __name__ == '__main__':
    unittest.main()
