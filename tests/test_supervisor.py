import json
import os
import unittest
import uuid
from pathlib import Path

from supervisor.engine import Supervisor, exclusive, initialize
from supervisor.executor import validate_proposal
from supervisor.reports import render
from supervisor.safety import Blocked, git, run_command
from supervisor.workspace import changed_files, collect

ROOT = Path(__file__).resolve().parents[1]


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / "test-scratch"
        scratch.mkdir(exist_ok=True)
        # Inherit workspace permissions. tempfile's private ACL is incompatible
        # with this host's restricted token; keep test artifacts for inspection.
        self.directory = (scratch / uuid.uuid4().hex).resolve()
        self.directory.mkdir()
        self.assertTrue(self.directory.is_relative_to(scratch.resolve()))
        self.root = self.directory / "runtime"
        initialize(self.root)
        self.supervisor = Supervisor(self.root)
        self.supervisor.control("running")

    def tearDown(self):
        self.supervisor.close()

    def candidate(self, **overrides):
        base = git(self.supervisor.repo, "rev-parse", "validated")
        return validate_proposal({"problem": "problem", "strategy": "alternative", "files": ["calc.py"],
                                  "impact": 5, "cost": 2, "risk": "low", "evidence": "fixture",
                                  "success_metric": "20% faster", **overrides}, base)

    def test_real_rejection_then_alternative_acceptance_and_backups(self):
        base = git(self.supervisor.repo, "rev-parse", "validated")
        rejected = self.supervisor.cycle()
        self.assertEqual(rejected["reason"], "independent_validation_failed")
        self.assertEqual(git(self.supervisor.repo, "rev-parse", "validated"), base)
        self.assertTrue(Path(rejected["worktree"]).exists())
        accepted = self.supervisor.cycle()
        self.assertEqual(accepted["status"], "accepted_in_snapshot")
        self.assertGreaterEqual(accepted["improvement_percent"], 20)
        self.assertNotEqual(accepted["proposal"]["strategy"], rejected["proposal"]["strategy"])
        self.assertEqual(git(self.supervisor.repo, "rev-parse", "validated"), accepted["commit"])
        for bundle in (self.root / "backups").glob("*.bundle"):
            git(self.supervisor.repo, "bundle", "verify", str(bundle))
        self.assertEqual(len(list((self.root / "backups").glob("*.bundle"))), 3)

    def test_completed_roadmap_does_not_stop_discovery_and_auto_pauses(self):
        self.supervisor.cycle()
        self.supervisor.cycle()
        for _ in range(3):
            result = self.supervisor.cycle()
            self.assertEqual(result["reason"], "no_new_validated_hypothesis")
        self.assertEqual(self.supervisor.store.get("control")["mode"], "auto_paused")
        self.assertEqual(self.supervisor.cycle()["status"], "idle")
        self.assertEqual(len(self.supervisor.store.proposals()), 2)

    def test_controls_persist_across_process_instances(self):
        for mode in ("paused", "stopped", "running"):
            self.supervisor.control(mode)
            other = Supervisor(self.root)
            try:
                self.assertEqual(other.store.get("control")["mode"], mode)
                if mode != "running":
                    self.assertEqual(other.cycle()["status"], "idle")
            finally:
                other.close()

    def test_repeated_blockage_stops_retrying_after_limited_cooldowns(self):
        self.supervisor.config["max_no_progress_cycles"] = 1
        self.supervisor.config["max_blocked_windows"] = 2
        self.supervisor.config["cooldown_seconds"] = 0
        self.supervisor.config["allowed_files"] = []
        self.supervisor.cycle()
        self.assertEqual(self.supervisor.store.get("control")["mode"], "auto_paused")
        self.supervisor.cycle()
        self.assertEqual(self.supervisor.store.get("control")["reason"], "repeated_blockage_requires_new_evidence")
        count = len(self.supervisor.store.history())
        self.assertEqual(self.supervisor.cycle()["status"], "idle")
        self.assertEqual(len(self.supervisor.store.history()), count)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object required")
    def test_owned_descendant_is_terminated_when_parent_command_finishes(self):
        import ctypes
        import sys
        from ctypes import wintypes
        script = ("import subprocess, sys, json; "
                  "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], "
                  "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
                  "print(json.dumps({'pid': child.pid}))")
        code, stdout, _ = run_command([sys.executable, "-c", script], self.root)
        self.assertEqual(code, 0)
        pid = json.loads(stdout)["pid"]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if handle:
            try:
                self.assertEqual(kernel.WaitForSingleObject(handle, 3000), 0)
            finally:
                kernel.CloseHandle(handle)

    def test_manual_stop_interrupts_active_measurement_without_promoting(self):
        import sys
        import threading
        base = git(self.supervisor.repo, "rev-parse", "validated")
        self.supervisor.config["benchmark_argv"] = [sys.executable, "-c", "import time; time.sleep(10)"]
        def stop():
            other = Supervisor(self.root)
            other.control("stopped")
            other.close()
        timer = threading.Timer(0.3, stop)
        timer.start()
        try:
            result = self.supervisor.cycle()
        finally:
            timer.join()
        self.assertEqual(result["reason"], "manual_interrupt")
        self.assertEqual(git(self.supervisor.repo, "rev-parse", "validated"), base)
        self.assertEqual(self.supervisor.store.get("control")["mode"], "stopped")

    def test_risky_proposal_requires_explicit_specific_approval(self):
        item = self.candidate(risk="high")
        self.supervisor.store.add(item)
        self.assertIsNone(self.supervisor.choose())
        self.assertEqual(self.supervisor.store.proposals()[0]["status"], "awaiting_approval")
        self.supervisor.approval(item["id"])
        self.assertEqual(self.supervisor.choose()["id"], item["id"])
        with self.assertRaises(Blocked):
            self.supervisor.approval("unknown")

    def test_proposals_deduplicate_and_attempt_limit_is_enforced(self):
        item = self.candidate()
        self.supervisor.store.add(item)
        self.supervisor.store.add(item)
        self.assertEqual(len(self.supervisor.store.proposals()), 1)
        for _ in range(2):
            self.supervisor.store.proposal_state(item["id"], "queued", attempt=True)
        self.assertIsNone(self.supervisor.choose())

    def test_budget_reservation_counts_unknown_usage(self):
        config = {"reserve_tokens_per_call": 100, "max_ai_calls_per_day": 2, "max_tokens_per_day": 200}
        self.supervisor.store.reserve("a", "today", config)
        self.supervisor.store.reserve("b", "today", config)
        with self.assertRaisesRegex(RuntimeError, "daily_budget"):
            self.supervisor.store.reserve("c", "today", config)
        self.supervisor.store.reserve("c", "tomorrow", config)
        self.supervisor.store.usage("c", 25)
        row = self.supervisor.store.db.execute("SELECT tokens FROM calls WHERE id='c'").fetchone()
        self.assertEqual(row[0], 25)

    def test_zero_budget_rejects_before_first_call(self):
        with self.assertRaisesRegex(RuntimeError, "daily_budget"):
            self.supervisor.store.reserve("a", "today", self.supervisor.config)
        self.assertEqual(self.supervisor.store.db.execute("SELECT count(*) FROM calls").fetchone()[0], 0)

    def test_disk_limit_pauses_without_deleting_any_data(self):
        self.supervisor.config["max_disk_mb"] = 0
        bundle = self.root / "backups/baseline.bundle"
        before = bundle.read_bytes()
        self.assertFalse(self.supervisor.ready())
        self.assertEqual(self.supervisor.store.get("control")["reason"], "disk_budget_exhausted")
        self.assertEqual(bundle.read_bytes(), before)

    def test_crash_record_is_interrupted_and_requires_review(self):
        self.supervisor.store.cycle("crashed", {"id": "crashed", "status": "running"})
        self.assertEqual(self.supervisor.cycle()["status"], "idle")
        self.assertEqual(self.supervisor.store.get("control")["reason"], "crash_review_required")
        self.assertEqual(self.supervisor.store.history()[0]["status"], "interrupted")

    def test_exclusive_lock_rejects_second_supervisor(self):
        with exclusive(self.root):
            with self.assertRaisesRegex(Blocked, "already_running"):
                with exclusive(self.root):
                    self.fail("second supervisor entered")

    def test_snapshot_excludes_secrets_and_preserves_original(self):
        source = self.directory / "source"
        source.mkdir()
        (source / "README.md").write_text("project")
        (source / "src").mkdir()
        (source / "src/module.py").write_text("x = 1")
        (source / ".env").write_text("DO_NOT_COPY=secret")
        (source / ".local").mkdir()
        (source / ".local/database.db").write_bytes(b"private")
        files = collect(source)
        self.assertEqual(set(files), {"README.md", "src/module.py"})
        copied = self.directory / "snapshot"
        initialize(copied, source)
        supervisor = Supervisor(copied)
        try:
            supervisor.original_unchanged()
            (source / "src/module.py").write_text("x = 2")
            with self.assertRaisesRegex(Blocked, "original_project_changed"):
                supervisor.original_unchanged()
            self.assertEqual((source / ".env").read_text(), "DO_NOT_COPY=secret")
            supervisor.control("running")
            self.assertEqual(supervisor.cycle()["status"], "idle")
            self.assertEqual(supervisor.store.get("control")["reason"], "live_execution_not_authorized")
        finally:
            supervisor.close()

    def test_secret_like_snapshot_content_is_refused(self):
        source = self.directory / "source"
        source.mkdir()
        (source / "README.md").write_text("-----BEGIN PRIVATE KEY-----")
        with self.assertRaisesRegex(Blocked, "content_requires_review"):
            collect(source)

    def test_changed_scope_test_tampering_and_deletion_are_refused(self):
        repo = self.supervisor.repo
        (repo / "contract.py").write_text("print('fake pass')")
        with self.assertRaisesRegex(Blocked, "scope"):
            changed_files(repo, ["calc.py"], ["contract.py"])
        git(repo, "restore", "contract.py")
        (repo / "calc.py").unlink()  # Disposable test fixture only.
        with self.assertRaisesRegex(Blocked, "deletion_requires_review"):
            changed_files(repo, ["calc.py"], ["contract.py"])

    def test_protected_paths_and_traversal_in_proposals_are_refused(self):
        for file in ("../README.md", "/absolute", "C:/absolute", ".env", ".local/database.db"):
            with self.subTest(file=file), self.assertRaises(Blocked):
                self.candidate(files=[file])

    def test_timeout_and_manual_interrupt(self):
        import sys
        with self.assertRaisesRegex(Blocked, "command_timeout"):
            run_command([sys.executable, "-c", "import time; time.sleep(10)"], self.root, seconds=0.1)
        with self.assertRaisesRegex(Blocked, "manual_interrupt"):
            run_command([sys.executable, "-c", "import time; time.sleep(10)"], self.root, interrupt=lambda: True)

    def test_output_is_bounded_even_if_child_exits_immediately(self):
        import sys
        with self.assertRaisesRegex(Blocked, "command_output_limit"):
            run_command([sys.executable, "-c", "print('x' * 100000)"], self.root, max_output_bytes=1000)

    def test_report_escapes_untrusted_text_and_contains_no_active_scripts(self):
        self.supervisor.store.cycle("xss", {"id": "xss", "status": "rejected", "reason": "<script>alert(1)</script>"}, "rejected")
        page = render(self.supervisor).read_text(encoding="utf-8")
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn('name="viewport"', page)


if __name__ == "__main__":
    unittest.main()
