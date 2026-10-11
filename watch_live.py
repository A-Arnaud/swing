"""Refresh the local report while the detached supervisor is at work."""
import argparse
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

from supervisor.reports import render


class StoreView:
    def __init__(self, path):
        self.db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def history(self, limit=30):
        return [json.loads(row[0]) for row in self.db.execute(
            'SELECT report FROM cycles ORDER BY started DESC LIMIT ?', (limit,))]

    def proposals(self):
        return [dict(id=r[0], status=r[1], attempts=r[2], payload=json.loads(r[3]))
                for r in self.db.execute('SELECT id,status,attempts,payload FROM proposals ORDER BY id')]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True, type=Path)
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    config = json.loads((runtime / 'config.json').read_text(encoding='utf-8'))
    store = StoreView(runtime / 'state.sqlite')
    observer = SimpleNamespace(root=runtime, store=store, config=config,
                               repo=runtime / 'repository')
    try:
        while store.get('control', {}).get('mode') in {'running', 'auto_paused'}:
            render(observer)
            if store.get('control', {}).get('mode') == 'auto_paused':
                break
            time.sleep(1)
    finally:
        store.db.close()


if __name__ == '__main__':
    main()
