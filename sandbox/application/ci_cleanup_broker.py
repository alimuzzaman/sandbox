#!/usr/bin/python3
"""Fixed Linux privileged boundary for terminal CI workspace removal.

The broker has no caller-selected absolute paths. Its installed entry point is
root-owned, its roots are pinned by a root-owned configuration file, and every
removal first moves the exact checked entry into a root-only quarantine.
"""

from __future__ import annotations

import ctypes
import base64
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import uuid
from contextlib import contextmanager
from typing import Any


HELPER_PATH = Path("/usr/local/libexec/sandbox-ci-cleanup-helper")
CONFIG_ROOT = Path("/etc/sandbox-ci-cleanup")
QUARANTINE_ROOT = Path("/var/lib/sandbox-ci-cleanup")
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_NAMESPACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
_MAX_CHECKOUT_ENTRIES = 1_000_000
_MAX_CHECKOUT_DEPTH = 256
_MAX_METADATA_BYTES = 64 * 1024
_MAX_JOURNAL_BYTES = 32 * 1024
_MAX_JOURNAL_IDS = 512
_MAX_JOURNAL_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_JOURNAL_FILES = _MAX_JOURNAL_IDS * 3 + 2
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")


class CiCleanupBrokerError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _identity(value: Any) -> tuple[int, int]:
    if (not isinstance(value, dict) or set(value) != {"device", "inode"}
            or any(type(value[key]) is not int or value[key] < 0
                   for key in ("device", "inode"))):
        raise CiCleanupBrokerError("cleanup_identity_invalid")
    return value["device"], value["inode"]


