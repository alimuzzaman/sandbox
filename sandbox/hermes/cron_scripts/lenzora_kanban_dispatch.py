#!/usr/bin/env python3
"""Dispatch at most one ready Lenzora task and fail truthfully."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    hermes = str(Path.home() / ".local/bin/hermes")
    env = {**os.environ, "HERMES_HOME": str(Path.home() / ".hermes")}
    try:
        result = subprocess.run(
            [hermes, "kanban", "--board", "lenzora-coding", "dispatch", "--max", "1",
             "--failure-limit", "1", "--json"],
            env=env, text=True, capture_output=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"lenzora-dispatch: unavailable ({exc.__class__.__name__})", file=sys.stderr)
        return 2
    if result.returncode:
        print("lenzora-dispatch: command failed", file=sys.stderr)
        return result.returncode or 2
    print(result.stdout.strip() or "lenzora-dispatch: no ready task")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
