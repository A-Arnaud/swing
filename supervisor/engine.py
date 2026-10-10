import contextlib
import json
import math
import os
import statistics
import sys
import time
import uuid
from pathlib import Path

from .executor import CodexExecutor, DemoExecutor
from .safety import Blocked, digest, git, run_command, tree_hash
from .store import Store
from .workspace import backup, changed_files, collect, initialize_repo

DEFAULTS = {
    "mode": "demo", "execution_backend": "direct", "live_enabled": False, "isolated_execution_confirmed": False,
    "max_ai_calls_per_day": 0, "max_tokens_per_day": 0, "reserve_tokens_per_call": 100_000,
    "max_call_seconds": 600, "max_attempts_per_proposal": 2, "max_no_progress_cycles": 3,
    "max_blocked_windows": 3,
    "cooldown_seconds": 3600, "interval_seconds": 900, "max_disk_mb": 500,
    "min_improvement_percent": 20,
    "model": "gpt-6.1-sol",
    "codex_executable": "", "validation_argv": [sys.executable, "contract.py"],
    "benchmark_argv": [sys.executable, "bench.py"], "benchmark_repetitions": 3,
    "validation_timeout_seconds": 120, "allowed_files": ["calc.py"],
    "protected_files": ["contract.py", "bench.py"],
}

DEMO_FILES = {
    "README.md": b"# Synthetic supervisor fixture\nOptimize exact summation. No market data or real trading.\n",
    "calc.py": b"def total(values):\n    result = 0\n    for i in range(len(values)):\n        result += sum(values[:i + 1]) - sum(values[:i])\n    return result\n",
    "contract.py": b"from calc import total\nfor values in ([], [1], [1, 2, 3], [-3, 2, 0], [0.5, 1.5]):\n    assert total(values) == sum(values), 'summation contract'\nprint('contract passed')\n",
    "bench.py": b"import json, statistics, time\nfrom calc import total\nvalues = list(range(4000))\nsamples = []\nfor _ in range(3):\n    start = time.perf_counter()\n    assert total(values) == sum(values)\n    samples.append((time.perf_counter() - start) * 1000)\nprint(json.dumps({'metric': 'summation_ms', 'value': statistics.median(samples)}))\n",
}


@contextlib.contextmanager
def exclusive(root):
    path = root / "supervisor.lock"
    handle = path.open("a+b")
    try:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise Blocked("supervisor_already_running") from None
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise Blocked("supervisor_already_running") from None
        yield
    finally:
        handle.close()  # OS releases the lock, including on a crash; no stale PID killing.


