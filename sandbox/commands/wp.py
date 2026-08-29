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


def _disable_help_pager(argv: list[str]) -> list[str]:
    """Keep WP-CLI help noninteractive inside the container boundary.

    The managed WordPress image does not promise a pager binary. Add WP-CLI's
    explicit no-pager switch only for the help command, and preserve an
    operator's explicit pager choice when one was supplied.
    """
    if not argv or "--no-pager" in argv or "--pager" in argv:
        return list(argv)
    for index, token in enumerate(argv):
        if token == "help":
            return [*argv, "--no-pager"]
        if not isinstance(token, str) or not token.startswith("-"):
            break
    return list(argv)


def _clean_eval_parse_diagnostic(argv: list[str], stdout: str, stderr: str) -> tuple[str, str]:
    """Remove only WP's duplicate generic wrapper around an eval parse error."""
    if not argv or argv[0] != "eval":
        return stdout, stderr
    combined = f"{stdout}\n{stderr}"
    if not re.search(r"\b(?:php\s+)?parse\s+error\b", combined, flags=re.IGNORECASE):
        return stdout, stderr
    wrapper = re.compile(
        r"(?im)^[ \t]*(?:error:\s*)?there has been a critical error on this website\.?[ \t]*(?:\r?\n|$)"
    )
    return wrapper.sub("", stdout), wrapper.sub("", stderr)


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


_PLUGIN_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _plugin_deactivate_request(argv: list[str]) -> tuple[list[str], int] | None:
    """Parse the safe, positional part of ``plugin deactivate``.

    The partial-result mode must know exactly which operands are plugin slugs;
    reject option-before-slug and ``--all`` forms rather than guessing around
    WP-CLI options that may consume a following value.
    """
    if len(argv) < 3 or argv[:2] != ["plugin", "deactivate"]:
        return None
    slugs: list[str] = []
    index = 2
    while index < len(argv) and not argv[index].startswith("-"):
        slug = argv[index]
        if not _PLUGIN_SLUG_RE.fullmatch(slug):
            return None
        slugs.append(slug)
        index += 1
    if not slugs:
        return None
    return slugs, index


def _plugin_list_state(instance: str) -> dict[str, str] | None:
    """Read installed plugin state for an explicit partial deactivation."""
    try:
        result = wpcli(
            ["plugin", "list", "--fields=name,status", "--format=json",
             "--skip-plugins"],
            instance=instance, check=False, capture=True,
        )
    except Exception:
        return None
    if getattr(result, "returncode", None) not in (None, 0):
        return None
    output = getattr(result, "stdout", "") or ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    try:
        rows = json.loads(output)
    except (TypeError, ValueError):
        return None
    if not isinstance(rows, list):
        return None
    states: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        name, status = row.get("name"), row.get("status")
        if not isinstance(name, str) or not _PLUGIN_SLUG_RE.fullmatch(name):
            return None
        if not isinstance(status, str) or not status.strip():
            return None
        states[name] = status.strip().lower()
    return states


def _run_plugin_deactivate_allow_missing(argv: list[str], instance: str) -> None:
    """Run an explicit, typed partial deactivation without masking failures."""
    parsed = _plugin_deactivate_request(argv)
    if parsed is None:
        die("--allow-missing for plugin deactivate requires one or more plugin "
            "slugs before WP-CLI options; --all is not supported")
    requested, option_start = parsed
    states = _plugin_list_state(instance)
    if states is None:
        die("could not verify the installed plugin list; no deactivation was "
            "attempted")
    missing = sorted({slug for slug in requested if slug not in states})
    inactive = sorted({slug for slug in requested if states.get(slug) == "inactive"})
    skipped = set(missing) | set(inactive)
    attempted = []
    for slug in requested:
        if slug not in skipped and slug not in attempted:
            attempted.append(slug)
    warnings = False
    if attempted:
        command = [*argv[:2], *attempted, *argv[option_start:]]
        try:
            result = wpcli(command, instance=instance, check=False, capture=True)
        except Exception:
            result = None
        if result is None or getattr(result, "returncode", None) not in (None, 0):
            _print_stream(getattr(result, "stdout", None) if result else None)
            _print_stream(getattr(result, "stderr", None) if result else None, stderr=True)
            code = getattr(result, "returncode", 1) if result else 1
            die(f"wp command failed with exit code {code}", code=code or 1)
        warnings = bool((getattr(result, "stderr", "") or "").strip())
    status = "partial" if missing else "complete"
    print(json.dumps({
        "ok": True,
        "status": status,
        "command": ["plugin", "deactivate"],
        "requested": requested,
        "deactivated": attempted,
        "absent": missing,
        "already_inactive": inactive,
        "warnings": warnings,
    }, sort_keys=True))


