"""Publish independently validated application files, preserving originals."""
import json
import os
from pathlib import Path

from .safety import Blocked, digest, plain_file, safe_relative
from .workspace import collect


def promote(supervisor, worktree, names, cycle_id):
    source = Path(supervisor.store.get('source')).resolve()
    expected = supervisor.store.get('source_manifest')
    current = {name: digest(data) for name, data in collect(source, supervisor.config.get('source_files')).items()}
    if current != expected:
        raise Blocked('source_changed_before_promotion')
    directory = supervisor.root / 'backups' / (cycle_id + '-source')
    directory.mkdir(parents=True, exist_ok=False)
    records = []
    original_source_files = supervisor.config.get('source_files')
    for name in names:
        relative = safe_relative(name)
        if name not in supervisor.config['allowed_files']:
            raise Blocked('promotion_outside_scope')
        target = source / relative
        if not target.resolve().is_relative_to(source):
            raise Blocked('promotion_outside_source')
        if name in expected:
            plain_file(target)
            old = target.read_bytes()
            if digest(old) != expected.get(name):
                raise Blocked('source_changed_before_promotion')
        else:
            if not supervisor.config.get('allow_new_files') or target.exists() or target.is_symlink():
                raise Blocked('source_changed_before_promotion')
            for parent in target.parents:
                if parent == source:
                    break
                if parent.exists():
                    plain_file(parent)
            old = None
        new = (worktree / relative).read_bytes()
        saved = directory / relative
        saved.parent.mkdir(parents=True, exist_ok=True)
        if old is not None:
            saved.write_bytes(old)
        records.append((name, target, old, new))
    journal = directory / 'promotion.json'
    metadata = {'cycle': cycle_id, 'status': 'prepared', 'files': names,
                'before': {n: digest(old) if old is not None else None for n, _, old, _ in records},
                'after': {n: digest(new) for n, _, _, new in records}}
    journal.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    supervisor.store.set('pending_promotion', str(journal))
    written = []
    try:
        for name, target, old, new in records:
            if supervisor.interrupted():
                raise Blocked('manual_interrupt')
            if (old is not None and target.read_bytes() != old) or (old is None and target.exists()):
                raise Blocked('source_changed_during_promotion')
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.' + cycle_id + '.tmp')
            try:
                temporary.write_bytes(new)
                if old is None:
                    # Atomic no-overwrite creation, including a concurrent operator file.
                    os.link(temporary, target)
                else:
                    os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            written.append((name, target, old, new))
        updated = dict(expected)
        updated.update({name: digest(new) for name, _, _, new in records})
        selected = list(dict.fromkeys(original_source_files + names)) if original_source_files is not None else None
        if {name: digest(data) for name, data in collect(source, selected).items()} != updated:
            raise Blocked('source_changed_during_promotion')
        supervisor.store.set('source_manifest', updated)
        metadata['status'] = 'applied'
        journal.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        supervisor.store.set('pending_promotion', None)
        if selected is not None:
            supervisor.config['source_files'] = selected
        return {'status': 'applied', 'files': names, 'backup': str(directory)}
    except BaseException:
        # Never overwrite a concurrent user's edit during rollback.
        for name, target, old, new in reversed(written):
            if target.read_bytes() != new:
                raise Blocked('promotion_rollback_conflict') from None
            if old is None:
                target.unlink()
            else:
                target.write_bytes(old)
        metadata['status'] = 'rolled_back'
        journal.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        supervisor.store.set('source_manifest', expected)
        supervisor.store.set('pending_promotion', None)
        raise
