"""Local project reconnaissance and a fixed, shell-free validation plan."""
import json
import ast
from pathlib import Path
import re


def inspect_project(source, files):
    names = sorted(files)
    languages = sorted({{'.py': 'Python', '.js': 'JavaScript', '.mjs': 'JavaScript',
                         '.ts': 'TypeScript', '.tsx': 'TypeScript', '.jsx': 'JavaScript',
                         '.rs': 'Rust', '.go': 'Go', '.cs': 'C#', '.java': 'Java',
                         '.html': 'HTML', '.css': 'CSS'}.get(Path(n).suffix, '') for n in names} - {''})
    recipes, frameworks = [], []
    for name in names:
        parent = Path(name).parent.as_posix()
        if parent == '.':
            parent = ''
        if Path(name).name == 'package.json':
            package = json.loads((source / name).read_text(encoding='utf-8'))
            dependencies = {**package.get('dependencies', {}), **package.get('devDependencies', {})}
            frameworks += [f for f in ('react', 'next', 'vue', 'svelte', 'vite', 'express') if f in dependencies]
            for script in ('test', 'lint', 'typecheck', 'build'):
                text = package.get('scripts', {}).get(script, '')
                if text and 'no test specified' not in text:
                    recipes.append({'name': f'{name}: {script}', 'cwd': parent,
                                    'command': 'npm test -- --watch=false' if script == 'test' and 'react-scripts' in text
                                    else 'npm run ' + script})
        elif Path(name).name == 'Cargo.toml':
            recipes.append({'name': 'Rust tests', 'cwd': parent, 'command': 'cargo test'})
        elif Path(name).name == 'go.mod':
            recipes.append({'name': 'Go tests', 'cwd': parent, 'command': 'go test ./...'})
    if any(n.endswith('.py') for n in names):
        roots = sorted({n.split('/tests/', 1)[0] if '/tests/' in n else ''
                        for n in names if n.endswith('.py') and ('/tests/' in n or n.startswith('tests/') or '/' not in n and Path(n).name.startswith('test_'))})
        for root in roots:
            prefix = root + '/' if root else ''
            tests = [n for n in names if n.endswith('.py') and (n.startswith(prefix + 'tests/') or not root and '/' not in n and Path(n).name.startswith('test_'))]
            pytest = False
            for name in tests:
                text = (source / name).read_text(encoding='utf-8')
                pytest = pytest or bool(re.search(r'\b(?:import pytest|from pytest)', text))
                try:
                    pytest = pytest or any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_')
                                           for node in ast.parse(text).body)
                except SyntaxError:
                    pass
            directory = 'tests' if any(n.startswith(prefix + 'tests/') for n in tests) else '.'
            recipes.append({'name': 'Python tests', 'cwd': root,
                            'command': 'python -m pytest ' + directory if pytest else 'python -m unittest discover -s ' + directory})
        for name in names:
            if Path(name).name == 'pyproject.toml':
                text = (source / name).read_text(encoding='utf-8')
                frameworks += [f for f in ('fastapi', 'django', 'flask') if f in text.lower()]
    dotnet = next((n for n in names if n.endswith('.sln')), None)
    if dotnet is None:
        dotnet = next((n for n in names if n.endswith(('.csproj', '.fsproj'))), None)
    if dotnet:
        recipes.append({'name': '.NET tests', 'cwd': Path(dotnet).parent.as_posix(), 'command': 'dotnet test'})
    if not recipes and any(n.endswith(('.test.js', '.test.mjs', '.test.cjs')) and b'node:test' in (source / n).read_bytes() for n in names):
        recipes.append({'name': 'Node regression tests', 'cwd': '', 'command': 'node --test'})
    return {'version': 1, 'languages': languages, 'frameworks': sorted(set(frameworks)),
            'folders': sorted({n.split('/')[0] for n in names if '/' in n})[:30],
            'entrypoints': [n for n in names if Path(n).name in
                           {'main.py', 'app.py', 'manage.py', 'index.html', 'main.ts', 'main.tsx', 'main.go', 'main.rs'}][:25],
            'manifests': [n for n in names if Path(n).name in
                          {'package.json', 'pyproject.toml', 'Cargo.toml', 'go.mod', 'requirements.txt'}][:25],
            'recipes': recipes[:20], 'validation_level': 'project_checks' if recipes else 'structural',
            'note': 'Structural checks alone do not prove feature behavior. Add regression tests for requested features.'}


def configure_checks(runtime, source, files, override=''):
    from github_projects import command
    import sys
    profile = inspect_project(source, files)
    recipes = [{'name': 'Operator checks', 'cwd': '', 'command': override}] if override else profile['recipes']
    checks = []
    missing = []
    for recipe in recipes:
        try:
            argv = command(recipe['command'])
        except ValueError:
            # Import still works; the runtime reports the missing tool explicitly.
            argv = []
            missing.append(recipe['name'])
        if argv and argv[0] == sys.executable:
            python_spec = runtime / f'check-command-{len(checks)}.json'
            python_spec.write_text(json.dumps(argv), encoding='utf-8')
            argv = [sys.executable, '-I', '-B', str(Path(__file__).with_name('imported_checks.py')), str(python_spec)]
        checks.append({**recipe, 'argv': argv})
    profile['missing_tools'] = missing
    profile['validation_level'] = 'project_checks' if checks else 'structural'
    specification = runtime / 'automatic-checks.json'
    fallback_spec = runtime / 'check-created-tests.json'
    fallback_spec.write_text(json.dumps([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests']), encoding='utf-8')
    python_fallback = [sys.executable, '-I', '-B', str(Path(__file__).with_name('imported_checks.py')), str(fallback_spec)]
    pytest_spec = runtime / 'check-created-pytest.json'
    pytest_spec.write_text(json.dumps([sys.executable, '-m', 'pytest', 'tests']), encoding='utf-8')
    pytest_fallback = [sys.executable, '-I', '-B', str(Path(__file__).with_name('imported_checks.py')), str(pytest_spec)]
    specification.write_text(json.dumps({'checks': checks, 'profile': profile, 'python_fallback': python_fallback,
                                        'pytest_fallback': pytest_fallback}, indent=2), encoding='utf-8')
    (runtime / 'project-profile.json').write_text(json.dumps(profile, indent=2), encoding='utf-8')
    return profile, [sys.executable, '-I', '-B', str(Path(__file__).with_name('automatic_checks.py')), str(specification)]
