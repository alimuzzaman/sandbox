"""Revoke expired approved authorizations by restoring each catalog prompt."""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from authorization_core import (AuthorizationError, approval_prompt, audit, expire, now, read_state,
                                state_digest, supersede_approved, write_state)

PLUGIN_ROOT = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get("SANDBOX_AUTHORIZATION_CONFIG", PLUGIN_ROOT / "sandbox-authorization-config.json"))
_JOB_ID = re.compile(r"^[0-9a-f]{8,32}$")


def _config() -> dict:
    try:
        value = json.loads(CONFIG.read_text())
    except (OSError, ValueError) as exc:
        raise AuthorizationError("authorization companion is not configured") from exc
    if not isinstance(value, dict):
        raise AuthorizationError("authorization companion is not configured")
    return value


def _catalog(path: Path) -> dict[str, dict]:
    try:
        jobs = json.loads(path.read_text())["jobs"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AuthorizationError("authorization catalog is not configured") from exc
    return {item["name"]: item for item in jobs if isinstance(item, dict) and isinstance(item.get("name"), str)
            and item.get("kind") == "agent" and item.get("enabled") is True
            and isinstance(item.get("prompt"), str) and item["prompt"].strip()}


def _hermes_bin(config: dict) -> str:
    for candidate in (os.environ.get("HERMES_BIN"), config.get("hermes_bin"), shutil.which("hermes"),
                      str(Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes")):
        if isinstance(candidate, str) and candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise AuthorizationError("Hermes CLI is unavailable")


def _cron_jobs() -> list[dict]:
    path = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "cron" / "jobs.json"
    try:
        raw = json.loads(path.read_text())
        jobs = raw.get("jobs") if isinstance(raw, dict) else raw
    except (OSError, ValueError) as exc:
        raise AuthorizationError("Hermes cron state is unavailable") from exc
    if not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
        raise AuthorizationError("Hermes cron state is invalid")
    return jobs


def _job_id(jobs: list[dict], name: str) -> str:
    matches = [job for job in jobs if job.get("name") == name and job.get("enabled") is True]
    if len(matches) != 1 or not _JOB_ID.fullmatch(str(matches[0].get("id") or "")):
        raise AuthorizationError("matching catalog cron job was not found")
    return matches[0]["id"]


def _prompt_is_current(jobs: list[dict], job_id: str, prompt: str) -> bool:
    return any(job.get("id") == job_id and job.get("prompt") == prompt for job in jobs)


def _edit(hermes: str, job_id: str, prompt: str) -> None:
    result = subprocess.run([hermes, "cron", "edit", job_id, "--prompt", prompt], text=True,
                            capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise AuthorizationError("Hermes cron prompt update failed")


def reconcile(*, refresh: bool = False) -> dict:
    config = _config()
    state_path, catalog_path = config.get("state_path"), config.get("catalog_path")
    if not isinstance(state_path, str) or not state_path or not isinstance(catalog_path, str) or not catalog_path:
        raise AuthorizationError("authorization companion is not configured")
    state = read_state(Path(os.path.expandvars(state_path)).expanduser())
    target = Path(os.path.expandvars(state_path)).expanduser()
    expected_digest = state_digest(state) if target.exists() else None
    catalog = _catalog(Path(os.path.expandvars(catalog_path)).expanduser())
    hermes, jobs = _hermes_bin(config), _cron_jobs()
    before = json.dumps(state, sort_keys=True)
    expire(state)
    approved = {}
    for item in state["authorizations"]["requests"].values():
        if item.get("status") == "approved":
            current = approved.get(item.get("job_name"))
            if current is None or (str(item.get("approved_at") or ""), str(item.get("created_at") or ""), item["id"]) > (
                    str(current.get("approved_at") or ""), str(current.get("created_at") or ""), current["id"]):
                approved[item["job_name"]] = item
    for job_name, item in approved.items():
        supersede_approved(state, job_name, item["id"], "authorization-expiry")
    expired_count, refreshed_count = 0, 0
    for item in state["authorizations"]["requests"].values():
        if item.get("status") != "approved":
            continue
        try:
            expired = datetime.fromisoformat(item["expires_at"]) <= now()
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthorizationError("authorization request has an invalid expiry") from exc
        job = catalog.get(item.get("job_name"))
        if not job:
            # A catalog job can be deliberately disabled or removed while a
            # time-limited approval is active. There is no remaining cron
            # prompt to restore, but the approval must not stay active or
            # make this safety-maintenance job fail forever.
            item["status"] = "expired"
            audit(state, item, "expired", "authorization-expiry")
            expired_count += 1
            continue
        job_id = _job_id(jobs, item["job_name"])
        approved_prompt = approval_prompt(job, item)
        if expired:
            prior_state = copy.deepcopy(state)
            item["status"] = "expired"
            audit(state, item, "expired", "authorization-expiry")
            try:
                write_state(target, state, expected_digest=expected_digest)
                _edit(hermes, job_id, job["prompt"])
            except Exception as expiry_error:
                try:
                    write_state(target, prior_state, expected_digest=state_digest(state))
                    _edit(hermes, job_id, approved_prompt)
                except Exception as rollback_error:
                    raise AuthorizationError("authorization expiry rollback failed") from rollback_error
                raise expiry_error
            expected_digest = state_digest(state)
            before = json.dumps(state, sort_keys=True)
            expired_count += 1
        elif refresh:
            if not _prompt_is_current(jobs, job_id, approved_prompt):
                _edit(hermes, job_id, approved_prompt)
                refreshed_count += 1
    if json.dumps(state, sort_keys=True) != before:
        write_state(target, state, expected_digest=expected_digest)
    return {"expired_count": expired_count, "refreshed_count": refreshed_count}


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore catalog prompts after authorization expiry")
    parser.add_argument("--refresh", action="store_true", help="reapply expiry-bearing prompts to unexpired approvals")
    args = parser.parse_args()
    try:
        print(json.dumps(reconcile(refresh=args.refresh), sort_keys=True))
        return 0
    except (AuthorizationError, OSError, subprocess.SubprocessError, ValueError):
        print("authorization expiry reconciliation failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
