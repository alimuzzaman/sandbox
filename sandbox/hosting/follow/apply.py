"""The body of one follow attempt: ``host follow apply`` (spec 089, FR-008, FR-010).

It runs only as its own server CLI durable job, submitted by the poll. Inside
that job it holds the follow's apply lock, checks its attempt and checkouts,
runs the declared prepare step, then runs ``host stack up`` plan and confirm
in-process: the same code, owner target and nested apply job a controller's
dispatched ``host stack up`` uses. It waits for that apply job and records the
outcome. It never removes or rolls back anything itself.

Before prepare, before plan and again right before confirm it re-reads the
target: a busy target ends the attempt as ``busy`` (the next poll tries the
same heads again), and a stack removed or reaped meanwhile pauses the follow
instead of being recreated.
"""
from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sandbox.hosting.follow import git
from sandbox.hosting.follow import spec as follow_spec
from sandbox.hosting.follow import tick as follow_tick
from sandbox.hosting.follow.spec import FollowError
from sandbox.hosting.follow.store import FollowStore, utc_now
from sandbox.hosting.ports import LOCK_BUSY_EXIT

TERMINAL = follow_tick.TERMINAL
ACTOR = "follower"
POLL_SECONDS = 5
PREPARE_STOP_GRACE = 30
# The job supervisor sends SIGKILL 20 s after SIGTERM; stop prepare well before.
PREPARE_CANCEL_GRACE = 10
PREPARE_POLL_SECONDS = 0.1
_ATTEMPT = re.compile(r"^[0-9a-f]{16}$")
# `host stack up` refusals that mean "someone else is changing this target now".
BUSY_CODES = frozenset({"stack_apply_in_progress", "stack_submission_unknown", "stack_reaping"})


def _redact(text: str) -> str:
    from sandbox.core import _remote
    return _remote.redact_text(str(text))[:300]


def owner_target(remote_name: str | None, *, code: str, message: str):
    """The retained server CLI job's owner context, or a FollowError.

    A changed or unknown retained context (``OwnerTargetUnavailable``,
    ``ServerCLIEffectUnknown``) is a refusal with that error's code, never a
    traceback.
    """
    from sandbox.application.server_cli import ServerCLIEffectUnknown
    from sandbox.hosting.server_cli_target import OwnerTargetUnavailable, selected_owner_target
    try:
        target = selected_owner_target(remote_name) if remote_name else None
        if target is None:
            raise FollowError(code, message)
        return target.require_current()
    except (OwnerTargetUnavailable, ServerCLIEffectUnknown) as exc:
        raise FollowError(getattr(exc, "code", None) or "server_cli_owner_target_unavailable",
                          f"the server CLI job's owner target is unavailable: {_redact(exc)}") from None


def follow_source(record: dict, heads: dict) -> dict | None:
    """The tracked branch and fetched head of the repository holding the template."""
    repo = follow_spec.template_repo(record.get("template") or "")
    branch = next((item["branch"] for item in record["repos"] if item["name"] == repo), None)
    if repo is None or branch is None or not heads.get(repo):
        return None
    return {"branch": branch, "commit": heads[repo]}


def stack_namespace(record: dict, template: Path, attempt_id: str, *, plan: bool,
                    source: dict | None = None) -> argparse.Namespace:
    """The arguments a person's ``host stack up`` would carry, plus the follow's source."""
    return argparse.Namespace(
        action="stack", image_action="up", stack_action="up", template=str(template),
        remote=record["remote"], name=record["name"], ref=record["ref"], owner=record["owner"],
        environment=record.get("environment"), lifetime=record.get("lifetime"), ttl=None, copy=None,
        port=None, generate_secrets=bool(record.get("generate")),
        request_id=f"follow-{record['name']}-{attempt_id}-apply", json=True,
        server_cli_stack_ref=None, follow_source=source, plan=plan, confirm=not plan)


