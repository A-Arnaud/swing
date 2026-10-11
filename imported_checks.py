"""Run operator-selected Python checks against the candidate, not an installed editable project."""
import json
import os
from pathlib import Path
import runpy
import sys


def main():
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ[key] = '1'
    argv = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    root = Path.cwd()
    sys.path[:0] = [str(root / 'src'), str(root)]
    args = argv[1:]
    if len(args) >= 2 and args[0] == '-m':
        sys.argv = [args[1], *args[2:]]
        runpy.run_module(args[1], run_name='__main__', alter_sys=True)
    elif args and not args[0].startswith('-'):
        path = (root / args[0]).resolve()
        path.relative_to(root.resolve())
        sys.argv = [str(path), *args[1:]]
        runpy.run_path(str(path), run_name='__main__')
    else:
        raise ValueError('python_validation_requires_module_or_script')


if __name__ == '__main__':
    main()
