"""Per-user host supervision for the loopback activation authority."""

from __future__ import annotations

import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time


LABEL = "dev.sandbox.activation"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run(argv: list[str]) -> bool:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def install() -> dict[str, object]:
    root, sb = _repo_root(), _repo_root() / "sb"
    if sys.platform == "darwin":
        path = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"Label": LABEL, "ProgramArguments": [str(sb), "activation", "serve"],
                   "WorkingDirectory": str(root), "RunAtLoad": True, "KeepAlive": True,
                   "ProcessType": "Background"}
        path.write_bytes(plistlib.dumps(payload, sort_keys=True))
    elif sys.platform.startswith("linux"):
        path = Path.home() / ".config" / "systemd" / "user" / "sandbox-activation.service"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "[Unit]\nDescription=Sandbox request activation and idle scheduler\n\n"
            "[Service]\nType=simple\nRestart=on-failure\nRestartSec=2\n"
            f"WorkingDirectory={root}\nExecStart={sb} activation serve\n\n"
            "[Install]\nWantedBy=default.target\n", encoding="utf-8")
    else:
        return {"ok": False, "state": "unsupported"}
    path.chmod(0o600)
    return {"ok": True, "state": "installed", "path": str(path)}


def enable() -> dict[str, object]:
    installed = install()
    if not installed.get("ok"):
        return installed
    if sys.platform == "darwin":
        domain = f"gui/{os.getuid()}"
        path = str(installed["path"])
        _run(["launchctl", "bootout", domain, path])
        transition_ok = _run(["launchctl", "bootstrap", domain, path])
        transition_ok = _run(["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"]) and transition_ok
    else:
        _run(["systemctl", "--user", "daemon-reload"])
        transition_ok = _run(["systemctl", "--user", "enable", "--now", "sandbox-activation.service"])

    # The service health probe is the authority for an idempotent enable.  On
    # macOS, launchctl may return a non-zero transition result when the job was
    # already bootstrapped or is being replaced, even though kickstart leaves a
    # healthy supervisor running.  Reporting that healthy state as a failure
    # makes callers retry a working service and can create a false outage.
    from sandbox.core._domains import _activation_gateway_healthy
    deadline = time.monotonic() + 3
    healthy = False
    while time.monotonic() < deadline:
        if _activation_gateway_healthy():
            healthy = True
            break
        time.sleep(.1)
    if not healthy:
        healthy = _activation_gateway_healthy()
    ok = healthy
    result = {**installed, "ok": ok,
              "state": "enabled" if ok else "enable_failed"}
    if healthy and not transition_ok:
        result["warning"] = "supervisor transition returned non-zero; health probe is active"
    return result


def disable() -> dict[str, object]:
    if sys.platform == "darwin":
        path = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        ok = _run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)])
    elif sys.platform.startswith("linux"):
        ok = _run(["systemctl", "--user", "disable", "--now", "sandbox-activation.service"])
    else:
        return {"ok": False, "state": "unsupported"}
    return {"ok": ok, "state": "disabled" if ok else "disable_failed"}


def status() -> dict[str, object]:
    from sandbox.core._domains import _activation_gateway_healthy
    healthy = _activation_gateway_healthy()
    if sys.platform == "darwin":
        installed = (Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist").is_file()
        enabled = _run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"])
    elif sys.platform.startswith("linux"):
        installed = (Path.home() / ".config" / "systemd" / "user" /
                     "sandbox-activation.service").is_file()
        enabled = _run(["systemctl", "--user", "is-enabled", "sandbox-activation.service"])
    else:
        installed = enabled = False
    return {"ok": healthy, "state": "active" if healthy else "inactive",
            "installed": installed, "enabled": enabled, "healthy": healthy}


__all__ = ["disable", "enable", "install", "status"]
