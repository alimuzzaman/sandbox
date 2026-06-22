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


def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def info(msg: str) -> None:
    print(f"• {msg}")


def ok(msg: str) -> None:
    print(f"✓ {msg}")


class _RunResult:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run(cmd: list[str], check: bool = True, capture: bool = False, **kw):
    if not capture:
        print(f"  $ {' '.join(cmd)}")

    # Web-streaming path: only when not capturing (capture callers want the
    # buffered value back) and the flag is on. Merge stderr into stdout and
    # echo each line as it arrives so the console tails the real output.
    if _WEB_STREAM[0] and not capture:
        kw.pop("capture_output", None)
        proc = subprocess.Popen(cmd, text=True, cwd=str(ROOT),
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, **kw)
        collected = []
        for line in proc.stdout:
            collected.append(line)
            print(line, end="")            # → active _JobStream (web console)
        proc.wait()
        if check and proc.returncode != 0:
            sys.exit(proc.returncode)
        return _RunResult(proc.returncode, "".join(collected))

    res = subprocess.run(cmd, check=False, text=True,
                         capture_output=capture, cwd=str(ROOT), **kw)
    if check and res.returncode != 0:
        sys.exit(res.returncode)
    return res


def _pkg_manager() -> tuple[str, str] | tuple[None, None]:
    """Detect the platform package manager. Returns (name, sudo_prefix) where
    sudo_prefix is '' for brew (never sudo) or 'sudo ' for apt/dnf."""
    if shutil.which("brew"):
        return ("brew", "")
    if shutil.which("apt-get"):
        return ("apt", "sudo ")
    if shutil.which("dnf"):
        return ("dnf", "sudo ")
    return (None, None)


def _offer_install(label: str, cmd: str, *, verb: str = "Install") -> bool:
    """Offer to run a fix command for a missing/blocked prerequisite. Prompts
    (default No); on 'y' runs it and returns True on success. Non-interactive
    (no TTY) never runs — just prints the command and returns False, so CI/web
    contexts fall back to the printed hint. The user types any sudo password at
    the real prompt. `verb` tailors the wording (e.g. "Install", "Start")."""
    if not sys.stdin.isatty():
        print(f"      → run: {cmd}")
        return False
    try:
        ans = input(f"      {verb} now? [y/N] ({cmd}): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if ans not in ("y", "yes"):
        print(f"      → skipped. Run when ready: {cmd}")
        return False
    print(f"      running: {cmd}")
    # Run through the shell so pipes/&& and sudo prompts work normally.
    res = subprocess.run(cmd, shell=True)
    if res.returncode == 0:
        ok(f"{verb.lower().rstrip('e')}ed {label}")
        return True
    info(f"{verb.lower()} failed (exit {res.returncode}) — run manually: {cmd}")
    return False


def _prompt(label: str, default: str = "", secret: bool = False) -> str:
    """Prompt the user. Empty input keeps the default (or skips if no default)."""
    if default:
        hint = "•••••• (saved)" if secret else default
        suffix = f" [{hint}] (Enter to keep)"
    else:
        suffix = " (Enter to skip)"
    if secret:
        import getpass
        val = getpass.getpass(f"  {label}{suffix}: ").strip()
    else:
        val = input(f"  {label}{suffix}: ").strip()
    return val or default


def _sudo_env():
    """Environment for interactive sudo that pops a native macOS password dialog
    (via SUDO_ASKPASS) instead of prompting in the terminal. Falls back silently
    to the terminal if the helper/osascript isn't usable."""
    env = dict(os.environ)
    if sys.platform == "darwin" and ASKPASS_HELPER.exists():
        env["SUDO_ASKPASS"] = str(ASKPASS_HELPER)
    return env


def _sudo(cmd, reason=None, **kw):
    """Run `sudo <cmd>` using the GUI password dialog (sudo -A) when available.
    `cmd` is the argv AFTER 'sudo'. Use for INTERACTIVE sudo (first-time setup);
    passwordless calls keep using `sudo -n` directly.

    `reason` is a human explanation of WHY the password is needed. It's passed via
    `sudo -p` so it becomes the prompt the askpass dialog shows — the user sees a
    concrete sentence ("Sandbox needs admin rights to …") instead of a bare
    "Password:". Keep it one line; the dialog renders it verbatim."""
    flag = ["-A"] if (sys.platform == "darwin" and ASKPASS_HELPER.exists()) else []
    prompt = ["-p", reason] if reason else []
    return subprocess.run(["sudo", *flag, *prompt, *cmd], env=_sudo_env(), **kw)


def _run_cmd_capture(fn, args_ns) -> tuple[bool, str]:
    """Run a cmd_* handler, capturing its stdout/stderr and turning a die()
    (SystemExit) into a failed result instead of killing the server."""
    buf = io.StringIO()
    ok_flag = True
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            fn(load_config(), args_ns)
    except SystemExit as e:            # die() → non-zero exit
        ok_flag = (str(e) in ("0", "None"))
    except Exception as e:             # never let one action crash the server
        ok_flag = False
        buf.write(f"\nerror: {e}\n")
    return ok_flag, buf.getvalue()