def _invoke(action: str, *args: str) -> dict[str, Any]:
    if not sys.platform.startswith("linux"):
        raise CiCleanupBrokerError("cleanup_broker_unsupported")
    try:
        info = HELPER_PATH.lstat()
    except OSError as exc:
        raise CiCleanupBrokerError("cleanup_broker_unavailable") from exc
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o755 or info.st_nlink != 1):
        raise CiCleanupBrokerError("cleanup_broker_unsafe")
    try:
        result = subprocess.run(
            ("/usr/bin/sudo", "-n", str(HELPER_PATH), action, *args),
            capture_output=True, text=True, timeout=45, check=False,
            close_fds=True,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CiCleanupBrokerError("cleanup_broker_unavailable") from exc
    try:
        payload = json.loads(result.stdout) if result.stdout else {}
    except (TypeError, ValueError):
        payload = {}
    if (result.returncode != 0 or not isinstance(payload, dict)
            or payload.get("ok") is not True):
        code = payload.get("code") if isinstance(payload, dict) else None
        if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", code):
            code = "cleanup_broker_failed"
        raise CiCleanupBrokerError(code)
    return payload


def begin_cleanup(cleanup_id: str, request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cleanup_id, str) or _HEX32.fullmatch(cleanup_id) is None:
        raise CiCleanupBrokerError("cleanup_identity_invalid")
    if not isinstance(request, dict):
        raise CiCleanupBrokerError("cleanup_request_invalid")
    try:
        encoded = base64.urlsafe_b64encode(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).decode("ascii").rstrip("=")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CiCleanupBrokerError("cleanup_request_invalid") from exc
    if len(encoded) > 48 * 1024:
        raise CiCleanupBrokerError("cleanup_request_too_large")
    return _invoke("begin-cleanup", cleanup_id, encoded)


def resume_cleanup_checkout(cleanup_id: str) -> dict[str, Any]:
    if not isinstance(cleanup_id, str) or _HEX32.fullmatch(cleanup_id) is None:
        raise CiCleanupBrokerError("cleanup_identity_invalid")
    return _invoke("resume-checkout", cleanup_id)


def resume_cleanup_metadata(cleanup_id: str) -> dict[str, Any]:
    if not isinstance(cleanup_id, str) or _HEX32.fullmatch(cleanup_id) is None:
        raise CiCleanupBrokerError("cleanup_identity_invalid")
    return _invoke("resume-metadata", cleanup_id)


def acknowledge_cleanup(cleanup_id: str, workspace_id: str,
                        authority_digest: str,
                        request: dict[str, Any]) -> dict[str, Any]:
    if (not isinstance(cleanup_id, str) or _HEX32.fullmatch(cleanup_id) is None
            or not isinstance(workspace_id, str)
            or not re.fullmatch(r"ws_[0-9a-f]{32}", workspace_id)
            or not isinstance(authority_digest, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", authority_digest)
            or not isinstance(request, dict)):
        raise CiCleanupBrokerError("cleanup_identity_invalid")
    try:
        encoded = base64.urlsafe_b64encode(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).decode("ascii").rstrip("=")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CiCleanupBrokerError("cleanup_request_invalid") from exc
    if len(encoded) > 48 * 1024:
        raise CiCleanupBrokerError("cleanup_request_too_large")
    return _invoke("ack-cleanup", cleanup_id, workspace_id, authority_digest, encoded)


def finalize_checkout(operation_name: str, operation_identity: dict[str, int],
                      checkout_identity: dict[str, int]) -> None:
    """Reject historical finalization requests that lack a durable journal."""
    raise CiCleanupBrokerError("cleanup_journal_required")


def recover_quarantined_checkout(checkout_identity: dict[str, int]) -> None:
    """Reject inode-only recovery, which cannot bind a job or workspace."""
    raise CiCleanupBrokerError("cleanup_journal_required")


def retire_artifact(name: str, identity: dict[str, int], digest: str,
                    size_bytes: int) -> int:
    if (not isinstance(name, str) or re.fullmatch(r"[0-9a-f]{32}\.tar\.gz", name) is None
            or not isinstance(digest, str) or _HEX64.fullmatch(digest) is None
            or type(size_bytes) is not int or not 0 <= size_bytes <= _MAX_ARTIFACT_BYTES):
        raise CiCleanupBrokerError("cleanup_artifact_invalid")
    device, inode = _identity(identity)
    result = _invoke("retire-artifact", name, str(device), str(inode), digest,
                     str(size_bytes))
    reclaimed = result.get("reclaimed_bytes")
    return reclaimed if type(reclaimed) is int and reclaimed >= 0 else size_bytes


def remove_workspace_metadata(namespace: str, label: str,
                              directory_identity: dict[str, int],
                              file_identity: dict[str, int]) -> None:
    """Reject metadata deletion without the shared immutable cleanup intent."""
    raise CiCleanupBrokerError("cleanup_journal_required")


def _open_absolute(path: Path) -> int:
    if not path.is_absolute():
        raise CiCleanupBrokerError("cleanup_root_invalid")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                            os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_child_dir(parent_fd: int, name: str, *, owner: int | None = None,
                    mode: int | None = None) -> int:
    if name in {"", ".", ".."} or "/" in name:
        raise CiCleanupBrokerError("cleanup_path_invalid")
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                 os.O_CLOEXEC, dir_fd=parent_fd)
    info = os.fstat(fd)
    if (not stat.S_ISDIR(info.st_mode)
            or owner is not None and info.st_uid != owner
            or mode is not None and stat.S_IMODE(info.st_mode) != mode):
        os.close(fd)
        raise CiCleanupBrokerError("cleanup_path_unsafe")
    return fd


def _rename_noreplace(source_fd: int, source: str,
                      target_fd: int, target: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise CiCleanupBrokerError("cleanup_atomic_rename_unavailable")
    result = renameat2(source_fd, os.fsencode(source), target_fd,
                       os.fsencode(target), 1)
    if result == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise CiCleanupBrokerError("cleanup_target_exists")
    raise OSError(error, os.strerror(error))


def _read_config(owner_uid: int, config_root: Path = CONFIG_ROOT) -> dict[str, Any]:
    if type(owner_uid) is not int or owner_uid <= 0:
        raise CiCleanupBrokerError("cleanup_caller_invalid")
    root_fd = _open_absolute(config_root)
    try:
        root = os.fstat(root_fd)
        if root.st_uid != 0 or stat.S_IMODE(root.st_mode) & 0o022:
            raise CiCleanupBrokerError("cleanup_config_unsafe")
        fd = os.open(f"{owner_uid}.json", os.O_RDONLY | os.O_NOFOLLOW |
                     os.O_CLOEXEC, dir_fd=root_fd)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_size > 4096):
                raise CiCleanupBrokerError("cleanup_config_unsafe")
            chunks = []
            while len(b"".join(chunks)) <= 4096:
                block = os.read(fd, 4097)
                if not block:
                    break
                chunks.append(block)
            payload = json.loads(b"".join(chunks))
        finally:
            os.close(fd)
    finally:
        os.close(root_fd)
    required = {"schema_version", "owner_uid", "roots"}
    if (not isinstance(payload, dict) or set(payload) != required
            or payload.get("schema_version") != 1
            or payload.get("owner_uid") != owner_uid
            or not isinstance(payload.get("roots"), dict)
            or set(payload["roots"]) != {"deployment", "legacy", "artifacts"}):
        raise CiCleanupBrokerError("cleanup_config_invalid")
    roots: dict[str, tuple[Path, tuple[int, int]]] = {}
    for key, value in payload["roots"].items():
        if (not isinstance(value, dict) or set(value) != {"path", "device", "inode"}
                or not isinstance(value.get("path"), str)
                or type(value.get("device")) is not int
                or type(value.get("inode")) is not int):
            raise CiCleanupBrokerError("cleanup_config_invalid")
        roots[key] = (Path(value["path"]), (value["device"], value["inode"]))
    payload["roots"] = roots
    return payload


def _open_pinned_root(config: dict[str, Any], key: str) -> int:
    path, expected = config["roots"][key]
    fd = _open_absolute(path)
    observed = os.fstat(fd)
    if (observed.st_dev, observed.st_ino) != expected:
        os.close(fd)
        raise CiCleanupBrokerError("cleanup_root_changed")
    return fd


def _state_child_fd(owner_uid: int, child_name: str) -> int:
    base_fd = owner_fd = None
    try:
        base_fd = _open_absolute(QUARANTINE_ROOT)
        base_info = os.fstat(base_fd)
        root_uid = os.geteuid()
        if (root_uid != 0 or base_info.st_uid != root_uid
                or stat.S_IMODE(base_info.st_mode) != 0o700):
            raise CiCleanupBrokerError("cleanup_quarantine_unsafe")
        owner_fd = _open_child_dir(
            base_fd, str(owner_uid), owner=root_uid, mode=0o700)
        return _open_child_dir(owner_fd, child_name, owner=root_uid, mode=0o700)
    except OSError as exc:
        raise CiCleanupBrokerError("cleanup_quarantine_unavailable") from exc
    finally:
        for fd in (owner_fd, base_fd):
            if fd is not None:
                os.close(fd)


def _quarantine_fd(owner_uid: int) -> int:
    return _state_child_fd(owner_uid, "quarantine")


def _operations_fd(owner_uid: int) -> int:
    return _state_child_fd(owner_uid, "operations")


def _restore_if_safe(quarantine_fd: int, quarantine_name: str,
                     parent_fd: int, source_name: str) -> None:
    try:
        _rename_noreplace(quarantine_fd, quarantine_name, parent_fd, source_name)
        os.fsync(parent_fd)
    except (OSError, CiCleanupBrokerError):
        # The root-owned quarantine is retained for explicit recovery.
        pass


def _move_into_quarantine(parent_fd: int, source_name: str,
                          quarantine_fd: int, expected: tuple[int, int],
                          kind: str) -> tuple[str, int]:
    source_fd = _open_child_dir(parent_fd, source_name)
    try:
        before = os.fstat(source_fd)
        if (before.st_dev, before.st_ino) != expected:
            raise CiCleanupBrokerError("cleanup_identity_changed")
        quarantine_name = f"{kind}-{uuid.uuid4().hex}"
        _rename_noreplace(parent_fd, source_name, quarantine_fd, quarantine_name)
        moved_fd = _open_child_dir(quarantine_fd, quarantine_name)
        moved = os.fstat(moved_fd)
        if (moved.st_dev, moved.st_ino) != expected:
            os.close(moved_fd)
            _restore_if_safe(quarantine_fd, quarantine_name, parent_fd, source_name)
            raise CiCleanupBrokerError("cleanup_identity_changed")
        os.fchown(moved_fd, 0, 0)
        os.fchmod(moved_fd, 0o700)
        os.fsync(moved_fd)
        os.fsync(quarantine_fd)
        return quarantine_name, moved_fd
    finally:
        os.close(source_fd)


def _operation_config() -> tuple[dict[str, Any], int]:
    if os.geteuid() != 0:
        raise CiCleanupBrokerError("cleanup_privilege_required")
    try:
        owner_uid = int(os.environ.get("SUDO_UID", ""))
    except (TypeError, ValueError):
        raise CiCleanupBrokerError("cleanup_caller_invalid") from None
    config = _read_config(owner_uid)
    return config, owner_uid


def _capability(config: dict[str, Any], owner_uid: int) -> dict[str, Any]:
    descriptors = []
    try:
        for key in ("deployment", "legacy", "artifacts"):
            descriptors.append(_open_pinned_root(config, key))
        descriptors.append(_quarantine_fd(owner_uid))
        descriptors.append(_operations_fd(owner_uid))
        if len({os.fstat(fd).st_dev for fd in descriptors}) != 1:
            raise CiCleanupBrokerError("cleanup_cross_device_unavailable")
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
    return {"ok": True, "status": "ready", "owner_uid": owner_uid}


def _finalize_checkout(config: dict[str, Any], owner_uid: int,
                       name: str, expected_op: tuple[int, int],
                       expected_tree: tuple[int, int]) -> dict[str, Any]:
    raise CiCleanupBrokerError("cleanup_journal_required")
    if _HEX32.fullmatch(name) is None:
        raise CiCleanupBrokerError("cleanup_identity_invalid")
    deployment_fd = _open_pinned_root(config, "deployment")
    quarantine_fd = _quarantine_fd(owner_uid)
    cleanup_fd = operation_fd = owned_fd = moved_fd = None
    quarantine_name = None
    try:
        cleanup_fd = _open_child_dir(deployment_fd, ".sandbox-ci-cleanup",
                                     owner=owner_uid, mode=0o700)
        operation_fd = _open_child_dir(cleanup_fd, name, owner=owner_uid, mode=0o700)
        operation_info = os.fstat(operation_fd)
        if (operation_info.st_dev, operation_info.st_ino) != expected_op:
            raise CiCleanupBrokerError("cleanup_identity_changed")
        if os.listdir(operation_fd) != ["owned"]:
            raise CiCleanupBrokerError("cleanup_operation_not_empty")
        owned_fd = _open_child_dir(operation_fd, "owned", owner=owner_uid, mode=0o700)
        owned_info = os.fstat(owned_fd)
        if ((owned_info.st_dev, owned_info.st_ino) != expected_tree
                or os.listdir(owned_fd)):
            raise CiCleanupBrokerError("cleanup_checkout_not_empty")
        os.close(owned_fd)
        owned_fd = None
        os.close(operation_fd)
        operation_fd = None
        quarantine_name, moved_fd = _move_into_quarantine(
            cleanup_fd, name, quarantine_fd, expected_op, "checkout")
        moved_owned = _open_child_dir(moved_fd, "owned", owner=owner_uid, mode=0o700)
        try:
            moved_info = os.fstat(moved_owned)
            if ((moved_info.st_dev, moved_info.st_ino) != expected_tree
                    or os.listdir(moved_owned)):
                raise CiCleanupBrokerError("cleanup_checkout_not_empty")
            os.fchown(moved_owned, 0, 0)
            os.fchmod(moved_owned, 0o700)
            os.fsync(moved_owned)
        finally:
            os.close(moved_owned)
        os.rmdir("owned", dir_fd=moved_fd)
        os.fsync(moved_fd)
        os.close(moved_fd)
        moved_fd = None
        os.rmdir(quarantine_name, dir_fd=quarantine_fd)
        os.fsync(quarantine_fd)
        return {"ok": True, "status": "completed"}
    except (OSError, CiCleanupBrokerError):
        raise
    finally:
        for fd in (moved_fd, owned_fd, operation_fd, cleanup_fd,
                   quarantine_fd, deployment_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _remove_checkout_contents(directory_fd: int, device: int,
                              counters: list[int], depth: int = 0) -> None:
    if depth > _MAX_CHECKOUT_DEPTH:
        raise CiCleanupBrokerError("cleanup_checkout_depth_limit")
    for name in os.listdir(directory_fd):
        counters[0] += 1
        if counters[0] > _MAX_CHECKOUT_ENTRIES:
            raise CiCleanupBrokerError("cleanup_checkout_entry_limit")
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if info.st_dev != device:
            raise CiCleanupBrokerError("cleanup_cross_device_unavailable")
        if stat.S_ISDIR(info.st_mode):
            child_fd = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                os.O_CLOEXEC, dir_fd=directory_fd,
            )
            try:
                child_info = os.fstat(child_fd)
                if ((child_info.st_dev, child_info.st_ino) !=
                        (info.st_dev, info.st_ino)):
                    raise CiCleanupBrokerError("cleanup_identity_changed")
                os.fchown(child_fd, 0, 0)
                os.fchmod(child_fd, 0o700)
                _remove_checkout_contents(child_fd, device, counters, depth + 1)
                os.fsync(child_fd)
            finally:
                os.close(child_fd)
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise CiCleanupBrokerError("cleanup_identity_changed")
            os.rmdir(name, dir_fd=directory_fd)
        else:
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise CiCleanupBrokerError("cleanup_identity_changed")
            os.unlink(name, dir_fd=directory_fd)
    os.fsync(directory_fd)


def _recover_quarantined_checkout(config: dict[str, Any], owner_uid: int,
                                  expected_tree: tuple[int, int]) -> dict[str, Any]:
    raise CiCleanupBrokerError("cleanup_journal_required")
    deployment_fd = _open_pinned_root(config, "deployment")
    quarantine_fd = _quarantine_fd(owner_uid)
    cleanup_fd = operation_fd = moved_fd = None
    try:
        cleanup_fd = _open_child_dir(deployment_fd, ".sandbox-ci-cleanup",
                                     owner=owner_uid, mode=0o700)
        entries = os.listdir(cleanup_fd)
        if len(entries) > 4096:
            raise CiCleanupBrokerError("cleanup_recovery_limit")
        matches: list[tuple[str, tuple[int, int], str]] = []
        for name in entries:
            if _HEX32.fullmatch(name) is None:
                continue
            try:
                operation_fd = _open_child_dir(
                    cleanup_fd, name, owner=owner_uid, mode=0o700)
            except (OSError, CiCleanupBrokerError):
                continue
            try:
                operation_info = os.fstat(operation_fd)
                if os.listdir(operation_fd) != ["owned"]:
                    continue
                try:
                    owned_fd = _open_child_dir(
                        operation_fd, "owned", owner=owner_uid, mode=0o700)
                except (OSError, CiCleanupBrokerError):
                    continue
                try:
                    owned_info = os.fstat(owned_fd)
                    if (owned_info.st_dev, owned_info.st_ino) == expected_tree:
                        matches.append((name, (operation_info.st_dev,
                                               operation_info.st_ino), "deployment"))
                finally:
                    os.close(owned_fd)
            finally:
                os.close(operation_fd)
                operation_fd = None
        for name in os.listdir(quarantine_fd):
            match = re.fullmatch(r"checkout-([0-9a-f]{32})", name)
            if match is None:
                continue
            try:
                checkout_fd = _open_child_dir(quarantine_fd, name)
            except (OSError, CiCleanupBrokerError):
                continue
            try:
                checkout_info = os.fstat(checkout_fd)
                if (checkout_info.st_dev, checkout_info.st_ino) == expected_tree:
                    matches.append((match.group(1), expected_tree, "quarantine"))
            finally:
                os.close(checkout_fd)
        if not matches:
            raise CiCleanupBrokerError("cleanup_recovery_not_found")
        if len(matches) != 1:
            raise CiCleanupBrokerError("cleanup_recovery_ambiguous")
        name, identity, location = matches[0]
        quarantine_name = f"checkout-{name}"
        if location == "deployment":
            operation_fd = _open_child_dir(
                cleanup_fd, name, owner=owner_uid, mode=0o700)
            try:
                operation_info = os.fstat(operation_fd)
                if (operation_info.st_dev, operation_info.st_ino) != identity:
                    raise CiCleanupBrokerError("cleanup_identity_changed")
                owned_info = os.stat(
                    "owned", dir_fd=operation_fd, follow_symlinks=False)
                if (owned_info.st_dev, owned_info.st_ino) != expected_tree:
                    raise CiCleanupBrokerError("cleanup_identity_changed")
                _rename_noreplace(
                    operation_fd, "owned", quarantine_fd, quarantine_name)
                os.fsync(operation_fd)
                moved_fd = _open_child_dir(
                    quarantine_fd, quarantine_name, owner=owner_uid)
                moved_info = os.fstat(moved_fd)
                if (moved_info.st_dev, moved_info.st_ino) != expected_tree:
                    raise CiCleanupBrokerError("cleanup_identity_changed")
                os.fchown(moved_fd, 0, 0)
                os.fchmod(moved_fd, 0o700)
                os.fsync(moved_fd)
                os.close(moved_fd)
                moved_fd = None
                if os.listdir(operation_fd):
                    raise CiCleanupBrokerError("cleanup_operation_not_empty")
            finally:
                os.close(operation_fd)
                operation_fd = None
            current = os.stat(name, dir_fd=cleanup_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != identity:
                raise CiCleanupBrokerError("cleanup_identity_changed")
            os.rmdir(name, dir_fd=cleanup_fd)
            os.fsync(cleanup_fd)
        moved_fd = _open_child_dir(
            quarantine_fd, quarantine_name)
        try:
            moved_info = os.fstat(moved_fd)
            if (moved_info.st_dev, moved_info.st_ino) != expected_tree:
                raise CiCleanupBrokerError("cleanup_identity_changed")
            os.fchown(moved_fd, 0, 0)
            os.fchmod(moved_fd, 0o700)
            _remove_checkout_contents(moved_fd, expected_tree[0], [0])
            os.fsync(moved_fd)
        finally:
            os.close(moved_fd)
            moved_fd = None
        os.rmdir(quarantine_name, dir_fd=quarantine_fd)
        os.fsync(quarantine_fd)
        return {"ok": True, "status": "completed", "recovered": True}
    finally:
        for fd in (moved_fd, operation_fd, cleanup_fd, quarantine_fd, deployment_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _remove_metadata(config: dict[str, Any], owner_uid: int, namespace: str,
                     label: str, expected_dir: tuple[int, int],
                     expected_file: tuple[int, int]) -> dict[str, Any]:
    raise CiCleanupBrokerError("cleanup_journal_required")
    if _NAMESPACE.fullmatch(namespace) is None or _LABEL.fullmatch(label) is None:
        raise CiCleanupBrokerError("cleanup_metadata_invalid")
    legacy_fd = _open_pinned_root(config, "legacy")
    quarantine_fd = _quarantine_fd(owner_uid)
    namespace_fd = directory_fd = moved_fd = file_fd = None
    quarantine_name = None
    try:
        namespace_fd = _open_child_dir(legacy_fd, namespace, owner=owner_uid, mode=0o700)
        directory_fd = _open_child_dir(namespace_fd, label, owner=owner_uid, mode=0o700)
        directory_info = os.fstat(directory_fd)
        if (directory_info.st_dev, directory_info.st_ino) != expected_dir:
            raise CiCleanupBrokerError("cleanup_identity_changed")
        if os.listdir(directory_fd) != ["workspace.json"]:
            raise CiCleanupBrokerError("cleanup_metadata_changed")
        file_fd = os.open("workspace.json", os.O_RDONLY | os.O_NOFOLLOW |
                          os.O_CLOEXEC, dir_fd=directory_fd)
        info = os.fstat(file_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid
                or (info.st_dev, info.st_ino) != expected_file):
            raise CiCleanupBrokerError("cleanup_identity_changed")
        os.close(file_fd)
        file_fd = None
        os.close(directory_fd)
        directory_fd = None
        quarantine_name, moved_fd = _move_into_quarantine(
            namespace_fd, label, quarantine_fd, expected_dir, "metadata")
        moved_file = os.open("workspace.json", os.O_RDONLY | os.O_NOFOLLOW |
                             os.O_CLOEXEC, dir_fd=moved_fd)
        try:
            file_info = os.fstat(moved_file)
            if (not stat.S_ISREG(file_info.st_mode) or file_info.st_uid != owner_uid
                    or (file_info.st_dev, file_info.st_ino) != expected_file):
                raise CiCleanupBrokerError("cleanup_identity_changed")
        finally:
            os.close(moved_file)
        os.fchown(moved_fd, 0, 0)
        os.fchmod(moved_fd, 0o700)
        os.unlink("workspace.json", dir_fd=moved_fd)
        os.fsync(moved_fd)
        os.close(moved_fd)
        moved_fd = None
        os.rmdir(quarantine_name, dir_fd=quarantine_fd)
        os.fsync(quarantine_fd)
        return {"ok": True, "status": "completed"}
    finally:
        for fd in (file_fd, moved_fd, directory_fd, namespace_fd,
                   quarantine_fd, legacy_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _root_pins(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    pins = {}
    for key in ("deployment", "legacy", "artifacts"):
        path, (device, inode) = config["roots"][key]
        pins[key] = {"path": str(path), "device": device, "inode": inode}
    return pins


def _validate_cleanup_request(config: dict[str, Any] | None, owner_uid: int,
                             request: Any) -> dict[str, Any]:
    expected = {
        "schema_version", "job_id", "workspace_id", "authority_digest",
        "project_identity", "workspace_label", "workspace_mode",
        "checkout_locator", "checkout_locator_digest", "wrapper_identity",
        "checkout_identity", "metadata_namespace", "metadata_label",
        "metadata_directory_identity", "metadata_file_identity",
        "metadata_content_digest", "pinned_roots",
    }
    if not isinstance(request, dict) or set(request) != expected:
        raise CiCleanupBrokerError("cleanup_request_invalid")
    if type(request.get("schema_version")) is not int or request["schema_version"] != 1:
        raise CiCleanupBrokerError("cleanup_journal_version_unsupported")
    for field in ("job_id", "project_identity"):
        if not isinstance(request.get(field), str) or _SAFE_ID.fullmatch(request[field]) is None:
            raise CiCleanupBrokerError("cleanup_request_invalid")
    if (not isinstance(request.get("workspace_id"), str)
            or re.fullmatch(r"ws_[0-9a-f]{32}", request["workspace_id"]) is None
            or not isinstance(request.get("authority_digest"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", request["authority_digest"]) is None
            or not isinstance(request.get("workspace_label"), str)
            or _LABEL.fullmatch(request["workspace_label"]) is None
            or not isinstance(request.get("workspace_mode"), str)
            or request["workspace_mode"] not in {"isolated", "ephemeral"}
            or not isinstance(request.get("metadata_namespace"), str)
            or _NAMESPACE.fullmatch(request["metadata_namespace"]) is None
            or not isinstance(request.get("metadata_label"), str)
            or _LABEL.fullmatch(request["metadata_label"]) is None
            or request["metadata_label"] != request["workspace_label"]):
        raise CiCleanupBrokerError("cleanup_request_invalid")
    locator = request.get("checkout_locator")
    if (not isinstance(locator, str) or len(locator) > 4096 or "\x00" in locator
            or not os.path.isabs(locator) or os.path.normpath(locator) != locator):
        raise CiCleanupBrokerError("cleanup_request_invalid")
    locator_digest = "sha256:" + hashlib.sha256(locator.encode("utf-8")).hexdigest()
    if request.get("checkout_locator_digest") != locator_digest:
        raise CiCleanupBrokerError("cleanup_request_invalid")
    if not isinstance(request.get("metadata_content_digest"), str) or _HEX64.fullmatch(
            request["metadata_content_digest"]) is None:
        raise CiCleanupBrokerError("cleanup_request_invalid")
    wrapper_device, _ = _identity(request.get("wrapper_identity"))
    checkout_device, _ = _identity(request.get("checkout_identity"))
    metadata_device, _ = _identity(request.get("metadata_directory_identity"))
    file_device, _ = _identity(request.get("metadata_file_identity"))
    pins = request.get("pinned_roots")
    if (not isinstance(pins, dict) or set(pins) != {"deployment", "legacy", "artifacts"}
            or any(not isinstance(value, dict)
                   or set(value) != {"path", "device", "inode"}
                   or not isinstance(value.get("path"), str)
                   or type(value.get("device")) is not int or value["device"] < 0
                   or type(value.get("inode")) is not int or value["inode"] < 0
                   for value in pins.values())):
        raise CiCleanupBrokerError("cleanup_request_invalid")
    if config is not None:
        if pins != _root_pins(config):
            raise CiCleanupBrokerError("cleanup_root_changed")
        deployment_path, (deployment_device, _) = config["roots"]["deployment"]
        legacy_path, (legacy_device, _) = config["roots"]["legacy"]
        try:
            relative = os.path.relpath(locator, str(deployment_path))
        except (OSError, ValueError) as exc:
            raise CiCleanupBrokerError("cleanup_request_invalid") from exc
        if (relative in {"", ".", ".."} or relative.startswith("../")
                or os.path.isabs(relative)
                or any(part in {"", ".", ".."} for part in relative.split(os.sep))):
            raise CiCleanupBrokerError("cleanup_request_invalid")
        if wrapper_device != deployment_device or checkout_device != deployment_device:
            raise CiCleanupBrokerError("cleanup_cross_device_unavailable")
        if metadata_device != legacy_device or file_device != legacy_device:
            raise CiCleanupBrokerError("cleanup_cross_device_unavailable")
    if os.path.basename(locator) in {"", ".", ".."}:
        raise CiCleanupBrokerError("cleanup_request_invalid")
    if config is not None:
        metadata_path = legacy_path / request["metadata_namespace"] / request["metadata_label"]
        if len(str(metadata_path)) > 4096:
            raise CiCleanupBrokerError("cleanup_request_invalid")
    return request


def _request_digest(cleanup_id: str, owner_uid: int,
                    request: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json({
        "cleanup_id": cleanup_id,
        "owner_uid": owner_uid,
        "request": request,
    })).hexdigest()


def _validate_journal_name(cleanup_id: str) -> None:
    if not isinstance(cleanup_id, str) or _HEX32.fullmatch(cleanup_id) is None:
        raise CiCleanupBrokerError("cleanup_identity_invalid")


def _open_state_file(directory_fd: int, name: str, *, create: bool) -> int:
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    created = False
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            raise CiCleanupBrokerError("cleanup_journal_missing")
        flags |= os.O_CREAT | os.O_EXCL
        created = True
    try:
        fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
    except FileExistsError:
        if not created:
            raise CiCleanupBrokerError("cleanup_journal_unavailable")
        try:
            fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=directory_fd)
            created = False
        except OSError as exc:
            raise CiCleanupBrokerError("cleanup_journal_unavailable") from exc
    except OSError as exc:
        raise CiCleanupBrokerError("cleanup_journal_unavailable") from exc
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
            or info.st_size != 0):
        os.close(fd)
        raise CiCleanupBrokerError("cleanup_journal_unsafe")
    if created:
        os.fsync(fd)
        os.fsync(directory_fd)
    return fd


@contextmanager
def _locked_journal(owner_uid: int, cleanup_id: str, *, create: bool):
    _validate_journal_name(cleanup_id)
    operations_fd = quota_fd = id_fd = None
    try:
        operations_fd = _operations_fd(owner_uid)
        quota_fd = _open_state_file(operations_fd, "quota.lock", create=True)
        fcntl.flock(quota_fd, fcntl.LOCK_EX)
        if create:
            count, _total, identifiers = _journal_usage(operations_fd)
            if cleanup_id not in identifiers and count >= _MAX_JOURNAL_IDS:
                raise CiCleanupBrokerError("cleanup_journal_quota")
        id_fd = _open_state_file(
            operations_fd, f"{cleanup_id}.lock", create=create)
        fcntl.flock(id_fd, fcntl.LOCK_EX)
        yield operations_fd
    except CiCleanupBrokerError:
        raise
    except OSError as exc:
        raise CiCleanupBrokerError("cleanup_journal_unavailable") from exc
    finally:
        if create and operations_fd is not None and id_fd is not None:
            try:
                if _journal_file_info(operations_fd, f"{cleanup_id}.json") is None:
                    opened = os.fstat(id_fd)
                    current = _journal_file_info(operations_fd, f"{cleanup_id}.lock")
                    if (current is not None and
                            (current.st_dev, current.st_ino) ==
                            (opened.st_dev, opened.st_ino)):
                        os.unlink(f"{cleanup_id}.lock", dir_fd=operations_fd)
                        os.fsync(operations_fd)
            except OSError:
                pass
        for fd in (id_fd, quota_fd, operations_fd):
            if fd is not None:
                try:
                    if fd in (id_fd, quota_fd):
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
                except OSError:
                    pass


def _journal_file_info(operations_fd: int, name: str) -> os.stat_result | None:
    try:
        info = os.stat(name, dir_fd=operations_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise CiCleanupBrokerError("cleanup_journal_unsafe")
    return info


def _journal_usage(operations_fd: int) -> tuple[int, int, set[str]]:
    total = 0
    identifiers: set[str] = set()
    lock_ids: set[str] = set()
    journal_ids: set[str] = set()
    names = os.listdir(operations_fd)
    if len(names) > _MAX_JOURNAL_FILES:
        raise CiCleanupBrokerError("cleanup_journal_quota")
    stale_temps = []
    stale_bytes = 0
    for name in names:
        if name == "quota.lock":
            info = _journal_file_info(operations_fd, name)
            if info is None or info.st_size != 0:
                raise CiCleanupBrokerError("cleanup_journal_unsafe")
            continue
        lock_match = re.fullmatch(r"([0-9a-f]{32})\.lock", name)
        journal_match = re.fullmatch(r"([0-9a-f]{32})\.json", name)
        temp_match = re.fullmatch(r"\.tmp-([0-9a-f]{32})-[0-9a-f]{32}", name)
        if not (lock_match or journal_match or temp_match):
            raise CiCleanupBrokerError("cleanup_journal_unsafe")
        info = _journal_file_info(operations_fd, name)
        if info is None or info.st_size > _MAX_JOURNAL_BYTES:
            raise CiCleanupBrokerError("cleanup_journal_unsafe")
        if lock_match:
            if info.st_size != 0:
                raise CiCleanupBrokerError("cleanup_journal_unsafe")
            identifiers.add(lock_match.group(1))
            lock_ids.add(lock_match.group(1))
        elif journal_match:
            identifiers.add(journal_match.group(1))
            journal_ids.add(journal_match.group(1))
            total += info.st_size
        else:
            total += info.st_size
            stale_bytes += info.st_size
            stale_temps.append(name)
    for name in stale_temps:
        os.unlink(name, dir_fd=operations_fd)
    if stale_temps:
        os.fsync(operations_fd)
        total -= stale_bytes
    if not journal_ids.issubset(lock_ids):
        raise CiCleanupBrokerError("cleanup_journal_unsafe")
    if (len(identifiers) > _MAX_JOURNAL_IDS
            or total > _MAX_JOURNAL_TOTAL_BYTES):
        raise CiCleanupBrokerError("cleanup_journal_quota")
    return len(identifiers), total, identifiers


def _read_journal(operations_fd: int, cleanup_id: str,
                  owner_uid: int) -> dict[str, Any] | None:
    name = f"{cleanup_id}.json"
    info = _journal_file_info(operations_fd, name)
    if info is None:
        return None
    if info.st_size > _MAX_JOURNAL_BYTES:
        raise CiCleanupBrokerError("cleanup_journal_unsafe")
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=operations_fd)
    except OSError as exc:
        raise CiCleanupBrokerError("cleanup_journal_unsafe") from exc
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
                or opened.st_nlink != 1 or opened.st_size > _MAX_JOURNAL_BYTES):
            raise CiCleanupBrokerError("cleanup_journal_unsafe")
        raw = bytearray()
        while len(raw) <= _MAX_JOURNAL_BYTES:
            block = os.read(fd, min(4096, _MAX_JOURNAL_BYTES + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        if len(raw) > _MAX_JOURNAL_BYTES:
            raise CiCleanupBrokerError("cleanup_journal_unsafe")
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise CiCleanupBrokerError("cleanup_journal_invalid") from exc
    finally:
        os.close(fd)
    if not isinstance(payload, dict):
        raise CiCleanupBrokerError("cleanup_journal_invalid")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise CiCleanupBrokerError("cleanup_journal_version_unsupported")
    if payload.get("cleanup_id") != cleanup_id or payload.get("owner_uid") != owner_uid:
        raise CiCleanupBrokerError("cleanup_journal_identity_changed")
    if payload.get("state") == "acknowledged":
        required = {
            "schema_version", "cleanup_id", "owner_uid", "state",
            "request_digest", "acknowledged_workspace_id",
            "acknowledged_authority_digest", "receipt",
        }
        if set(payload) != required:
            raise CiCleanupBrokerError("cleanup_journal_invalid")
        if (not isinstance(payload.get("request_digest"), str)
                or _HEX64.fullmatch(payload["request_digest"]) is None
                or not isinstance(payload.get("acknowledged_workspace_id"), str)
                or re.fullmatch(r"ws_[0-9a-f]{32}",
                                payload["acknowledged_workspace_id"]) is None
                or not isinstance(payload.get("acknowledged_authority_digest"), str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}",
                                payload["acknowledged_authority_digest"]) is None
                or not isinstance(payload.get("receipt"), dict)):
            raise CiCleanupBrokerError("cleanup_journal_invalid")
        receipt = payload["receipt"]
        if (set(receipt) != {
                "schema_version", "cleanup_id", "request_digest",
                "checkout_identity", "wrapper_identity",
                "metadata_directory_identity", "metadata_file_identity",
                "metadata", "status",
        } or type(receipt.get("schema_version")) is not int
                or receipt["schema_version"] != 1
                or receipt.get("cleanup_id") != cleanup_id
                or receipt.get("request_digest") != payload["request_digest"]
                or receipt.get("status") != "completed"):
            raise CiCleanupBrokerError("cleanup_journal_invalid")
        for key in ("checkout_identity", "wrapper_identity",
                    "metadata_directory_identity", "metadata_file_identity"):
            _identity(receipt.get(key))
        metadata_receipt = receipt.get("metadata")
        if (not isinstance(metadata_receipt, dict)
                or set(metadata_receipt) != {
                    "cleanup_id", "request_digest", "file_identity",
                    "directory_identity",
                }
                or metadata_receipt.get("cleanup_id") != cleanup_id
                or metadata_receipt.get("request_digest") != payload["request_digest"]
                or metadata_receipt.get("file_identity") !=
                receipt["metadata_file_identity"]
                or metadata_receipt.get("directory_identity") !=
                receipt["metadata_directory_identity"]):
            raise CiCleanupBrokerError("cleanup_journal_invalid")
        _identity(metadata_receipt.get("file_identity"))
        _identity(metadata_receipt.get("directory_identity"))
        return payload
    required = {
        "schema_version", "cleanup_id", "owner_uid", "state", "request",
        "request_digest", "checkout", "metadata", "receipt",
    }
    if set(payload) != required or payload.get("state") != "active":
        raise CiCleanupBrokerError("cleanup_journal_invalid")
    request = _validate_cleanup_request(None, owner_uid, payload["request"])
    # Active journals are rebound to the currently loaded pinned-root config
    # by each caller before any filesystem operation.
    if not isinstance(payload.get("request_digest"), str) or _HEX64.fullmatch(
            payload["request_digest"]) is None:
        raise CiCleanupBrokerError("cleanup_journal_invalid")
    if payload["request_digest"] != _request_digest(cleanup_id, owner_uid, request):
        raise CiCleanupBrokerError("cleanup_journal_identity_changed")
    checkout = payload.get("checkout")
    if not isinstance(checkout, dict) or set(checkout) != {"phase", "target"}:
        raise CiCleanupBrokerError("cleanup_journal_invalid")
    _validate_phase(checkout.get("phase"))
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict) or set(metadata) != {
            "phase", "file_phase", "directory_phase", "target"}:
        raise CiCleanupBrokerError("cleanup_journal_invalid")
    for key in ("phase", "file_phase", "directory_phase"):
        _validate_phase(metadata.get(key))
    if (checkout.get("target") != f"checkout-{cleanup_id}"
            or metadata.get("target") != f"metadata-{cleanup_id}"):
        raise CiCleanupBrokerError("cleanup_journal_invalid")
    receipt = payload.get("receipt")
    if receipt is not None:
        if not isinstance(receipt, dict):
            raise CiCleanupBrokerError("cleanup_journal_invalid")
        if checkout.get("phase") == "removed" and metadata.get("phase") != "removed":
            expected_receipt = _checkout_receipt(
                cleanup_id, request, payload["request_digest"])
        elif metadata.get("phase") == "removed":
            expected_receipt = _metadata_receipt(
                cleanup_id, request, payload["request_digest"])
        else:
            raise CiCleanupBrokerError("cleanup_journal_invalid")
        if receipt != expected_receipt:
            raise CiCleanupBrokerError("cleanup_journal_invalid")
    elif checkout.get("phase") == "removed" and metadata.get("phase") == "removed":
        raise CiCleanupBrokerError("cleanup_journal_invalid")
    return payload


def _validate_phase(phase: Any) -> None:
    if phase not in {"prepared", "quarantined", "removing", "removed"}:
        raise CiCleanupBrokerError("cleanup_journal_invalid")


def _store_journal(operations_fd: int, cleanup_id: str,
                   payload: dict[str, Any], *, create: bool = False) -> None:
    raw = _canonical_json(payload) + b"\n"
    if len(raw) > _MAX_JOURNAL_BYTES:
        raise CiCleanupBrokerError("cleanup_journal_too_large")
    name = f"{cleanup_id}.json"
    old = _journal_file_info(operations_fd, name)
    if create and old is not None:
        raise CiCleanupBrokerError("cleanup_id_reused")
    if not create and old is None:
        raise CiCleanupBrokerError("cleanup_journal_missing")
    count, total, ids = _journal_usage(operations_fd)
    new_count = count + (1 if cleanup_id not in ids else 0)
    peak_total = total + len(raw)
    if new_count > _MAX_JOURNAL_IDS or peak_total > _MAX_JOURNAL_TOTAL_BYTES:
        raise CiCleanupBrokerError("cleanup_journal_quota")
    temporary = f".tmp-{cleanup_id}-{uuid.uuid4().hex}"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=operations_fd)
    except OSError as exc:
        raise CiCleanupBrokerError("cleanup_journal_unavailable") from exc
    try:
        os.fchmod(fd, 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise CiCleanupBrokerError("cleanup_journal_unsafe")
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise CiCleanupBrokerError("cleanup_journal_unavailable")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        if create:
            _rename_noreplace(operations_fd, temporary, operations_fd, name)
        else:
            os.replace(temporary, name, src_dir_fd=operations_fd,
                       dst_dir_fd=operations_fd)
        os.fsync(operations_fd)
    except (OSError, CiCleanupBrokerError):
        try:
            os.unlink(temporary, dir_fd=operations_fd)
        except OSError:
            pass
        raise


def _active_request(config: dict[str, Any], owner_uid: int,
                    cleanup_id: str, journal: dict[str, Any]) -> dict[str, Any]:
    if journal.get("state") == "acknowledged":
        raise CiCleanupBrokerError("cleanup_already_acknowledged")
    request = _validate_cleanup_request(config, owner_uid, journal.get("request"))
    if journal["request_digest"] != _request_digest(cleanup_id, owner_uid, request):
        raise CiCleanupBrokerError("cleanup_journal_identity_changed")
    return request


def _metadata_directory_path(config: dict[str, Any], request: dict[str, Any]) -> Path:
    legacy_path, _ = config["roots"]["legacy"]
    return legacy_path / request["metadata_namespace"] / request["metadata_label"]


def _verify_metadata_content(raw: bytes, config: dict[str, Any],
                             request: dict[str, Any]) -> None:
    if (len(raw) > _MAX_METADATA_BYTES
            or hashlib.sha256(raw).hexdigest() != request["metadata_content_digest"]):
        raise CiCleanupBrokerError("cleanup_metadata_changed")
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise CiCleanupBrokerError("cleanup_metadata_changed") from exc
    if (not isinstance(document, dict)
            or document.get("workspace_id") != request["workspace_id"]
            or document.get("project_identity") != request["project_identity"]
            or document.get("label") != request["workspace_label"]
            or document.get("mode") != request["workspace_mode"]
            or document.get("path") != str(_metadata_directory_path(config, request))):
        raise CiCleanupBrokerError("cleanup_metadata_changed")


def _read_metadata_file(directory_fd: int, config: dict[str, Any],
                        owner_uid: int, request: dict[str, Any], *,
                        require_content: bool = True) -> bool:
    expected_dir = _identity(request["metadata_directory_identity"])
    expected_file = _identity(request["metadata_file_identity"])
    directory_info = os.fstat(directory_fd)
    if ((directory_info.st_dev, directory_info.st_ino) != expected_dir
            or directory_info.st_uid not in {owner_uid, os.geteuid()}
            or not stat.S_ISDIR(directory_info.st_mode)):
        raise CiCleanupBrokerError("cleanup_identity_changed")
    entries = os.listdir(directory_fd)
    if entries == [] and not require_content:
        return False
    if entries != ["workspace.json"]:
        raise CiCleanupBrokerError("cleanup_metadata_changed")
    try:
        fd = os.open("workspace.json", os.O_RDONLY | os.O_NOFOLLOW |
                     os.O_CLOEXEC, dir_fd=directory_fd)
    except OSError as exc:
        raise CiCleanupBrokerError("cleanup_identity_changed") from exc
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid not in {
                owner_uid, os.geteuid()} or before.st_nlink != 1
                or (before.st_dev, before.st_ino) != expected_file
                or before.st_size > _MAX_METADATA_BYTES):
            raise CiCleanupBrokerError("cleanup_identity_changed")
        raw = bytearray()
        while len(raw) <= _MAX_METADATA_BYTES:
            block = os.read(fd, min(4096, _MAX_METADATA_BYTES + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        after = os.fstat(fd)
        if ((after.st_dev, after.st_ino, after.st_size) !=
                (before.st_dev, before.st_ino, before.st_size)):
            raise CiCleanupBrokerError("cleanup_identity_changed")
        if require_content:
            _verify_metadata_content(bytes(raw), config, request)
    finally:
        os.close(fd)
    if os.listdir(directory_fd) != ["workspace.json"]:
        raise CiCleanupBrokerError("cleanup_metadata_changed")
    return True


def _open_metadata_source(config: dict[str, Any], owner_uid: int,
                          request: dict[str, Any]) -> tuple[int, int]:
    legacy_fd = _open_pinned_root(config, "legacy")
    try:
        namespace_fd = _open_child_dir(
            legacy_fd, request["metadata_namespace"],
            owner=owner_uid, mode=0o700)
    except BaseException:
        os.close(legacy_fd)
        raise
    return legacy_fd, namespace_fd


def _verify_checkout_child(wrapper_fd: int, expected_identity: tuple[int, int],
                           owner_uid: int, *, allow_absent: bool) -> bool:
    entries = os.listdir(wrapper_fd)
    if entries == [] and allow_absent:
        return False
    if entries != ["owned"]:
        raise CiCleanupBrokerError("cleanup_operation_not_empty")
    try:
        child_fd = _open_child_dir(wrapper_fd, "owned")
    except OSError as exc:
        raise CiCleanupBrokerError("cleanup_identity_changed") from exc
    try:
        child_info = os.fstat(child_fd)
        if ((child_info.st_dev, child_info.st_ino) != expected_identity
                or child_info.st_uid not in {owner_uid, os.geteuid()}
                or stat.S_IMODE(child_info.st_mode) != 0o700):
            raise CiCleanupBrokerError("cleanup_identity_changed")
    finally:
        os.close(child_fd)
    return True


def _open_checkout_parent(config: dict[str, Any],
                          request: dict[str, Any]) -> tuple[int, str]:
    deployment_path, _ = config["roots"]["deployment"]
    relative = os.path.relpath(request["checkout_locator"], str(deployment_path))
    parts = relative.split(os.sep)
    current_fd = _open_pinned_root(config, "deployment")
    device = os.fstat(current_fd).st_dev
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                              os.O_CLOEXEC, dir_fd=current_fd)
            info = os.fstat(next_fd)
            if info.st_dev != device:
                os.close(next_fd)
                raise CiCleanupBrokerError("cleanup_cross_device_unavailable")
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, parts[-1]
    except BaseException:
        os.close(current_fd)
        raise


def _open_checkout_entry(parent_fd: int, name: str,
                         expected: tuple[int, int]) -> int | None:
    try:
        fd = _open_child_dir(parent_fd, name)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CiCleanupBrokerError("cleanup_identity_changed") from exc
    info = os.fstat(fd)
    if (info.st_dev, info.st_ino) != expected:
        os.close(fd)
        raise CiCleanupBrokerError("cleanup_identity_changed")
    return fd


def _open_cleanup_wrapper(deployment_fd: int, cleanup_id: str,
                          owner_uid: int, expected: tuple[int, int],
                          *, missing_ok: bool) -> tuple[int | None, int | None]:
    try:
        cleanup_fd = _open_child_dir(
            deployment_fd, ".sandbox-ci-cleanup", owner=owner_uid, mode=0o700)
    except FileNotFoundError:
        if missing_ok:
            return None, None
        raise CiCleanupBrokerError("cleanup_recovery_not_found")
    try:
        wrapper_fd = _open_checkout_entry(cleanup_fd, cleanup_id, expected)
        if wrapper_fd is None and not missing_ok:
            raise CiCleanupBrokerError("cleanup_recovery_not_found")
        return cleanup_fd, wrapper_fd
    except BaseException:
        os.close(cleanup_fd)
        raise


def _verify_checkout_source(config: dict[str, Any], owner_uid: int,
                            cleanup_id: str, request: dict[str, Any]) -> None:
    deployment_fd = _open_pinned_root(config, "deployment")
    cleanup_fd = wrapper_fd = parent_fd = checkout_fd = None
    try:
        cleanup_fd, wrapper_fd = _open_cleanup_wrapper(
            deployment_fd, cleanup_id, owner_uid,
            _identity(request["wrapper_identity"]), missing_ok=False)
        if os.listdir(wrapper_fd) != []:
            raise CiCleanupBrokerError("cleanup_operation_not_empty")
        parent_fd, leaf = _open_checkout_parent(config, request)
        checkout_fd = _open_checkout_entry(
            parent_fd, leaf, _identity(request["checkout_identity"]))
        if checkout_fd is None:
            raise CiCleanupBrokerError("cleanup_recovery_not_found")
        info = os.fstat(checkout_fd)
        if info.st_uid != owner_uid or stat.S_IMODE(info.st_mode) != 0o700:
            raise CiCleanupBrokerError("cleanup_path_unsafe")
    finally:
        for fd in (checkout_fd, parent_fd, wrapper_fd, cleanup_fd, deployment_fd):
            if fd is not None:
                os.close(fd)


def _begin_cleanup(config: dict[str, Any], owner_uid: int, cleanup_id: str,
                   request: dict[str, Any]) -> dict[str, Any]:
    _validate_journal_name(cleanup_id)
    request = _validate_cleanup_request(config, owner_uid, request)
    digest = _request_digest(cleanup_id, owner_uid, request)
    with _locked_journal(owner_uid, cleanup_id, create=True) as operations_fd:
        existing = _read_journal(operations_fd, cleanup_id, owner_uid)
        if existing is not None:
            if existing["request_digest"] != digest:
                raise CiCleanupBrokerError("cleanup_id_reused")
            return _journal_response(existing)
        _verify_checkout_source(config, owner_uid, cleanup_id, request)
        legacy_fd, namespace_fd = _open_metadata_source(config, owner_uid, request)
        directory_fd = None
        try:
            directory_fd = _open_child_dir(
                namespace_fd, request["metadata_label"], owner=owner_uid, mode=0o700)
            _read_metadata_file(
                directory_fd, config, owner_uid, request, require_content=True)
        finally:
            for fd in (directory_fd, namespace_fd, legacy_fd):
                if fd is not None:
                    os.close(fd)
        payload = {
            "schema_version": 1,
            "cleanup_id": cleanup_id,
            "owner_uid": owner_uid,
            "state": "active",
            "request": request,
            "request_digest": digest,
            "checkout": {"phase": "prepared", "target": f"checkout-{cleanup_id}"},
            "metadata": {
                "phase": "prepared", "file_phase": "prepared",
                "directory_phase": "prepared", "target": f"metadata-{cleanup_id}",
            },
            "receipt": None,
        }
        _store_journal(operations_fd, cleanup_id, payload, create=True)
        return _journal_response(payload)


def _journal_response(journal: dict[str, Any]) -> dict[str, Any]:
    receipt = journal.get("receipt")
    if receipt is None:
        receipt = {
            "schema_version": 1,
            "cleanup_id": journal["cleanup_id"],
            "request_digest": journal["request_digest"],
            "checkout_phase": journal.get("checkout", {}).get("phase"),
            "metadata_file_phase": journal.get("metadata", {}).get("file_phase"),
            "metadata_directory_phase": journal.get("metadata", {}).get("directory_phase"),
        }
    if journal.get("state") == "acknowledged":
        return {"ok": True, "status": "completed", "receipt": receipt}
    return {"ok": True, "status": "in_progress", "receipt": receipt}


def _checkout_receipt(cleanup_id: str, request: dict[str, Any],
                       digest: str) -> dict[str, Any]:
    return {
        "cleanup_id": cleanup_id,
        "request_digest": digest,
        "checkout_identity": request["checkout_identity"],
        "wrapper_identity": request["wrapper_identity"],
        "metadata_directory_identity": request["metadata_directory_identity"],
        "metadata_file_identity": request["metadata_file_identity"],
    }


def _resume_checkout(config: dict[str, Any], owner_uid: int,
                     cleanup_id: str) -> dict[str, Any]:
    with _locked_journal(owner_uid, cleanup_id, create=False) as operations_fd:
        journal = _read_journal(operations_fd, cleanup_id, owner_uid)
        if journal is None:
            raise CiCleanupBrokerError("cleanup_journal_missing")
        if journal.get("state") == "acknowledged":
            return _journal_response(journal)
        request = _active_request(config, owner_uid, cleanup_id, journal)
        checkout = journal["checkout"]
        phase = checkout["phase"]
        if phase == "removed":
            return _journal_response(journal)

        deployment_fd = _open_pinned_root(config, "deployment")
        quarantine_fd = _quarantine_fd(owner_uid)
        cleanup_fd = source_wrapper_fd = target_fd = parent_fd = child_fd = None
        try:
            cleanup_fd, source_wrapper_fd = _open_cleanup_wrapper(
                deployment_fd, cleanup_id, owner_uid,
                _identity(request["wrapper_identity"]), missing_ok=True)
            target_fd = _open_checkout_entry(
                quarantine_fd, checkout["target"],
                _identity(request["wrapper_identity"]))
            if target_fd is not None:
                target_info = os.fstat(target_fd)
                if (target_info.st_uid not in {owner_uid, os.geteuid()}
                        or stat.S_IMODE(target_info.st_mode) != 0o700):
                    raise CiCleanupBrokerError("cleanup_path_unsafe")
                os.fchown(target_fd, os.geteuid(), os.getegid())
                os.fchmod(target_fd, 0o700)
                os.fsync(target_fd)
            if source_wrapper_fd is not None and target_fd is not None:
                raise CiCleanupBrokerError("cleanup_recovery_ambiguous")
            expected_child = _identity(request["checkout_identity"])
            parent_fd, leaf = _open_checkout_parent(config, request)
            source_checkout_fd = _open_checkout_entry(parent_fd, leaf, expected_child)
            try:
                if source_wrapper_fd is not None:
                    if phase != "prepared":
                        raise CiCleanupBrokerError("cleanup_identity_changed")
                    child_present = _verify_checkout_child(
                        source_wrapper_fd, expected_child, owner_uid,
                        allow_absent=True)
                    if child_present and source_checkout_fd is not None:
                        raise CiCleanupBrokerError("cleanup_recovery_ambiguous")
                    if not child_present:
                        if source_checkout_fd is None:
                            raise CiCleanupBrokerError("cleanup_recovery_not_found")
                        current = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                        if (current.st_dev, current.st_ino) != expected_child:
                            raise CiCleanupBrokerError("cleanup_identity_changed")
                        _rename_noreplace(parent_fd, leaf, source_wrapper_fd, "owned")
                        os.fsync(parent_fd)
                        os.fsync(source_wrapper_fd)
                        opened_child = os.fstat(source_checkout_fd)
                        moved_child = os.stat(
                            "owned", dir_fd=source_wrapper_fd,
                            follow_symlinks=False)
                        if ((opened_child.st_dev, opened_child.st_ino) != expected_child
                                or (moved_child.st_dev, moved_child.st_ino) != expected_child):
                            raise CiCleanupBrokerError("cleanup_identity_changed")
                        try:
                            os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                        except FileNotFoundError:
                            pass
                        else:
                            raise CiCleanupBrokerError("cleanup_recovery_ambiguous")
                        os.close(source_checkout_fd)
                        source_checkout_fd = None
                    if not _verify_checkout_child(
                            source_wrapper_fd, expected_child, owner_uid,
                            allow_absent=False):
                        raise CiCleanupBrokerError("cleanup_identity_changed")
                    if source_checkout_fd is not None:
                        raise CiCleanupBrokerError("cleanup_recovery_ambiguous")
                    if child_present:
                        os.fsync(parent_fd)
                        os.fsync(source_wrapper_fd)
                    _rename_noreplace(
                        cleanup_fd, cleanup_id, quarantine_fd, checkout["target"])
                    os.fsync(cleanup_fd)
                    os.fsync(quarantine_fd)
                    os.fchown(source_wrapper_fd, os.geteuid(), os.getegid())
                    os.fchmod(source_wrapper_fd, 0o700)
                    os.fsync(source_wrapper_fd)
                    os.close(source_wrapper_fd)
                    source_wrapper_fd = None
                    target_fd = _open_checkout_entry(
                        quarantine_fd, checkout["target"],
                        _identity(request["wrapper_identity"]))
                    if target_fd is None:
                        raise CiCleanupBrokerError("cleanup_identity_changed")
                    checkout["phase"] = "quarantined"
                    _store_journal(operations_fd, cleanup_id, journal)
                elif target_fd is not None:
                    if source_checkout_fd is not None:
                        raise CiCleanupBrokerError("cleanup_recovery_ambiguous")
                    child_present = _verify_checkout_child(
                        target_fd, expected_child, owner_uid,
                        allow_absent=(phase == "removing"))
                    if phase == "prepared":
                        if not child_present:
                            raise CiCleanupBrokerError("cleanup_recovery_not_found")
                        if cleanup_fd is not None:
                            os.fsync(cleanup_fd)
                        os.fsync(quarantine_fd)
                        checkout["phase"] = "quarantined"
                        _store_journal(operations_fd, cleanup_id, journal)
                    elif phase == "quarantined" and not child_present:
                        raise CiCleanupBrokerError("cleanup_recovery_not_found")
                elif phase == "removing":
                    if source_checkout_fd is not None:
                        raise CiCleanupBrokerError("cleanup_identity_changed")
                    os.fsync(parent_fd)
                    os.fsync(quarantine_fd)
                    checkout["phase"] = "removed"
                    journal["receipt"] = _checkout_receipt(
                        cleanup_id, request, journal["request_digest"])
                    _store_journal(operations_fd, cleanup_id, journal)
                    return _journal_response(journal)
                else:
                    raise CiCleanupBrokerError("cleanup_recovery_not_found")
            finally:
                if source_checkout_fd is not None:
                    os.close(source_checkout_fd)

            if target_fd is None:
                raise CiCleanupBrokerError("cleanup_identity_changed")
            phase = checkout["phase"]
            if phase == "quarantined":
                checkout["phase"] = "removing"
                _store_journal(operations_fd, cleanup_id, journal)
                phase = "removing"
            if phase != "removing":
                raise CiCleanupBrokerError("cleanup_journal_invalid")
            if _verify_checkout_child(
                    target_fd, expected_child, owner_uid, allow_absent=True):
                child_fd = _open_child_dir(target_fd, "owned")
                info = os.fstat(child_fd)
                if (info.st_dev, info.st_ino) != expected_child:
                    raise CiCleanupBrokerError("cleanup_identity_changed")
                os.fchown(child_fd, os.geteuid(), os.getegid())
                os.fchmod(child_fd, 0o700)
                _remove_checkout_contents(child_fd, expected_child[0], [0])
                os.fsync(child_fd)
                os.close(child_fd)
                child_fd = None
                current = os.stat("owned", dir_fd=target_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != expected_child:
                    raise CiCleanupBrokerError("cleanup_identity_changed")
                os.rmdir("owned", dir_fd=target_fd)
                os.fsync(target_fd)
            if os.listdir(target_fd):
                raise CiCleanupBrokerError("cleanup_operation_not_empty")
            wrapper_info = os.fstat(target_fd)
            if _identity(request["wrapper_identity"]) != (
                    wrapper_info.st_dev, wrapper_info.st_ino):
                raise CiCleanupBrokerError("cleanup_identity_changed")
            os.close(target_fd)
            target_fd = None
            current_target = os.stat(
                checkout["target"], dir_fd=quarantine_fd, follow_symlinks=False)
            if (current_target.st_dev, current_target.st_ino) != _identity(
                    request["wrapper_identity"]):
                raise CiCleanupBrokerError("cleanup_identity_changed")
            os.rmdir(checkout["target"], dir_fd=quarantine_fd)
            os.fsync(quarantine_fd)
            checkout["phase"] = "removed"
            journal["receipt"] = _checkout_receipt(
                cleanup_id, request, journal["request_digest"])
            _store_journal(operations_fd, cleanup_id, journal)
            return _journal_response(journal)
        finally:
            for fd in (child_fd, target_fd, source_wrapper_fd, parent_fd,
                       cleanup_fd, quarantine_fd, deployment_fd):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass


def _metadata_receipt(cleanup_id: str, request: dict[str, Any],
                      digest: str) -> dict[str, Any]:
    return {
        "cleanup_id": cleanup_id,
        "request_digest": digest,
        "file_identity": request["metadata_file_identity"],
        "directory_identity": request["metadata_directory_identity"],
    }


def _resume_metadata(config: dict[str, Any], owner_uid: int,
                     cleanup_id: str) -> dict[str, Any]:
    with _locked_journal(owner_uid, cleanup_id, create=False) as operations_fd:
        journal = _read_journal(operations_fd, cleanup_id, owner_uid)
        if journal is None:
            raise CiCleanupBrokerError("cleanup_journal_missing")
        if journal.get("state") == "acknowledged":
            return _journal_response(journal)
        request = _active_request(config, owner_uid, cleanup_id, journal)
        metadata = journal["metadata"]
        if metadata["phase"] == "removed":
            return _journal_response(journal)
        legacy_fd, namespace_fd = _open_metadata_source(config, owner_uid, request)
        quarantine_fd = _quarantine_fd(owner_uid)
        source_fd = target_fd = None
        try:
            try:
                source_fd = _open_child_dir(
                    namespace_fd, request["metadata_label"],
                    owner=owner_uid, mode=0o700)
            except FileNotFoundError:
                source_fd = None
            try:
                target_fd = _open_child_dir(
                    quarantine_fd, metadata["target"])
            except FileNotFoundError:
                target_fd = None
            if target_fd is not None:
                target_info = os.fstat(target_fd)
                if ((target_info.st_dev, target_info.st_ino) != _identity(
                        request["metadata_directory_identity"])
                        or target_info.st_uid not in {owner_uid, os.geteuid()}
                        or stat.S_IMODE(target_info.st_mode) != 0o700):
                    raise CiCleanupBrokerError("cleanup_identity_changed")
                os.fchown(target_fd, os.geteuid(), os.getegid())
                os.fchmod(target_fd, 0o700)
                os.fsync(target_fd)
            if source_fd is not None and target_fd is not None:
                raise CiCleanupBrokerError("cleanup_recovery_ambiguous")
            if source_fd is None and target_fd is None:
                if metadata["directory_phase"] != "removing":
                    raise CiCleanupBrokerError("cleanup_recovery_not_found")
                os.fsync(namespace_fd)
                os.fsync(quarantine_fd)
                metadata["directory_phase"] = "removed"
                metadata["phase"] = "removed"
                journal["receipt"] = _metadata_receipt(
                    cleanup_id, request, journal["request_digest"])
                if metadata["file_phase"] != "removed":
                    raise CiCleanupBrokerError("cleanup_journal_invalid")
                _store_journal(operations_fd, cleanup_id, journal)
                return _journal_response(journal)
            if source_fd is not None:
                if metadata["phase"] != "prepared" or metadata["file_phase"] != "prepared":
                    raise CiCleanupBrokerError("cleanup_identity_changed")
                _read_metadata_file(
                    source_fd, config, owner_uid, request, require_content=True)
                directory_info = os.fstat(source_fd)
                if (directory_info.st_dev, directory_info.st_ino) != _identity(
                        request["metadata_directory_identity"]):
                    raise CiCleanupBrokerError("cleanup_identity_changed")
                _rename_noreplace(
                    namespace_fd, request["metadata_label"], quarantine_fd,
                    metadata["target"])
                os.fsync(namespace_fd)
                os.fsync(quarantine_fd)
                os.fchown(source_fd, os.geteuid(), os.getegid())
                os.fchmod(source_fd, 0o700)
                os.fsync(source_fd)
                os.close(source_fd)
                source_fd = None
                target_fd = _open_child_dir(
                    quarantine_fd, metadata["target"])
                metadata["phase"] = "quarantined"
                metadata["file_phase"] = "quarantined"
                metadata["directory_phase"] = "quarantined"
                _store_journal(operations_fd, cleanup_id, journal)
            elif metadata["phase"] == "prepared":
                _read_metadata_file(
                    target_fd, config, owner_uid, request, require_content=True)
                os.fsync(namespace_fd)
                os.fsync(quarantine_fd)
                metadata["phase"] = "quarantined"
                metadata["file_phase"] = "quarantined"
                metadata["directory_phase"] = "quarantined"
                _store_journal(operations_fd, cleanup_id, journal)

            if target_fd is None:
                raise CiCleanupBrokerError("cleanup_identity_changed")
            if metadata["file_phase"] in {"quarantined", "removing"}:
                file_exists = _read_metadata_file(
                    target_fd, config, owner_uid, request,
                    require_content=(metadata["file_phase"] == "quarantined"))
                if metadata["file_phase"] == "quarantined":
                    metadata["file_phase"] = "removing"
                    metadata["phase"] = "removing"
                    _store_journal(operations_fd, cleanup_id, journal)
                    file_exists = True
                if file_exists:
                    current = os.stat(
                        "workspace.json", dir_fd=target_fd, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) != _identity(
                            request["metadata_file_identity"]):
                        raise CiCleanupBrokerError("cleanup_identity_changed")
                    os.unlink("workspace.json", dir_fd=target_fd)
                    os.fsync(target_fd)
                elif metadata["file_phase"] != "removing":
                    raise CiCleanupBrokerError("cleanup_recovery_not_found")
                else:
                    os.fsync(target_fd)
                metadata["file_phase"] = "removed"
                _store_journal(operations_fd, cleanup_id, journal)
            elif metadata["file_phase"] != "removed":
                raise CiCleanupBrokerError("cleanup_journal_invalid")

            if metadata["directory_phase"] in {"quarantined", "removing"}:
                if metadata["directory_phase"] == "quarantined":
                    metadata["directory_phase"] = "removing"
                    metadata["phase"] = "removing"
                    _store_journal(operations_fd, cleanup_id, journal)
                if os.listdir(target_fd):
                    raise CiCleanupBrokerError("cleanup_metadata_changed")
                info = os.fstat(target_fd)
                if (info.st_dev, info.st_ino) != _identity(
                        request["metadata_directory_identity"]):
                    raise CiCleanupBrokerError("cleanup_identity_changed")
                os.close(target_fd)
                target_fd = None
                current = os.stat(
                    metadata["target"], dir_fd=quarantine_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != _identity(
                        request["metadata_directory_identity"]):
                    raise CiCleanupBrokerError("cleanup_identity_changed")
                os.rmdir(metadata["target"], dir_fd=quarantine_fd)
                os.fsync(quarantine_fd)
                metadata["directory_phase"] = "removed"
            elif metadata["directory_phase"] != "removed":
                raise CiCleanupBrokerError("cleanup_journal_invalid")
            if metadata["file_phase"] != "removed":
                raise CiCleanupBrokerError("cleanup_journal_invalid")
            metadata["phase"] = "removed"
            journal["receipt"] = _metadata_receipt(
                cleanup_id, request, journal["request_digest"])
            _store_journal(operations_fd, cleanup_id, journal)
            return _journal_response(journal)
        finally:
            for fd in (target_fd, source_fd, quarantine_fd,
                       namespace_fd, legacy_fd):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass


def _acknowledge_cleanup(config: dict[str, Any], owner_uid: int,
                         cleanup_id: str, workspace_id: str,
                         authority_digest: str,
                         supplied_request: dict[str, Any]) -> dict[str, Any]:
    with _locked_journal(owner_uid, cleanup_id, create=False) as operations_fd:
        journal = _read_journal(operations_fd, cleanup_id, owner_uid)
        if journal is None:
            raise CiCleanupBrokerError("cleanup_journal_missing")
        supplied_request = _validate_cleanup_request(
            None if journal.get("state") == "acknowledged" else config,
            owner_uid, supplied_request)
        supplied_digest = _request_digest(cleanup_id, owner_uid, supplied_request)
        if journal.get("state") == "acknowledged":
            if (journal["acknowledged_workspace_id"] != workspace_id
                    or journal["acknowledged_authority_digest"] != authority_digest
                    or journal["request_digest"] != supplied_digest):
                raise CiCleanupBrokerError("cleanup_acknowledgement_changed")
            return _journal_response(journal)
        request = _active_request(config, owner_uid, cleanup_id, journal)
        if (request["workspace_id"] != workspace_id
                or request["authority_digest"] != authority_digest
                or journal["request_digest"] != supplied_digest):
            raise CiCleanupBrokerError("cleanup_acknowledgement_changed")
        if (journal["checkout"]["phase"] != "removed"
                or journal["metadata"]["phase"] != "removed"
                or journal["metadata"]["file_phase"] != "removed"
                or journal["metadata"]["directory_phase"] != "removed"):
            raise CiCleanupBrokerError("cleanup_phases_incomplete")
        receipt = {
            "schema_version": 1,
            **_checkout_receipt(cleanup_id, request, journal["request_digest"]),
            "metadata": _metadata_receipt(
                cleanup_id, request, journal["request_digest"]),
            "status": "completed",
        }
        tombstone = {
            "schema_version": 1,
            "cleanup_id": cleanup_id,
            "owner_uid": owner_uid,
            "state": "acknowledged",
            "request_digest": journal["request_digest"],
            "acknowledged_workspace_id": workspace_id,
            "acknowledged_authority_digest": authority_digest,
            "receipt": receipt,
        }
        _store_journal(operations_fd, cleanup_id, tombstone)
        return _journal_response(tombstone)


def _retire_artifact(config: dict[str, Any], owner_uid: int, name: str,
                     expected: tuple[int, int], digest: str,
                     size_bytes: int) -> dict[str, Any]:
    if (re.fullmatch(r"[0-9a-f]{32}\.tar\.gz", name) is None
            or _HEX64.fullmatch(digest) is None
            or not 0 <= size_bytes <= _MAX_ARTIFACT_BYTES):
        raise CiCleanupBrokerError("cleanup_artifact_invalid")
    root_fd = _open_pinned_root(config, "artifacts")
    quarantine_fd = _quarantine_fd(owner_uid)
    file_fd = moved_fd = None
    quarantine_name = None
    try:
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                          dir_fd=root_fd)
        info = os.fstat(file_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid
                or info.st_nlink != 1 or info.st_size != size_bytes
                or (info.st_dev, info.st_ino) != expected):
            raise CiCleanupBrokerError("cleanup_artifact_identity_changed")
        os.close(file_fd)
        file_fd = None
        quarantine_name = f"artifact-{uuid.uuid4().hex}"
        _rename_noreplace(root_fd, name, quarantine_fd, quarantine_name)
        moved_fd = os.open(quarantine_name, os.O_RDONLY | os.O_NOFOLLOW |
                           os.O_CLOEXEC, dir_fd=quarantine_fd)
        moved = os.fstat(moved_fd)
        if (not stat.S_ISREG(moved.st_mode) or moved.st_uid != owner_uid
                or moved.st_nlink != 1 or moved.st_size != size_bytes
                or (moved.st_dev, moved.st_ino) != expected):
            _restore_if_safe(quarantine_fd, quarantine_name, root_fd, name)
            raise CiCleanupBrokerError("cleanup_artifact_identity_changed")
        os.fchown(moved_fd, 0, 0)
        os.fchmod(moved_fd, 0o400)
        hasher = hashlib.sha256()
        while True:
            block = os.read(moved_fd, 1024 * 1024)
            if not block:
                break
            hasher.update(block)
        final = os.fstat(moved_fd)
        if (final.st_size != size_bytes or hasher.hexdigest() != digest):
            _restore_if_safe(quarantine_fd, quarantine_name, root_fd, name)
            raise CiCleanupBrokerError("cleanup_artifact_digest_changed")
        reclaimed_bytes = int(final.st_blocks) * 512
        os.close(moved_fd)
        moved_fd = None
        os.unlink(quarantine_name, dir_fd=quarantine_fd)
        os.fsync(quarantine_fd)
        return {"ok": True, "status": "completed", "reclaimed_bytes": reclaimed_bytes}
    finally:
        for fd in (moved_fd, file_fd, quarantine_fd, root_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _helper_main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    response: dict[str, Any]
    try:
        config, owner_uid = _operation_config()
        if argv == ["capability"]:
            response = _capability(config, owner_uid)
        elif len(argv) == 3 and argv[0] == "begin-cleanup":
            encoded = argv[2]
            if len(encoded) > 48 * 1024:
                raise CiCleanupBrokerError("cleanup_request_too_large")
            try:
                padded = encoded + "=" * ((4 - len(encoded) % 4) % 4)
                raw = base64.b64decode(padded, altchars=b"-_", validate=True)
                request = json.loads(raw)
                if _canonical_json(request) != raw:
                    raise ValueError("request encoding is not canonical")
            except (TypeError, ValueError, UnicodeError) as exc:
                raise CiCleanupBrokerError("cleanup_request_invalid") from exc
            response = _begin_cleanup(config, owner_uid, argv[1], request)
        elif len(argv) == 2 and argv[0] == "resume-checkout":
            response = _resume_checkout(config, owner_uid, argv[1])
        elif len(argv) == 2 and argv[0] == "resume-metadata":
            response = _resume_metadata(config, owner_uid, argv[1])
        elif len(argv) == 5 and argv[0] == "ack-cleanup":
            encoded = argv[4]
            if len(encoded) > 48 * 1024:
                raise CiCleanupBrokerError("cleanup_request_too_large")
            try:
                padded = encoded + "=" * ((4 - len(encoded) % 4) % 4)
                raw = base64.b64decode(padded, altchars=b"-_", validate=True)
                request = json.loads(raw)
                if _canonical_json(request) != raw:
                    raise ValueError("request encoding is not canonical")
            except (TypeError, ValueError, UnicodeError) as exc:
                raise CiCleanupBrokerError("cleanup_request_invalid") from exc
            response = _acknowledge_cleanup(
                config, owner_uid, argv[1], argv[2], argv[3], request)
        elif len(argv) == 6 and argv[0] == "finalize-checkout":
            raise CiCleanupBrokerError("cleanup_journal_required")
        elif len(argv) == 3 and argv[0] == "recover-quarantined-checkout":
            raise CiCleanupBrokerError("cleanup_journal_required")
        elif len(argv) == 7 and argv[0] == "remove-metadata":
            raise CiCleanupBrokerError("cleanup_journal_required")
        elif len(argv) == 6 and argv[0] == "retire-artifact":
            numbers = [int(argv[2]), int(argv[3]), int(argv[5])]
            response = _retire_artifact(config, owner_uid, argv[1],
                (numbers[0], numbers[1]), argv[4], numbers[2])
        else:
            raise CiCleanupBrokerError("cleanup_action_invalid")
    except (ValueError, OSError, CiCleanupBrokerError, json.JSONDecodeError):
        exc = sys.exc_info()[1]
        code = getattr(exc, "code", "cleanup_broker_failed")
        if not isinstance(code, str) or re.fullmatch(r"[a-z0-9_]{1,64}", code) is None:
            code = "cleanup_broker_failed"
        response = {"ok": False, "code": code}
    sys.stdout.write(json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n")
    return 0 if response.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(_helper_main())
