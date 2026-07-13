#!/usr/bin/env python3
"""Requeue at most one marked Codex quota block after a bounded cooldown."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BOARD = "lenzora-coding"
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
HERMES = str(Path.home() / ".local/bin/hermes")
STATE = HERMES_HOME / "sandbox-cron-state" / "codex-quota-requeue.json"
SIGNAL = re.compile(r"(?i)(?:codex-quota:|\b429\b|rate.?limit|quota exceeded|usage limit)")


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [HERMES, "kanban", "--board", BOARD, *args],
        env={**os.environ, "HERMES_HOME": str(HERMES_HOME)}, text=True,
        capture_output=True, timeout=30, check=False,
    )


def main() -> int:
    try:
        listed = run("list", "--status", "blocked", "--json")
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"quota-requeue: inspection unavailable ({exc.__class__.__name__})", file=sys.stderr)
        return 2
    if listed.returncode:
        print("quota-requeue: blocked-task inspection failed", file=sys.stderr)
        return listed.returncode or 2
    try:
        payload = json.loads(listed.stdout)
        tasks = payload if isinstance(payload, list) else payload.get("tasks", [])
        if not isinstance(tasks, list):
            raise ValueError
    except (json.JSONDecodeError, AttributeError, ValueError):
        print("quota-requeue: malformed blocked-task response", file=sys.stderr)
        return 2
    candidate = next((item for item in tasks if isinstance(item, dict) and SIGNAL.search(json.dumps(item))), None)
    if not candidate:
        print("quota-requeue: no marked quota blocks")
        return 0
    identifier = str(candidate.get("id") or candidate.get("task_id") or "")
    if not identifier:
        print("quota-requeue: marked task has no id", file=sys.stderr)
        return 2
    try:
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
    except (OSError, json.JSONDecodeError):
        state = {}
    now = time.time()
    if now < float(state.get(identifier, 0)):
        print("quota-requeue: cooldown active")
        return 0
    try:
        released = run("unblock", identifier, "--reason", "bounded Codex quota probe after 30m")
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"quota-requeue: unblock unavailable ({exc.__class__.__name__})", file=sys.stderr)
        return 2
    if released.returncode:
        print("quota-requeue: unblock failed", file=sys.stderr)
        return released.returncode or 2
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.write_text(json.dumps({identifier: now + 1800}, sort_keys=True) + "\n")
    STATE.chmod(0o600)
    print(f"quota-requeue: released {identifier}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
