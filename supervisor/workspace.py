import json
from pathlib import Path

from .safety import Blocked, digest, forbidden, git, plain_file, safe_relative, secret_like

TOP_FILES = {"README.md", "STATUS.md", "pyproject.toml", "requirements.lock", "requirements-research.lock",
             "Dockerfile", "docker-compose.yml", "mission.txt", "mission2.txt", ".env.example"}
TOP_DIRS = {"src", "tests", "docs", "specifications", "scripts", "migrations", "examples"}
ASSET_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.woff', '.woff2', '.ttf', '.otf', '.avif'}


def collect(source, selected=None):
    """Allowlisted text/code only. No live databases, environment, Git metadata or links."""
    source = Path(source).resolve()
    files = {}
    total = 0
    if selected is not None:
        for name in selected:
            relative = safe_relative(name)
            if forbidden(name):
                raise Blocked('protected_import_path')
            path = source / relative
            if not path.resolve().is_relative_to(source):
                raise Blocked('path_outside_source')
            for parent in [path, *path.parents]:
                if parent == source:
                    break
                plain_file(parent)
            data = path.read_bytes()
            if len(data) > 2_000_000 or (b'\0' in data and path.suffix.lower() not in ASSET_EXTENSIONS) or secret_like(data):
                raise Blocked('snapshot_content_requires_review')
            total += len(data)
            if total > 25_000_000:
                raise Blocked('snapshot_size_limit')
            files[name] = data
        if not files:
            raise Blocked('empty_snapshot')
        return files
    def visit(directory):
        nonlocal total
        for path in sorted(directory.iterdir()):
            name = path.relative_to(source).as_posix()
            if forbidden(name):
                continue
            plain_file(path)
            if path.is_dir():
                if directory == source and path.name not in TOP_DIRS:
                    continue
                visit(path)
            elif directory != source or path.name in TOP_FILES:
                if path.stat().st_size > 2_000_000:
                    raise Blocked("snapshot_file_too_large")
                data = path.read_bytes()
                if b"\0" in data or secret_like(data):
                    raise Blocked("snapshot_content_requires_review")
                total += len(data)
                if total > 25_000_000:
                    raise Blocked("snapshot_size_limit")
                files[name] = data
    visit(source)
    if not files:
        raise Blocked("empty_snapshot")
    return files


def initialize_repo(repo, files):
    if repo.exists():
        raise Blocked("snapshot_already_exists")
    repo.mkdir(parents=True)
    for name, data in files.items():
        path = repo / safe_relative(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    git(repo, "init", "-b", "validated")
    git(repo, "config", "user.name", "Improvement Supervisor")
    git(repo, "config", "user.email", "supervisor@localhost.invalid")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "add", "--all")
    git(repo, "commit", "-m", "Sanitized baseline; original project untouched")
    return {name: digest(data) for name, data in files.items()}


def changed_files(worktree, allowed, protected):
    names = git(worktree, "diff", "--name-only", "HEAD").splitlines()
    names += git(worktree, "ls-files", "--others", "--exclude-standard").splitlines()
    if not names:
        raise Blocked("no_code_change")
    for name in names:
        path = worktree / safe_relative(name)
        if not path.resolve().is_relative_to(worktree.resolve()):
            raise Blocked("path_outside_workspace")
        if forbidden(name) or name not in allowed or name in protected:
            raise Blocked("change_outside_approved_scope")
        if not path.exists():
            raise Blocked("deletion_requires_review")
        plain_file(path)
        if path.stat().st_size > 2_000_000 or secret_like(path.read_bytes()):
            raise Blocked("unsafe_candidate_content")
    return sorted(set(names))


def backup(repo, destination):
    git(repo, "bundle", "create", str(destination), "validated")


def source_manifest(files):
    return json.dumps({name: digest(data) for name, data in files.items()}, sort_keys=True)
