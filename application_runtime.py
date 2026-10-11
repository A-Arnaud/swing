"""Run the real Token Radar source; reload only fully promoted source revisions."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid
import urllib.request

from supervisor.engine import exclusive
from supervisor.store import Store

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
RUNTIME = HERE / 'runtime-chatgpt-plus'
STATE = PROJECT / '.local/application-state.json'
RELOAD = PROJECT / '.local/application-reload'
LOGS = PROJECT / '.local/logs'
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def publish(**values):
    values.update(updated_at=time.time(), project=str(PROJECT), url='http://127.0.0.1:8000/',
                  manager_pid=os.getpid(), postgres_port=15432, redis_port=16379)
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(values, indent=2), encoding='utf-8')
    os.replace(temporary, STATE)


def listening(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


def spawn(name, argv, env, cwd=PROJECT):
    stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    with (LOGS / f'{name}.{stamp}.log').open('ab') as out, (LOGS / f'{name}.{stamp}.err.log').open('ab') as err:
        return subprocess.Popen(argv, cwd=cwd, env=env, stdout=out, stderr=err, creationflags=HIDDEN)


def environment():
    # Values are never printed or written to the public application state.
    from token_radar.config import Settings
    env = dict(os.environ)
    env.update(RADAR_MODE='live', ALERTS_ENABLED='false', PYTHONUNBUFFERED='1',
               DATABASE_URL='postgresql://radar:' + (PROJECT / '.local/db-password').read_text().strip()
               + '@127.0.0.1:15432/radar', REDIS_URL='redis://127.0.0.1:16379/0')
    cfg = Settings(radar_mode='live', alerts_enabled=False)
    ready = bool(cfg.telegram_bot_token.get_secret_value() and cfg.telegram_chat_id)
    env['ALERTS_ENABLED'] = 'true' if ready else 'false'
    # Pin the evaluated settings so child processes inherit the same configuration.
    env['TELEGRAM_BOT_TOKEN'] = cfg.telegram_bot_token.get_secret_value()
    env['TELEGRAM_CHAT_ID'] = cfg.telegram_chat_id
    key = hashlib.sha256((env['TELEGRAM_BOT_TOKEN'] + '\0' + env['TELEGRAM_CHAT_ID']).encode()).hexdigest()
    return env, ready, key


def validated_revision():
    store = Store(RUNTIME / 'state.sqlite', readonly=True)
    try:
        if store.get('pending_promotion') or Path(store.get('source', '')).resolve() != PROJECT:
            return None
        manifest = store.get('source_manifest', {})
        source = {k: v for k, v in manifest.items() if k.startswith('src/')}
        if not source:
            return None
        for name, expected in source.items():
            path = PROJECT / name
            if not path.resolve().is_relative_to(PROJECT) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                return None
        return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    finally:
        store.close()


def stop(children):
    for child in children:
        if child.poll() is None:
            if os.name == 'nt':
                # The venv python.exe is a redirector with a child interpreter.
                # Stop our complete owned tree, not just that redirector.
                subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               check=True, creationflags=HIDDEN)
            else:
                child.terminate()
    for child in children:
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def main():
    os.chdir(PROJECT)
    LOGS.mkdir(exist_ok=True)
    children = []
    with exclusive(RUNTIME, 'application.lock'):
        try:
            if listening(8000):
                raise RuntimeError('api_port_already_in_use')
            env, telegram, credentials = environment()
            pg = PROJECT / '.tools/postgres/pgsql/bin/pg_ctl.exe'
            if not listening(15432):
                subprocess.run([str(pg), '-D', str(PROJECT / '.local/postgres'), '-l', str(LOGS / 'postgres.log'),
                                '-o', '-p 15432 -h 127.0.0.1', '-w', 'start'], check=True, creationflags=HIDDEN)
            if not listening(16379):
                spawn('redis', [str(PROJECT / '.tools/redis/Redis-8.10.2-Windows-x64-msys2/redis-server.exe'),
                               '--bind', '127.0.0.1', '--port', '16379', '--appendonly', 'yes', '--dir', '.'],
                      env, PROJECT / '.local/redis')
            subprocess.run([sys.executable, '-m', 'token_radar.database.migrate'], cwd=PROJECT, env=env,
                           check=True, creationflags=HIDDEN)
            revision = None
            if not validated_revision():
                raise RuntimeError('source_not_fully_validated')
            while True:
                candidate = validated_revision()
                new_env, new_telegram, new_credentials = environment()
                if candidate and (candidate != revision or new_credentials != credentials or not children or RELOAD.exists()):
                    RELOAD.unlink(missing_ok=True)
                    publish(status='reloading', telegram_enabled=new_telegram)
                    stop(children)
                    env, telegram, credentials = new_env, new_telegram, new_credentials
                    children = [spawn('worker', [sys.executable, '-m', 'token_radar.worker'], env),
                                spawn('api', [sys.executable, '-m', 'uvicorn', 'token_radar.api:app',
                                              '--host', '127.0.0.1', '--port', '8000'], env)]
                    revision = candidate
                    ready = False
                    for _ in range(40):
                        if any(child.poll() is not None for child in children):
                            raise RuntimeError('application_process_exited')
                        try:
                            with urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2) as response:
                                ready = response.status == 200
                            if ready:
                                break
                        except OSError:
                            pass
                        time.sleep(1)
                    if not ready:
                        raise RuntimeError('application_readiness_failed')
                    publish(status='running', revision=revision, telegram_enabled=telegram,
                            telegram_status='configured' if telegram else 'credentials_missing',
                            pids=[c.pid for c in children], reloaded_at=time.time())
                if children and any(child.poll() is not None for child in children):
                    raise RuntimeError('application_process_exited')
                time.sleep(3)
        except BaseException as exc:
            stop(children)
            # Exception type and known fixed error text only; never serialize credentials.
            reason = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            publish(status='stopped', error=reason)
            raise


if __name__ == '__main__':
    main()
