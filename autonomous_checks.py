"""Trusted offline application checks; candidate cannot modify this harness."""
import os
import subprocess
import sys
from pathlib import Path


def main():
    root = Path.cwd()
    sys.path.insert(0, str(root / 'src'))
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    # Bound numerical-library thread pools so LightGBM tests fit the Windows
    # per-process memory budget, instead of starting one BLAS worker per CPU.
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ[key] = '1'
    # Never connect tests to a user's real database or inherited application keys.
    os.environ.pop('TEST_DATABASE_URL', None)
    import pytest
    # Host recovery requires .local and .venv and can operate Windows services.
    # Those scripts stay protected; their tests do not validate application edits.
    status = pytest.main(['-q', '-p', 'pytest_asyncio.plugin', '-p', 'no:cacheprovider',
                         '--ignore=tests/test_local_recovery.py', 'tests'])
    if status:
        return status
    return subprocess.run([sys.executable, '-I', '-m', 'ruff', 'check', '--no-cache',
                           'src', 'tests', 'scripts'], check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