def _exited(process: subprocess.Popen) -> bool:
    """True once prepare's leader has exited, without reaping it.

    An unreaped leader keeps its pid, so ``killpg(process.pid)`` can only reach
    prepare's own process group.
    """
    try:
        return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    except ChildProcessError:
        return True


def _wait(process: subprocess.Popen, until: float, fired: list) -> bool:
    """Wait until the leader exits (True), a stop signal fired or the deadline passed."""
    while not _exited(process):
        if fired or time.monotonic() >= until:
            return False
        time.sleep(PREPARE_POLL_SECONDS)
    return True


def _stop_group(process: subprocess.Popen, grace: float) -> None:
    """SIGTERM prepare's group, SIGKILL it after the grace period, then reap the leader."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    _wait(process, time.monotonic() + grace, [])
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_prepare(argv: list[str], *, cwd: Path, env: dict, timeout: int,
                grace: float = PREPARE_STOP_GRACE, cancel_grace: float = PREPARE_CANCEL_GRACE) -> None:
    """Run the owner's prepare step without a shell; its output joins the job log.

    Prepare runs in its own process group, so a cancelled or timed-out attempt
    job would not reach it. While it runs, SIGTERM and SIGINT only mark the
    attempt cancelled; the wait then stops prepare's group (SIGTERM, SIGKILL
    after the grace period) and ends the attempt as ``follow_prepare_cancelled``.
    A signal that lands during a timeout stop or just after prepare exits is
    handled the same way, so the outcome is always a ``FollowError``.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        process = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                   start_new_session=True)
    except OSError as exc:
        raise FollowError("follow_prepare_unavailable", f"prepare could not start: {exc.strerror}") from None
    previous: dict = {}
    fired: list = []

    def cancel(signum, _frame):
        fired.append(signum)

    if threading.current_thread() is threading.main_thread():
        for number in (signal.SIGTERM, signal.SIGINT):
            previous[number] = signal.signal(number, cancel)
    timed_out = False
    try:
        exited = _wait(process, time.monotonic() + timeout, fired)
        timed_out = not exited and not fired
        if fired or timed_out:
            _stop_group(process, cancel_grace if fired else grace)
        else:
            process.wait()
    finally:
        if process.returncode is None:
            _stop_group(process, grace)
        for number, handler in previous.items():
            signal.signal(number, handler)
    if timed_out:
        raise FollowError("follow_prepare_timeout", f"prepare ran past its {timeout}s limit")
    if fired:
        raise FollowError("follow_prepare_cancelled",
                          "the attempt job was stopped; its prepare step was stopped first")
    if process.returncode != 0:
        raise FollowError("follow_prepare_failed", f"prepare exited {process.returncode}")


def _default_stack_up(namespace: argparse.Namespace) -> dict:
    from sandbox.commands.host_stack import _up
    return _up(namespace)


def _default_job_view(job_id: str | None) -> dict | None:
    from sandbox.commands.host_stack import job_view
    return job_view(job_id)


def _owner_context(remote_name: str):
    return owner_target(remote_name, code="follow_apply_requires_server_job",
                        message="host follow apply runs only inside the follow's own server CLI job")


class _Stop(Exception):
    """End the attempt without a failure: ``busy`` (retry next poll) or ``removed`` (pause)."""

    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


