"""Personal shell-secret file support for permanent Sandbox hosting."""
from __future__ import annotations

import os
import hashlib
import re
import secrets
import shlex
import json
import time
import tempfile
import fcntl
import stat
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path


DEFAULT_SECRET_FILE = Path.home() / ".zshrc.secrets"
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_HELD_SOURCE_LOCKS: ContextVar[frozenset[str]] = ContextVar(
    "hosting_held_secret_source_locks", default=frozenset())


class SecretError(ValueError):
    pass


def secret_file() -> Path:
    """Return the user-selected file without resolving or exposing its values."""
    return Path(os.environ.get("SANDBOX_SECRETS_FILE", DEFAULT_SECRET_FILE)).expanduser()


def read_secret_file(path: Path | None = None) -> dict[str, str]:
    """Read simple shell assignments; commands and expansions are rejected."""
    path = path or secret_file()
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ASSIGNMENT.fullmatch(raw)
        if not match:
            raise SecretError(f"unsupported syntax in {path} line {number}")
        key, encoded = match.groups()
        quoted_literal = encoded.strip().startswith("'") and encoded.strip().endswith("'")
        if "`" in encoded or "$(" in encoded or ("$" in encoded and not quoted_literal):
            raise SecretError(f"shell expansion is not allowed in {path} line {number}")
        try:
            words = shlex.split(encoded, posix=True)
        except ValueError as exc:
            raise SecretError(f"invalid quoted value in {path} line {number}") from exc
        if len(words) > 1:
            raise SecretError(f"unquoted whitespace in {path} line {number}")
        values[key] = words[0] if words else ""
    return values


def resolve_secret(key: str, path: Path | None = None) -> str | None:
    """Environment wins for CI; the personal file supports non-login CLI runs."""
    value = os.environ.get(key)
    if value:
        return value
    return read_secret_file(path).get(key) or None


def write_secret(key: str, value: str, path: Path | None = None) -> None:
    selected = Path(path or secret_file()).expanduser()
    lock_identity = str(selected)
    if lock_identity in _HELD_SOURCE_LOCKS.get():
        _write_secret_unlocked(key, value, selected)
        return
    with _secret_source_lock(selected):
        _write_secret_unlocked(key, value, selected)


