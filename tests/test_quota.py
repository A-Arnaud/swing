import json
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from supervisor.engine import Supervisor, initialize
from supervisor.executor import CodexExecutor, quota_error
from supervisor.quota import codex_environment, summarize_limits
from supervisor.safety import Blocked


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / 'test-scratch' / uuid.uuid4().hex / 'runtime'
        initialize(self.root)
        self.s = Supervisor(self.root)
        self.s.control('running')
        self.s.config.update(live_enabled=True, isolated_execution_confirmed=True,
                             chatgpt_quota_guard=True, codex_executable=sys.executable,
                             max_ai_calls_per_day=5, max_tokens_per_day=500000)
        self.transport = CodexExecutor(self.s.config, self.s.store, self.s.interrupted)

    def tearDown(self):
        self.s.close()

    def limits(self, primary=20, secondary=30):
        return {'rateLimits': {'limitId': 'codex', 'primary': {'usedPercent': primary},
                              'secondary': {'usedPercent': secondary}}}

    def test_exhaustion_of_either_window_stops_before_call(self):
        for p, s in ((100, 0), (0, 100)):
            self.assertTrue(summarize_limits(self.limits(p, s))['exhausted'])
        with patch('supervisor.quota.read_quota', return_value=summarize_limits(self.limits(100))), \
                patch('supervisor.executor.run_command') as command:
            with self.assertRaisesRegex(Blocked, 'chatgpt_quota_exhausted'):
                self.transport.call(self.s.repo, 'test')
            command.assert_not_called()
        self.assertEqual(self.s.store.get('control')['mode'], 'stopped')
        self.assertEqual(self.s.store.db.execute('SELECT count(*) FROM calls').fetchone()[0], 0)

    def test_unknown_quota_stops_without_spending(self):
        with patch('supervisor.quota.read_quota', side_effect=Blocked('chatgpt_quota_unknown')), \
                patch('supervisor.executor.run_command') as command:
            with self.assertRaisesRegex(Blocked, 'chatgpt_quota_unknown'):
                self.transport.call(self.s.repo, 'test')
            command.assert_not_called()
        self.assertEqual(self.s.store.get('control')['mode'], 'stopped')

    def test_model_medium_and_chatgpt_auth_are_explicit(self):
        events = [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'done'}},
                  {'type': 'turn.completed', 'usage': {'input_tokens': 20, 'output_tokens': 5}}]
        with patch('supervisor.quota.read_quota', return_value=summarize_limits(self.limits())), \
                patch('supervisor.executor.run_command', return_value=(0, '\n'.join(map(json.dumps, events)).encode(), b'')) as call:
            self.assertEqual(self.transport.call(self.s.repo, 'test'), 'done')
        argv = call.call_args.args[0]
        self.assertEqual(argv[argv.index('--model') + 1], 'gpt-6.1-sol')
        self.assertIn('model_reasoning_effort="medium"', argv)
        self.assertIn('forced_login_method="chatgpt"', argv)

    def test_cli_usage_limit_is_persistent_and_loop_exits(self):
        events = b'{"type":"turn.failed","error":{"message":"You have hit your usage limit"}}'
        with patch('supervisor.quota.read_quota', return_value=summarize_limits(self.limits())), \
                patch('supervisor.executor.run_command', return_value=(1, events, b'')):
            with self.assertRaisesRegex(Blocked, 'chatgpt_quota_exhausted'):
                self.transport.call(self.s.repo, 'test')
        self.s.run()  # Must return immediately, no cooldown/retry/reset.
        other = Supervisor(self.root)
        try:
            self.assertEqual(other.store.get('control')['reason'], 'chatgpt_quota_exhausted')
        finally:
            other.close()

    def test_source_text_cannot_signal_quota_exhaustion(self):
        self.assertFalse(quota_error({'type': 'item.completed', 'item': {'text': 'usage limit'}}))
        self.assertTrue(quota_error({'type': 'error', 'codexErrorInfo': 'UsageLimitExceeded'}))

    def test_invalid_windows_and_other_bucket_are_refused(self):
        for value in (None, -1, 101, float('nan'), True, '100'):
            with self.assertRaisesRegex(Blocked, 'chatgpt_quota_unknown'):
                summarize_limits(self.limits(value))
        with self.assertRaises(Blocked):
            summarize_limits({'rateLimits': {'limitId': 'another-model', 'primary': {'usedPercent': 10}}})

    def test_api_keys_are_not_inherited(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'example', 'AZURE_OPENAI_API_KEY': 'example'}):
            env = codex_environment()
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotIn('AZURE_OPENAI_API_KEY', env)
