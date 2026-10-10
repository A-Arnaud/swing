"""Trusted offline validation/benchmark, outside candidate worktrees."""
import importlib
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


def cases():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for size in (0, 1, 3, 128, 4096):
        rows = [{'id': i, 'observed_at': start + timedelta(seconds=i),
                 'ingested_at': start + timedelta(seconds=i), 'status': 'ok', 'pair': 'fixture',
                 'price_usd': 1 + i / 1000, 'market_cap_usd': 10000 + i,
                 'liquidity_usd': 12000, 'volume_5m_usd': 8000, 'buy_count': 10,
                 'unique_buyers': 8} for i in range(size)]
        yield rows, start + timedelta(seconds=max(0, size - 1)), start


def main():
    mode = sys.argv[1]
    # -I avoids environment import paths; the candidate package is selected explicitly.
    sys.path.insert(0, str(Path.cwd() / 'src'))
    features = importlib.import_module('token_radar.features').market_features
    data = list(cases())
    results = [features(*case) for case in data]
    expected = Path(sys.argv[2])
    if mode == 'reference':
        expected.write_text(json.dumps(results, sort_keys=True) + '\n', encoding='utf-8')
        return 0
    assert results == json.loads(expected.read_text()), 'independent feature contract changed'
    if mode == 'validate':
        os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
        os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
        import pytest
        # Scope: features and their application consumers. Host recovery tests need
        # the original .venv/.local; they cannot run in a sanitized snapshot.
        tests = ['tests/test_analysis.py', 'tests/test_core.py', 'tests/test_market_batch.py',
                 'tests/test_regime.py', 'tests/test_selection.py', 'tests/test_outcomes.py', 'tests/test_api.py']
        status = pytest.main(['-q', '-p', 'pytest_asyncio.plugin', '-p', 'no:cacheprovider', *tests, *sys.argv[3:]])
        if status:
            return status
        return subprocess.run([sys.executable, '-I', '-m', 'ruff', 'check', '--no-cache', 'src', 'tests', 'scripts'],
                              check=False).returncode
    if mode != 'benchmark':
        raise ValueError('invalid mode')
    samples = []
    for _ in range(3):
        start = time.perf_counter()
        for _ in range(20):
            for case in data:
                features(*case)
        samples.append((time.perf_counter() - start) * 1000)
    print(json.dumps({'metric': 'market_features_fixture_ms', 'value': statistics.median(samples)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
