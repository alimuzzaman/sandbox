#!/usr/bin/env python3
"""Run the local authorization expiry revoker without exposing arbitrary input."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    script = Path.home() / ".hermes" / "plugins" / "sandbox-authorizations" / "expire.py"
    try:
        result = subprocess.run([sys.executable, str(script), "--refresh"], text=True, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"authorization-expiry: unavailable ({exc.__class__.__name__})", file=sys.stderr)
        return 2
    if result.returncode:
        print("authorization-expiry: reconciliation failed", file=sys.stderr)
        return result.returncode or 2
    print(result.stdout.strip() or "authorization-expiry: no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
