#!/usr/bin/env python3
"""Provision the content-addressed remote image staging helper.

This script is intentionally owner-scoped. It installs no packages, contacts no
registry, and starts no process. Both first-time remote provisioning and remote
service migration invoke it after the exact Sandbox source has been staged.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile


REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
MANIFESTS = (
    ("manifest.json", 1, "sandbox-image-stage-helper-v1", "systemd-cgroup-v2-stage-v1"),
    ("manifest-v2.json", 2, "sandbox-image-stage-helper-v2",
     "systemd-cgroup-v2-batch-stage-v2"),
)


def _rename_no_replace(source: Path, target: Path) -> None:
    """Atomically publish one directory without replacing a concurrent final."""
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    target_bytes = os.fsencode(target)
    if hasattr(libc, "renameat2"):
        result = libc.renameat2(-100, source_bytes, -100, target_bytes, 1)
    elif hasattr(libc, "renamex_np"):
        # Darwin RENAME_EXCL. The remote contract is Linux/renameat2; this
        # branch keeps the same no-replace semantics in local macOS tests.
        result = libc.renamex_np(source_bytes, target_bytes, 0x00000004)
    else:
        raise RuntimeError("atomic no-replace helper publication is unavailable")
    if result == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(error, os.strerror(error), target)
    raise OSError(error, os.strerror(error), target)


def _read_owner_file(path: Path, expected_mode: int, owner_uid: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid
                or stat.S_IMODE(info.st_mode) != expected_mode or info.st_nlink != 1):
            raise RuntimeError(f"unsafe staging helper file identity: {path.name}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(fd)


def _read_source(path: Path, owner_uid: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid:
            raise RuntimeError("staging helper source identity is unsafe")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(fd)


def _ensure_owner_directories(home: Path, target: Path, owner_uid: int) -> None:
    if not home.is_absolute() or target.parent != home / "runtime" / "helpers":
        raise RuntimeError("invalid staging helper directory identity")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        parts = target.parts[1:]
        home_parts = home.parts[1:]
        protected_index = len(home_parts) + 1  # runtime/helpers and descendants
        for index, part in enumerate(parts):
            home_or_runtime = index in {len(home_parts) - 1, len(home_parts)}
            protected = index >= protected_index
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=fd)
            except FileNotFoundError:
                if not (home_or_runtime or protected):
                    raise RuntimeError("staging helper parent directory is missing") from None
                os.mkdir(part, 0o700, dir_fd=fd)
                os.fsync(fd)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=fd)
            info = os.fstat(child)
            if (not stat.S_ISDIR(info.st_mode)
                    or (home_or_runtime and info.st_uid != owner_uid)
                    or (protected and info.st_uid != owner_uid)
                    or (protected and stat.S_IMODE(info.st_mode) != 0o700)
                    or (not home_or_runtime and not protected
                        and info.st_uid not in {0, owner_uid})
                    or (not home_or_runtime and not protected
                        and stat.S_IMODE(info.st_mode) & 0o022)):
                os.close(child)
                raise RuntimeError("staging helper directory ownership or mode is unsafe")
            if home_or_runtime:
                os.fchmod(child, stat.S_IMODE(info.st_mode) & ~0o022)
                os.fsync(child)
            os.close(fd)
            fd = child
    finally:
        os.close(fd)


def _install_helper(path: Path, source: bytes, owner_uid: int) -> None:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o500)
    except FileExistsError:
        pass
    else:
        try:
            os.fchmod(fd, 0o500)
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(source)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(fd)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    installed = _read_owner_file(path, 0o500, owner_uid)
    if hashlib.sha256(installed).digest() != hashlib.sha256(source).digest():
        raise RuntimeError("installed image staging helper digest mismatch")


def _write_manifest(path: Path, payload: dict[str, object], owner_uid: int) -> None:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    if path.exists() or path.is_symlink():
        try:
            existing = _read_owner_file(path, 0o600, owner_uid)
            current = json.loads(existing)
        except (OSError, ValueError, TypeError, RuntimeError):
            raise RuntimeError(f"installed {path.name} staging helper manifest mismatch") from None
        expected_keys = set(payload)
        if (not isinstance(current, dict) or set(current) != expected_keys
                or current.get("schema_version") != payload["schema_version"]
                or current.get("artifact_digest") != payload["artifact_digest"]
                or current.get("entry") != payload["entry"]
                or current.get("capability_revision") != payload["capability_revision"]
                or REVISION_RE.fullmatch(str(current.get("runtime_revision", ""))) is None):
            raise RuntimeError(f"installed {path.name} staging helper manifest mismatch")
        if existing != encoded:
            raise RuntimeError(f"installed {path.name} staging helper manifest mismatch")
        return
    else:
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if _read_owner_file(path, 0o600, owner_uid) != encoded:
                    raise RuntimeError(f"concurrent {path.name} staging helper manifest mismatch")
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    _read_owner_file(path, 0o600, owner_uid)


def _manifest_payloads(digest: str, runtime_revision: str):
    for name, schema_version, entry, capability_revision in MANIFESTS:
        yield name, {
            "schema_version": schema_version,
            "artifact_digest": f"sha256:{digest}",
            "entry": entry,
            "runtime_revision": runtime_revision,
            "capability_revision": capability_revision,
        }


def _validate_bundle(root: Path, source: bytes, digest: str,
                     runtime_revision: str, owner_uid: int) -> None:
    root_info = root.lstat()
    if (not stat.S_ISDIR(root_info.st_mode) or root.is_symlink()
            or root_info.st_uid != owner_uid or stat.S_IMODE(root_info.st_mode) != 0o700):
        raise RuntimeError("installed staging helper directory identity is unsafe")
    installed = _read_owner_file(root / "staging_helper.py", 0o500, owner_uid)
    if hashlib.sha256(installed).hexdigest() != digest or installed != source:
        raise RuntimeError("installed image staging helper digest mismatch")
    for name, payload in _manifest_payloads(digest, runtime_revision):
        try:
            actual = json.loads(_read_owner_file(root / name, 0o600, owner_uid))
        except (OSError, ValueError, TypeError, RuntimeError):
            raise RuntimeError(f"installed {name} staging helper manifest mismatch") from None
        if actual != payload:
            raise RuntimeError(f"installed {name} staging helper manifest mismatch")


def provision(sandbox_home: Path, runtime_revision: str) -> dict[str, str]:
    if REVISION_RE.fullmatch(runtime_revision) is None:
        raise RuntimeError("Sandbox runtime revision is unavailable or invalid")
    owner_uid = os.geteuid()
    source_path = sandbox_home / "sb-src" / "sandbox" / "hosting" / "images" / "staging_helper.py"
    source = _read_source(source_path, owner_uid)
    digest = hashlib.sha256(source).hexdigest()
    # Revision participates in the immutable directory identity. A migration can
    # therefore prepare a new manifest without rewriting authority below a
    # helper that an already-running staging unit may still have open.
    root = (sandbox_home / "runtime" / "helpers" / "image-stage"
            / f"sha256-{digest}-revision-{runtime_revision}")
    _ensure_owner_directories(sandbox_home, root.parent, owner_uid)
    if root.exists() or root.is_symlink():
        _validate_bundle(root, source, digest, runtime_revision, owner_uid)
        return {"artifact_digest": f"sha256:{digest}", "runtime_revision": runtime_revision}
    temporary = Path(tempfile.mkdtemp(prefix=".image-stage-helper.", dir=root.parent))
    published = False
    try:
        temporary.chmod(0o700)
        _install_helper(temporary / "staging_helper.py", source, owner_uid)
        for name, payload in _manifest_payloads(digest, runtime_revision):
            _write_manifest(temporary / name, payload, owner_uid)
        _validate_bundle(temporary, source, digest, runtime_revision, owner_uid)
        directory_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        try:
            _rename_no_replace(temporary, root)
        except FileExistsError:
            _validate_bundle(root, source, digest, runtime_revision, owner_uid)
        else:
            published = True
            parent_fd = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
    finally:
        if not published and temporary.exists():
            shutil.rmtree(temporary)
    _validate_bundle(root, source, digest, runtime_revision, owner_uid)
    return {"artifact_digest": f"sha256:{digest}", "runtime_revision": runtime_revision}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sandbox-home", type=Path, required=True)
    parser.add_argument("--runtime-revision", required=True)
    args = parser.parse_args()
    result = provision(args.sandbox_home, args.runtime_revision)
    print("image staging helper provisioned at "
          f"{result['artifact_digest']} revision {result['runtime_revision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
