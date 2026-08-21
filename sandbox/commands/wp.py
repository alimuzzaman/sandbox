from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
import types as _types
from contextlib import contextmanager
import io
import threading
from contextlib import redirect_stdout, redirect_stderr



from sandbox.core import *  # noqa: F401,F403

from sandbox.registry import register
from sandbox.application.context import (
    managed_native_instance_selected, preflight_instance_capability,
)


def _timeout_stream(value) -> str:
    """Normalize partial ``TimeoutExpired`` output without changing streams."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _print_stream(value, *, stderr: bool = False) -> None:
    if not value:
        return
    print(value, end="" if value.endswith("\n") else "\n", file=sys.stderr if stderr else sys.stdout)



def cmd_wp(cfg, args) -> None:
    error = preflight_instance_capability(cfg, args.resolved_instance, "wordpress.cli")
    if error is not None:
        die(error.message)
    if not args.passthrough:
        die("usage: ./sb wp <wp-cli args>")
    pt = list(args.passthrough)
    # `./sb wp --async <args>` runs the command as a background job (spec 004).
    if getattr(args, "run_async", False):
        if pt and pt[0] == "--":
            pt = pt[1:]
        if not pt:
            die("usage: ./sb wp --async <wp-cli args>")
        if managed_native_instance_selected(args.resolved_instance) is not None:
            die("managed-native async wp requires an adapter-native durable "
                "transport; host/legacy job fallback is disabled")
        from sandbox.commands.jobs import launch_job
        jid = launch_job(args.resolved_instance, pt)
        print(jid)
        print(f"started background job {jid}", file=sys.stderr)
        print(f"  poll:   ./sb job {jid}", file=sys.stderr)
        print(f"  follow: ./sb job {jid} --follow", file=sys.stderr)
        print(f"  kill:   ./sb job {jid} --kill", file=sys.stderr)
        return
    if pt and pt[0] == "--":
        pt = pt[1:]
    if not pt:
        die("usage: ./sb wp <wp-cli args>")
    # Keep direct command namespaces (including out-of-tree callers/tests)
    # aligned with the parser's bounded synchronous default.
    timeout = getattr(args, "timeout", 60)
    try:
        result = wpcli(pt, instance=args.resolved_instance, check=False,
                       capture=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # subprocess.run may return partial output on a timeout. Preserve each
        # stream exactly where it belongs, then make the completion uncertainty
        # explicit: a caller must inspect before retrying or choose --async.
        _print_stream(_timeout_stream(getattr(exc, "stdout", None)
                                      or getattr(exc, "output", None)))
        _print_stream(_timeout_stream(getattr(exc, "stderr", None)), stderr=True)
        die(
            f"wp command timed out after {timeout} seconds; completion is "
            "unknown—inspect state before retrying, or use --async for long work",
            code=124,
        )
    stdout = getattr(result, "stdout", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)
    returncode = int(getattr(result, "returncode", 0) or 0)
    if returncode:
        die(f"wp command failed with exit code {returncode}", code=returncode)

def cmd_seed(cfg, args) -> None:
    if not args.file:
        die("usage: ./sb seed <file-in-runtime/seeds>")
    # Containers mount runtime/seeds at /seeds; herd reads the host path.
    seed = (str(RUNTIME_DIR / "seeds" / args.file)
            if _is_herd_instance(args.resolved_instance)
            else f"/seeds/{args.file}")
    wpcli(["import", seed, "--authors=create"],
          instance=args.resolved_instance)

def cmd_visit(cfg, args) -> None:
    """Headless-browser inspection of a URL.

    All flags are forwarded to tools/visit/visit.py; this wrapper just
    ensures the venv + Chromium are present, then exec's the runner so
    the user sees its JSON output and exit code directly.
    """
    py = ensure_tools_venv()
    script = TOOLS_DIR / "visit" / "visit.py"
    cmd = [str(py), str(script), *args.passthrough]
    os.execv(str(py), cmd)

register({
    'wp': cmd_wp,
    'seed': cmd_seed,
    'visit': cmd_visit,
})
