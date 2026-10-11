"""New source and regression-test paths, approved one task at a time."""
from pathlib import Path

from .safety import Blocked, forbidden, plain_file, safe_relative

EXTENSIONS = {'.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.css', '.scss',
              '.html', '.vue', '.svelte', '.go', '.rs', '.java', '.kt', '.cs', '.fs',
              '.c', '.h', '.cpp', '.hpp', '.swift', '.rb', '.php', '.dart', '.scala', '.md', '.rst', '.svg'}


def is_test_path(name):
    parts = Path(name.lower()).parts
    return any(p.startswith(('test', 'spec')) for p in parts) or '.test.' in name.lower() or '.spec.' in name.lower() or name.lower().endswith('_test.go')


def new_file_allowed(name, config):
    if not config.get('allow_new_files') or name in config.get('protected_files', []):
        return False
    try:
        path = safe_relative(name)
    except (Blocked, TypeError):
        return False
    if forbidden(name) or any(p.startswith('.') for p in path.parts):
        return False
    if path.name in {'AGENTS.md', 'SKILL.md', 'setup.py', 'conftest.py', 'sitecustomize.py'}:
        return False
    if path.suffix.lower() not in EXTENSIONS:
        return False
    return name not in config.get('source_files', [])


def task_paths_allowed(proposal, config):
    return all(name in config['allowed_files'] or new_file_allowed(name, config) for name in proposal['files'])


def inspect_new_paths(proposal, config, worktree, source=None):
    result = []
    for name in proposal['files']:
        if name in config['allowed_files']:
            continue
        if not new_file_allowed(name, config):
            raise Blocked('new_file_outside_scope')
        for root in [worktree] + ([Path(source)] if source else []):
            root = root.resolve()
            target = root / safe_relative(name)
            target.resolve().relative_to(root)
            if target.exists() or target.is_symlink():
                raise Blocked('new_file_conflicts_with_existing_path')
            for parent in target.parents:
                if parent == root:
                    break
                if parent.exists():
                    plain_file(parent)
        result.append(name)
    return result
