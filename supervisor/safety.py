import fnmatch
import hashlib
import os
import re
import subprocess
import threading
import time
from pathlib import Path


class Blocked(RuntimeError):
    pass


def safe_relative(name):
    path = Path(name)
    if not name or path.anchor or path.is_absolute() or ".." in path.parts or ":" in name or "\\" in name:
        raise Blocked("invalid_relative_path")
    return path


def forbidden(name):
    parts = name.lower().replace("\\", "/").split("/")
    return any(part in {".git", ".local", ".venv", ".tools", ".aws", ".codex", "node_modules",
                        "__pycache__", ".bootstrap", "build", "dist"} or
               part.startswith(".env") and part != ".env.example" or
               any(fnmatch.fnmatch(part, pattern) for pattern in
                   ("*.pem", "*.key", "*.pfx", "*.db", "*.sqlite*", "*password*", "*credentials*",
                    "auth.json", "*.log", "*.pyc")) for part in parts)


def plain_file(path):
    stat = path.lstat()
    if path.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
        raise Blocked("link_or_reparse_point")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def secret_like(data):
    # Additional heuristic only; directory exclusion is the first protection.
    return bool(re.search(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}", data))


def sanitized_environment():
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT",
               "PROGRAMFILES", "PROGRAMFILES(X86)", "SYSTEMDRIVE"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull)
    return env


def terminate_tree(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        # Exact owned PID; never kill by process name or a PID read from old state.
        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        import signal
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=10)


def run_command(argv, cwd, seconds=30, interrupt=lambda: False, env=None, input_text=None,
                max_output_bytes=2_000_000):
    """Trusted argv only, no shell; outputs stay in memory and are not published."""
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    job = None
    if os.name == "nt":
        from .windows_job import Job
        job = Job()
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env or sanitized_environment(), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
    except BaseException:
        if job:
            job.close()
        raise
    deadline = time.monotonic() + seconds
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()
    output_lock = threading.Lock()
    def read(stream, index):
        while chunk := stream.read(65536):
            with output_lock:
                if len(buffers[0]) + len(buffers[1]) + len(chunk) > max_output_bytes:
                    overflow.set()
                    return
                buffers[index].extend(chunk)
    def write():
        try:
            if input_text is not None:
                process.stdin.write(input_text.encode("utf-8"))
                process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            process.stdin.close()
    readers = [threading.Thread(target=read, args=(stream, index), daemon=True)
               for index, stream in enumerate((process.stdout, process.stderr))]
    writer = threading.Thread(target=write, daemon=True)
    try:
        if job:
            job.assign(process)
        for thread in [*readers, writer]:
            thread.start()
        while process.poll() is None:
            if overflow.is_set():
                raise Blocked("command_output_limit")
            if interrupt():
                raise Blocked("manual_interrupt")
            if time.monotonic() >= deadline:
                raise Blocked("command_timeout")
            time.sleep(0.05)
        for thread in readers:
            thread.join(timeout=1)
        if overflow.is_set():
            raise Blocked("command_output_limit")
        return process.returncode, bytes(buffers[0]), bytes(buffers[1])
    finally:
        if job:
            job.close()
        if process.poll() is None:
            terminate_tree(process)
        for thread in [*readers, writer]:
            if thread.ident is not None:
                thread.join(timeout=1)
        for stream in (process.stdout, process.stderr):
            stream.close()


def git(repo, *args):
    code, out, _ = run_command(["git", "-c", "core.hooksPath=" + os.devnull, *args], repo)
    if code:
        raise Blocked("git_operation_failed")
    return out.decode("utf-8").strip()


def tree_hash(root, files):
    result = {}
    for name in files:
        path = root / safe_relative(name)
        plain_file(path)
        result[name] = digest(path.read_bytes())
    return result
