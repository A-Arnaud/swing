"""Windows/Codex adaptation of snarktank/ralph (MIT; see vendor/ralph).

Fresh context, one story and persistent progress; acceptance belongs to Supervisor.
The upstream Bash launcher is retained for reference and never executed.
"""
import json
import re
from pathlib import Path

from .executor import DemoExecutor
from .safety import Blocked, git


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


class RalphExecutor:
    def __init__(self, root, transport, store, interrupt):
        self.root = Path(root)
        self.transport, self.store, self.interrupt = transport, store, interrupt

    def directory(self, cycle_id):
        if not re.fullmatch(r'[a-f0-9]{32}', cycle_id):
            raise Blocked('invalid_ralph_cycle_id')
        return self.root / 'ralph' / cycle_id

    def discover(self, worktree, history, base):
        # Discovery is outside Ralph's finite PRD: completed stories do not end supervision.
        return self.transport.discover(worktree, history, base)

    def pursue_objective(self, worktree, history, base, objective):
        if not hasattr(self.transport, 'pursue_objective'):
            raise Blocked('objective_transport_unavailable')
        return self.transport.pursue_objective(worktree, history, base, objective)

    def understand_project(self, worktree, base):
        if not hasattr(self.transport, 'understand_project'):
            raise Blocked('project_understanding_transport_unavailable')
        return self.transport.understand_project(worktree, base)

    def implement(self, worktree, proposal):
        if self.interrupt():
            raise Blocked('manual_interrupt')
        cycle_id = worktree.name
        branch = git(worktree, 'branch', '--show-current')
        if branch != 'cycle/' + cycle_id or git(worktree, 'rev-parse', 'HEAD') != proposal['base']:
            raise Blocked('ralph_branch_mismatch')
        directory = self.directory(cycle_id)
        directory.mkdir(parents=True, exist_ok=False)
        prd = {'project': 'isolated improvement', 'branchName': branch,
               'userStories': [{'id': proposal['id'], 'title': proposal['problem'],
                                'description': proposal['strategy'], 'priority': 1,
                                'acceptanceCriteria': [proposal['success_metric']],
                                'evidence': proposal['evidence'],
                                'evidence_base': proposal.get('evidence_base', proposal['base']),
                                'changes_since_discovery': proposal.get('changes_since_discovery', []),
                                'files': proposal['files'], 'passes': False}],
               'status': 'running', 'base': proposal['base']}
        atomic_json(directory / 'prd.json', prd)
        # Only verified summaries cross sessions; no model output or claimed learnings.
        history = [{'status': r['status'], 'reason': r.get('reason'),
                    'strategy': r.get('proposal', {}).get('strategy'),
                    'improvement_percent': r.get('improvement_percent')}
                   for r in self.store.history(10) if r['status'] != 'running']
        atomic_json(directory / 'progress.json', history)
        template = (Path(__file__).resolve().parents[1] / 'prompts/ralph-codex.md').read_text(encoding='utf-8')
        prompt = template + '\nPRD_DATA_BEGIN\n' + json.dumps(prd, ensure_ascii=False, separators=(',', ':')) + '\nPRD_DATA_END\n'
        if hasattr(self.transport, 'project_context'):
            # Context already contains relevant verified results; do not send them twice.
            prompt += self.transport.project_context(worktree, proposal['base'], proposal['files'],
                                                     proposal.get('instructions_fingerprint'))
        else:
            prompt += '\nVERIFIED_PROGRESS_BEGIN\n' + json.dumps(history) + '\nVERIFIED_PROGRESS_END\n'
        # Each call launches codex exec anew; never resumes an implementation thread.
        result = self.transport.call(worktree, prompt)
        # COMPLETE is recorded as a claim, never evidence of correctness.
        prd.update(status='awaiting_independent_validation',
                   completion_claim='<promise>COMPLETE</promise>' in result)
        atomic_json(directory / 'prd.json', prd)

    def record_result(self, report):
        directory = self.directory(report['id'])
        path = directory / 'prd.json'
        if not path.exists():
            return
        prd = json.loads(path.read_text(encoding='utf-8'))
        prd['status'] = report['status']
        prd['userStories'][0]['passes'] = report['status'] == 'accepted_in_snapshot'
        prd['validation_reason'] = report.get('reason')
        prd['validated_commit'] = report.get('commit')
        atomic_json(path, prd)
        atomic_json(directory / 'result.json', report)


class RalphDemoTransport(DemoExecutor):
    """Fake Codex transport only; worktree edits and validation are real."""
    def call(self, worktree, prompt):
        prd = json.loads(prompt.split('PRD_DATA_BEGIN\n', 1)[1].split('\nPRD_DATA_END', 1)[0])
        story = prd['userStories'][0]
        self.implement(worktree, {'strategy': story['description']})
        # Deliberately claims completion even for the incorrect strategy.
        return '<promise>COMPLETE</promise>'
