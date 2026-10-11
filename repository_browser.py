"""Read-only, bounded repository views. Never check out a branch or expose secrets."""
import os
import re
import threading
import time
from pathlib import Path

from supervisor.safety import Blocked, forbidden, git, plain_file, run_command, safe_relative, secret_like
from supervisor.store import Store

_cache = {}
_lock = threading.Lock()
LIMIT = 256_000


def raw_git(root, *args):
    code, data, _ = run_command(['git', '-c', 'core.hooksPath=' + os.devnull, *args], root)
    if code:
        raise ValueError('repository_unavailable')
    return data.decode('utf-8')


def visible(name):
    return bool(name) and not forbidden(name) and not any(
        part in {'.github', '.idea', '.vscode'} for part in name.split('/'))


def checked(root, name):
    relative = safe_relative(name)
    if not visible(name):
        raise ValueError('file_unavailable')
    candidate = root / relative
    candidate.resolve().relative_to(root.resolve())
    current = root
    plain_file(current)
    for part in relative.parts:
        current /= part
        plain_file(current)
    return candidate


def metadata(root, revision):
    key = (str(root), revision)
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < 15:
            return cached[1]
    # One bounded history request, not one Git process for every file.
    history = git(root, '-c', 'core.quotepath=false', 'log', '-100', '--format=%x00%ct%x1f%s', '--name-only', revision, '--')
    result = {}
    for block in history.split('\x00')[1:]:
        header, _, paths = block.strip().partition('\n')
        timestamp, _, message = header.partition('\x1f')
        if not timestamp.isdigit():
            continue
        for name in paths.splitlines():
            if not name:
                continue
            parts = name.split('/')
            for index in range(1, len(parts) + 1):
                result.setdefault('/'.join(parts[:index]), {'time': int(timestamp), 'message': message[:180]})
    with _lock:
        if len(_cache) > 32:
            _cache.clear()
        _cache[key] = (time.monotonic(), result)
    return result


def browse(runtime, directory='', file='', branch=''):
    store = Store(Path(runtime) / 'state.sqlite', readonly=True)
    try:
        root = Path(store.get('source')).resolve()
    finally:
        store.close()
    plain_file(root)
    if directory:
        safe_relative(directory)
        if not visible(directory):
            raise ValueError('directory_unavailable')
    current = git(root, 'branch', '--show-current')
    refs = git(root, 'for-each-ref', '--format=%(refname:short)%09%(objectname)', 'refs/heads', 'refs/remotes')
    branches = dict(line.split('\t', 1) for line in refs.splitlines() if '\t' in line and not line.split('\t')[0].endswith('/HEAD'))
    if branch and branch not in branches:
        raise ValueError('branch_unavailable')
    local = not branch or branch == current
    revision = git(root, 'rev-parse', 'HEAD') if local else branches[branch]
    history = metadata(root, revision)
    if local:
        names = raw_git(root, '-c', 'core.quotepath=false', 'ls-files', '-z', '--cached', '--others', '--exclude-standard').split('\x00')
        modified = set()
        entries = raw_git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all').split('\x00')
        index = 0
        while index < len(entries):
            entry = entries[index]
            if len(entry) >= 4:
                modified.add(entry[3:])
                if 'R' in entry[:2] or 'C' in entry[:2]:
                    index += 1
            index += 1
    else:
        tree = raw_git(root, 'ls-tree', '-rz', revision).split('\x00')
        names = [entry.split('\t', 1)[1] for entry in tree if '\t' in entry and entry.startswith(('100644 ', '100755 '))]
        modified = set()
    names = sorted(set(name for name in names if visible(name)))
    prefix = directory + '/' if directory else ''
    rows = {}
    for name in names:
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix):]
        part = rest.split('/')[0]
        path = prefix + part
        if not part:
            continue
        try:
            candidate = checked(root, name) if local else None
            if candidate and not candidate.is_file():
                continue
        except (OSError, ValueError, Blocked):
            continue
        row = rows.setdefault(path, {'name': part, 'path': path, 'kind': 'folder' if '/' in rest else 'file',
                                    'modified': False, **history.get(path, {'time': None, 'message': ''})})
        if name in modified:
            row['modified'] = True
            row['message'] = 'Modification locale'
            row['time'] = max(row.get('time') or 0, candidate.stat().st_mtime)
    result = {'branch': current or 'HEAD détachée', 'selected_branch': branch or current,
              'branches': list(branches)[:300], 'local': local, 'directory': directory,
              'entries': sorted(rows.values(), key=lambda row: (row['kind'] != 'folder', row['name'].lower()))[:1000],
              'truncated': len(rows) > 1000, 'updated_at': time.time(), 'file': None, 'readme': None}

    def read(name):
        safe_relative(name)
        if name not in names:
            raise ValueError('file_unavailable')
        if local:
            candidate = checked(root, name)
            with candidate.open('rb') as stream:
                data = stream.read(LIMIT + 1)
        else:
            size = git(root, 'cat-file', '-s', revision + ':' + name)
            if int(size) > LIMIT:
                return {'path': name, 'unavailable': 'Fichier trop volumineux'}
            code, data, _ = run_command(['git', 'show', revision + ':' + name], root, max_output_bytes=LIMIT + 1000)
            if code:
                raise ValueError('file_unavailable')
        if len(data) > LIMIT:
            return {'path': name, 'unavailable': 'Fichier trop volumineux'}
        if secret_like(data) or re.search(rb'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|\d{8,12}:[A-Za-z0-9_-]{30,})', data):
            return {'path': name, 'unavailable': 'Contenu sensible masqué'}
        try:
            content = data.decode('utf-8')
            if '\x00' in content:
                raise UnicodeError()
        except UnicodeError:
            return {'path': name, 'unavailable': 'Aperçu texte indisponible'}
        return {'path': name, 'content': content}

    if file:
        result['file'] = read(file)
    else:
        readme = next((name for name in names if name.startswith(prefix) and '/' not in name[len(prefix):]
                       and name[len(prefix):].lower() in {'readme.md', 'readme', 'readme.rst', 'readme.txt'}), None)
        if readme:
            result['readme'] = read(readme)
    return result
