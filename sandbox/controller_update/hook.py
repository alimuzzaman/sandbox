"""CLI entry for controller auto-update (spec 072, contracts/cli-behavior.md).

``maybe_auto_update`` runs right after argument parsing, before any command
effect. It returns to let the command continue, replaces the process with a
re-run of the same command on the updated code, or exits 1 when an update
could not be rolled back. It never raises otherwise.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Mapping, Sequence, TextIO

from sandbox.controller_update.models import (
    AlreadyMoved, Decision, NoAction, RolledBack, Skip, Stranded, Updated,
)

ENV_SWITCH = "SANDBOX_AUTO_UPDATE"
ENV_REEXEC_GUARD = "SANDBOX_AUTO_UPDATE_REEXEC"
ENV_JOB_CHILD = "SANDBOX_DURABLE_JOB_ID"
CONFIG_KEY = "controller_auto_update"  # under `defaults:`

# Commands that always address a remote, and commands that may route to the
# project's declared remote unless run with --local.
REMOTE_FAMILY = frozenset({"remote", "deploy", "host", "preview", "hermes"})
REMOTE_ROUTED = frozenset({"test", "e2e", "ci", "job", "jobs"})

_OFF = frozenset({"0", "false", "no", "off"})
_ON = frozenset({"1", "true", "yes", "on"})


def env_policy(environ: Mapping[str, str]) -> bool | None:
    """``False``/``True`` when the env var decides, ``None`` when it does not."""
    value = (environ.get(ENV_SWITCH) or "").strip().lower()
    if value in _OFF:
        return False
    if value in _ON:
        return True
    return None


def is_remote_capable(args) -> bool:
    cmd = getattr(args, "cmd", None)
    if cmd in REMOTE_FAMILY:
        return True
    remote = getattr(args, "remote", None)
    if isinstance(remote, str) and remote.strip():
        return True
    return cmd in REMOTE_ROUTED and not getattr(args, "local", False)


MACHINE_FORMATS = frozenset({"json", "jsonl"})


def is_machine_output(args) -> bool:
    """Every CLI machine-output switch: ``--json`` and ``--format json|jsonl``.

    ``tests/test_controller_update_hook.py`` scans ``sandbox/cli.py`` and the
    command modules for other output-format options, so a new one fails a
    test until it is added here.
    """
    if getattr(args, "json", False):
        return True
    return getattr(args, "format", None) in MACHINE_FORMATS


def is_sb_entry(argv: Sequence[str], entry: Path, main_file: str | None = None) -> bool:
    """True only for a process executing the real ``sb`` entry.

    Like ``sandbox.core._config._is_cli_process``: both ``argv[0]`` and the
    ``__main__`` module must be the entry, so a test runner or another tool
    that imports the CLI with an ``sb``-shaped argv never updates.
    """
    if main_file is None:
        main_file = getattr(sys.modules.get("__main__"), "__file__", None) or ""
    if not argv or not argv[0] or not main_file:
        return False
    try:
        target = Path(entry).resolve()
        return Path(argv[0]).resolve() == target and Path(main_file).resolve() == target
    except OSError:
        return False


def config_enabled() -> bool:
    """``defaults.controller_auto_update`` from the merged machine config."""
    from sandbox.core._config import load_config
    defaults = load_config().get("defaults") or {}
    value = defaults.get(CONFIG_KEY, True) if isinstance(defaults, dict) else True
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _OFF:
            return False
        return True
    return value is not False


def _default_engine(root: Path, *, is_enabled: Callable[[], bool]) -> Decision:
    from sandbox.controller_update import engine
    return engine.run(root, is_enabled=is_enabled)


def _say(stream: TextIO, line: str) -> None:
    try:
        stream.write(line.rstrip("\n") + "\n")
        stream.flush()
    except (OSError, ValueError):
        pass


def maybe_auto_update(args, argv: Sequence[str] | None = None, *,
                      environ: Mapping[str, str] | None = None,
                      root: Path | None = None, entry: Path | None = None,
                      execve: Callable = os.execve, python: str | None = None,
                      engine_run: Callable[..., Decision] = _default_engine,
                      config_enabled: Callable[[], bool] = config_enabled,
                      stderr: TextIO | None = None, stdout: TextIO | None = None,
                      main_file: str | None = None) -> None:
    """Run the auto-update check for one parsed command.

    ``stdout`` is accepted only so tests can prove nothing is written there;
    every notice goes to ``stderr``.
    """
    argv = list(sys.argv if argv is None else argv)
    environ = os.environ if environ is None else environ
    stderr = sys.stderr if stderr is None else stderr
    if root is None or entry is None:
        from sandbox.core._paths import ENTRY, ROOT
        root = ROOT if root is None else root
        entry = ENTRY if entry is None else entry

    # Gating order: first match wins, all silent (contracts/cli-behavior.md).
    if environ.get(ENV_REEXEC_GUARD) or environ.get(ENV_JOB_CHILD):
        return
    if is_machine_output(args):
        return
    if not is_sb_entry(argv, entry, main_file):
        return
    decided = env_policy(environ)
    if decided is False or not is_remote_capable(args):
        return

    def enabled() -> bool:
        if decided is not None:
            return decided
        try:
            return bool(config_enabled())
        except (Exception, SystemExit):  # an unreadable config must not stop the command
            return True

    try:
        decision = engine_run(Path(root), is_enabled=enabled)
    except Exception:
        return

    if isinstance(decision, NoAction):
        return
    if isinstance(decision, Skip | RolledBack):
        _say(stderr, decision.notice)
        return
    if isinstance(decision, Stranded):
        _say(stderr, decision.notice)
        raise SystemExit(1)
    if isinstance(decision, Updated):
        for line in decision.notices:
            _say(stderr, line)
        _say(stderr, f"sb: controller updated {decision.old[:7]} -> {decision.new[:7]} "
                     f"from {decision.upstream}; re-running the command.")
    elif isinstance(decision, AlreadyMoved):
        _say(stderr, f"sb: controller moved to {decision.new[:7]} by another process; "
                     "re-running the command.")
    else:
        return
    env = dict(environ)
    env[ENV_REEXEC_GUARD] = "1"
    try:
        # Same interpreter (the venv the refresh just converged), same entry,
        # same arguments, plus the guard that forbids a second update.
        interpreter = python or sys.executable
        execve(interpreter, [interpreter, str(entry), *argv[1:]], env)
    except OSError as exc:
        # The checkout already moved; continuing would mix old loaded modules
        # with new files on disk.
        _say(stderr, f"sb: could not re-run the command on the updated controller "
                     f"({exc.strerror or exc}); run it again.")
        raise SystemExit(1) from None