def _write_secret_unlocked(key: str, value: str, path: Path) -> None:
    """Set one exported key atomically without returning its value.

    Only the target assignment is rewritten.  Comments, section banners, and the
    ordering produced by ``sb secrets organize`` are preserved, so a hosting or
    Cloudflare write no longer flattens the file back into one sorted block.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key or ""):
        raise SecretError("invalid secret key")
    from sandbox.secrets.parser import (
        SecretParseError, parse_document, render_assignment, replace_assignment,
    )
    read_secret_file(path)  # keep the legacy fail-closed syntax contract
    current = path.read_bytes() if path.exists() else b""
    if not current.strip():
        current = b"# Personal secrets. Keep this file out of Git.\n"
    try:
        document = parse_document(current)
        if key in document.entries:
            updated = replace_assignment(document, key, value)
        else:
            prefix = b"" if current.endswith(b"\n") else b"\n"
            line = render_assignment(key, value, exported=True).encode("utf-8")
            updated = current + prefix + line + b"\n"
    except SecretParseError as exc:
        # The code is a stable reason; the offending line is never included.
        raise SecretError(f"cannot update {path}: {exc.code}") from None
    temporary = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(updated)
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def generate_secret() -> str:
    return secrets.token_urlsafe(36)


def migrate_zshrc(path: Path | None = None, zshrc: Path | None = None) -> list[str]:
    """Move known credential exports into the sourced personal secret file."""
    path = path or secret_file()
    zshrc = zshrc or Path.home() / ".zshrc"
    names = {
        "TEMPLATELY_API_KEY", "TEMPLATELY_API_KEY_DEV", "TEMPLATELY_API_KEY_FREE",
        "TEMPLATELY_API_KEY_FREE_DEV", "TEMPLATELY_API_KEY_UNVERIFIED_DEV",
        "WORKOS_CLIENT_ID", "WORKOS_API_KEY", "WORKOS_REDIRECT_URI", "OPENAI_API_KEY",
    }
    source = f'[[ -r "$HOME/{path.name}" ]] && source "$HOME/{path.name}"'
    existing = read_secret_file(path)
    kept: list[str] = []
    moved: list[str] = []
    for raw in zshrc.read_text().splitlines():
        match = _ASSIGNMENT.fullmatch(raw)
        if match and match.group(1) in names:
            key = match.group(1)
            encoded = match.group(2)
            try:
                words = shlex.split(encoded, posix=True)
            except ValueError as exc:
                raise SecretError(f"cannot migrate malformed {key}") from exc
            existing[key] = words[0] if words else ""
            moved.append(key)
            continue
        kept.append(raw)
    if source not in kept:
        kept.append("")
        kept.append("# Personal API and deployment secrets (chmod 0600).")
        kept.append(source)
    if moved:
        for name in moved:
            write_secret(name, existing[name], path)
        zshrc.write_text("\n".join(kept) + "\n")
    return moved
_HOSTING_BINDING_KEY = "hosting/recovery-binding-key-v1"
MAX_HOSTING_BINDING_REVISION = 1_000_000
MAX_HOSTING_BINDING_METADATA_BYTES = 64 * 1024


def _hosting_binding_key_version(key: bytes) -> str:
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("hosting recovery binding key is invalid")
    return "v1-" + hashlib.sha256(key).hexdigest()[:16]


def prepare_hosting_binding_key() -> tuple[bytes, str]:
    """Prepare exact key authority in memory without creating any path."""
    key = os.urandom(32)
    return key, _hosting_binding_key_version(key)


def hosting_binding_key(path: Path | None = None, *, create: bool = True,
                        prepared: tuple[bytes, str] | None = None) -> tuple[bytes, str]:
    """Load or create the owner-only local key used for opaque secret identity.

    The key is never returned by a CLI result and is kept outside managed host
    receipts. Losing or changing it deliberately invalidates older evidence.
    """
    from sandbox.core._paths import RUNTIME_DIR

    key_path = Path(path or (RUNTIME_DIR / "hosting" / "recovery-binding.key")).expanduser()
    if key_path.is_symlink():
        raise ValueError("hosting recovery binding key is invalid")
    key_path = key_path.parent.resolve() / key_path.name
    if not key_path.exists() and not create:
        # Observation is read-only. Missing authority must not create even its
        # parent directory as a side effect.
        raise ValueError("hosting recovery binding key is unavailable")
    if key_path.exists():
        if key_path.is_symlink() or not key_path.is_file():
            raise ValueError("hosting recovery binding key is invalid")
        descriptor = os.open(
            key_path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
            getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                    info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077):
                raise ValueError("hosting recovery binding key is invalid")
            with os.fdopen(descriptor, "rb") as handle:
                key = handle.read()
                descriptor = -1
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    else:
        if not create:
            raise ValueError("hosting recovery binding key is unavailable")
        if prepared is None:
            key, prepared_version = prepare_hosting_binding_key()
        else:
            key, prepared_version = prepared
            if prepared_version != _hosting_binding_key_version(key):
                raise ValueError("hosting recovery binding key is invalid")
        _ensure_directory_durable(key_path.parent)
        descriptor = os.open(
            key_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
            handle.flush()
            os.fsync(handle.fileno())
        parent = os.open(
            key_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    version = _hosting_binding_key_version(key)
    if prepared is not None and version != prepared[1]:
        raise ValueError("hosting recovery binding key changed before publication")
    return key, version


def _file_epoch(selected: Path) -> dict | None:
    try:
        info = selected.lstat()
    except OSError:
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
            info.st_nlink != 1 or
            stat.S_IMODE(info.st_mode) & 0o077):
        return None
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}


def _secret_file_epoch(path: Path | None = None) -> dict | None:
    return _file_epoch(Path(path or secret_file()).expanduser())


def _binding_key_epoch(path: Path | None = None) -> dict | None:
    from sandbox.core._paths import RUNTIME_DIR
    return _file_epoch(Path(path or (
        RUNTIME_DIR / "hosting" / "recovery-binding.key")).expanduser())


def _ensure_directory_durable(path: Path) -> None:
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise ValueError("hosting recovery directory is unsafe")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        parent = os.open(directory.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent)
        finally:
            os.close(parent)


def _metadata_digest(value: dict) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def opaque_hosting_digest(raw_digest: str, *, key: bytes, label: str) -> str:
    """Blind a non-secret digest that was computed over secret-bearing bytes."""
    import hmac
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", raw_digest or ""):
        raise ValueError("hosting digest is invalid")
    return "sha256:" + hmac.new(
        key, label.encode() + b"\0" + raw_digest.encode(), hashlib.sha256).hexdigest()


@contextmanager
def _secret_source_lock(path: Path, *, timeout_seconds: float = 5):
    safe_name = path.name.lstrip(".")
    lock_path = path.with_name(f".{safe_name}.sb-secrets.lock")
    descriptor = os.open(
        lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    deadline = time.monotonic() + timeout_seconds
    token = None
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1):
            raise ValueError("hosting secret source lock is unsafe")
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError("hosting secret source is busy")
                time.sleep(0.02)
        identity = str(path)
        token = _HELD_SOURCE_LOCKS.set(_HELD_SOURCE_LOCKS.get() | {identity})
        yield
    finally:
        if token is not None:
            _HELD_SOURCE_LOCKS.reset(token)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def hosting_binding_broker_lock(path: Path | None = None, *,
                                timeout_seconds: float = 5):
    """Serialize opaque binding validation/writes without reading secret values."""
    from sandbox.core._paths import RUNTIME_DIR
    lock_path = (Path(path).expanduser() if path is not None else None)
    with _secret_source_lock(secret_file(), timeout_seconds=timeout_seconds):
        # The guarded secret source lock already serializes every binding
        # reader/writer. Do not create the recovery authority directory merely
        # to preflight a request that may be rejected as oversized.
        if lock_path is None:
            yield
            return
        _ensure_directory_durable(lock_path.parent)
        descriptor = os.open(
            lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError("hosting secret binding broker is busy")
                    time.sleep(0.02)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def write_hosting_binding_metadata(target_key: str, values: dict[str, str], *,
                                    key: bytes, key_version: str,
                                    path: Path | None = None,
                                    secret_path: Path | None = None,
                                    key_path: Path | None = None,
                                    prepared: dict | None = None) -> dict:
    """Persist broker-owned opaque bindings without persisting secret material."""
    from sandbox.core._paths import RUNTIME_DIR
    from sandbox.hosting.recovery.models import secret_binding_identities

    root = Path(path or (RUNTIME_DIR / "hosting" / "secret-bindings")).expanduser()
    if root.is_symlink():
        raise ValueError("hosting secret binding metadata is invalid")
    target_digest = hashlib.sha256(target_key.encode()).hexdigest()
    destination = root / f"{target_digest}.json"
    if destination.is_symlink():
        raise ValueError("hosting secret binding metadata is invalid")
    revision = _next_hosting_binding_revision(destination)
    created_at = (prepared or {}).get("created_at", int(time.time()))
    metadata = _build_hosting_binding_metadata(
        target_digest, values, key=key, key_version=key_version,
        revision=revision, created_at=created_at, secret_path=secret_path,
        key_path=key_path)
    if prepared is not None and metadata != prepared:
        raise ValueError("hosting secret binding metadata changed before publication")
    _ensure_directory_durable(root)
    root.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(prefix="binding-", suffix=".json", dir=root)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(metadata, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    parent = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return {"metadata_id": metadata["metadata_id"], "key_version": key_version,
            "revision": revision}


def _next_hosting_binding_revision(destination: Path) -> int:
    if destination.is_symlink():
        raise ValueError("hosting secret binding metadata is invalid")
    if not destination.exists():
        return 1
    if not destination.is_file():
        raise ValueError("hosting secret binding metadata is invalid")
    try:
        previous = json.loads(destination.read_text())
    except (OSError, ValueError, TypeError):
        raise ValueError("hosting secret binding metadata revision is invalid") from None
    revision = previous.get("revision") if isinstance(previous, dict) else None
    if (isinstance(revision, bool) or not isinstance(revision, int) or
            revision < 1 or revision >= MAX_HOSTING_BINDING_REVISION):
        raise ValueError("hosting secret binding metadata revision is invalid")
    return revision + 1


def _build_hosting_binding_metadata(target_digest: str, values: dict[str, str], *,
                                    key: bytes, key_version: str, revision: int,
                                    created_at: int, secret_path: Path | None,
                                    key_path: Path | None) -> dict:
    from sandbox.hosting.recovery.models import secret_binding_identities

    if (not isinstance(key_version, str) or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", key_version) is None):
        raise ValueError("secret binding key version is invalid")
    secret_epoch = _secret_file_epoch(secret_path)
    if secret_epoch is None:
        raise ValueError("hosting secret source is unavailable or unsafe")
    metadata = {
        "schema_version": 1, "target_digest": "sha256:" + target_digest,
        "revision": revision, "key_version": key_version,
        "key_identity": "sha256:" + hashlib.sha256(key).hexdigest(),
        "bindings": secret_binding_identities(
            values, key=key, key_version=key_version)["bindings"],
        "secret_file_epoch": secret_epoch,
        "environment_backed": sorted(
            name for name in values if os.environ.get(name) is not None),
        "created_at": created_at,
    }
    metadata["metadata_id"] = _metadata_digest(metadata)
    return metadata


def prospective_hosting_binding_reference(target_key: str,
                                           values: dict[str, str], *,
                                           key: bytes, key_version: str,
                                           path: Path | None = None,
                                           secret_path: Path | None = None,
                                           key_path: Path | None = None) -> dict:
    """Compute exact next broker metadata without writing any authority."""
    from sandbox.core._paths import RUNTIME_DIR
    root = Path(path or (RUNTIME_DIR / "hosting" / "secret-bindings")).expanduser()
    if root.is_symlink():
        raise ValueError("hosting secret binding metadata is invalid")
    target_digest = hashlib.sha256(target_key.encode()).hexdigest()
    destination = root / f"{target_digest}.json"
    revision = _next_hosting_binding_revision(destination)
    return _build_hosting_binding_metadata(
        target_digest, values, key=key, key_version=key_version,
        revision=revision, created_at=int(time.time()), secret_path=secret_path,
        key_path=key_path)


def read_hosting_binding_metadata(target_key: str, *, path: Path | None = None,
                                  secret_path: Path | None = None,
                                  key_path: Path | None = None) -> dict:
    """Read only opaque broker metadata; never parse or materialize secret values."""
    from sandbox.core._paths import RUNTIME_DIR

    selected_root = Path(path or (
        RUNTIME_DIR / "hosting" / "secret-bindings")).expanduser()
    try:
        if selected_root.is_symlink():
            raise ValueError
        root = selected_root.resolve(strict=True)
        root_info = root.lstat()
        if (not stat.S_ISDIR(root_info.st_mode) or
                root_info.st_uid != os.geteuid() or
                stat.S_IMODE(root_info.st_mode) != 0o700):
            raise ValueError
        root_fd = os.open(
            root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
            getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    except (OSError, ValueError):
        raise ValueError("hosting secret binding metadata is invalid") from None
    target_digest = hashlib.sha256(target_key.encode()).hexdigest()
    try:
        root_open_info = os.fstat(root_fd)
        if (root_open_info.st_dev, root_open_info.st_ino) != (root_info.st_dev, root_info.st_ino):
            raise ValueError("hosting secret binding metadata is invalid")
        try:
            descriptor = os.open(
                f"{target_digest}.json", os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
                getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                dir_fd=root_fd)
        except OSError:
            raise ValueError("hosting secret binding metadata is unavailable") from None
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                    info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or
                    info.st_size > MAX_HOSTING_BINDING_METADATA_BYTES):
                raise ValueError("hosting secret binding metadata is invalid")
            chunks = bytearray()
            while len(chunks) <= MAX_HOSTING_BINDING_METADATA_BYTES:
                chunk = os.read(descriptor, min(
                    65536, MAX_HOSTING_BINDING_METADATA_BYTES + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
            if len(chunks) > MAX_HOSTING_BINDING_METADATA_BYTES:
                raise ValueError("hosting secret binding metadata is invalid")
        finally:
            os.close(descriptor)
    finally:
        os.close(root_fd)
    try:
        metadata = json.loads(bytes(chunks))
    except (TypeError, ValueError, UnicodeDecodeError):
        raise ValueError("hosting secret binding metadata is invalid") from None
    required = {"schema_version", "target_digest", "revision", "key_version",
                "key_identity", "bindings", "secret_file_epoch",
                "environment_backed", "created_at", "metadata_id"}
    if not isinstance(metadata, dict) or set(metadata) != required:
        raise ValueError("hosting secret binding metadata is invalid")
    claimed = metadata.pop("metadata_id", None) if isinstance(metadata, dict) else None
    revision = metadata.get("revision")
    bindings = metadata.get("bindings")
    epoch = metadata.get("secret_file_epoch")
    shape_valid = (
        isinstance(revision, int) and not isinstance(revision, bool) and
        1 <= revision <= MAX_HOSTING_BINDING_REVISION and
        isinstance(metadata.get("key_version"), str) and
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", metadata["key_version"]) is not None and
        isinstance(metadata.get("created_at"), int) and
        not isinstance(metadata.get("created_at"), bool) and metadata["created_at"] >= 0 and
        isinstance(bindings, list) and len(bindings) <= 64 and
        all(isinstance(item, dict) and set(item) == {"reference", "digest"} and
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", item.get("reference", "")) and
            re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("digest", ""))
            for item in bindings) and
        len({item["reference"] for item in bindings}) == len(bindings) and
        isinstance(epoch, dict) and
        set(epoch) == {"device", "inode", "size", "mtime_ns", "ctime_ns"} and
        all(isinstance(item, int) and not isinstance(item, bool) and item >= 0
            for item in epoch.values()) and metadata.get("environment_backed") == [] and
        re.fullmatch(r"sha256:[0-9a-f]{64}", metadata.get("key_identity", "")) is not None and
        re.fullmatch(r"sha256:[0-9a-f]{64}", claimed or "") is not None)
    secret_epoch = _secret_file_epoch(secret_path)
    try:
        current_key, _current_key_version = hosting_binding_key(key_path, create=False)
    except ValueError:
        current_key = None
    if (secret_epoch is None or current_key is None or
            not shape_valid or metadata.get("schema_version") != 1 or
            metadata.get("target_digest") != "sha256:" + target_digest or
            claimed != _metadata_digest(metadata) or metadata.get("environment_backed") or
            metadata.get("secret_file_epoch") != secret_epoch or
            metadata.get("key_identity") !=
            "sha256:" + hashlib.sha256(current_key).hexdigest()):
        raise ValueError("hosting secret binding metadata is stale")
    return {"metadata_id": claimed, "key_version": metadata.get("key_version"),
            "revision": metadata.get("revision")}
