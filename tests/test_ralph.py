import json
import sys
import unittest
import uuid
from pathlib import Path

from supervisor.engine import Supervisor, initialize
from supervisor.executor import CodexExecutor
from supervisor.ralph import RalphDemoTransport, RalphExecutor
from supervisor.safety import Blocked, git

ROOT = Path(__file__).resolve().parents[1]


class RalphTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / 'test-scratch' / uuid.uuid4().hex / 'runtime'
        initialize(self.root, execution_backend='ralph')
        self.supervisor = Supervisor(self.root)
        self.supervisor.control('running')

    def tearDown(self):
        self.supervisor.close()

    def prd(self, report):
        return json.loads((self.root / 'ralph' / report['id'] / 'prd.json').read_text())

    def test_false_completion_rejected_alternative_accepted_then_discovery_continues(self):
        base = git(self.supervisor.repo, 'rev-parse', 'validated')
        rejected = self.supervisor.cycle()
        self.assertEqual(rejected['reason'], 'independent_validation_failed')
        self.assertTrue(self.prd(rejected)['completion_claim'])
        self.assertFalse(self.prd(rejected)['userStories'][0]['passes'])
        self.assertEqual(git(self.supervisor.repo, 'rev-parse', 'validated'), base)
        accepted = self.supervisor.cycle()
        self.assertEqual(accepted['status'], 'accepted_in_snapshot')
        self.assertTrue(self.prd(accepted)['userStories'][0]['passes'])
        self.assertGreaterEqual(accepted['improvement_percent'], 20)
        previous = json.loads((self.root / 'ralph' / accepted['id'] / 'progress.json').read_text())
        self.assertEqual(previous[0]['reason'], 'independent_validation_failed')
        for _ in range(3):
            self.assertEqual(self.supervisor.cycle()['reason'], 'no_new_validated_hypothesis')
        self.assertEqual(self.supervisor.store.get('control')['mode'], 'auto_paused')
        for bundle in (self.root / 'backups').glob('*.bundle'):
            git(self.supervisor.repo, 'bundle', 'verify', str(bundle))

    def test_pause_and_restart_keep_ralph_backend_and_verified_progress(self):
        rejected = self.supervisor.cycle()
        self.supervisor.control('paused')
        other = Supervisor(self.root)
        try:
            self.assertIsInstance(other.executor, RalphExecutor)
            self.assertEqual(other.cycle()['status'], 'idle')
            other.control('running')
            self.assertEqual(other.cycle()['status'], 'accepted_in_snapshot')
            self.assertFalse(self.prd(rejected)['userStories'][0]['passes'])
        finally:
            other.close()

    def test_agent_cannot_self_approve_test_tampering(self):
        class Tamper(RalphDemoTransport):
            def call(self, worktree, prompt):
                (worktree / 'contract.py').write_text("print('fake pass')\n")
                return '<promise>COMPLETE</promise>'
        self.supervisor.executor.transport = Tamper()
        report = self.supervisor.cycle()
        self.assertEqual(report['status'], 'rejected')
        self.assertFalse(self.prd(report)['userStories'][0]['passes'])

    def test_branch_mismatch_never_launches_transport(self):
        proposal = self.supervisor.executor.discover(self.supervisor.repo, [],
                                                   git(self.supervisor.repo, 'rev-parse', 'HEAD'))[0]
        with self.assertRaisesRegex(Blocked, 'ralph_branch_mismatch'):
            self.supervisor.executor.implement(self.supervisor.repo, proposal)

    def test_live_authorization_and_budget_gates_are_inherited(self):
        transport = CodexExecutor(self.supervisor.config, self.supervisor.store, self.supervisor.interrupted)
        with self.assertRaisesRegex(Blocked, 'live_execution_not_authorized'):
            transport.call(self.supervisor.repo, 'test')
        self.supervisor.config.update(live_enabled=True, isolated_execution_confirmed=True,
                                      codex_executable=sys.executable)
        with self.assertRaisesRegex(RuntimeError, 'daily_budget_exhausted'):
            transport.call(self.supervisor.repo, 'test')
        self.assertEqual(self.supervisor.store.db.execute('SELECT count(*) FROM calls').fetchone()[0], 0)

    def test_manual_stop_before_iteration_does_not_create_task(self):
        self.supervisor.control('stopped')
        with self.assertRaisesRegex(Blocked, 'manual_interrupt'):
            self.supervisor.executor.implement(self.supervisor.repo, {})
        self.assertFalse((self.root / 'ralph').exists())
