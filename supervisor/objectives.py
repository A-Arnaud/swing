"""Persistent operator objective; an empty task queue never implies completion."""
import time

from .guidance import snapshot
from .safety import Blocked, plain_file, safe_relative


def current(store):
    guidance = snapshot(store)
    if not guidance['has_instructions']:
        return None
    value = store.get('operator_objective')
    if not value or value['fingerprint'] != guidance['fingerprint']:
        return {'fingerprint': guidance['fingerprint'], 'state': 'planning',
                'criteria': [], 'task_ids': [], 'reason': ''}
    return value


def sync(store):
    value = current(store)
    if value is not None:
        store.set('operator_objective', value)
    return value


def validate_assessment(result, objective, worktree, config):
    if not isinstance(result, dict) or set(result) != {'criteria', 'proposals'}:
        raise Blocked('invalid_objective_assessment')
    criteria = result['criteria']
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 8:
        raise Blocked('objective_criteria_required')
    for item in criteria:
        if not isinstance(item, dict) or set(item) != {'criterion', 'status', 'evidence', 'files'}:
            raise Blocked('invalid_objective_criterion')
        if item['status'] not in {'met', 'unmet', 'unknown'}:
            raise Blocked('invalid_objective_criterion')
        for name in ('criterion', 'evidence'):
            if not isinstance(item[name], str) or not 1 <= len(item[name].strip()) <= 800:
                raise Blocked('objective_evidence_required')
        files = item['files']
        if not isinstance(files, list) or len(files) > 8 or (item['status'] == 'met' and not files):
            raise Blocked('objective_evidence_required')
        for name in files:
            if name not in config['allowed_files'] + config['protected_files']:
                raise Blocked('objective_evidence_outside_scope')
            path = worktree / safe_relative(name)
            path.resolve().relative_to(worktree.resolve())
            plain_file(path)
    previous = objective['criteria']
    if previous and [c['criterion'] for c in criteria] != [c['criterion'] for c in previous]:
        raise Blocked('objective_criteria_changed')
    if not isinstance(result['proposals'], list) or len(result['proposals']) > 3:
        raise Blocked('invalid_objective_tasks')
    if all(c['status'] == 'met' for c in criteria) and result['proposals']:
        raise Blocked('inconsistent_objective_assessment')
    return criteria


def complete(store, objective, base, validation):
    if snapshot(store)['fingerprint'] != objective['fingerprint']:
        raise Blocked('instructions_changed_before_task')
    if not objective['criteria'] or not all(c['status'] == 'met' for c in objective['criteria']):
        raise Blocked('objective_not_verified')
    if validation.get('exit_code') != 0:
        raise Blocked('objective_not_verified')
    objective.update(state='completed', verified_base=base, validation=validation,
                     verified_at=time.time(), reason='')
    store.set('operator_objective', objective)
