import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .safety import Blocked, run_command, sanitized_environment


def quota_error(event):
    # Inspect errors only: source text / final messages cannot control shutdown.
    error = event.get("error") if event.get("type") == "turn.failed" else event if event.get("type") == "error" else None
    if not isinstance(error, dict):
        return False
    text = json.dumps(error).lower()
    return any(marker in text for marker in ("usagelimitexceeded", "usage_limit_exceeded",
               "insufficient_quota", "usage limit", "quota exceeded", "quota exhausted"))


def validate_proposal(item, base):
    required = {"problem", "strategy", "files", "impact", "cost", "risk", "evidence", "success_metric"}
    if not isinstance(item, dict) or set(item) != required:
        raise Blocked("invalid_proposal_schema")
    if item["risk"] not in {"low", "medium", "high"}:
        raise Blocked("invalid_risk")
    for name in ("impact", "cost"):
        if type(item[name]) not in {int, float} or not 1 <= item[name] <= 10:
            raise Blocked("invalid_proposal_score")
    if not isinstance(item["files"], list) or not item["files"] or len(item["files"]) > 8:
        raise Blocked("invalid_proposal_files")
    from .safety import digest, forbidden, safe_relative
    for name in item["files"]:
        if not isinstance(name, str):
            raise Blocked("invalid_proposal_files")
        safe_relative(name)
        if forbidden(name):
            raise Blocked("protected_proposal_path")
    for name in ("problem", "strategy", "evidence", "success_metric"):
        if not isinstance(item[name], str) or not 1 <= len(item[name]) <= 1500:
            raise Blocked("invalid_proposal_text")
    # Normalize case/whitespace and bind the hypothesis to a baseline.
    identity = json.dumps([base, item["problem"].strip().lower(), item["strategy"].strip().lower(),
                           sorted(item["files"])], ensure_ascii=True).encode()
    return {**item, "id": digest(identity)[:24], "base": base}


class DemoExecutor:
    """Explicit fake agent, real filesystem changes and independent real measurements."""
    def discover(self, worktree, history, base):
        candidates = [
            {"problem": "Quadratic summation", "strategy": "cache-count", "impact": 10, "cost": 1,
             "evidence": "Nested prefix summation in calc.py; try a cached shortcut."},
            {"problem": "Quadratic summation", "strategy": "linear-sum", "impact": 9, "cost": 1,
             "evidence": "Each prefix is recomputed; replace with one summation."},
        ]
        # After the measured optimization, discovery continues but does not invent a gain.
        if "return sum(values)" in (worktree / "calc.py").read_text():
            return []
        return [validate_proposal({**item, "risk": "low", "files": ["calc.py"],
                                  "success_metric": "At least 20% faster, contract unchanged"}, base)
                for item in candidates]

    def implement(self, worktree, proposal):
        result = "return len(values)" if proposal["strategy"] == "cache-count" else "return sum(values)"
        code = f"def total(values):\n    {result}\n"
        # A distinct executing process, while the supervisor remains alive.
        import sys
        script = "from pathlib import Path; import sys; Path('calc.py').write_text(sys.stdin.read(), encoding='utf-8')"
        code_status, _, _ = run_command([sys.executable, "-c", script], worktree, input_text=code)
        if code_status:
            raise Blocked("demo_executor_failed")


