"""Explicit project deletion, limited to SWING-owned project directories."""
import json
import os
import re
import shutil
import stat
from pathlib import Path

from supervisor.safety import Blocked, digest, plain_file
from supervisor.store import Store


def runtime_guard(runtime):
    from supervisor.engine import exclusive
    root = Path(runtime).resolve()
    if root.parent.parent.name == 'swing-projects' and re.fullmatch('[0-9a-f]{32}', root.parent.name):
        return exclusive(root.parent.parent, '.operation-' + root.parent.name + '.lock')
    return exclusive(root.parent, '.operation-' + digest(str(root).encode())[:24] + '.lock')


def main_visible(runtime):
    store = Store(Path(runtime) / 'state.sqlite', readonly=True)
    try:
        return not store.get('project_removed', False)
    finally:
        store.close()


def target(default, identifier):
    default = Path(default).absolute()
    if not identifier:
        store = Store(default / 'state.sqlite', readonly=True)
        try:
            if store.get('project_removed'):
                raise ValueError('project_unavailable')
            source = Path(store.get('source')).resolve()
        finally:
            store.close()
        data = (default / 'config.json').read_bytes()
        config = json.loads(data)
        name = config.get('project_name') or source.name.replace('-', ' ').title()
        return {'id': '', 'name': name, 'kind': 'detach', 'path': str(source),
                'fingerprint': digest(data + str(source).encode()), 'runtime': default}
    if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
        raise ValueError('invalid_project_id')
    base = default.parent / 'swing-projects'
    for parent in [base, *base.parents]:
        plain_file(parent)
    base = base.resolve()
    folder = base / identifier
    plain_file(folder)
    if folder.resolve().parent != base.resolve() or folder.resolve() != folder.absolute():
        raise ValueError('unsafe_project_path')
    metadata = folder / 'project.json'
    plain_file(metadata)
    data = metadata.read_bytes()
    info = json.loads(data)
    if info.get('id') != identifier or info.get('state') not in {'brief', 'connected'} or not isinstance(info.get('name'), str):
        raise ValueError('invalid_project_metadata')
    runtime = folder / 'runtime' if info['state'] == 'connected' else None
    if runtime:
        plain_file(runtime)
        plain_file(runtime / 'state.sqlite')
        store = Store(runtime / 'state.sqlite', readonly=True)
        try:
            source = Path(store.get('source')).absolute()
            if source.resolve() != folder / 'source':
                raise ValueError('external_project_source')
            plain_file(source)
        finally:
            store.close()
    return {'id': identifier, 'name': info['name'], 'kind': 'delete', 'path': str(folder),
            'fingerprint': digest(data), 'runtime': runtime}


def busy(runtime):
    if runtime is None:
        return False
    store = Store(Path(runtime) / 'state.sqlite', readonly=True)
    try:
        return (store.get('control', {}).get('mode') != 'stopped' or bool(store.get('pending_promotion'))
                or bool(store.db.execute("SELECT 1 FROM cycles WHERE status='running' LIMIT 1").fetchone()))
    finally:
        store.close()


def preview(default, identifier):
    result = target(default, identifier)
    result['busy'] = busy(result['runtime'])
    result.pop('runtime')
    return result


def delete_project(default, body, worker_active=lambda _runtime: False):
    if not isinstance(body, dict) or not isinstance(body.get('id'), str):
        raise ValueError('invalid_project_id')
    value = target(default, body['id'])
    if body.get('confirmation') != value['name']:
        raise ValueError('project_name_mismatch')
    if body.get('fingerprint') != value['fingerprint']:
        raise ValueError('project_changed_retry')
    from contextlib import nullcontext
    runtime = value['runtime']
    try:
        guard = runtime_guard(runtime) if runtime else nullcontext()
        with guard:
            if busy(runtime) or worker_active(runtime):
                raise ValueError('project_busy')
            if value['kind'] == 'detach':
                store = Store(runtime / 'state.sqlite')
                try:
                    store.set('project_removed', True)
                finally:
                    store.close()
                return {'status': 'removed', 'source_preserved': True}
            folder = Path(value['path'])
            # Check the final absolute target before recursive deletion. Never follow a link.
            base = Path(default).absolute().parent / 'swing-projects'
            if folder.resolve().parent != base.resolve() or folder.name != body['id']:
                raise ValueError('unsafe_project_path')
            pending = [folder]
            while pending:
                directory = pending.pop()
                plain_file(directory)
                with os.scandir(directory) as children:
                    for child in children:
                        plain_file(Path(child.path))
                        if child.is_dir(follow_symlinks=False):
                            pending.append(Path(child.path))
            if runtime:
                store = Store(runtime / 'state.sqlite')
                try:
                    store.set('project_deleting', True)
                finally:
                    store.close()

            def retry_readonly(function, name, error):
                path = Path(name)
                path.resolve().relative_to(folder.resolve())
                plain_file(path)
                if not isinstance(error, PermissionError) or function not in {os.unlink, os.rmdir, os.remove}:
                    raise error
                os.chmod(path, stat.S_IMODE(path.stat().st_mode) | stat.S_IWRITE)
                function(path)

            try:
                shutil.rmtree(folder, onexc=retry_readonly)
            except OSError:
                if runtime and (runtime / 'state.sqlite').is_file():
                    store = Store(runtime / 'state.sqlite')
                    try:
                        store.set('project_deleting', False)
                    finally:
                        store.close()
                raise ValueError('project_delete_failed') from None
            return {'status': 'deleted', 'github_preserved': True}
    except Blocked as error:
        raise ValueError('project_busy' if str(error) == 'supervisor_already_running' else 'unsafe_project_path') from None
