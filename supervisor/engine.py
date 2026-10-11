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
    "reasoning_effort": "medium", "chatgpt_quota_guard": False,
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


class ObjectiveVerified(Exception):
    """A verified checkpoint ends this cycle without an artificial code edit."""


@contextlib.contextmanager
def exclusive(root, filename="supervisor.lock"):
    path = root / filename
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


def initialize(root, source=None, execution_backend="direct", source_files=None):
    if execution_backend not in {"direct", "ralph"}:
        raise Blocked("invalid_execution_backend")
    root = Path(root).resolve()
    if root.exists():
        raise Blocked("runtime_already_exists")
    root.mkdir(parents=True)
    files = collect(source, source_files) if source else DEMO_FILES
    manifest = initialize_repo(root / "repository", files)
    config = dict(DEFAULTS)
    config["execution_backend"] = execution_backend
    if source:
        config.update(mode="snapshot", allowed_files=[], protected_files=list(files),
                      validation_argv=[], benchmark_argv=[])
        if source_files is not None:
            config['source_files'] = list(source_files)
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
            if self.config.get("agent_transport") == "conversation":
                from .conversation import ConversationExecutor
                from .reports import render
                transport = ConversationExecutor(self.root, self.config, self.store, self.interrupted, lambda: render(self))
            else:
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

    def phase(self, value):
        from .reports import render
        self.store.set("phase", value)
        render(self)

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
        if self.store.get("pending_promotion"):
            raise Blocked("incomplete_promotion_requires_recovery")
        source = self.store.get("source")
        if source:
            current = {name: digest(data) for name, data in collect(source, self.config.get('source_files')).items()}
            if current != self.store.get("source_manifest"):
                raise Blocked("original_project_changed_reimport_required")

    def verify(self, worktree, strict=True):
        if not self.config["validation_argv"]:
            raise Blocked("validation_not_configured")
        start = time.perf_counter()
        code, stdout, stderr = run_command(self.config["validation_argv"], worktree,
                                          self.config["validation_timeout_seconds"], self.interrupted)
        # Do not publish output that might contain secrets or untrusted file content.
        result = {"exit_code": code, "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
                  "output_sha256": digest(stdout + stderr)}
        from .feedback import validation_feedback
        self._last_validation_failure = validation_feedback(stdout, stderr) if code else ''
        if len(self.config['validation_argv']) >= 4 and Path(self.config['validation_argv'][3]).resolve() == Path(__file__).resolve().parents[1] / 'automatic_checks.py' and not code:
            try:
                result['check_level'] = json.loads(stdout.splitlines()[-1])['level']
            except (ValueError, KeyError, IndexError, TypeError):
                raise Blocked('automatic_validation_result_missing') from None
        if code and strict:
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

    def validate_candidate(self, worktree, proposal, protected, report):
        """One focused repair on the same candidate; every model call keeps quota controls."""
        self._last_validation_failure = ''
        try:
            return self.verify(worktree)
        except Blocked as error:
            if (str(error) != 'independent_validation_failed'
                    or not self.config.get('repair_validation_failures', self.config['mode'] != 'demo')
                    or not self._last_validation_failure
                    or self.interrupted()):
                raise
            attempts = next(p['attempts'] for p in self.store.proposals() if p['id'] == proposal['id'])
            if attempts >= min(2, self.config['max_attempts_per_proposal']):
                raise
            from .guidance import matches, snapshot
            if not matches(proposal, snapshot(self.store)):
                raise Blocked('instructions_changed_before_task')
            self.original_unchanged()
            changed_files(worktree, proposal['files'], self.config['protected_files'])
            if tree_hash(worktree, self.config['protected_files']) != protected:
                raise Blocked('validation_contract_modified')
            if git(worktree, 'rev-parse', 'HEAD') != proposal['base']:
                raise Blocked('executor_changed_git_history')
            repair = {**proposal, 'repair_feedback': self._last_validation_failure}
            self.store.proposal_state(proposal['id'], 'running', attempt=True)
            report['repair_attempts'] = 1
            self.store.cycle(report['id'], report)
            self.phase('Correction ciblée après échec des tests : ' + proposal['problem'])
            started = time.perf_counter()
            self.executor.implement(worktree, repair)
            report['repair_seconds'] = round(time.perf_counter() - started, 3)
            report['changed_files'] = changed_files(worktree, proposal['files'], self.config['protected_files'])
            if tree_hash(worktree, self.config['protected_files']) != protected:
                raise Blocked('validation_contract_modified')
            self.phase('Nouvelle vérification indépendante de la correction')
            return self.verify(worktree)

    def choose(self):
        from .guidance import matches, snapshot
        from .objectives import current
        guidance = snapshot(self.store)
        objective = current(self.store) if self.store.get('operator_objective') else None
        candidates = []
        base = git(self.repo, "rev-parse", "validated")
        entries = self.store.proposals()
        from .creation import task_paths_allowed
        states = {p['id']: p['status'] for p in entries}
        for entry in entries:
            proposal = entry["payload"]
            if entry["status"] not in {"queued", "deferred_instructions"} or entry["attempts"] >= self.config["max_attempts_per_proposal"]:
                continue
            if not matches(proposal, guidance):
                self.store.proposal_state(entry['id'], 'deferred_instructions')
                continue
            if objective and objective['state'] != 'completed' and proposal.get('objective_fingerprint') != objective['fingerprint']:
                continue
            dependencies = proposal.get('objective_dependencies', [])
            if any(states.get(id) != 'accepted' for id in dependencies):
                if any(states.get(id) in {'rejected', 'outside_trusted_scope', 'obsolete', 'blocked_dependency'} for id in dependencies):
                    self.store.proposal_state(entry['id'], 'blocked_dependency')
                continue
            if entry['status'] == 'deferred_instructions':
                self.store.proposal_state(entry['id'], 'queued')
            if not task_paths_allowed(proposal, self.config):
                self.store.proposal_state(entry["id"], "outside_trusted_scope")
                continue
            if proposal["base"] != base:
                if not self.config.get('persistent_backlog', False):
                    self.store.proposal_state(entry["id"], "obsolete")
                    continue
                evidence_base = proposal.get('evidence_base', proposal['base'])
                # Only carry work forward along the supervisor's own validated history.
                if git(self.repo, 'merge-base', evidence_base, base) != evidence_base:
                    self.store.proposal_state(entry['id'], 'obsolete')
                    continue
                proposal = {**proposal, 'base': base, 'evidence_base': evidence_base,
                            'recheck_required': True,
                            'changes_since_discovery': git(self.repo, 'diff', '--name-only',
                                                           evidence_base, base).splitlines()}
                self.store.update_proposal(proposal)
                # An approval on an old baseline never silently approves a new one.
                if proposal['risk'] != 'low':
                    self.store.set('approval:' + entry['id'], False)
            if proposal["risk"] != "low" and not self.store.get("approval:" + entry["id"], False):
                self.store.proposal_state(entry["id"], "awaiting_approval")
                continue
            score = (-proposal.get('objective_order', 0) if objective and objective['state'] != 'completed'
                     else proposal["impact"] / proposal["cost"])
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
                if self.config.get('project_onboarding') and not self.store.get('project_understanding'):
                    self.phase("Compréhension du projet importé")
                    if not hasattr(self.executor, 'understand_project'):
                        raise Blocked('project_understanding_transport_unavailable')
                    understanding = self.executor.understand_project(worktree, base)
                    if git(worktree, 'status', '--porcelain') or git(worktree, 'rev-parse', 'HEAD') != base or tree_hash(worktree, self.config['protected_files']) != protected:
                        raise Blocked('project_understanding_modified_source')
                    self.store.set('project_understanding', understanding)
                self.phase("Vérification du projet avant modification")
                report["before_validation"] = self.verify(worktree, strict=False) if self.config.get('allow_repair_baseline') else self.verify(worktree)
                self.store.set('baseline_checks', report['before_validation'])
                report["before_measurement"] = self.measure(worktree)
                from .objectives import complete, sync
                objective = sync(self.store)
                pursuing = objective is not None and objective['state'] != 'completed'
                proposal = self.choose()
                report['proposal_source'] = 'backlog' if proposal else 'discovery'
                if proposal is None:
                    discovery_started = time.perf_counter()
                    if pursuing:
                        report['proposal_source'] = 'objective'
                        pending = [p for p in self.store.proposals()
                                   if p['payload'].get('objective_fingerprint') == objective['fingerprint']
                                   and p['status'] in {'awaiting_approval', 'queued'}]
                        if pending:
                            raise Blocked('objective_waiting_for_approval_or_scope')
                        self.phase("Bilan des consignes et prochaines tâches")
                        if not hasattr(self.executor, 'pursue_objective'):
                            raise Blocked('objective_transport_unavailable')
                        criteria, items = self.executor.pursue_objective(worktree, self.store.history(10), base, objective)
                        from .objectives import validate_assessment
                        validate_assessment({'criteria': criteria, 'proposals': items}, objective, worktree, self.config)
                        if git(worktree, 'status', '--porcelain') or git(worktree, 'rev-parse', 'HEAD') != base or tree_hash(worktree, self.config['protected_files']) != protected:
                            raise Blocked('objective_readonly_check_modified_project')
                        from .guidance import snapshot
                        if snapshot(self.store)['fingerprint'] != objective['fingerprint']:
                            raise Blocked('instructions_changed_before_task')
                        objective.update(criteria=criteria, state='active', assessed_base=base, reason='')
                        self.store.set('operator_objective', objective)
                        if all(c['status'] == 'met' for c in criteria):
                            self.original_unchanged()
                            if self.interrupted():
                                raise Blocked('manual_interrupt')
                            self.phase("Vérification finale des consignes")
                            validation = self.verify(worktree)
                            if git(worktree, 'status', '--porcelain') or tree_hash(worktree, self.config['protected_files']) != protected or git(worktree, 'rev-parse', 'HEAD') != base:
                                raise Blocked('objective_readonly_check_modified_project')
                            self.original_unchanged()
                            complete(self.store, objective, base, validation)
                            report['objective_validation'] = validation
                            raise ObjectiveVerified()
                        if not items:
                            raise Blocked('objective_needs_evidence_or_scope')
                    else:
                        from .guidance import matches, snapshot
                        guidance = snapshot(self.store)
                        if self.config['mode'] != 'demo' and any(p['status'] == 'awaiting_approval' and matches(p['payload'], guidance) for p in self.store.proposals()):
                            raise Blocked('work_waiting_for_approval')
                        self.phase("Recherche ciblée à partir de la mémoire du projet")
                        items = self.executor.discover(worktree, self.store.history(10), base)
                    for index, item in enumerate(items):
                        if pursuing:
                            item['objective_dependencies'] = [p['id'] for p in items[:index]]
                        self.store.add(item)
                    if pursuing:
                        objective['task_ids'] = list(dict.fromkeys(objective['task_ids'] + [p['id'] for p in items]))
                        self.store.set('operator_objective', objective)
                    report['discovery_seconds'] = round(time.perf_counter() - discovery_started, 3)
                    proposal = self.choose()
                if proposal is None:
                    raise Blocked('objective_needs_evidence_or_scope' if pursuing else "no_new_validated_hypothesis")
                report["proposal"] = proposal
                from .creation import inspect_new_paths
                new_files = inspect_new_paths(proposal, self.config, worktree, self.store.get('source'))
                report['new_files'] = new_files
                if pursuing:
                    objective.update(state='active', reason='')
                    self.store.set('operator_objective', objective)
                self.store.proposal_state(proposal["id"], "running", attempt=True)
                self.store.cycle(id, report)
                self.phase("Codex travaille : " + proposal["problem"])
                implementation_started = time.perf_counter()
                self.executor.implement(worktree, proposal)
                report['implementation_seconds'] = round(time.perf_counter() - implementation_started, 3)
                report["changed_files"] = changed_files(worktree, proposal["files"], self.config["protected_files"])
                if tree_hash(worktree, self.config["protected_files"]) != protected:
                    raise Blocked("validation_contract_modified")
                self.phase("Tests indépendants et contrôle des modifications")
                report["after_validation"] = self.validate_candidate(worktree, proposal, protected, report)
                report["after_measurement"] = self.measure(worktree)
                if tree_hash(worktree, self.config["protected_files"]) != protected:
                    raise Blocked("validation_contract_modified")
                before, after = report["before_measurement"], report["after_measurement"]
                if self.config.get("acceptance_mode", "benchmark") == "application_checks":
                    level = report['after_validation'].get('check_level', self.config.get('validation_level'))
                    from .creation import EXTENSIONS
                    code_extensions = EXTENSIONS - {'.md', '.rst', '.css', '.scss', '.html', '.svg'}
                    if level == 'structural' and any(Path(name).suffix.lower() in code_extensions for name in report['changed_files']):
                        raise Blocked('behavioral_tests_required')
                    report["acceptance"] = ('independent_structural_checks' if level == 'structural'
                                            else "independent_application_tests_and_lint")
                else:
                    if not before or not after or before["metric"] != after["metric"]:
                        raise Blocked("measurable_acceptance_not_configured")
                    improvement = (1 - after["median"] / before["median"]) * 100
                    report["improvement_percent"] = improvement
                    if improvement < self.config["min_improvement_percent"]:
                        raise Blocked("insufficient_measured_improvement")
                self.original_unchanged()
                from .guidance import matches, snapshot
                if not matches(proposal, snapshot(self.store)):
                    raise Blocked('instructions_changed_before_task')
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
                if new_files:
                    self.config['allowed_files'] = list(dict.fromkeys(self.config['allowed_files'] + new_files))
                if self.config.get("auto_promote_source", False):
                    from .promotion import promote
                    try:
                        self.phase("Sauvegarde et application au projet")
                        report["promotion"] = promote(self, worktree, report["changed_files"], id)
                    except BaseException:
                        self.store.set("control", {"mode": "stopped", "reason": "source_promotion_failed"})
                        raise
                self.store.proposal_state(proposal["id"], "accepted")
                if new_files:
                    from .creation import is_test_path
                    new_tests = [name for name in new_files if is_test_path(name)]
                    self.config['allowed_files'] = [name for name in self.config['allowed_files'] if name not in new_tests]
                    self.config['protected_files'] = list(dict.fromkeys(self.config['protected_files'] + new_tests))
                    if self.config.get('source_files') is not None:
                        self.config['source_files'] = list(dict.fromkeys(self.config['source_files'] + new_files))
                    temporary = self.root / 'config.tmp'
                    temporary.write_text(json.dumps(self.config, indent=2), encoding='utf-8')
                    temporary.replace(self.root / 'config.json')
                self.store.set("no_progress", 0)
                self.store.set("blocked_windows", 0)
            except ObjectiveVerified:
                report.update(status='objective_verified', reason='operator_criteria_verified')
                self.store.set('no_progress', 0)
            except (Blocked, RuntimeError, OSError) as error:
                report.update(status="rejected", reason=str(error))
                if proposal:
                    self.store.proposal_state(proposal["id"], "rejected")
                from .objectives import current
                objective = current(self.store)
                if objective and objective['state'] != 'completed':
                    objective.update(state='blocked', reason=str(error))
                    self.store.set('operator_objective', objective)
                if str(error) in {'objective_waiting_for_approval_or_scope', 'objective_needs_evidence_or_scope', 'objective_transport_unavailable'}:
                    self.store.set('control', {'mode': 'stopped', 'reason': str(error)})
                if self.config.get('stop_when_no_actionable_work', self.config['mode'] != 'demo') and str(error) in {'no_new_validated_hypothesis', 'work_waiting_for_approval'}:
                    self.store.set('control', {'mode': 'stopped', 'reason': str(error)})
                if self.config.get("auto_promote_source") and str(error) in {
                    "source_changed_before_promotion", "source_changed_during_promotion",
                    "promotion_rollback_conflict", "incomplete_promotion_requires_recovery",
                    "original_project_changed_reimport_required"}:
                    self.store.set("control", {"mode": "stopped", "reason": str(error)})
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
            self.phase("Cycle terminé : " + report["status"])
            if hasattr(self.executor, "record_result"):
                self.executor.record_result(report)
            return report

    def run(self, max_cycles=None):
        cycles = 0
        while self.store.get("control", {}).get("mode") != "stopped":
            result = self.cycle()
            from .reports import render
            render(self)
            if self.store.get("control", {}).get("mode") == "stopped":
                return
            if result["status"] != "idle":
                cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                return
            delay = self.next_delay(result)
            self.store.set('next_cycle_at', time.time() + delay)
            if self.store.get('control', {}).get('mode') == 'running':
                self.phase(f"Prochain cycle dans {delay:g} s · pistes conservées")
            deadline = time.monotonic() + delay
            waiting_mode = self.store.get("control", {}).get("mode")
            while time.monotonic() < deadline:
                mode = self.store.get("control", {}).get("mode")
                if mode == "stopped":
                    return
                if mode == "running" and waiting_mode != "running":
                    break
                time.sleep(min(1, max(0, deadline - time.monotonic())))

    def next_delay(self, result):
        # Keep cooldowns and failure pacing; avoid two idle minutes after success
        # or when another actionable hypothesis is already waiting.
        interval = self.config['interval_seconds']
        if self.store.get('control', {}).get('mode') != 'running':
            return interval
        from .creation import task_paths_allowed
        backlog = any(p['status'] == 'queued' and
                      p['attempts'] < self.config['max_attempts_per_proposal'] and
                      task_paths_allowed(p['payload'], self.config) and
                      (p['payload']['risk'] == 'low' or self.store.get('approval:' + p['id'], False))
                      for p in self.store.proposals())
        if backlog or result['status'] in {'accepted_in_snapshot', 'objective_verified'}:
            return max(1, min(interval, self.config.get('active_interval_seconds', interval)))
        return interval
