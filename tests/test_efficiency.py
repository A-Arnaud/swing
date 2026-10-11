import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from supervisor.engine import Supervisor, initialize
from supervisor.executor import CodexExecutor, validate_proposal
from supervisor.feedback import repair_context, validation_feedback
from supervisor.ralph import RalphExecutor
from supervisor.safety import Blocked, git


class EfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'runtime'
        initialize(self.root)
        self.transport = Mock()
        self.s = Supervisor(self.root, executor=self.transport)
        self.s.config.update(acceptance_mode='application_checks', benchmark_argv=[],
                             repair_validation_failures=True, stop_when_no_actionable_work=True)
        self.s.control('running')
        base = git(self.s.repo, 'rev-parse', 'validated')
        self.proposal = validate_proposal(dict(problem='Exact linear sum', strategy='linear summation',
            files=['calc.py'], impact=9, cost=1, risk='low', evidence='calc.py repeats prefix sums',
            success_metric='Same exact values in one traversal'), base)
        self.transport.discover.return_value = [self.proposal]

    def tearDown(self):
        self.s.close()
        self.temp.cleanup()

    def test_failed_candidate_is_repaired_without_a_new_plan(self):
        def implement(tree, proposal):
            if 'repair_feedback' in proposal:
                self.assertIn('summation contract', proposal['repair_feedback'])
                self.assertIn('return len(values)', (tree / 'calc.py').read_text())
                result = 'return sum(values)'
            else:
                result = 'return len(values)'
            (tree / 'calc.py').write_text('def total(values):\n    ' + result + '\n')
        self.transport.implement.side_effect = implement
        result = self.s.cycle()
        self.assertEqual(result['status'], 'accepted_in_snapshot', result.get('reason'))
        self.assertEqual(result['repair_attempts'], 1)
        self.assertEqual(self.transport.implement.call_count, 2)
        self.transport.discover.assert_called_once()
        self.assertEqual(self.s.store.proposals()[0]['attempts'], 2)
        self.assertEqual(result['after_validation']['exit_code'], 0)

    def test_repair_failure_is_bounded_and_never_accepted(self):
        self.transport.implement.side_effect = lambda tree, proposal: (tree / 'calc.py').write_text('def total(values):\n    return len(values)\n')
        result = self.s.cycle()
        self.assertEqual(result['reason'], 'independent_validation_failed')
        self.assertEqual(self.transport.implement.call_count, 2)
        self.assertEqual(git(self.s.repo, 'rev-parse', 'validated'), self.proposal['base'])

    def test_empty_search_stops_without_automatic_retry(self):
        self.transport.discover.return_value = []
        self.assertEqual(self.s.cycle()['reason'], 'no_new_validated_hypothesis')
        self.assertEqual(self.s.store.get('control')['mode'], 'stopped')
        for _ in range(3):
            self.assertEqual(self.s.cycle()['status'], 'idle')
        self.transport.discover.assert_called_once()

    def test_protected_changes_do_not_trigger_repair(self):
        def tamper(tree, proposal):
            (tree / 'calc.py').write_text('def total(values):\n    return len(values)\n')
            (tree / 'contract.py').write_text('print("pass")')
        self.transport.implement.side_effect = tamper
        self.s.cycle()
        self.transport.implement.assert_called_once()

    def test_repair_respects_attempt_budget(self):
        self.s.config['max_attempts_per_proposal'] = 1
        self.transport.implement.side_effect = lambda tree, p: (tree / 'calc.py').write_text('def total(values):\n    return len(values)\n')
        self.s.cycle()
        self.transport.implement.assert_called_once()

    def test_quota_stop_during_repair_preserves_original_baseline(self):
        def implement(tree, proposal):
            if proposal.get('repair_feedback'):
                self.s.store.set('control', {'mode': 'stopped', 'reason': 'chatgpt_quota_exhausted'})
                raise Blocked('chatgpt_quota_exhausted')
            (tree / 'calc.py').write_text('def total(values):\n    return len(values)\n')
        self.transport.implement.side_effect = implement
        result = self.s.cycle()
        self.assertEqual(result['reason'], 'chatgpt_quota_exhausted')
        self.assertEqual(self.s.store.get('control')['reason'], 'chatgpt_quota_exhausted')
        self.assertEqual(git(self.s.repo, 'rev-parse', 'validated'), self.proposal['base'])

    def test_repair_keeps_ralph_prd_and_actual_validation(self):
        native = Mock()
        def call(tree, prompt):
            repair = 'TARGETED REPAIR:' in prompt
            (tree / 'calc.py').write_text('def total(values):\n    return ' + ('sum' if repair else 'len') + '(values)\n')
            return '<promise>COMPLETE</promise>'
        native.call.side_effect = call
        native.project_context.return_value = 'Concise project context'
        native.discover.return_value = [self.proposal]
        self.s.executor = RalphExecutor(self.root, native, self.s.store, self.s.interrupted)
        result = self.s.cycle()
        self.assertEqual(result['status'], 'accepted_in_snapshot', result.get('reason'))
        native.discover.assert_called_once()
        self.assertEqual(native.call.call_count, 2)
        prd = json.loads((self.root / 'ralph' / result['id'] / 'prd.json').read_text())
        self.assertTrue(prd['userStories'][0]['passes'])

    def test_diagnostics_are_compact_and_credentials_redacted(self):
        message = ('x' * 4000 + '\nTOKEN=private\nghp_' + 'a' * 40 + '\nAssertionError: expected 42').encode()
        feedback = validation_feedback(message, b'')
        self.assertLessEqual(len(feedback), 2400)
        self.assertNotIn('private', feedback)
        self.assertNotIn('ghp_', feedback)
        self.assertIn('AssertionError', feedback)

    def test_repair_context_is_in_both_transports_without_duplication(self):
        repair = {**self.proposal, 'repair_feedback': 'AssertionError: expected 42'}
        codex = CodexExecutor(self.s.config, self.s.store, self.s.interrupted)
        with patch.object(codex, 'call', return_value='done') as call:
            codex.implement(self.s.repo, repair)
        prompt = call.call_args.args[1]
        self.assertEqual(prompt.count('AssertionError: expected 42'), 1)
        self.assertIn('final repair attempt', repair_context(repair))

    def test_instruction_change_before_repair_prevents_second_call(self):
        def implement(tree, proposal):
            (tree / 'calc.py').write_text('def total(values):\n    return len(values)\n')
            self.s.store.set('ralph_instructions', 'Different requirement')
        self.transport.implement.side_effect = implement
        self.assertEqual(self.s.cycle()['reason'], 'instructions_changed_before_task')
        self.transport.implement.assert_called_once()

    def test_pending_approval_stops_without_another_discovery(self):
        self.s.config['mode'] = 'snapshot'
        self.s.config['live_enabled'] = True
        proposal = {**self.proposal, 'risk': 'medium'}
        self.s.store.add(proposal)
        self.assertEqual(self.s.cycle()['reason'], 'work_waiting_for_approval')
        self.transport.discover.assert_not_called()

    def test_old_guidance_approval_does_not_block_current_work(self):
        self.s.config.update(mode='snapshot', live_enabled=True)
        proposal = {**self.proposal, 'risk': 'medium', 'instructions_fingerprint': 'previous-guidance'}
        self.s.store.add(proposal)
        self.s.store.proposal_state(proposal['id'], 'awaiting_approval')
        self.transport.discover.return_value = []
        self.assertEqual(self.s.cycle()['reason'], 'no_new_validated_hypothesis')
        self.transport.discover.assert_called_once()
