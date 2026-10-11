"""Export current local source without Git writes, credentials or runtime files."""
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from repository_browser import raw_git
from supervisor.safety import Blocked, forbidden, plain_file, safe_relative, secret_like
from supervisor.store import Store


def source_file(root, name):
    path = root / safe_relative(name)
    path.resolve().relative_to(root)
    current = root
    plain_file(current)
    for part in safe_relative(name).parts:
        current /= part
        plain_file(current)
    return path


def archive(runtime):
    runtime = Path(runtime)
    config = json.loads((runtime / 'config.json').read_text(encoding='utf-8'))
    store = Store(runtime / 'state.sqlite', readonly=True)
    try:
        if store.get('pending_promotion'):
            raise Blocked('export_source_busy')
        root = Path(store.get('source')).resolve()
        names = sorted(set(filter(None, raw_git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').split('\x00'))))
        if len(names) > 20000:
            raise Blocked('export_size_limit')
        title = config.get('project_name') or root.name
        title = re.sub(r'[^a-zA-Z0-9_-]+', '-', title).strip('-')[:70] or 'projet'
        output = io.BytesIO()
        signatures = {}
        total = 0
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=5) as zipped:
            for name in names:
                if forbidden(name):
                    continue
                try:
                    path = source_file(root, name)
                except (OSError, ValueError, Blocked):
                    continue
                if not path.is_file():
                    continue
                before = path.stat()
                if before.st_size > 16_000_000:
                    raise Blocked('export_size_limit')
                with path.open('rb') as stream:
                    data = stream.read(16_000_001)
                after = path.stat()
                signature = (before.st_size, before.st_mtime_ns)
                if signature != (after.st_size, after.st_mtime_ns) or len(data) != before.st_size:
                    raise Blocked('export_source_busy')
                if secret_like(data) or re.search(rb'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|\d{8,12}:[A-Za-z0-9_-]{30,})', data):
                    continue
                total += len(data)
                if total > 64_000_000:
                    raise Blocked('export_size_limit')
                signatures[name] = signature
                date = datetime.fromtimestamp(before.st_mtime).timetuple()[:6]
                date = max((1980, 1, 1, 0, 0, 0), min(date, (2107, 12, 31, 23, 59, 58)))
                info = zipfile.ZipInfo(title + '/' + name, date)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o100755 if before.st_mode & 0o111 else 0o100644) << 16
                zipped.writestr(info, data)
        if not signatures:
            raise Blocked('export_empty')
        if store.get('pending_promotion'):
            raise Blocked('export_source_busy')
        for name, signature in signatures.items():
            stat = source_file(root, name).stat()
            if (stat.st_size, stat.st_mtime_ns) != signature:
                raise Blocked('export_source_busy')
        if names != sorted(set(filter(None, raw_git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').split('\x00')))):
            raise Blocked('export_source_busy')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        return f'{title}-{stamp}.zip', output.getvalue()
    finally:
        store.close()