def _stage_host_package_paths(
    argv: list[str], instance: str, project_root: str | Path | None = None,
) -> tuple[list[str], list[Path]]:
    """Expose supported host file paths through the Docker download-cache mount."""
    supported = {
        ("plugin", "install"),
        ("theme", "install"),
        ("media", "import"),
        ("eval-file",),
    }
    command = (
        tuple(argv[:2])
        if argv[:2] in (["plugin", "install"], ["theme", "install"], ["media", "import"])
        else tuple(argv[:1])
    )
    if _is_herd_instance(instance) or len(argv) < 2 or command not in supported:
        return list(argv), []
    staged: list[Path] = []
    rewritten = list(argv)
    cache = Path(RUNTIME_DIR) / "dl-cache" / "wp-http"
    base = Path(project_root).expanduser() if project_root else Path.cwd()
    operand_start = 1 if command == ("eval-file",) else 2
    for index, token in enumerate(argv[operand_start:], start=operand_start):
        if token == "--" or token.startswith("-"):
            continue
        source = Path(token).expanduser()
        if not source.is_absolute():
            source = (base / source).resolve()
        try:
            if not source.is_file() or source.is_symlink():
                continue
            size = source.stat().st_size
            if size <= 0 or size > 512 * 1024 * 1024:
                raise ValueError("local file must be a regular file no larger than 512 MiB")
            cache.mkdir(parents=True, exist_ok=True)
            temporary = cache / f".sandbox-host-package-{os.getpid()}-{len(staged)}"
            shutil.copyfile(source, temporary)
            temporary.chmod(0o644)
            staged.append(temporary)
            rewritten[index] = f"/sandbox-dl-cache/{temporary.name}"
        except (OSError, ValueError) as exc:
            for path in staged:
                path.unlink(missing_ok=True)
            die(f"could not stage local file for the container: {exc}")
    return rewritten, staged


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
    pt = _disable_help_pager(pt)
    allow_missing = bool(getattr(args, "allow_missing", False))
    plugin_partial = _plugin_deactivate_request(pt)
    if allow_missing and not (_is_option_get_probe(pt) or plugin_partial):
        die("--allow-missing is only valid with `option get KEY` or an explicit "
            "`plugin deactivate SLUG...` command; no command was executed.")
    _reject_ignored_post_list_search(pt)
    if allow_missing and plugin_partial:
        if getattr(args, "run_async", False):
            die("--allow-missing plugin deactivation requires synchronous state "
                "inspection; remove --async or use a normal WP-CLI command")
        _run_plugin_deactivate_allow_missing(pt, args.resolved_instance)
        return
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
    pt, staged_packages = _stage_host_package_paths(
        pt, args.resolved_instance, getattr(args, "project_dir", None),
    )
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
    finally:
        for path in staged_packages:
            path.unlink(missing_ok=True)
    stdout = getattr(result, "stdout", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    returncode = int(getattr(result, "returncode", 0) or 0)
    stdout, stderr = _clean_eval_parse_diagnostic(pt, stdout, stderr)
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
