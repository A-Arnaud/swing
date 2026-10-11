"""File handoff to the current conversation. Never starts a model process."""
import json
import time
import uuid
from pathlib import Path

from .executor import CodexExecutor
from .ralph import atomic_json
from .safety import Blocked, plain_file


class ConversationExecutor(CodexExecutor):
    def __init__(self, root, config, store, interrupt, refresh):
        super().__init__(config, store, interrupt)
        self.root = Path(root)
        self.refresh = refresh

    def check_quota(self, worktree):
        if self.config.get('chatgpt_quota_guard') is not True:
            raise Blocked('chatgpt_quota_guard_required')
        from .quota import read_quota
        try:
            quota = read_quota(self.config['codex_executable'], worktree, self.interrupt)
            self.store.set('chatgpt_quota', quota)
            if quota['exhausted']:
                raise Blocked('chatgpt_quota_exhausted')
        except Blocked as error:
            if str(error) != 'manual_interrupt':
                self.store.set('control', {'mode': 'stopped', 'reason': str(error)})
            raise

    def call(self, worktree, prompt, schema=None, readonly=False):
        if not self.config['live_enabled'] or not (
            self.config['isolated_execution_confirmed'] is True
            or self.config.get('local_execution_authorized') is True
        ):
            raise Blocked('live_execution_not_authorized')
        if self.interrupt():
            raise Blocked('manual_interrupt')
        self.check_quota(worktree)
        task_id = uuid.uuid4().hex
        directory = self.root / 'conversation' / task_id
        directory.mkdir(parents=True, exist_ok=False)
        request = directory / 'request.json'
        response = directory / 'response.json'
        task = {'task_id': task_id, 'status': 'waiting_for_conversation',
                'kind': 'discover' if readonly else 'implement',
                'worktree': str(Path(worktree).resolve()), 'prompt': prompt,
                'allowed_files': self.config.get('allowed_files', []),
                'requested_model': self.config['model'],
                'requested_reasoning_effort': self.config['reasoning_effort'],
                'schema': str(schema) if schema else None,
                'response_path': str(response), 'created_at': time.time(),
                'response_format': {'task_id': task_id, 'result': 'agent final text; JSON string for discovery'}}
        atomic_json(request, task)
        state = {'task_id': task_id, 'status': task['status'], 'kind': task['kind'],
                 'request_path': str(request), 'response_path': str(response),
                 'worktree': task['worktree']}
        atomic_json(self.root / 'conversation' / 'current.json', state)
        self.store.set('conversation_task', state)
        self.refresh()
        try:
            while not response.exists():
                if self.interrupt():
                    raise Blocked('manual_interrupt')
                time.sleep(.5)
            if self.interrupt():
                raise Blocked('manual_interrupt')
            plain_file(response)
            if response.stat().st_size > 2_000_000:
                raise Blocked('conversation_response_too_large')
            answer = json.loads(response.read_text(encoding='utf-8'))
            if not isinstance(answer, dict) or set(answer) != {'task_id', 'result'} or answer['task_id'] != task_id or not isinstance(answer['result'], str):
                raise Blocked('invalid_conversation_response')
            if readonly:
                result = json.loads(answer['result'])
                if not isinstance(result, dict) or set(result) != {'proposals'} or not isinstance(result['proposals'], list):
                    raise Blocked('invalid_proposal_schema')
            self.check_quota(worktree)
            state['status'] = 'answered'
            self.store.set('conversation_task', state)
            atomic_json(self.root / 'conversation' / 'current.json', state)
            self.refresh()
            return answer['result']
        except (Blocked, ValueError, OSError) as error:
            state['status'] = 'cancelled'
            self.store.set('conversation_task', state)
            atomic_json(self.root / 'conversation' / 'current.json', state)
            self.refresh()
            if isinstance(error, Blocked):
                raise
            raise Blocked('invalid_conversation_response') from None
