#!/usr/bin/python3
"""Fixed Linux privileged boundary for terminal CI workspace removal.

The broker has no caller-selected absolute paths. Its installed entry point is
root-owned, its roots are pinned by a root-owned configuration file, and every
removal first moves the exact checked entry into a root-only quarantine.
"""

from __future__ import annotations

import ctypes
import errno
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


def finalize_checkout(operation_name: str, operation_identity: dict[str, int],
                      checkout_identity: dict[str, int]) -> None:
    if not isinstance(operation_name, str) or _HEX32.fullmatch(operation_name) is None:
        raise CiCleanupBrokerError("cleanup_identity_invalid")
    op_dev, op_ino = _identity(operation_identity)
    tree_dev, tree_ino = _identity(checkout_identity)
    _invoke("finalize-checkout", operation_name, str(op_dev), str(op_ino),
            str(tree_dev), str(tree_ino))


def recover_quarantined_checkout(checkout_identity: dict[str, int]) -> None:
    device, inode = _identity(checkout_identity)
    _invoke("recover-quarantined-checkout", str(device), str(inode))


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
    if (_NAMESPACE.fullmatch(namespace or "") is None
            or _LABEL.fullmatch(label or "") is None):
        raise CiCleanupBrokerError("cleanup_metadata_invalid")
    dir_dev, dir_ino = _identity(directory_identity)
    file_dev, file_ino = _identity(file_identity)
    _invoke("remove-metadata", namespace, label, str(dir_dev), str(dir_ino),
            str(file_dev), str(file_ino))


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


def _quarantine_fd(owner_uid: int) -> int:
    base_fd = owner_fd = None
    try:
        base_fd = _open_absolute(QUARANTINE_ROOT)
        base_info = os.fstat(base_fd)
        if base_info.st_uid != 0 or stat.S_IMODE(base_info.st_mode) != 0o700:
            raise CiCleanupBrokerError("cleanup_quarantine_unsafe")
        owner_fd = _open_child_dir(base_fd, str(owner_uid), owner=0, mode=0o700)
        quarantine_fd = _open_child_dir(
            owner_fd, "quarantine", owner=0, mode=0o700)
        return quarantine_fd
    except OSError as exc:
        raise CiCleanupBrokerError("cleanup_quarantine_unavailable") from exc
    finally:
        for fd in (owner_fd, base_fd):
            if fd is not None:
                os.close(fd)


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
        if len({os.fstat(fd).st_dev for fd in descriptors}) != 1:
            raise CiCleanupBrokerError("cleanup_cross_device_unavailable")
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
    return {"ok": True, "status": "ready", "owner_uid": owner_uid}


def _finalize_checkout(config: dict[str, Any], owner_uid: int,
                       name: str, expected_op: tuple[int, int],
                       expected_tree: tuple[int, int]) -> dict[str, Any]:
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
        elif len(argv) == 6 and argv[0] == "finalize-checkout":
            name = argv[1]
            numbers = [int(value) for value in argv[2:]]
            response = _finalize_checkout(config, owner_uid, name,
                (numbers[0], numbers[1]), (numbers[2], numbers[3]))
        elif len(argv) == 3 and argv[0] == "recover-quarantined-checkout":
            response = _recover_quarantined_checkout(config, owner_uid,
                                               (int(argv[1]), int(argv[2])))
        elif len(argv) == 7 and argv[0] == "remove-metadata":
            numbers = [int(value) for value in argv[3:]]
            response = _remove_metadata(config, owner_uid, argv[1], argv[2],
                (numbers[0], numbers[1]), (numbers[2], numbers[3]))
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