def initialize(root, source=None, execution_backend="direct"):
    if execution_backend not in {"direct", "ralph"}:
        raise Blocked("invalid_execution_backend")
    root = Path(root).resolve()
    if root.exists():
        raise Blocked("runtime_already_exists")
    root.mkdir(parents=True)
    files = collect(source) if source else DEMO_FILES
    manifest = initialize_repo(root / "repository", files)
    config = dict(DEFAULTS)
    config["execution_backend"] = execution_backend
    if source:
        config.update(mode="snapshot", allowed_files=[], protected_files=list(files),
                      validation_argv=[], benchmark_argv=[])
    (root / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    store = Store(root / "state.sqlite")
    store.set("control", {"mode": "paused", "reason": "initialized_not_started"})
    store.set("source", str(Path(source).resolve()) if source else None)
    store.set("source_manifest", manifest)
    store.set("no_progress", 0)
    store.close()
    (root / "worktrees").mkdir()
    (root / "backups").mkdir()
    backup(root / "repository", root / "backups/baseline.bundle")
    return root


class Supervisor:
    def __init__(self, root, executor=None):
        self.root = Path(root).resolve()
        self.config = json.loads((self.root / "config.json").read_text())
        self.store = Store(self.root / "state.sqlite")
        self.repo = self.root / "repository"
        backend = self.config.get("execution_backend", "direct")
        if backend not in {"direct", "ralph"}:
            self.store.close()
            raise Blocked("invalid_execution_backend")
        if executor is not None:
            self.executor = executor
        elif backend == "ralph":
            from .ralph import RalphDemoTransport, RalphExecutor
            transport = (RalphDemoTransport() if self.config["mode"] == "demo" else
                         CodexExecutor(self.config, self.store, self.interrupted))
            self.executor = RalphExecutor(self.root, transport, self.store, self.interrupted)
        else:
            self.executor = (DemoExecutor() if self.config["mode"] == "demo" else
                             CodexExecutor(self.config, self.store, self.interrupted))

    def close(self):
        self.store.close()

    def control(self, mode):
        if mode not in {"running", "paused", "stopped"}:
            raise ValueError("invalid_control")
        self.store.set("control", {"mode": mode, "reason": "manual"})

    def interrupted(self):
        return self.store.get("control", {}).get("mode") != "running"

    def ready(self):
        control = self.store.get("control")
        if control.get("mode") == "auto_paused" and time.time() >= control.get("until", math.inf):
            self.store.set("control", {"mode": "running", "reason": "cooldown_elapsed"})
            self.store.set("no_progress", 0)
        if self.interrupted():
            return False
        if self.config["mode"] != "demo" and not self.config["live_enabled"]:
            self.store.set("control", {"mode": "paused", "reason": "live_execution_not_authorized"})
            return False
        size = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
        if size > self.config["max_disk_mb"] * 1024 * 1024:
            self.store.set("control", {"mode": "paused", "reason": "disk_budget_exhausted"})
            return False
        return True

    def original_unchanged(self):
        source = self.store.get("source")
        if source:
            current = {name: digest(data) for name, data in collect(source).items()}
            if current != self.store.get("source_manifest"):
                raise Blocked("original_project_changed_reimport_required")

    def verify(self, worktree):
        if not self.config["validation_argv"]:
            raise Blocked("validation_not_configured")
        start = time.perf_counter()
        code, stdout, stderr = run_command(self.config["validation_argv"], worktree,
                                          self.config["validation_timeout_seconds"], self.interrupted)
        # Do not publish output that might contain secrets or untrusted file content.
        result = {"exit_code": code, "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
                  "output_sha256": digest(stdout + stderr)}
        if code:
            raise Blocked("independent_validation_failed")
        return result

    def measure(self, worktree):
        if not self.config["benchmark_argv"]:
            return None
        samples, metric = [], None
        for _ in range(self.config["benchmark_repetitions"]):
            code, stdout, _ = run_command(self.config["benchmark_argv"], worktree,
                                         self.config["validation_timeout_seconds"], self.interrupted)
            if code:
                raise Blocked("benchmark_failed")
            try:
                data = json.loads(stdout)
                value = data["value"]
                if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
                    raise ValueError()
                if metric is not None and metric != data["metric"]:
                    raise ValueError()
                metric = data["metric"]
            except (ValueError, KeyError, TypeError):
                raise Blocked("invalid_benchmark_result") from None
            samples.append(value)
        return {"metric": metric, "samples": samples, "median": statistics.median(samples)}

    def choose(self):
        candidates = []
        for entry in self.store.proposals():
            proposal = entry["payload"]
            if entry["status"] != "queued" or entry["attempts"] >= self.config["max_attempts_per_proposal"]:
                continue
            if proposal["base"] != git(self.repo, "rev-parse", "validated"):
                self.store.proposal_state(entry["id"], "obsolete")
                continue
            if proposal["risk"] != "low" and not self.store.get("approval:" + entry["id"], False):
                self.store.proposal_state(entry["id"], "awaiting_approval")
                continue
            if any(name not in self.config["allowed_files"] for name in proposal["files"]):
                self.store.proposal_state(entry["id"], "outside_trusted_scope")
                continue
            score = proposal["impact"] / proposal["cost"]
            candidates.append((score, entry["id"], proposal))
        return max(candidates, default=(None, None, None))[2]

    def approval(self, id):
        item = next((p for p in self.store.proposals() if p["id"] == id), None)
        if item is None or item["status"] != "awaiting_approval":
            raise Blocked("no_pending_approval")
        self.store.set("approval:" + id, True)
        self.store.proposal_state(id, "queued")

    def cycle(self):
        with exclusive(self.root):
            # Prior running records are interrupted, never accepted on restart.
            for (old_id, raw) in self.store.db.execute("SELECT id,report FROM cycles WHERE status='running'").fetchall():
                report = json.loads(raw)
                report.update(status="interrupted", reason="supervisor_restarted")
                self.store.cycle(old_id, report, "interrupted")
                if hasattr(self.executor, "record_result"):
                    self.executor.record_result(report)
                self.store.set("control", {"mode": "paused", "reason": "crash_review_required"})
            if not self.ready():
                return {"status": "idle", "control": self.store.get("control")}
            id = uuid.uuid4().hex
            report = {"id": id, "started_at": time.time(), "mode": self.config["mode"], "status": "running",
                      "execution_backend": self.config.get("execution_backend", "direct")}
            self.store.cycle(id, report)
            proposal = None
            try:
                self.original_unchanged()
                base = git(self.repo, "rev-parse", "validated")
                report["base"] = base
                worktree = self.root / "worktrees" / id
                branch = "cycle/" + id
                git(self.repo, "worktree", "add", "-b", branch, str(worktree), base)
                report.update(branch=branch, worktree=str(worktree))
                protected = tree_hash(worktree, self.config["protected_files"])
                report["before_validation"] = self.verify(worktree)
                report["before_measurement"] = self.measure(worktree)
                proposal = self.choose()
                if proposal is None:
                    for item in self.executor.discover(worktree, self.store.history(10), base):
                        self.store.add(item)
                    proposal = self.choose()
                if proposal is None:
                    raise Blocked("no_new_validated_hypothesis")
                report["proposal"] = proposal
                self.store.proposal_state(proposal["id"], "running", attempt=True)
                self.store.cycle(id, report)
                self.executor.implement(worktree, proposal)
                report["changed_files"] = changed_files(worktree, proposal["files"], self.config["protected_files"])
                if tree_hash(worktree, self.config["protected_files"]) != protected:
                    raise Blocked("validation_contract_modified")
                report["after_validation"] = self.verify(worktree)
                report["after_measurement"] = self.measure(worktree)
                if tree_hash(worktree, self.config["protected_files"]) != protected:
                    raise Blocked("validation_contract_modified")
                before, after = report["before_measurement"], report["after_measurement"]
                if not before or not after or before["metric"] != after["metric"]:
                    raise Blocked("measurable_acceptance_not_configured")
                improvement = (1 - after["median"] / before["median"]) * 100
                report["improvement_percent"] = improvement
                if improvement < self.config["min_improvement_percent"]:
                    raise Blocked("insufficient_measured_improvement")
                self.original_unchanged()
                if self.interrupted():
                    raise Blocked("manual_interrupt")
                # Independent contract/scope checked again after executing all candidate code.
                changed_files(worktree, proposal["files"], self.config["protected_files"])
                if git(worktree, "rev-parse", "HEAD") != base:
                    raise Blocked("executor_changed_git_history")
                git(worktree, "add", "--", *report["changed_files"])
                git(worktree, "commit", "-m", "Validated isolated improvement " + id)
                commit = git(worktree, "rev-parse", "HEAD")
                backup(self.repo, self.root / "backups" / (id + "-before.bundle"))
                if git(self.repo, "rev-parse", "validated") != base or git(self.repo, "status", "--porcelain"):
                    raise Blocked("baseline_changed_during_cycle")
                git(self.repo, "merge", "--ff-only", branch)
                backup(self.repo, self.root / "backups" / (id + "-after.bundle"))
                report.update(status="accepted_in_snapshot", commit=commit)
                self.store.proposal_state(proposal["id"], "accepted")
                self.store.set("no_progress", 0)
                self.store.set("blocked_windows", 0)
            except (Blocked, RuntimeError) as error:
                report.update(status="rejected", reason=str(error))
                if proposal:
                    self.store.proposal_state(proposal["id"], "rejected")
                count = self.store.get("no_progress", 0) + 1
                self.store.set("no_progress", count)
                if count >= self.config["max_no_progress_cycles"] and not self.interrupted():
                    windows = self.store.get("blocked_windows", 0) + 1
                    self.store.set("blocked_windows", windows)
                    if windows >= self.config.get("max_blocked_windows", 3):
                        self.store.set("control", {"mode": "paused", "reason": "repeated_blockage_requires_new_evidence"})
                    else:
                        self.store.set("control", {"mode": "auto_paused", "reason": "no_progress",
                                                   "until": time.time() + self.config["cooldown_seconds"]})
            report["elapsed_seconds"] = round(time.time() - report["started_at"], 3)
            self.store.cycle(id, report, report["status"])
            if hasattr(self.executor, "record_result"):
                self.executor.record_result(report)
            return report

    def run(self, max_cycles=None):
        cycles = 0
        while self.store.get("control", {}).get("mode") != "stopped":
            result = self.cycle()
            from .reports import render
            render(self)
            if result["status"] != "idle":
                cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                return
            deadline = time.monotonic() + self.config["interval_seconds"]
            waiting_mode = self.store.get("control", {}).get("mode")
            while time.monotonic() < deadline:
                mode = self.store.get("control", {}).get("mode")
                if mode == "stopped":
                    return
                if mode == "running" and waiting_mode != "running":
                    break
                time.sleep(min(1, max(0, deadline - time.monotonic())))
