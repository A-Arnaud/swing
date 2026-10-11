"""Read-only dashboard projections and local project briefs."""
import difflib
import json
import time
import uuid
from pathlib import Path

from supervisor.guidance import snapshot
from supervisor.safety import plain_file, safe_relative
from supervisor.store import Store


def status(runtime):
    config = json.loads((runtime / 'config.json').read_text(encoding='utf-8'))
    store = Store(runtime / 'state.sqlite', readonly=True)
    try:
        source = store.get('source')
        project = Path(source) if source else runtime
        calls, tokens, incomplete = store.db.execute(
            'SELECT count(*),coalesce(sum(tokens),0),sum(CASE WHEN tokens IS NULL THEN 1 ELSE 0 END) FROM calls'
        ).fetchone()
        result = {key: store.get(key) for key in ('control', 'phase', 'chatgpt_quota', 'memory_status',
                   'next_cycle_at', 'instruction_delivery', 'last_model_usage')}
        result.update(updated_at=time.time(), model=config.get('model'),
                      reasoning_effort=config.get('reasoning_effort'), model_calls=calls,
                      token_usage={'measured_total': tokens, 'unknown_calls': incomplete or 0},
                      no_progress_cycles=store.get('no_progress', 0),
                      project={'name': config.get('project_name') or project.name.replace('-', ' ').title(),
                               'path': str(project), 'github_url': config.get('github_url')},
                      ralph_instructions=store.get('ralph_instructions', ''),
                      instructions_fingerprint=snapshot(store)['fingerprint'],
                      cycles=store.history(12), proposals=store.proposals())
        # Raw evidence and worktree paths are unnecessary for the overview.
        for cycle in result['cycles']:
            cycle.pop('worktree', None)
            if cycle.get('proposal'):
                cycle['proposal'] = {k: cycle['proposal'][k] for k in
                                     ('id', 'problem', 'strategy', 'files', 'success_metric')
                                     if k in cycle['proposal']}
        for entry in result['proposals']:
            entry['payload'] = {k: entry['payload'][k] for k in ('id', 'problem', 'impact', 'cost', 'files', 'risk')}
        return result
    finally:
        store.close()


def changes(runtime):
    config = json.loads((runtime / 'config.json').read_text(encoding='utf-8'))
    store = Store(runtime / 'state.sqlite', readonly=True)
    try:
        history = store.history(30)
        active = next((c for c in history if c['status'] == 'running'), None)
        cycle = active or next((c for c in history if c.get('promotion', {}).get('status') == 'applied'), None)
        if not cycle:
            return {'files': [], 'state': 'empty'}
        if active:
            before = runtime / 'repository'
            after = runtime / 'worktrees' / cycle['id']
        else:
            before = Path(cycle['promotion']['backup'])
            before.resolve().relative_to((runtime / 'backups').resolve())
            after = Path(store.get('source'))
        files = []
        for name in cycle.get('changed_files', cycle.get('proposal', {}).get('files', [])):
            if name not in config['allowed_files']:
                continue
            paths = [root / safe_relative(name) for root in (before, after)]
            for path, root in zip(paths, (before, after)):
                path.resolve().relative_to(root.resolve())
                plain_file(path)
            if any(p.stat().st_size > 1_000_000 for p in paths):
                continue
            old, new = [p.read_text(encoding='utf-8').splitlines() for p in paths]
            diff = list(difflib.unified_diff(old, new, fromfile='avant/' + name, tofile='apres/' + name, n=3))
            files.append({'name': name, 'diff': '\n'.join(diff[:400]), 'truncated': len(diff) > 400})
        return {'files': files, 'state': 'working' if active else 'applied', 'cycle': cycle['id']}
    finally:
        store.close()


def projects(runtime):
    folder = runtime.parent / 'swing-projects'
    return [json.loads(path.read_text(encoding='utf-8')) for path in sorted(folder.glob('*/project.json'))]


def save_project(runtime, body):
    for field, limit in [('name', 80), ('idea', 4000), ('instructions', 4000)]:
        if not isinstance(body.get(field), str) or len(body[field]) > limit:
            raise ValueError('invalid_project_' + field)
    if not body['name'].strip() or not body['idea'].strip():
        raise ValueError('project_name_and_idea_required')
    identifier = body.get('id') or uuid.uuid4().hex
    if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
        raise ValueError('invalid_project_id')
    folder = runtime.parent / 'swing-projects' / identifier
    if (folder / 'project.json').exists():
        existing = json.loads((folder / 'project.json').read_text(encoding='utf-8'))
        if existing.get('state') == 'connected':
            raise ValueError('connected_project_requires_workspace')
    folder.mkdir(parents=True, exist_ok=True)
    result = {field: body[field].strip() for field in ('name', 'idea', 'instructions')}
    result.update(id=identifier, state='brief', updated_at=time.time())
    path = folder / 'project.json'
    temp = folder / (uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)
    return result
