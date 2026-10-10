"""Create a paused real-project runtime. Does not attest isolation or launch AI."""
import json
import sys
from pathlib import Path

from supervisor.engine import Supervisor, initialize
from supervisor.reports import render
from supervisor.safety import Blocked, run_command


def main():
    root = Path(__file__).resolve().parent
    source = root.parents[1]
    runtime = root / 'runtime-chatgpt-plus'
    initialize(runtime, source, 'ralph')
    config = json.loads((runtime / 'config.json').read_text())
    candidates = list((Path.home() / 'AppData/Roaming/npm/node_modules/@openai/codex').glob(
        'node_modules/@openai/codex-win32-x64/vendor/*/bin/codex.exe'))
    if len(candidates) != 1:
        raise Blocked('native_codex_executable_not_found')
    harness = root / 'project_checks.py'
    reference = runtime / 'feature-reference.json'
    code, _, _ = run_command([sys.executable, '-I', '-B', str(harness), 'reference', str(reference)],
                             runtime / 'repository')
    if code:
        raise Blocked('reference_creation_failed')
    allowed = ['src/token_radar/features.py']
    config.update(model='gpt-6.1-sol', reasoning_effort='medium', chatgpt_quota_guard=True,
                  codex_executable=str(candidates[0]), live_enabled=True,
                  isolated_execution_confirmed=False, max_ai_calls_per_day=256,
                  max_tokens_per_day=25000000, max_disk_mb=1500,
                  validation_timeout_seconds=300, allowed_files=allowed,
                  protected_files=[p for p in config['protected_files'] if p not in allowed],
                  validation_argv=[sys.executable, '-I', '-B', str(harness), 'validate', str(reference)],
                  benchmark_argv=[sys.executable, '-I', '-B', str(harness), 'benchmark', str(reference)])
    (runtime / 'config.json').write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    s = Supervisor(runtime)
    try:
        s.store.set('control', {'mode': 'paused', 'reason': 'execution_environment_not_verified'})
        print(json.dumps({'runtime': str(runtime), 'report': str(render(s)),
                          'model': config['model'], 'reasoning_effort': 'medium',
                          'control': s.store.get('control')}))
    finally:
        s.close()


if __name__ == '__main__':
    main()
