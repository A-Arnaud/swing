"""Independent validation harness outside the candidate: fixed recipes, no shell."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys


def run(argv, cwd):
    result = subprocess.run(argv, cwd=cwd, capture_output=True)
    if result.returncode:
        # The supervising process hashes output; no raw log is sent to the dashboard.
        sys.stderr.buffer.write(result.stdout + result.stderr)
        raise RuntimeError('project_check_failed')


def main():
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ[key] = '1'
    os.environ['CI'] = '1'
    specification = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    root = Path.cwd().resolve()
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z']).decode().split('\0')
    checked = 0
    python_tests, node_tests = [], []
    uses_pytest, has_unittest_cases = False, False
    for name in filter(None, names):
        path = (root / name).resolve()
        path.relative_to(root)
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix == '.py':
            text = path.read_text(encoding='utf-8-sig')
            tree = ast.parse(text, filename=name)
            checked += 1
            if Path(name).name.startswith('test_') and name.startswith('tests/'):
                python_tests.append(name)
                uses_pytest = uses_pytest or 'import pytest' in text or 'from pytest' in text or any(
                    isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith('test_') for n in tree.body)
                has_unittest_cases = has_unittest_cases or any(isinstance(n, ast.ClassDef) and any(
                    isinstance(f, ast.FunctionDef) and f.name.startswith('test_') for f in n.body) for n in tree.body)
        elif path.suffix == '.json':
            json.loads(path.read_text(encoding='utf-8-sig'))
            checked += 1
        elif path.suffix in {'.js', '.mjs', '.cjs'} and path.stat().st_size < 2_000_000:
            # JSX and bundler modules use the project's build/typecheck recipe.
            if not specification['checks']:
                import shutil
                node = shutil.which('node')
                if not node:
                    raise RuntimeError('node_runtime_required')
                run([node, '--check', str(path)], root)
                checked += 1
            if Path(name).name.endswith(('.test.js', '.test.mjs', '.test.cjs')):
                node_tests.append(name)
        elif path.suffix in {'.html', '.css', '.md', '.rst', '.svg'}:
            path.read_text(encoding='utf-8')
            checked += 1
    for check in specification['checks']:
        if not check['argv']:
            raise RuntimeError('validation_tool_missing: ' + check['name'])
        cwd = (root / check['cwd']).resolve()
        cwd.relative_to(root)
        argv = check['argv']
        # Python recipes use the external trusted loader in their own process.
        run(argv, cwd)
        checked += 1
    python_covered = any(not c['cwd'] and c['command'].startswith(('python -m pytest', 'python -m unittest')) for c in specification['checks'])
    node_covered = any(not c['cwd'] and c['command'].startswith(('npm test', 'npm run test', 'node --test')) for c in specification['checks'])
    if not python_covered and python_tests:
        if not uses_pytest and not has_unittest_cases:
            raise RuntimeError('regression_tests_missing_cases')
        run(specification['pytest_fallback'] if uses_pytest else specification['python_fallback'], root)
        checked += 1
    if not node_covered and node_tests:
        import shutil
        run([shutil.which('node'), '--test', *node_tests], root)
        checked += 1
    if not checked:
        raise RuntimeError('no_checks_available_for_project')
    level = 'project_checks' if specification['checks'] or python_tests or node_tests else 'structural'
    print(json.dumps({'checks': checked, 'level': level}))


if __name__ == '__main__':
    main()
