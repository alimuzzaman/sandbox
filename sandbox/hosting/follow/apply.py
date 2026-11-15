"""The body of one follow attempt: ``host follow apply`` (spec 089, FR-008, FR-010).

It runs only as its own server CLI durable job, submitted by the poll. Inside
that job it holds the follow's apply lock, checks its attempt and checkouts,
runs the declared prepare step, then runs ``host stack up`` plan and confirm
in-process: the same code, owner target and nested apply job a controller's
dispatched ``host stack up`` uses. It waits for that apply job and records the
outcome. It never removes or rolls back anything itself.
"""
from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sandbox.hosting.follow import git
from sandbox.hosting.follow import spec as follow_spec
from sandbox.hosting.follow.spec import FollowError
from sandbox.hosting.follow.store import FollowStore, utc_now

TERMINAL = frozenset({"succeeded", "failed", "timed_out", "cancelled", "interrupted"})
ACTOR = "follower"
POLL_SECONDS = 5
_ATTEMPT = re.compile(r"^[0-9a-f]{16}$")


def _redact(text: str) -> str:
    from sandbox.core import _remote
    return _remote.redact_text(str(text))[:300]


def stack_namespace(record: dict, template: Path, attempt_id: str, *, plan: bool) -> argparse.Namespace:
    """The arguments a person's ``host stack up`` would carry."""
    return argparse.Namespace(
        action="stack", image_action="up", stack_action="up", template=str(template),
        remote=record["remote"], name=record["name"], ref=record["ref"], owner=record["owner"],
        environment=record.get("environment"), lifetime=record.get("lifetime"), ttl=None, copy=None,
        port=None, generate_secrets=bool(record.get("generate")),
        request_id=f"follow-{record['name']}-{attempt_id}-apply", json=True,
        server_cli_stack_ref=None, plan=plan, confirm=not plan)


def run_prepare(argv: list[str], *, cwd: Path, env: dict, timeout: int) -> None:
    """Run the owner's prepare step without a shell; its output joins the job log."""
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        process = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                   start_new_session=True)
    except OSError as exc:
        raise FollowError("follow_prepare_unavailable", f"prepare could not start: {exc.strerror}") from None
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=30)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        raise FollowError("follow_prepare_timeout", f"prepare ran past its {timeout}s limit") from None
    if code != 0:
        raise FollowError("follow_prepare_failed", f"prepare exited {code}")


def _default_stack_up(namespace: argparse.Namespace) -> dict:
    from sandbox.commands.host_stack import _up
    return _up(namespace)


def _default_job_view(job_id: str | None) -> dict | None:
    from sandbox.commands.host_stack import job_view
    return job_view(job_id)


def _owner_context(remote_name: str):
    from sandbox.hosting.server_cli_target import selected_owner_target
    target = selected_owner_target(remote_name)
    if target is None:
        raise FollowError("follow_apply_requires_server_job",
                          "host follow apply runs only inside the follow's own server CLI job")
    return target.require_current()


class Attempt:
    def __init__(self, store: FollowStore | None = None, *, context_for: Callable = _owner_context,
                 stack_up: Callable = _default_stack_up, job_view: Callable = _default_job_view,
                 prepare: Callable = run_prepare, head: Callable = git.head,
                 environment: Callable | None = None, sleep: Callable = time.sleep,
                 now: Callable = lambda: datetime.now(timezone.utc)):
        self.store = store or FollowStore()
        self.context_for, self.stack_up, self.job_view = context_for, stack_up, job_view
        self.prepare, self.head, self.sleep, self.now = prepare, head, sleep, now
        self.environment = environment or self._owner_environment

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
            except FollowError as exc:
                outcome["reason"] = f"{exc.code}: {_redact(exc.message)}"
            except Exception as exc:  # stack up refusals and unexpected errors are an outcome
                code = getattr(exc, "code", None) or type(exc).__name__
                outcome["reason"] = f"{code}: {_redact(exc)}"
            return self._finish(name, attempt_id, heads, outcome)

    def _apply(self, record: dict, context, attempt_id: str, heads: dict) -> dict:
        name = record["name"]
        work = self.store.work_dir(name)
        repo_dirs = {repo["name"]: self.store.repo_dir(name, repo["name"]) for repo in record["repos"]}
        for repo, directory in repo_dirs.items():
            if self.head(directory) != heads.get(repo):
                raise FollowError("follow_source_changed",
                                  f"checkout {repo} is not at the attempt's commit {heads.get(repo, '')[:12]}")
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
        template = Path(follow_spec.substitute(record["template"], **values)).resolve()
        if work.resolve() not in template.parents or not template.is_file():
            raise FollowError("follow_template_missing",
                              "the stack template is not a file inside the follow's checkouts")
        planned = self.stack_up(stack_namespace(record, template, attempt_id, plan=True))
        apex = sorted(host for host in planned.get("hostnames") or [] if follow_spec.is_apex(host))
        if apex:
            raise FollowError("follow_apex_hostname_refused",
                              f"the stack would route apex names {', '.join(apex)}; refused")
        if planned.get("slug") != name:
            raise FollowError("follow_stack_mismatch", "the plan names a different stack")
        result = self.stack_up(stack_namespace(record, template, attempt_id, plan=False))
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
            return {"status": "failed", "apply_job_id": apply_job,
                    "reason": f"stack_apply_{lifecycle}: exit {job.get('exit_code')}"}
        return {"status": "succeeded", "reason": None, "apply_job_id": apply_job}

    def _finish(self, name: str, attempt_id: str, heads: dict, outcome: dict) -> dict:
        with self.store.lock(name):
            record = self.store.read(name)
            if record is not None:
                state = record["state"]
                attempt = state.get("attempt") or {}
                if attempt.get("id") == attempt_id:
                    attempt.update({**outcome, "finished_at": utc_now()})
                if outcome["status"] == "succeeded":
                    state.update({"applied": heads, "failed_heads": None})
                else:
                    state["failed_heads"] = heads
                self.store.write(record)
            self.store.audit("attempt_finished", name, actor=ACTOR, attempt=attempt_id,
                             status=outcome["status"], reason=outcome["reason"],
                             apply_job_id=outcome.get("apply_job_id"))
        return {"ok": outcome["status"] == "succeeded", "action": "follow-apply", "name": name,
                "attempt": attempt_id, "heads": heads, **outcome}