class Attempt:
    def __init__(self, store: FollowStore | None = None, *, context_for: Callable = _owner_context,
                 stack_up: Callable = _default_stack_up, job_view: Callable = _default_job_view,
                 prepare: Callable = run_prepare, head: Callable = git.head,
                 environment: Callable | None = None, sleep: Callable = time.sleep,
                 now: Callable = lambda: datetime.now(timezone.utc),
                 stack_lookup: Callable = follow_tick.stack_record, busy: Callable = follow_tick.target_busy,
                 lifetime: Callable = follow_tick.lifetime_state, home: Callable = follow_tick.sandbox_home):
        self.store = store or FollowStore()
        self.context_for, self.stack_up, self.job_view = context_for, stack_up, job_view
        self.prepare, self.head, self.sleep, self.now = prepare, head, sleep, now
        self.environment = environment or self._owner_environment
        self.stack_lookup, self.busy, self.lifetime, self.home = stack_lookup, busy, lifetime, home

    @staticmethod
    def _owner_environment(context) -> dict:
        from sandbox.application.server_cli import owner_environment
        return owner_environment(context.owner)

    def run(self, remote_name: str, name: str, attempt_id: str) -> dict:
        follow_spec.validate_name(name)
        if not isinstance(attempt_id, str) or not _ATTEMPT.fullmatch(attempt_id):
            raise FollowError("follow_attempt_mismatch", "--attempt must be the attempt id the poll recorded")
        context = self.context_for(remote_name)
        with self.store.apply_lock(name, wait=30) as acquired:
            if not acquired:
                raise FollowError("follow_apply_busy", f"another attempt of follow {name} is running")
            with self.store.lock(name):
                record = self.store.read(name)
                attempt = (record or {}).get("state", {}).get("attempt") or {}
                if (record is None or record.get("remote") != remote_name or attempt.get("id") != attempt_id
                        or attempt.get("status") not in ("submitting", "running")):
                    raise FollowError("follow_attempt_mismatch",
                                      f"follow {name} has no attempt {attempt_id} waiting to run")
                attempt.update({"status": "running", "job_id": context.job_id})
                self.store.write(record)
            heads = dict(attempt["heads"])
            outcome = {"status": "failed", "reason": None, "apply_job_id": None}
            try:
                outcome.update(self._apply(record, context, attempt_id, heads))
            except _Stop as stop:
                outcome.update({"status": stop.status, "reason": stop.reason})
            except FollowError as exc:
                outcome["reason"] = f"{exc.code}: {_redact(exc.message)}"
            except Exception as exc:  # stack up refusals and unexpected errors are an outcome
                code = getattr(exc, "code", None) or type(exc).__name__
                outcome["reason"] = f"{code}: {_redact(exc)}"
                if code in BUSY_CODES:
                    outcome["status"] = "busy"
            return self._finish(name, attempt_id, heads, outcome, trigger=attempt.get("trigger"))

    def _gate(self, record: dict) -> None:
        """Stop unless the target still exists, is not being torn down and is free."""
        stack = self.stack_lookup(record["remote"], record["name"])
        gone, busy, _held = follow_tick.target_state(
            stack, seen=bool(record["state"].get("stack_seen")), home=self.home(),
            busy=self.busy, lifetime=self.lifetime)
        if gone is not None:
            raise _Stop("removed", gone)
        if busy is not None:
            raise _Stop("busy", busy)

    def _apply(self, record: dict, context, attempt_id: str, heads: dict) -> dict:
        name = record["name"]
        work = self.store.work_dir(name)
        repo_dirs = {repo["name"]: self.store.repo_dir(name, repo["name"]) for repo in record["repos"]}
        for repo, directory in repo_dirs.items():
            if self.head(directory) != heads.get(repo):
                raise FollowError("follow_source_changed",
                                  f"checkout {repo} is not at the attempt's commit {heads.get(repo, '')[:12]}")
        self._gate(record)
        values = {"work_dir": work, "name": name, "repo_dirs": repo_dirs, "heads": heads}
        if record.get("prepare"):
            argv = [follow_spec.substitute(item, **values) for item in record["prepare"]]
            env = dict(self.environment(context))
            env.update({"SANDBOX_FOLLOW_NAME": name, "SANDBOX_FOLLOW_DIR": str(work)})
            for repo, directory in repo_dirs.items():
                key = repo.upper().replace("-", "_")
                env[f"SANDBOX_FOLLOW_REPO_{key}"] = str(directory)
                env[f"SANDBOX_FOLLOW_COMMIT_{key}"] = heads[repo]
            print(f"follow {name}: prepare {argv[0]}", flush=True)
            self.prepare(argv, cwd=work, env=env, timeout=int(record["prepare_timeout"]))
            self._gate(record)
        template = Path(follow_spec.substitute(record["template"], **values)).resolve()
        if work.resolve() not in template.parents or not template.is_file():
            raise FollowError("follow_template_missing",
                              "the stack template is not a file inside the follow's checkouts")
        source = follow_source(record, heads)
        planned = self.stack_up(stack_namespace(record, template, attempt_id, plan=True, source=source))
        apex = sorted(host for host in planned.get("hostnames") or [] if follow_spec.is_apex(host))
        if apex:
            raise FollowError("follow_apex_hostname_refused",
                              f"the stack would route apex names {', '.join(apex)}; refused")
        if planned.get("slug") != name:
            raise FollowError("follow_stack_mismatch", "the plan names a different stack")
        # Prepare and plan can take long: someone may have removed the stack,
        # cleanup may be reaping it, or another apply may hold it by now.
        self._gate(record)
        result = self.stack_up(stack_namespace(record, template, attempt_id, plan=False, source=source))
        apply_job = result.get("job_id")
        with self.store.lock(name):
            current = self.store.read(name)
            if current is not None:
                current["state"]["stack_seen"] = True
                attempt = current["state"].get("attempt") or {}
                if attempt.get("id") == attempt_id:
                    attempt["apply_job_id"] = apply_job
                self.store.write(current)
        print(f"follow {name}: stack apply job {apply_job}", flush=True)
        deadline = datetime.fromisoformat(context.deadline_at.replace("Z", "+00:00"))
        while True:
            job = self.job_view(apply_job) or {}
            lifecycle = job.get("lifecycle")
            if lifecycle in TERMINAL:
                break
            if (deadline - self.now()).total_seconds() < 60:
                return {"status": "unknown", "reason": "follow_apply_wait_deadline", "apply_job_id": apply_job}
            self.sleep(POLL_SECONDS)
        if lifecycle != "succeeded":
            reason = f"stack_apply_{lifecycle}: exit {job.get('exit_code')}"
            if job.get("exit_code") == LOCK_BUSY_EXIT:
                # Another target's compose up held the publish lock; nothing ran.
                return {"status": "busy", "apply_job_id": apply_job,
                        "reason": f"hosting_compose_publish_busy after {reason}"}
            busy, _held = self.busy(self.stack_lookup(record["remote"], name), home=self.home())
            if busy is not None:
                # The nested apply most likely met a lease taken after the last
                # check (host_apply_lease_held): the next poll tries again.
                return {"status": "busy", "apply_job_id": apply_job, "reason": f"{busy} after {reason}"}
            return {"status": "failed", "apply_job_id": apply_job, "reason": reason}
        return {"status": "succeeded", "reason": None, "apply_job_id": apply_job}

    def _finish(self, name: str, attempt_id: str, heads: dict, outcome: dict, *,
                trigger: str | None = None) -> dict:
        status = outcome["status"]
        with self.store.lock(name):
            record = self.store.read(name)
            if record is not None:
                state = record["state"]
                attempt = state.get("attempt") or {}
                if attempt.get("id") == attempt_id:
                    attempt.update({**outcome, "finished_at": utc_now()})
                if status == "succeeded":
                    state.update({"applied": heads, "failed_heads": None})
                elif status == "busy":
                    # Not a failure: the next poll starts these heads again.
                    if trigger == "retry":
                        state["retry"] = True
                elif status == "removed":
                    state.update({"paused": True, "pause_reason": outcome["reason"]})
                else:
                    state["failed_heads"] = heads
                self.store.write(record)
            self.store.audit("attempt_finished", name, actor=ACTOR, attempt=attempt_id,
                             status=status, reason=outcome["reason"],
                             apply_job_id=outcome.get("apply_job_id"))
            if status == "removed" and record is not None:
                self.store.audit("auto_pause", name, actor=ACTOR, reason=outcome["reason"])
        return {"ok": status == "succeeded", "action": "follow-apply", "name": name,
                "attempt": attempt_id, "heads": heads, **outcome}