class CodexExecutor:
    def __init__(self, config, store, interrupt):
        self.config, self.store, self.interrupt = config, store, interrupt

    def call(self, worktree, prompt, schema=None, readonly=False):
        config = self.config
        # Local execution is a separate operator authorization, not an isolation attestation.
        if not config["live_enabled"] or not (
            config["isolated_execution_confirmed"] is True
            or config.get("local_execution_authorized", False) is True
        ):
            raise Blocked("live_execution_not_authorized")
        native = Path(config["codex_executable"])
        if not native.is_file() or native.suffix.lower() != ".exe" and os.name == "nt":
            raise Blocked("native_codex_executable_required")
        if config.get("chatgpt_quota_guard", False):
            from .quota import read_quota
            try:
                quota = read_quota(native, worktree, self.interrupt)
                self.store.set("chatgpt_quota", quota)
                if quota["exhausted"]:
                    raise Blocked("chatgpt_quota_exhausted")
            except Blocked as error:
                if str(error) != "manual_interrupt":
                    self.store.set("control", {"mode": "stopped", "reason": str(error)})
                raise
        id = uuid.uuid4().hex
        day = datetime.now(timezone.utc).date().isoformat()
        self.store.reserve(id, day, config)
        receipt = {**getattr(self, '_context_receipt', {}), 'call_id': id,
                   'sent_at': time.time(), 'prompt_chars': len(prompt), 'status': 'sent'}
        self.store.set('instruction_delivery', receipt)
        argv = [str(native), "exec", "--ignore-user-config", "--sandbox", "read-only" if readonly else "workspace-write",
                "-c", 'approval_policy="never"', "-c", "sandbox_workspace_write.network_access=false",
                "-c", 'web_search="disabled"', "--json", "--color", "never", "--cd", str(worktree)]
        argv += ["-c", 'model_reasoning_effort=' + json.dumps(config.get("reasoning_effort", "medium"))]
        if os.name == "nt":
            # --ignore-user-config must not inadvertently disable Windows sandboxing.
            argv += ["-c", 'windows.sandbox="unelevated"']
        if config.get("chatgpt_quota_guard", False):
            argv += ["-c", 'forced_login_method="chatgpt"']
        if config.get("model"):
            argv += ["--model", config["model"]]
        if schema:
            argv += ["--output-schema", str(schema)]
        argv += ["-"]
        env = sanitized_environment()
        # Auth is reused by Codex only. Never copy auth.json, export keys or log environment values.
        for key in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "CODEX_HOME", "HOME",
                    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                    "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
            if key in os.environ:
                env[key] = os.environ[key]
        code, stdout, _ = run_command(argv, worktree, config["max_call_seconds"], self.interrupt, env, prompt)
        final, usage, thread_id, exhausted = None, None, None, False
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            exhausted = exhausted or quota_error(event)
            if event.get("type") == "thread.started":
                thread_id = event.get("thread_id")
            if event.get("type") == "turn.completed":
                usage = event.get("usage")
            item = event.get("item", {})
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                final = item.get("text")
        if usage:
            tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            self.store.usage(id, tokens)
            self.store.set('last_model_usage', {key: usage.get(key) for key in
                           ('input_tokens', 'cached_input_tokens', 'output_tokens')})
            self.store.set('instruction_delivery', {**receipt, 'status': 'completed', 'tokens': tokens})
            # Reservation is an accounting estimate; included quota is checked separately.
            if config.get("enforce_token_reservation", True) and tokens > config["reserve_tokens_per_call"]:
                self.store.set("control", {"mode": "paused", "reason": "token_reservation_exceeded"})
                raise Blocked("token_reservation_exceeded")
        if thread_id:
            self.store.set("last_thread_id", thread_id)
        if exhausted:
            self.store.set("control", {"mode": "stopped", "reason": "chatgpt_quota_exhausted"})
            raise Blocked("chatgpt_quota_exhausted")
        if code or not final or not usage:
            raise Blocked("codex_call_failed_or_usage_unknown")
        return final

    def project_context(self, worktree, base, focus=None, expected_fingerprint=None, include_objective=True):
        from .memory import context
        from .guidance import snapshot
        guidance = snapshot(self.store)
        if expected_fingerprint is not None and guidance['fingerprint'] != expected_fingerprint:
            raise Blocked('instructions_changed_before_task')
        self._context_receipt = {k: guidance[k] for k in ('fingerprint', 'has_instructions')}
        self._context_receipt['stage'] = 'implementation' if focus else 'discovery'
        text = context(worktree, self.config, self.store, base, focus, guidance)
        from .objectives import current
        objective = current(self.store)
        if include_objective and objective and objective['criteria']:
            text += '\nOPERATOR_ACCEPTANCE_CRITERIA:\n' + json.dumps(
                [{'criterion': c['criterion'], 'status': c['status']} for c in objective['criteria']],
                ensure_ascii=False, separators=(',', ':'))
        profile = self.store.get('project_profile')
        if profile:
            text += '\nPROJECT_SETUP:\n' + json.dumps(profile, ensure_ascii=False, separators=(',', ':'))
            text += '\nPERSISTENT_PROJECT_UNDERSTANDING:\n' + json.dumps(
                self.store.get('project_understanding'), ensure_ascii=False, separators=(',', ':'))
            text += '\nBASELINE_CHECKS:\n' + json.dumps(self.store.get('baseline_checks'))
        if self.config.get('allow_new_files'):
            from .creation import EXTENSIONS
            text += ('\nCREATION_SCOPE: New source files and regression tests may be proposed using these extensions: '
                     + ','.join(sorted(EXTENSIONS)) + '. Each exact path must appear in proposal.files. '
                     'No hidden folders, secrets, dependencies or configuration changes. Existing protected tests '
                     'cannot be weakened; add new tests for new behavior. If only structural checks exist, a code '
                     'task MUST include new behavioral regression tests in proposal.files or it cannot be accepted. '
                     'For Python without a framework use unittest tests/test_*.py; for plain Node use *.test.js '
                     'with node:test. Missing checks are not evidence of success.\n')
        return text

    def understand_project(self, worktree, base):
        prompt = (
            'Understand this imported project once and produce a compact persistent orientation for future '
            'creation/modification/test iterations. Read the README, manifests, entrypoints and selected callers. '
            'Identify the actual purpose, major components/data flow, existing capabilities, gaps and testing '
            'limitations. Do not invent requirements or audit every file. Describe 1-3 useful next priorities '
            'consistent with operator guidance. The supplied validation recipes are controlled by the supervisor; '
            'do not invent a shell command or change them. Structural checks do not prove feature correctness. '
            'Read only: no edits, secrets, network, service operations, installs, deployment or other agents. '
            'Keep each text under 1200 characters and priorities under 300 characters.\n' +
            self.project_context(worktree, base))
        self._context_receipt['stage'] = 'understanding'
        fingerprint = self._context_receipt['fingerprint']
        schema = Path(__file__).resolve().parent.parent / 'schemas/project-understanding.json'
        raw = self.call(worktree, prompt, schema, readonly=True)
        try:
            result = json.loads(raw)
        except ValueError:
            raise Blocked('invalid_project_understanding') from None
        from .guidance import snapshot
        if snapshot(self.store)['fingerprint'] != fingerprint:
            raise Blocked('instructions_changed_before_task')
        if not isinstance(result, dict) or set(result) != {'purpose', 'architecture', 'testing', 'priorities'}:
            raise Blocked('invalid_project_understanding')
        for field in ('purpose', 'architecture', 'testing'):
            if not isinstance(result[field], str) or not 1 <= len(result[field]) <= 1200:
                raise Blocked('invalid_project_understanding')
        if not isinstance(result['priorities'], list) or len(result['priorities']) > 3 or any(
            not isinstance(p, str) or not 1 <= len(p) <= 300 for p in result['priorities']):
            raise Blocked('invalid_project_understanding')
        return {**result, 'base': base}

    def discover(self, worktree, history, base):
        prompt = (
            "Use the persistent project brief and verified results below. Do not restart a general audit, "
            "reread every specification or reopen completed operator criteria. If those criteria are met, preserve their outcomes "
            "and look for other justified improvements consistent with the operator's constraints. "
            "Pick one priority area from the brief and current guidance; "
            "inspect its source, callers and relevant tests. Stop exploring once one useful feature gap or defect is "
            "demonstrated. Creation, modification and improvement are all supported within the supplied scope. "
            "Return that proposal and up to two adjacent, already evidenced tasks for the "
            "persistent queue; do not spend extra time filling the queue. Return [] if none is justified. "
            "Do not repeat accepted/rejected work without new evidence. Each proposal needs precise code "
            "locations, a minimal reproduction, current vs expected behavior and observable acceptance. "
            "Prioritize correctness, missing-data handling, timing and measurement, not cosmetic churn. "
            "No speculative parameter/threshold/weight changes: application tests do not establish "
            "predictive lift. Mature independent held-out outcomes and a trusted evaluator are required. "
            "Unknown or censored evidence must remain unknown. Synthetic tests are not financial results. "
            "Read only: no file edits, secrets, external services, trading, deployments, process/service "
            "operations, deletion, permission changes or additional agents. Editable scope below is "
            "binding; existing protected tests, dependencies, configuration and host scripts remain protected. "
            "All existing independent checks must pass unchanged.\n\n" +
            self.project_context(worktree, base))
        schema = Path(__file__).resolve().parent.parent / "schemas/proposals.json"
        fingerprint = self._context_receipt['fingerprint']
        result = json.loads(self.call(worktree, prompt, schema, readonly=True))
        proposals = []
        for item in result['proposals']:
            proposal = validate_proposal(item, base)
            # Identical work with changed instructions is a distinct task decision.
            from .safety import digest
            proposal['id'] = digest((proposal['id'] + fingerprint).encode())[:24]
            proposal['instructions_fingerprint'] = fingerprint
            proposals.append(proposal)
        return proposals

    def pursue_objective(self, worktree, history, base, objective):
        from .guidance import snapshot
        from .objectives import validate_assessment
        from .safety import digest
        prompt = (
            "Work ONLY toward the operator's current instructions. Do not search for unrelated "
            "improvements. On the first checkpoint, translate ALL requested outcomes and constraints "
            "into 1-8 concrete, observable acceptance criteria, preserving the user's intent. "
            "Subsequent checkpoints MUST keep exactly the same criterion texts and order. Assess "
            "each criterion as met, unmet or unknown on this baseline. Provide precise code/test "
            "locations and behavioral evidence, not a promise or merely 'tests pass'. Unknown "
            "requirements, unavailable data and outcomes outside the editable scope remain unknown. "
            "Never replace a difficult requirement with an easier one or claim perfection. "
            "Return up to three evidenced, small tasks in dependency order that close unmet criteria; "
            "reuse the completed work below rather than restarting an audit. Each task must name "
            "the criterion it serves in success_metric. Return no unrelated tasks. When all criteria "
            "are met, return no tasks; independent checks will still decide the checkpoint. "
            "Read only: no edits, secrets, network, services, trading, deployment, deletion, permission "
            "changes or additional agents. Existing protected tests, dependencies and configuration remain unchanged. "
            "Create requested features and new regression tests where the supplied scope permits them. "
            "Predictive improvement needs mature held-out outcomes, never synthetic "
            "tests or speculative parameter changes. Be concise.\nOBJECTIVE_CHECKPOINT:\n" +
            json.dumps({'criteria': objective['criteria'],
                        'tasks': [{'problem': p['payload']['problem'], 'status': p['status'],
                                   'acceptance': p['payload']['success_metric']}
                                  for p in self.store.proposals()
                                  if p['payload'].get('objective_fingerprint') == objective['fingerprint']][-12:]},
                       ensure_ascii=False, separators=(',', ':')) + '\n' +
            self.project_context(worktree, base, expected_fingerprint=objective['fingerprint'], include_objective=False))
        self._context_receipt['stage'] = 'objective'
        schema = Path(__file__).resolve().parent.parent / 'schemas/objective.json'
        result = json.loads(self.call(worktree, prompt, schema, readonly=True))
        if snapshot(self.store)['fingerprint'] != objective['fingerprint']:
            raise Blocked('instructions_changed_before_task')
        criteria = validate_assessment(result, objective, worktree, self.config)
        proposals = []
        for index, item in enumerate(result['proposals']):
            proposal = validate_proposal(item, base)
            proposal.update(id=digest((proposal['id'] + objective['fingerprint']).encode())[:24],
                            instructions_fingerprint=objective['fingerprint'],
                            objective_fingerprint=objective['fingerprint'], objective_order=index)
            proposals.append(proposal)
        return criteria, proposals

    def implement(self, worktree, proposal):
        prompt = (
            "Implement only the approved proposal below. First verify its evidence against the current "
            "code and relevant callers/tests, particularly changes_since_discovery. If already fixed or "
            "unsupported, report that and leave files unchanged. Do not start another project-wide audit. "
            "Keep the edit small; preserve missing-data abstention and time-correct evidence. "
            "Do not commit or change existing protected tests, benchmarks, configuration, validation or files outside scope. "
            "Approved new source and regression-test files may be created. Test the requested behavior, not merely syntax. "
            "No secrets, network, deployments, trading, service/process operations, deletion, permission "
            "changes or additional agents. The supervisor alone decides acceptance. Report evidence "
            "limitations: passing tests is not predictive or financial improvement.\n" +
            self.project_context(worktree, proposal["base"], proposal['files'],
                                 proposal.get('instructions_fingerprint')) +
            "\nPROPOSAL:\n" + json.dumps(proposal, ensure_ascii=False, separators=(',', ':')))
        return self.call(worktree, prompt)
