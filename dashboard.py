"""Loopback-only Ralph dashboard and explicit stop control."""
import argparse
import contextlib
import json
import sqlite3
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from supervisor.store import Store
from dashboard_data import status, changes, projects, save_project


def serve(runtime):
    runtime = Path(runtime).resolve()
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = False

        def __init__(self, address, handler):
            super().__init__(address, handler)
            self.start_lock = threading.Lock()
            self.start_thread = None
            self.start_error = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def local_request(self):
            host = self.headers.get('Host', '')
            expected = f'127.0.0.1:{self.server.server_port}'
            return self.client_address[0] in {'127.0.0.1', '::1'} and host == expected

        def send_bytes(self, payload, content_type, status=200):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if not self.local_request():
                self.send_error(403)
                return
            route = urlsplit(self.path).path
            if route == '/':
                path, content = Path(__file__).with_name('dashboard.html'), 'text/html; charset=utf-8'
                self.send_bytes(path.read_bytes(), content)
            elif route == '/app.js':
                path = Path(__file__).with_name('dashboard.js')
                self.send_bytes(path.read_bytes(), 'text/javascript; charset=utf-8')
            elif route == '/style.css':
                path = Path(__file__).with_name('style.css')
                self.send_bytes(path.read_bytes(), 'text/css; charset=utf-8')
            elif route == '/swing.svg':
                self.send_bytes(Path(__file__).with_name('swing.svg').read_bytes(), 'image/svg+xml')
            elif route == '/favicon.ico':
                self.send_bytes(b'', 'image/x-icon', 204)
            elif route == '/report':
                path = runtime / 'reports' / 'index.html'
                self.send_bytes(path.read_bytes(), 'text/html; charset=utf-8')
            elif route == '/api/status':
                summary = status(runtime)
                summary['start_pending'] = bool(self.server.start_thread and self.server.start_thread.is_alive())
                summary['start_error'] = self.server.start_error
                payload = json.dumps(summary, ensure_ascii=False).encode('utf-8')
                self.send_bytes(payload, 'application/json; charset=utf-8')
            elif route in {'/api/changes', '/api/projects'}:
                try:
                    payload = changes(runtime) if route == '/api/changes' else {'projects': projects(runtime)}
                    self.send_bytes(json.dumps(payload, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
                except (OSError, ValueError, RuntimeError):
                    self.send_bytes(b'{"error":"data_unavailable"}', 'application/json; charset=utf-8', 503)
            else:
                self.send_error(404)

        def do_POST(self):
            origin = f'http://127.0.0.1:{self.server.server_port}'
            route = urlsplit(self.path).path
            if not self.local_request() or self.headers.get('Origin') != origin or route not in {'/api/stop','/api/start','/api/instructions','/api/projects'}:
                self.send_error(403)
                return
            try:
                if route == '/api/projects':
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 40000:
                        raise ValueError('invalid_length')
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError('invalid_project')
                    result = save_project(runtime, body)
                    self.send_bytes(json.dumps(result).encode(), 'application/json; charset=utf-8', 201)
                    return
                if route == '/api/instructions':
                    length = int(self.headers.get('Content-Length', '0'))
                    if length <= 0 or length > 16_384:
                        self.send_bytes(b'{"status":"invalid_length"}', 'application/json; charset=utf-8', 400)
                        return
                    body = json.loads(self.rfile.read(length))
                    instructions = body.get('instructions') if isinstance(body, dict) else None
                    if not isinstance(instructions, str) or len(instructions) > 4000:
                        self.send_bytes(b'{"status":"invalid_instructions"}', 'application/json; charset=utf-8', 400)
                        return
                    store = Store(runtime / 'state.sqlite')
                    store.set('ralph_instructions', instructions.strip())
                    store.set('instructions_saved_at', __import__('time').time())
                    store.close()
                    self.send_bytes(b'{"status":"saved"}', 'application/json; charset=utf-8')
                    return
                if route == '/api/start':
                    with self.server.start_lock:
                        db = sqlite3.connect((runtime / 'state.sqlite').resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
                        try:
                            row = db.execute("SELECT value FROM meta WHERE key='control'").fetchone()
                            control = json.loads(row[0]) if row else {}
                        finally:
                            db.close()
                        if control.get('mode') != 'stopped':
                            self.send_bytes(b'{"status":"already_running"}', 'application/json; charset=utf-8')
                            return
                        if self.server.start_thread is not None and self.server.start_thread.is_alive():
                            self.send_bytes(b'{"status":"already_starting"}', 'application/json; charset=utf-8')
                            return
                        prototype = Path(__file__).parent
                        logdir = runtime / 'logs'
                        logdir.mkdir(exist_ok=True)
                        tag = uuid.uuid4().hex
                        stdout = logdir / f'dashboard-start-{tag}.stdout.log'
                        stderr = logdir / f'dashboard-start-{tag}.stderr.log'
                        def run_supervisor():
                            from launch_project import main as launch_main
                            from supervisor.engine import exclusive
                            old_argv = sys.argv
                            self.server.start_error = None
                            try:
                                with stdout.open('w', encoding='utf-8') as out, stderr.open('w', encoding='utf-8') as err:
                                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                                        sys.argv = ['launch_project.py']
                                        with exclusive(runtime, 'launcher.lock'):
                                            launch_main()
                            except BaseException as exc:
                                self.server.start_error = str(exc) or type(exc).__name__
                                with stderr.open('a', encoding='utf-8') as err:
                                    err.write(f'\nDashboard relaunch failed: {self.server.start_error}\n')
                            finally:
                                sys.argv = old_argv
                        self.server.start_thread = threading.Thread(target=run_supervisor, name='ralph-dashboard-start', daemon=True)
                        self.server.start_thread.start()
                    self.send_bytes(b'{"status":"starting","quota_check":"before_model_call"}',
                                    'application/json; charset=utf-8', 202)
                    return
                store = Store(runtime / 'state.sqlite')
                current = store.get('control', {})
                if current.get('mode') == 'stopped':
                    store.close()
                    self.send_bytes(json.dumps({'status':'already_stopped',
                                                'reason':current.get('reason')}).encode(),
                                    'application/json; charset=utf-8')
                    return
                store.set('control', {'mode': 'stopped', 'reason': 'dashboard_stop_button'})
                store.set('phase', 'Arrêt demandé depuis le tableau de bord')
                store.close()
                self.send_bytes(b'{"status":"stopped"}', 'application/json; charset=utf-8')
            except (ValueError, TypeError):
                self.send_bytes(b'{"status":"invalid_request"}', 'application/json; charset=utf-8', 400)
            except (OSError, sqlite3.Error):
                self.send_bytes(b'{"status":"error"}', 'application/json; charset=utf-8', 503)

    endpoint = runtime / 'reports' / 'dashboard.json'
    port = 0
    try:
        saved = json.loads(endpoint.read_text(encoding='utf-8'))
        if saved.get('host') == '127.0.0.1' and type(saved.get('port')) is int and 1024 <= saved['port'] <= 65535:
            port = saved['port']
    except (OSError, ValueError):
        pass
    try:
        server = Server(('127.0.0.1', port), Handler)
    except OSError:
        server = Server(('127.0.0.1', 0), Handler)
    server.timeout = 1
    port = server.server_port
    temporary = endpoint.with_suffix('.tmp')
    temporary.write_text(json.dumps({'host': '127.0.0.1', 'port': port}), encoding='utf-8')
    temporary.replace(endpoint)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True, type=Path)
    args = parser.parse_args()
    serve(args.runtime)


if __name__ == '__main__':
    main()
