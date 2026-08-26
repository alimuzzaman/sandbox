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


def _reject_ignored_post_list_search(argv: list[str]) -> None:
    """Fail closed for the common, but unsupported, ``post list --search`` spelling.

    ``wp post list`` forwards query arguments to ``WP_Query``.  ``search`` is
    not a ``WP_Query`` query var, so WP-CLI accepts the option but silently
    returns an unfiltered list.  That is especially dangerous when the output
    feeds a delete/update loop.  Keep the raw passthrough contract for every
    other command, but make this known unsafe spelling explicit and point to
    the supported ``s`` query var instead.
    """
    if len(argv) < 2 or argv[:2] != ["post", "list"]:
        return
    for index, token in enumerate(argv[2:], start=2):
        if token == "--search":
            value = argv[index + 1] if index + 1 < len(argv) else ""
            if not value or value.startswith("-"):
                die("wp post list does not support --search; no command was "
                    "executed. Use --s=<term> or validate explicit IDs.")
            die("wp post list does not support --search; WP_Query would "
                "ignore it and return an unfiltered list. Use "
                "--s=<term> or validate explicit IDs before destructive "
                "actions.")
        if token.startswith("--search="):
            die("wp post list does not support --search; WP_Query would "
                "ignore it and return an unfiltered list. Use "
                "--s=<term> or validate explicit IDs before destructive "
                "actions.")


def _reject_redundant_wp_token(argv: list[str]) -> None:
    """Explain the common ``sb wp -- wp ...`` double-wrapper typo."""
    if argv and argv[0] == "wp":
        die("do not repeat the `wp` executable after `sb wp`; pass the "
            "WP-CLI command directly, for example: "
            "`./sb wp -- --require=FILE eval-file SCRIPT.php`. "
            "No command was executed.")

def _is_option_get_probe(argv: list[str]) -> bool:
    """Return whether argv is the narrow optional-option probe contract."""
    return len(argv) >= 3 and argv[:2] == ["option", "get"]


def _reports_missing_option(stdout: str, stderr: str) -> bool:
    """Recognize WP-CLI's missing-option diagnostic without hiding transport errors."""
    text = f"{stdout}\n{stderr}".lower()
    return bool(
        re.search(r"could not get.{0,160}\boption\b", text)
        or re.search(r"\boption\b.{0,160}(?:does not exist|not found)", text)
    )


def cmd_wp(cfg, args) -> None:
    error = preflight_instance_capability(cfg, args.resolved_instance, "wordpress.cli")
    if error is not None:
        die(error.message)
    if not args.passthrough:
        die("usage: ./sb wp <wp-cli args>")
    pt = list(args.passthrough)
    if pt and pt[0] == "--":
        pt = pt[1:]
    if not pt:
        die("usage: ./sb wp <wp-cli args>")
    _reject_redundant_wp_token(pt)
    if getattr(args, "allow_missing", False) and not _is_option_get_probe(pt):
        die("--allow-missing is only valid with `option get KEY`; "
            "no command was executed.")
    _reject_ignored_post_list_search(pt)
    # `./sb wp --async <args>` runs the command as a background job (spec 004).
    if getattr(args, "run_async", False):
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
    returncode = int(getattr(result, "returncode", 0) or 0)
    if (getattr(args, "allow_missing", False) and returncode
            and _reports_missing_option(stdout, stderr)):
        print('{"present":false,"value":null}')
        return
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)
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
    # The runner already emits one JSON report.  ``passthrough`` is a
    # REMAINDER argument, so a conventional ``--json`` placed after the URL
    # would otherwise be forwarded to the runner and rejected by its parser.
    # Accept it as a harmless output selector for consistency with the other
    # read-only CLI commands.
    passthrough = [value for value in args.passthrough if value != "--json"]
    cmd = [str(py), str(script), *passthrough]
    os.execv(str(py), cmd)

register({
    'wp': cmd_wp,
    'seed': cmd_seed,
    'visit': cmd_visit,
})
