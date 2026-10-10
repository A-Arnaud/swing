"""Read-only ChatGPT quota probe. Never reads credentials or redeems credits."""
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from .safety import Blocked, run_command, sanitized_environment, terminate_tree


def codex_environment():
    env = sanitized_environment()
    for key in ('USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'CODEX_HOME', 'HOME',
                'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
        if key in os.environ:
            env[key] = os.environ[key]
    # No API keys inherited. Managed proxies are preserved.
    return env


def summarize_limits(result):
    bucket = result.get('rateLimitsByLimitId', {}).get('codex') or result.get('rateLimits')
    if not isinstance(bucket, dict) or bucket.get('limitId') not in {None, 'codex'}:
        raise Blocked('chatgpt_quota_unknown')
    windows = []
    for name in ('primary', 'secondary'):
        window = bucket.get(name)
        if window is None:
            continue
        used = window.get('usedPercent')
        if type(used) not in {int, float} or not math.isfinite(used) or not 0 <= used <= 100:
            raise Blocked('chatgpt_quota_unknown')
        windows.append({'window': name, 'used_percent': used, 'resets_at': window.get('resetsAt')})
    if not windows:
        raise Blocked('chatgpt_quota_unknown')
    return {'checked_at': time.time(), 'windows': windows,
            'exhausted': any(w['used_percent'] >= 100 for w in windows) or
            bool(bucket.get('rateLimitReachedType'))}


def probe(native):
    """Called inside a bounded helper process owned by run_command's Job Object."""
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    process = subprocess.Popen([native, 'app-server', '-c', 'forced_login_method="chatgpt"'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=codex_environment(), **options)
    messages = queue.Queue(maxsize=256)
    def read():
        while line := process.stdout.readline(65537):
            if len(line) > 65536:
                break
            try:
                messages.put_nowait(json.loads(line))
            except (ValueError, queue.Full):
                break
        try:
            messages.put_nowait(None)
        except queue.Full:
            pass
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    def send(value):
        process.stdin.write((json.dumps(value) + '\n').encode())
        process.stdin.flush()
    def request(id, method, params=None):
        value = {'id': id, 'method': method}
        if params is not None:
            value['params'] = params
        send(value)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                item = messages.get(timeout=min(1, max(.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if item is None:
                break
            if item.get('id') == id:
                if 'error' in item:
                    break
                return item['result']
        raise Blocked('chatgpt_quota_unknown')
    try:
        request(1, 'initialize', {'clientInfo': {'name': 'radar_supervisor', 'version': '0.1.0'}})
        send({'method': 'initialized'})
        account = request(2, 'account/read', {'refreshToken': False}).get('account')
        if not account or account.get('type') != 'chatgpt':
            raise Blocked('chatgpt_login_required')
        # Identity/email and raw responses are never returned or written to disk.
        result = summarize_limits(request(3, 'account/rateLimits/read'))
        result['auth_type'] = 'chatgpt'
        return result
    finally:
        terminate_tree(process)
        process.stdin.close()
        reader.join(timeout=1)
        process.stdout.close()


def read_quota(native, worktree, interrupt=lambda: False):
    code, out, _ = run_command([sys.executable, '-m', 'supervisor.quota', str(native)],
                              Path(__file__).resolve().parents[1], 30, interrupt, codex_environment())
    try:
        result = json.loads(out)
        if code or 'error' in result:
            raise Blocked(result.get('error', 'chatgpt_quota_unknown'))
        return result
    except (ValueError, TypeError):
        raise Blocked('chatgpt_quota_unknown') from None


if __name__ == '__main__':
    try:
        print(json.dumps(probe(sys.argv[1])))
    except (Blocked, OSError, KeyError, TypeError, ValueError):
        print(json.dumps({'error': 'chatgpt_quota_unknown'}))
        raise SystemExit(1)
