"""Preflight and launch the prepared runtime without weakening execution gates."""
import argparse
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from supervisor.engine import Supervisor
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check only; never launches a model turn')
    args = parser.parse_args()
    s = Supervisor(Path(__file__).resolve().parent / 'runtime-chatgpt-plus')
    try:
        if restricted_token():
            raise Blocked('restricted_windows_execution_context')
        s.original_unchanged()
        quota = read_quota(s.config['codex_executable'], s.repo)
        s.store.set('chatgpt_quota', quota)
        if quota['exhausted']:
            raise Blocked('chatgpt_quota_exhausted')
        if not s.config['isolated_execution_confirmed']:
            raise Blocked('external_isolation_not_confirmed')
        if args.check:
            print(json.dumps({'status': 'ready', 'model': s.config['model'],
                              'reasoning_effort': s.config['reasoning_effort']}))
            return 0
        # The user explicitly authorized launch. Preserve the operator's safety attestation.
        s.control('running')
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
    raise SystemExit(main())
