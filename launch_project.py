"""Preflight and launch the prepared runtime without weakening execution gates."""
import argparse
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from supervisor.engine import Supervisor, exclusive
from supervisor.quota import read_quota
from supervisor.reports import render
from supervisor.safety import Blocked


def restricted_token():
    if os.name != 'nt':
        return False
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.IsTokenRestricted.argtypes = [wintypes.HANDLE]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise Blocked('execution_context_query_failed')
    try:
        return bool(advapi.IsTokenRestricted(token))
    finally:
        kernel.CloseHandle(token)


def main(runtime=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check only; never launches a model turn')
    parser.add_argument('--runtime', type=Path, default=Path(__file__).resolve().parent / 'runtime-chatgpt-plus')
    args = parser.parse_args([] if runtime is not None else None)
    from project_management import runtime_guard
    with runtime_guard(runtime or args.runtime):
        return launch(runtime or args.runtime, args.check)


def launch(runtime, check=False):
    s = Supervisor(runtime)
    try:
        if s.store.get('project_removed') or s.store.get('project_deleting'):
            raise Blocked('project_unavailable')
        if restricted_token():
            raise Blocked('restricted_windows_execution_context')
        s.original_unchanged()
        quota = read_quota(s.config['codex_executable'], s.repo)
        s.store.set('chatgpt_quota', quota)
        if quota['exhausted']:
            raise Blocked('chatgpt_quota_exhausted')
        if not (s.config['isolated_execution_confirmed'] is True or s.config.get('local_execution_authorized', False) is True):
            raise Blocked('external_isolation_not_confirmed')
        if check:
            print(json.dumps({'status': 'ready', 'model': s.config['model'],
                              'reasoning_effort': s.config['reasoning_effort'], 'isolated_execution_confirmed': s.config['isolated_execution_confirmed'], 'local_execution_authorized': s.config.get('local_execution_authorized', False)}))
            return 0
        # Launch uses the operator's isolation attestation or explicit local authorization.
        s.control('running')
        render(s)
        print(json.dumps({'status': 'started', 'model': s.config['model'], 'reasoning_effort': s.config['reasoning_effort'], 'execution_backend': s.config['execution_backend']}), flush=True)
        s.run()
        print(json.dumps({'status': 'ended', 'control': s.store.get('control')}))
        return 0
    except (Blocked, OSError) as error:
        reason = str(error) if isinstance(error, Blocked) else 'execution_environment_error'
        s.store.set('control', {'mode': 'stopped', 'reason': reason})
        print(json.dumps({'status': 'blocked', 'reason': reason}))
        return 1
    finally:
        render(s)
        s.close()


if __name__ == '__main__':
    try:
        # The supervisor itself locks its run; callers can lock this launcher too.
        raise SystemExit(main())
    except Blocked as error:
        print(json.dumps({'status': 'blocked', 'reason': str(error)}))
        raise SystemExit(1)
