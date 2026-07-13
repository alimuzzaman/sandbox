#!/usr/bin/env python3
"""Report Lenzora TODO progress without changing scheduler state."""
from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    candidates = (Path.cwd() / "TODO.md", Path.cwd() / "todo.md")
    todo = next((path for path in candidates if path.is_file()), None)
    if todo is None:
        print("todo-monitor: TODO file missing", file=sys.stderr)
        return 2
    try:
        text = todo.read_text()
    except OSError as exc:
        print(f"todo-monitor: read failed ({exc.__class__.__name__})", file=sys.stderr)
        return 2
    checked = sum(bool(re.match(r"^\s*- \[[xX]\]", line)) for line in text.splitlines())
    unchecked = sum(bool(re.match(r"^\s*- \[ \]", line)) for line in text.splitlines())
    print(f"TODO_TOTAL={checked + unchecked}")
    print(f"TODO_REMAINING={unchecked}")
    print("TODO_COMPLETE" if unchecked == 0 else "TODO_PENDING")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
