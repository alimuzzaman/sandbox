"""Portable process identity and scoped process-group signaling."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessIdentity:
    host_boot_id: str
    pid: int
    start_identity: str
    nonce_hash: str
    process_group_id: int | None = None


def _darwin_boot_id() -> str | None:
    """Observe the current kernel boot session without a host-name fallback."""
    try:
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=2, check=False,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"},
        )
        if result.returncode != 0 or not isinstance(result.stdout, bytes) or len(result.stdout) > 64:
            return None
        value = result.stdout.decode("ascii").strip().lower()
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None
    if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value) is None:
        return None
    return value


def _boot_id(proc_root: Path) -> str | None:
    path = proc_root / "sys/kernel/random/boot_id"
    try:
        return path.read_text().strip()
    except OSError:
        if platform.system() == "Darwin":
            # Never reinterpret an old hostname-bound record as this boot.
            # Missing native proof leaves process ownership unverified.
            return _darwin_boot_id()
        return None


def is_boot_session_identity(value: object) -> bool:
    """Legacy host names cannot prove a boot boundary or process loss."""
    return isinstance(value, str) and re.fullmatch(
        r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value) is not None


def process_absence_proven(pid: int) -> bool:
    """Separate a missing PID from unavailable/denied identity observation."""
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _linux_start_identity(pid: int, proc_root: Path) -> str | None:
    try:
        text = (proc_root / str(pid) / "stat").read_text()
    except OSError:
        return None
    close = text.rfind(")")
    if close < 0:
        return None
    fields = text[close + 2:].split()
    return fields[19] if len(fields) > 19 else None


def _portable_start_identity(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
            text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def capture_process_identity(pid: int, *, nonce: str = "", proc_root: Path = Path("/proc"),
                             process_group_id: int | None = None) -> ProcessIdentity | None:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    start = _linux_start_identity(pid, proc_root)
    if start is None:
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        start = _portable_start_identity(pid)
    if start is None:
        return None
    boot_id = _boot_id(proc_root)
    if boot_id is None:
        return None
    return ProcessIdentity(
        boot_id, pid, start,
        hashlib.sha256(nonce.encode()).hexdigest(),
        process_group_id=process_group_id,
    )


def verify_process_identity(expected: ProcessIdentity,
                            observed: ProcessIdentity | None) -> bool:
    return bool(observed and expected.host_boot_id == observed.host_boot_id
                and expected.pid == observed.pid
                and expected.start_identity == observed.start_identity
                and expected.nonce_hash == observed.nonce_hash)


def verify_owned_process_identity(expected: ProcessIdentity) -> bool:
    """Confirm that a durable child identity still owns its recorded process group."""
    if expected.process_group_id is None or expected.process_group_id <= 0:
        return False
    observed = capture_process_identity(
        expected.pid, nonce="", process_group_id=expected.process_group_id,
    )
    # Nonce material is intentionally not recoverable. Carry the durable hash into the
    # observed identity after PID/start/boot collection so verification remains exact.
    if observed is not None:
        observed = ProcessIdentity(
            observed.host_boot_id, observed.pid, observed.start_identity,
            expected.nonce_hash, observed.process_group_id,
        )
    return verify_process_identity(expected, observed)


def signal_owned_process_group(expected: ProcessIdentity, signal_number: int) -> bool:
    if not verify_owned_process_identity(expected):
        return False
    os.killpg(expected.process_group_id, signal_number)
    return True
