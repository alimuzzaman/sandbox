"""One poll of every follow on this host (spec 089, FR-005 to FR-007, FR-009, FR-010).

The systemd timer runs ``sb host follow tick --here``. A poll never applies
anything itself: on a change it records an attempt and submits one top-level
server CLI durable job, ``host follow apply``, through the installed job
contract, the same contract a controller's dispatch uses.
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Callable

from sandbox.hosting.follow import git
from sandbox.hosting.follow import spec as follow_spec
from sandbox.hosting.follow.spec import FollowError
from sandbox.hosting.follow.store import FollowStore, utc_now

ACTIVE = frozenset({"accepted", "queued", "running", "cancelling"})
IN_FLIGHT = frozenset({"submitting", "running"})
ACTOR = "follower"


# ------------------------------------------------------------------ decide

def decide(state: dict, *, heads: dict | None, fetch_error: str | None, busy: str | None,
           stack_present: bool) -> tuple[str, str]:
    """(action, reason) for a follow with no attempt in flight. Pure.

    Order: paused, fetch, removed stack, unchanged, failed heads, busy, start.
    """
    if state.get("paused"):
        return "skip", "paused"
    if fetch_error is not None or heads is None:
        return "skip", "fetch_failed"
    retry = bool(state.get("retry"))
    if state.get("stack_seen") and not stack_present:
        return "pause", "stack_removed"
    if not retry and heads == state.get("applied"):
        return "skip", "up_to_date"
    if not retry and heads == state.get("failed_heads"):
        return "skip", "waiting_after_failure"
    if busy is not None:
        return "skip", busy
    return "start", "retry_requested" if retry else "heads_changed"


# ------------------------------------------------------------- the target

def stack_record(remote_name: str, name: str) -> dict | None:
    from sandbox.hosting import stack_registry
    data = stack_registry.read()
    record = data["stacks"].get(stack_registry.record_key(remote_name, name))
    return record if isinstance(record, dict) else None


def sandbox_home() -> str:
    from sandbox.core import _paths
    return str(Path(_paths.RUNTIME_DIR).parent)


def target_busy(record: dict | None, *, home: str, now: float | None = None) -> tuple[str | None, int | None]:
    """(busy reason, seconds held) for the stack's target; (None, None) when free.

    A lease file is never broken here (spec 081 policy): only reported.
    """
    if record is None:
        return None, None
    from sandbox.hosting import target_lease
    from sandbox.commands import host_stack
    project, environment = record.get("project"), record.get("environment")
    if isinstance(project, str) and isinstance(environment, str):
        path = Path(target_lease.lease_path(home, project, environment))
        try:
            parts = path.read_text().split()
        except FileNotFoundError:
            parts = []
        except OSError:
            return "target_lease_unreadable", None
        if parts:
            since = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
            age = int((now if now is not None else time.time()) - since) if since else None
            return "target_lease_held", age
    try:
        host_stack._refuse_if_active(record)
    except host_stack.StackCommandError as exc:
        return ("stack_apply_active" if exc.code == host_stack.APPLY_IN_PROGRESS
                else "stack_apply_outcome_unknown"), None
    return None, None


# ------------------------------------------------------------- submission

def job_argv(record: dict, capability: dict, *, attempt_id: str, request_id: str,
             project_dir: Path, source_commit: str) -> list[str]:
    observed = capability.get("target_metadata") or {}
    routing = dict(record["routing"])
    for key in ("origin_ipv4", "origin_ipv6", "tailscale_host"):
        if observed.get(key):
            routing[key] = observed[key]
    return [capability["executable"], "job-start", "--local", "--server-cli",
            "--server-cli-target", capability["target_identity"],
            "--server-cli-revision", capability["runtime_revision"],
            "--server-cli-marker", capability["ownership_marker"],
            "--server-cli-origin", record["remote"],
            "--server-cli-target-metadata", json.dumps(routing, sort_keys=True, separators=(",", ":")),
            "--project-dir", str(project_dir), "--exact-project-dir",
            "--request-id", request_id, "--source-commit", source_commit,
            "--timeout", str(follow_spec.job_timeout(record)), "--json",
            "--", "host", "follow", "apply", "--remote", record["remote"], "--name", record["name"],
            "--attempt", attempt_id, "--json"]


def default_submit(argv: list[str]) -> str:
    from sandbox.commands.host_stack import _submit
    return _submit(argv)


def default_capability() -> dict:
    from sandbox.application.server_cli import capabilities
    return capabilities()


def default_job_view(job_id: str | None) -> dict | None:
    from sandbox.commands.host_stack import job_view
    return job_view(job_id)


class Tick:
    """One poll. Collaborators are injectable so the decision path is testable."""

    def __init__(self, store: FollowStore | None = None, *, submit: Callable = default_submit,
                 capability: Callable = default_capability, job_view: Callable = default_job_view,
                 fetch: Callable = git.fetch, checkout: Callable = git.checkout,
                 stack_lookup: Callable = stack_record, busy: Callable = target_busy,
                 home: Callable = sandbox_home, budget_seconds: float = 600,
                 monotonic: Callable = time.monotonic):
        self.store = store or FollowStore()
        self.submit, self.capability, self.job_view = submit, capability, job_view
        self.fetch, self.checkout = fetch, checkout
        self.stack_lookup, self.busy, self.home = stack_lookup, busy, home
        self.budget_seconds, self.monotonic = budget_seconds, monotonic
        self._capability = None

    # -------------------------------------------------------------- run
    def run(self, only: str | None = None) -> dict:
        with self.store.host_lock() as acquired:
            if not acquired:
                return {"ok": True, "action": "follow-tick", "status": "skipped_busy",
                        "reason": "another poll is running", "follows": []}
            started = self.monotonic()
            names = [only] if only else self.store.names()
            results = []
            for name in names:
                if self.monotonic() - started > self.budget_seconds:
                    results.append({"name": name, "action": "skip", "reason": "budget_exhausted"})
                    continue
                try:
                    results.append(self.one(name))
                except FollowError as exc:
                    results.append({"name": name, "action": "error", "reason": exc.code,
                                    "error": exc.message})
            return {"ok": True, "action": "follow-tick", "status": "ran", "follows": results}

    def _check(self, name: str, row: dict) -> dict:
        """Record the poll's outcome in the follow's last check."""
        with self.store.lock(name):
            record = self.store.read(name)
            if record is None:
                return row
            record["state"]["last_check"] = {"at": utc_now(), **{k: v for k, v in row.items() if k != "name"}}
            self.store.write(record)
        return row

    # ---------------------------------------------------------- in flight
    def _settle_in_flight(self, name: str) -> dict | None:
        """Handle an attempt still marked in flight; None when there is none."""
        replay = None
        with self.store.lock(name):
            record = self.store.read(name)
            if record is None:
                return {"name": name, "action": "skip", "reason": "removed"}
            attempt = record["state"].get("attempt")
            if not attempt or attempt.get("status") not in IN_FLIGHT:
                return None
            if self.store.apply_running(name):
                return {"name": name, "action": "skip", "reason": "attempt_running",
                        "attempt": attempt["id"]}
            job = self.job_view(attempt.get("job_id")) if attempt.get("job_id") else None
            if job and job.get("lifecycle") in ACTIVE:
                return {"name": name, "action": "skip", "reason": "attempt_running",
                        "attempt": attempt["id"]}
            if attempt.get("status") == "submitting" and not attempt.get("job_id"):
                # Acceptance was unknown: replay the identical request only.
                replay = (attempt["id"], list(attempt["argv"]))
            else:
                lifecycle = (job or {}).get("lifecycle") or "unknown"
                attempt.update({"status": "failed" if lifecycle != "succeeded" else "unknown",
                                "reason": f"attempt_ended_without_outcome:{lifecycle}",
                                "finished_at": utc_now()})
                record["state"]["failed_heads"] = attempt.get("heads")
                self.store.write(record)
                self.store.audit("attempt_finished", name, actor=ACTOR, attempt=attempt["id"],
                                 status=attempt["status"], reason=attempt["reason"],
                                 job_id=attempt.get("job_id"))
                row = {"name": name, "action": "settled", "reason": attempt["reason"],
                       "attempt": attempt["id"]}
        if replay is None:
            return self._check(name, row)
        attempt_id, argv = replay
        return self._check(name, {"name": name, **self._submit_attempt(name, attempt_id, argv),
                                  "replayed": True})

    def _submit_attempt(self, name: str, attempt_id: str, argv: list[str]) -> dict:
        """Submit outside the follow lock: the attempt job takes that lock as it starts."""
        from sandbox.commands.host_stack import StackCommandError, SUBMISSION_UNKNOWN
        try:
            job_id = self.submit(argv)
        except StackCommandError as exc:
            if exc.code == SUBMISSION_UNKNOWN:
                return {"action": "start", "reason": "submission_unknown", "attempt": attempt_id}
            with self.store.lock(name):
                record = self.store.read(name)
                attempt = (record or {}).get("state", {}).get("attempt") or {}
                if record is not None and attempt.get("id") == attempt_id and attempt.get("status") == "submitting":
                    attempt.update({"status": "failed", "reason": "submission_refused",
                                    "finished_at": utc_now()})
                    record["state"]["failed_heads"] = attempt.get("heads")
                    self.store.write(record)
                    self.store.audit("attempt_finished", name, actor=ACTOR, attempt=attempt_id,
                                     status="failed", reason="submission_refused")
            return {"action": "start", "reason": "submission_refused", "attempt": attempt_id}
        with self.store.lock(name):
            record = self.store.read(name)
            attempt = (record or {}).get("state", {}).get("attempt") or {}
            if record is not None and attempt.get("id") == attempt_id and attempt.get("status") == "submitting":
                attempt.update({"job_id": job_id, "status": "running"})
                self.store.write(record)
            self.store.audit("attempt_submitted", name, actor=ACTOR, attempt=attempt_id, job_id=job_id)
        return {"action": "start", "reason": "submitted", "attempt": attempt_id, "job_id": job_id}

    # ---------------------------------------------------------------- one
    def one(self, name: str) -> dict:
        settled = self._settle_in_flight(name)
        if settled is not None:
            return settled
        record = self.store.read(name)
        if record is None:
            return {"name": name, "action": "skip", "reason": "removed"}
        if record["state"].get("paused"):
            return self._check(name, {"name": name, "action": "skip", "reason": "paused"})
        heads, fetch_error = {}, None
        self.store.ensure()
        git.write_known_hosts(self.store.known_hosts)
        for repo in record["repos"]:
            try:
                heads[repo["name"]] = self.fetch(
                    self.store.repo_dir(name, repo["name"]), repo["github"], repo["branch"],
                    key=git.key_path(self.store.keys, repo["github"]), known_hosts=self.store.known_hosts)
            except FollowError as exc:
                fetch_error = exc.message
                break
        stack = self.stack_lookup(record["remote"], name)
        busy, held = self.busy(stack, home=self.home())
        pending = None
        with self.store.lock(name):
            record = self.store.read(name)
            if record is None:
                return {"name": name, "action": "skip", "reason": "removed"}
            state = record["state"]
            attempt = state.get("attempt")
            if attempt and attempt.get("status") in IN_FLIGHT:
                return {"name": name, "action": "skip", "reason": "attempt_running"}
            action, reason = decide(state, heads=None if fetch_error else heads, fetch_error=fetch_error,
                                    busy=busy, stack_present=stack is not None)
            row = {"name": name, "action": action, "reason": reason,
                   "heads": None if fetch_error else heads}
            if fetch_error:
                row["error"] = fetch_error
            if held is not None:
                row["busy_seconds"] = held
            if action == "pause":
                state.update({"paused": True, "pause_reason": reason})
                self.store.write(record)
                self.store.audit("auto_pause", name, actor=ACTOR, reason=reason)
            elif action == "start":
                pending = self._record_attempt(record, heads)
                if "argv" not in pending:
                    return {**row, **pending}
        if pending is None:
            return self._check(name, row)
        return {**row, **self._submit_attempt(name, pending["attempt"], pending["argv"])}

    def _record_attempt(self, record: dict, heads: dict) -> dict:
        """Check out the heads and record the attempt. Called under the follow lock."""
        name, state = record["name"], record["state"]
        try:
            if self._capability is None:
                self._capability = self.capability()
        except Exception as exc:  # ServerCLIUnavailable and friends
            code = getattr(exc, "code", None) or "server_cli_capability_unavailable"
            state["last_check"] = {"at": utc_now(), "action": "skip", "reason": code}
            self.store.write(record)
            return {"action": "skip", "reason": code}
        try:
            for repo in record["repos"]:
                self.checkout(self.store.repo_dir(name, repo["name"]), heads[repo["name"]])
        except FollowError as exc:
            state["last_check"] = {"at": utc_now(), "action": "skip", "reason": exc.code, "error": exc.message}
            self.store.write(record)
            return {"action": "skip", "reason": exc.code, "error": exc.message}
        attempt_id = secrets.token_hex(8)
        request_id = f"follow-{name}-{attempt_id}"
        primary = record["repos"][0]["name"]
        argv = job_argv(record, self._capability, attempt_id=attempt_id, request_id=request_id,
                        project_dir=self.store.repo_dir(name, primary), source_commit=heads[primary])
        state["attempt"] = {"id": attempt_id, "request_id": request_id, "heads": heads, "argv": argv,
                            "status": "submitting", "job_id": None, "apply_job_id": None,
                            "trigger": "retry" if state.get("retry") else "change",
                            "started_at": utc_now(), "finished_at": None, "reason": None}
        state["retry"] = False
        state["last_check"] = {"at": utc_now(), "action": "start", "reason": "submitting", "heads": heads}
        self.store.write(record)
        self.store.audit("attempt_started", name, actor=ACTOR, attempt=attempt_id, heads=heads,
                         request_id=request_id)
        return {"attempt": attempt_id, "argv": argv}


def run_here(only: str | None = None) -> dict:
    return Tick().run(only)
