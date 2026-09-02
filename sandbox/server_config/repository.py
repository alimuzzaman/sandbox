"""Owner-only, incarnation-scoped storage for server configuration state."""

from __future__ import annotations

from contextlib import contextmanager
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
from typing import Iterator, Mapping


_INCARNATION = re.compile(r"inc_[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_DIR_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_READ_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


def _canonical_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("state_shape_invalid")
        return {key: _canonical_value(item) for key, item in value.items()}
    raise ValueError("state_shape_invalid")


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            _canonical_value(value), sort_keys=True, separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        + b"\n"
    )


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _verify_directory(facts: os.stat_result, *, private: bool) -> None:
    forbidden_mode = 0o077 if private else 0o022
    if (
        not stat.S_ISDIR(facts.st_mode)
        or facts.st_uid != os.getuid()
        or facts.st_mode & forbidden_mode
    ):
        raise ValueError("repository_unsafe")


def _open_directory_path(path: Path, *, private: bool) -> int:
    try:
        descriptor = os.open(path, _DIR_FLAGS)
    except OSError:
        raise ValueError("repository_unsafe") from None
    try:
        _verify_directory(os.fstat(descriptor), private=private)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _open_directory_at(
    parent_descriptor: int, name: str, *, absent_ok: bool = False
) -> int | None:
    try:
        descriptor = os.open(name, _DIR_FLAGS, dir_fd=parent_descriptor)
    except FileNotFoundError:
        if absent_ok:
            return None
        raise ValueError("repository_unsafe") from None
    except OSError:
        raise ValueError("repository_unsafe") from None
    try:
        _verify_directory(os.fstat(descriptor), private=True)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _ensure_directory_at(parent_descriptor: int, name: str) -> int:
    descriptor = _open_directory_at(parent_descriptor, name, absent_ok=True)
    if descriptor is not None:
        return descriptor
    try:
        os.mkdir(name, 0o700, dir_fd=parent_descriptor)
    except FileExistsError:
        pass
    except OSError:
        raise ValueError("repository_unsafe") from None
    descriptor = _open_directory_at(parent_descriptor, name)
    assert descriptor is not None
    os.fsync(parent_descriptor)
    return descriptor


def _read_owned_file_at(
    parent_descriptor: int,
    name: str,
    *,
    maximum: int = 1_048_576,
    expected_mode: int = 0o600,
    absent_ok: bool = False,
) -> bytes | None:
    try:
        descriptor = os.open(name, _FILE_READ_FLAGS, dir_fd=parent_descriptor)
    except FileNotFoundError:
        if absent_ok:
            return None
        raise ValueError("repository_unsafe") from None
    except OSError:
        raise ValueError("repository_unsafe") from None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_mode & 0o777 != expected_mode
        ):
            raise ValueError("repository_unsafe")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns,
        )
        if before_identity != after_identity:
            raise ValueError("repository_unsafe")
        if len(payload) > maximum or len(payload) != before.st_size:
            raise ValueError("repository_unsafe")
        return payload
    finally:
        os.close(descriptor)


def _temporary_file_at(parent_descriptor: int, mode: int) -> tuple[int, str]:
    for _attempt in range(32):
        name = ".server-config-" + secrets.token_hex(16)
        try:
            descriptor = os.open(
                name,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0),
                mode,
                dir_fd=parent_descriptor,
            )
            os.fchmod(descriptor, mode)
            return descriptor, name
        except FileExistsError:
            continue
    raise ValueError("repository_unsafe")


def _write_and_sync(descriptor: int, payload: bytes) -> os.stat_result:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise ValueError("repository_unsafe")
        offset += written
    os.fsync(descriptor)
    return os.fstat(descriptor)


def _atomic_write_at(
    parent_descriptor: int, name: str, payload: bytes, *, mode: int = 0o600
) -> None:
    try:
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        current = None
    if current is not None and (
        not stat.S_ISREG(current.st_mode)
        or current.st_uid != os.getuid()
        or current.st_mode & 0o077
    ):
        raise ValueError("repository_unsafe")
    temporary_name: str | None = None
    descriptor: int | None = None
    try:
        descriptor, temporary_name = _temporary_file_at(parent_descriptor, mode)
        written = _write_and_sync(descriptor, payload)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary_name,
            name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        temporary_name = None
        installed = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            installed.st_dev != written.st_dev
            or installed.st_ino != written.st_ino
            or not stat.S_ISREG(installed.st_mode)
            or installed.st_uid != os.getuid()
            or installed.st_mode & 0o077
        ):
            raise ValueError("repository_unsafe")
        os.fsync(parent_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass


def _write_immutable_at(
    parent_descriptor: int,
    name: str,
    payload: bytes,
    *,
    conflict: str,
    mode: int = 0o600,
) -> None:
    existing = _read_owned_file_at(
        parent_descriptor, name, maximum=max(len(payload), 1),
        expected_mode=mode, absent_ok=True,
    )
    if existing is not None:
        if existing != payload:
            raise ValueError(conflict)
        return
    temporary_name: str | None = None
    descriptor: int | None = None
    try:
        descriptor, temporary_name = _temporary_file_at(parent_descriptor, mode)
        written = _write_and_sync(descriptor, payload)
        os.close(descriptor)
        descriptor = None
        linked = False
        try:
            os.link(
                temporary_name,
                name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            linked = True
        except FileExistsError:
            existing = _read_owned_file_at(
                parent_descriptor, name, maximum=max(len(payload), 1)
            )
            if existing != payload:
                raise ValueError(conflict) from None
        if linked:
            installed = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            if (
                installed.st_dev != written.st_dev
                or installed.st_ino != written.st_ino
                or not stat.S_ISREG(installed.st_mode)
                or installed.st_uid != os.getuid()
                or installed.st_mode & 0o777 != mode
            ):
                raise ValueError("repository_unsafe")
        os.fsync(parent_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass


def _read_json_at(parent_descriptor: int, name: str) -> object | None:
    payload = _read_owned_file_at(parent_descriptor, name, absent_ok=True)
    if payload is None:
        return None
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("state_corrupt") from None
    if not isinstance(value, dict):
        raise ValueError("state_corrupt")
    return value


def _generation_references(value: object) -> set[str]:
    references: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key.endswith("generation_id") and item is not None:
                if not isinstance(item, str) or _DIGEST.fullmatch(item) is None:
                    raise ValueError("state_corrupt")
                references.add(item)
            references.update(_generation_references(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            references.update(_generation_references(item))
    return references


def _retention_references(
    state: object | None, transaction: object | None
) -> set[str]:
    for value in (state, transaction):
        if value is not None and (
            not isinstance(value, Mapping) or value.get("schema") != 1
        ):
            raise ValueError("state_corrupt")
    if transaction is not None:
        terminal = transaction.get("terminal")
        if terminal is None and transaction.get("phase") not in {
            "requested", "prepared", "validated", "activating", "reloading",
            "observing_ready", "restoring_prior", "recovery_reloading",
            "recovery_observing_ready",
        }:
            raise ValueError("state_corrupt")
        if terminal is not None and terminal not in {
            "active", "no_op", "refused", "rolled_back", "conflict", "recovery_needed",
        }:
            raise ValueError("state_corrupt")
        if not all(
            isinstance(transaction.get(key), str)
            for key in ("prior_generation_id", "candidate_generation_id")
        ):
            raise ValueError("state_corrupt")
    references: set[str] = set()
    for value in (state, transaction):
        references.update(_generation_references(value))
    return references


class RepositoryMutation:
    """Operations that are valid only while one incarnation lock/root fd is held."""

    def __init__(self, repository: "ServerConfigRepository", root_descriptor: int):
        self._repository = repository
        self._root_descriptor = root_descriptor

    def store_fragment(self, payload: bytes) -> str:
        return self._repository._store_fragment_at(self._root_descriptor, payload)

    def read_fragment(self, content_id: str) -> bytes:
        return self._repository._read_fragment_at(self._root_descriptor, content_id)

    def publish_generation(
        self,
        files: Mapping[str, bytes],
        manifest: Mapping[str, object],
        *,
        generation_id: str | None = None,
    ) -> str:
        return self._repository._publish_generation_at(
            self._root_descriptor,
            files,
            manifest,
            generation_id=generation_id,
        )

    def write_state(self, value: Mapping[str, object]) -> None:
        self._repository._write_json_at(self._root_descriptor, "state.json", value)

    def write_receipt(self, value: Mapping[str, object]) -> None:
        self.write_state(value)

    def write_transaction(self, value: Mapping[str, object]) -> None:
        self._repository._write_json_at(
            self._root_descriptor, "transaction.json", value
        )

    def write_journal(self, value: Mapping[str, object]) -> None:
        self.write_transaction(value)

    def read_state(self) -> object | None:
        return _read_json_at(self._root_descriptor, "state.json")

    def read_receipt(self) -> object | None:
        return self.read_state()

    def read_transaction(self) -> object | None:
        return _read_json_at(self._root_descriptor, "transaction.json")

    def read_journal(self) -> object | None:
        return self.read_transaction()

    def prune_unreferenced_generations(
        self, *, retain: tuple[str, ...] = ()
    ) -> tuple[str, ...]:
        return self._repository._prune_unreferenced_generations_at(
            self._root_descriptor, retain=retain
        )

    def clear_transaction(self) -> None:
        transaction = self.read_transaction()
        if transaction is None:
            return
        _retention_references(None, transaction)
        if transaction.get("terminal") not in {
            "active", "no_op", "refused", "rolled_back", "conflict",
        }:
            raise ValueError("transaction_not_clearable")
        try:
            os.unlink("transaction.json", dir_fd=self._root_descriptor)
        except FileNotFoundError:
            return
        os.fsync(self._root_descriptor)


class ServerConfigRepository:
    def __init__(self, base: str | os.PathLike[str], incarnation: str):
        if not isinstance(incarnation, str) or not _INCARNATION.fullmatch(incarnation):
            raise ValueError("instance_incarnation_invalid")
        self.base = Path(base)
        if not self.base.name:
            raise ValueError("repository_unsafe")
        self.incarnation = incarnation
        self.root = self.base / incarnation
        self.fragments_dir = self.root / "fragments"
        self.generations_dir = self.root / "generations"
        self.state_path = self.root / "state.json"
        self.receipt_path = self.state_path
        self.transaction_path = self.root / "transaction.json"
        self.journal_path = self.transaction_path
        self.lock_path = self.root / ".lock"

    @contextmanager
    def _root_descriptor(self, *, create: bool) -> Iterator[int | None]:
        parent_descriptor = _open_directory_path(self.base.parent, private=False)
        base_descriptor: int | None = None
        root_descriptor: int | None = None
        try:
            if create:
                base_descriptor = _ensure_directory_at(parent_descriptor, self.base.name)
                root_descriptor = _ensure_directory_at(base_descriptor, self.incarnation)
            else:
                base_descriptor = _open_directory_at(
                    parent_descriptor, self.base.name, absent_ok=True
                )
                if base_descriptor is None:
                    yield None
                    return
                root_descriptor = _open_directory_at(
                    base_descriptor, self.incarnation, absent_ok=True
                )
                if root_descriptor is None:
                    yield None
                    return
            yield root_descriptor
        finally:
            if root_descriptor is not None:
                os.close(root_descriptor)
            if base_descriptor is not None:
                os.close(base_descriptor)
            os.close(parent_descriptor)

    def observe(self) -> dict[str, object]:
        with self._root_descriptor(create=False) as root_descriptor:
            if root_descriptor is None:
                return {"status": "absent"}
            state = _read_json_at(root_descriptor, "state.json")
            return {"status": "present", "state": state}

    def initialize(self) -> None:
        with self._root_descriptor(create=True) as root_descriptor:
            assert root_descriptor is not None
            fragments_descriptor = _ensure_directory_at(root_descriptor, "fragments")
            generations_descriptor = _ensure_directory_at(root_descriptor, "generations")
            os.close(fragments_descriptor)
            os.close(generations_descriptor)
            os.fsync(root_descriptor)

    @contextmanager
    def locked(self, *, deadline: float | None = None) -> Iterator[RepositoryMutation]:
        self.initialize()
        with self._root_descriptor(create=False) as root_descriptor:
            assert root_descriptor is not None
            try:
                descriptor = os.open(
                    ".lock",
                    os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=root_descriptor,
                )
            except OSError:
                raise ValueError("repository_unsafe") from None
            acquired = False
            try:
                facts = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(facts.st_mode)
                    or facts.st_uid != os.getuid()
                    or facts.st_mode & 0o077
                ):
                    raise ValueError("repository_unsafe")
                while True:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                        break
                    except OSError as error:
                        if error.errno not in {errno.EACCES, errno.EAGAIN}:
                            raise
                        if deadline is None or time.monotonic() >= deadline:
                            raise ValueError("operation_conflict") from None
                        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
                yield RepositoryMutation(self, root_descriptor)
            finally:
                if acquired:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _store_fragment_at(self, root_descriptor: int, payload: bytes) -> str:
        if not isinstance(payload, bytes) or not 1 <= len(payload) <= 262_144:
            raise ValueError("fragment_content_invalid")
        content_id = _digest(payload)
        fragments_descriptor = _open_directory_at(root_descriptor, "fragments")
        assert fragments_descriptor is not None
        try:
            _write_immutable_at(
                fragments_descriptor,
                content_id.removeprefix("sha256:") + ".fragment",
                payload,
                conflict="fragment_immutable",
            )
        finally:
            os.close(fragments_descriptor)
        return content_id

    def store_fragment(self, payload: bytes) -> str:
        with self.locked() as mutation:
            return mutation.store_fragment(payload)

    def _read_fragment_at(self, root_descriptor: int, content_id: str) -> bytes:
        if not isinstance(content_id, str) or _DIGEST.fullmatch(content_id) is None:
            raise ValueError("content_id_invalid")
        fragments_descriptor = _open_directory_at(
            root_descriptor, "fragments", absent_ok=True
        )
        if fragments_descriptor is None:
            raise ValueError("fragment_absent")
        try:
            payload = _read_owned_file_at(
                fragments_descriptor,
                content_id.removeprefix("sha256:") + ".fragment",
                maximum=262_144,
                absent_ok=True,
            )
        finally:
            os.close(fragments_descriptor)
        if payload is None:
            raise ValueError("fragment_absent")
        if _digest(payload) != content_id:
            raise ValueError("fragment_corrupt")
        return payload

    def read_fragment(self, content_id: str) -> bytes:
        with self._root_descriptor(create=False) as root_descriptor:
            if root_descriptor is None:
                raise ValueError("fragment_absent")
            return self._read_fragment_at(root_descriptor, content_id)

    def _generation_payload(
        self, files: Mapping[str, bytes], manifest: Mapping[str, object]
    ) -> tuple[str, bytes]:
        if (
            manifest.get("schema") != 1
            or not isinstance(manifest.get("fragment_set_id"), str)
            or _DIGEST.fullmatch(str(manifest.get("fragment_set_id"))) is None
            or not isinstance(manifest.get("renderer_revision"), str)
            or not manifest.get("renderer_revision")
        ):
            raise ValueError("generation_invalid")
        for name, payload in files.items():
            if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", name) is None:
                raise ValueError("generation_file_invalid")
            if not isinstance(payload, bytes):
                raise ValueError("generation_file_invalid")
        file_records = {
            name: {"sha256": hashlib.sha256(files[name]).hexdigest(), "mode": "0400"}
            for name in sorted(files)
        }
        payload = _canonical_json({"schema": 1, "manifest": manifest, "files": file_records})
        return _digest(payload), payload

    def _verify_generation_at(
        self,
        generations_descriptor: int,
        generation_name: str,
        files: Mapping[str, bytes],
        manifest_payload: bytes,
    ) -> bool:
        generation_descriptor = _open_directory_at(
            generations_descriptor, generation_name, absent_ok=True
        )
        if generation_descriptor is None:
            return False
        try:
            if set(os.listdir(generation_descriptor)) != {"manifest.json", *files.keys()}:
                raise ValueError("generation_immutable")
            installed_manifest = _read_owned_file_at(
                generation_descriptor,
                "manifest.json",
                maximum=max(len(manifest_payload), 1),
            )
            if installed_manifest != manifest_payload:
                raise ValueError("generation_immutable")
            for name, payload in files.items():
                installed = _read_owned_file_at(
                    generation_descriptor,
                    name,
                    maximum=max(len(payload), 1),
                    expected_mode=0o400,
                )
                facts = os.stat(name, dir_fd=generation_descriptor, follow_symlinks=False)
                if facts.st_mode & 0o777 != 0o400 or installed != payload:
                    raise ValueError("generation_immutable")
            return True
        finally:
            os.close(generation_descriptor)

    def _publish_generation_at(
        self,
        root_descriptor: int,
        files: Mapping[str, bytes],
        manifest: Mapping[str, object],
        *,
        generation_id: str | None = None,
    ) -> str:
        if not isinstance(files, Mapping) or not isinstance(manifest, Mapping):
            raise ValueError("generation_invalid")
        derived_id, manifest_payload = self._generation_payload(files, manifest)
        selected_id = generation_id or derived_id
        if not isinstance(selected_id, str) or _DIGEST.fullmatch(selected_id) is None:
            raise ValueError("generation_id_invalid")
        if selected_id != derived_id:
            raise ValueError("generation_immutable")
        generation_name = selected_id.removeprefix("sha256:")
        generations_descriptor = _open_directory_at(root_descriptor, "generations")
        assert generations_descriptor is not None
        temporary_name: str | None = None
        temporary_descriptor: int | None = None
        try:
            if self._verify_generation_at(
                generations_descriptor, generation_name, files, manifest_payload
            ):
                return selected_id
            for _attempt in range(32):
                temporary_name = ".generation-" + secrets.token_hex(16)
                try:
                    os.mkdir(temporary_name, 0o700, dir_fd=generations_descriptor)
                    break
                except FileExistsError:
                    temporary_name = None
                    continue
            else:
                raise ValueError("repository_unsafe")
            temporary_descriptor = _open_directory_at(
                generations_descriptor, temporary_name
            )
            assert temporary_descriptor is not None
            _write_immutable_at(
                temporary_descriptor, "manifest.json", manifest_payload,
                conflict="generation_immutable", mode=0o600,
            )
            for name, payload in files.items():
                _write_immutable_at(
                    temporary_descriptor, name, payload,
                    conflict="generation_immutable", mode=0o400,
                )
            os.fsync(temporary_descriptor)
            temporary_facts = os.fstat(temporary_descriptor)
            os.rename(
                temporary_name,
                generation_name,
                src_dir_fd=generations_descriptor,
                dst_dir_fd=generations_descriptor,
            )
            temporary_name = None
            installed_descriptor = _open_directory_at(
                generations_descriptor, generation_name
            )
            assert installed_descriptor is not None
            try:
                installed_facts = os.fstat(installed_descriptor)
                if (
                    installed_facts.st_dev != temporary_facts.st_dev
                    or installed_facts.st_ino != temporary_facts.st_ino
                ):
                    raise ValueError("repository_unsafe")
            finally:
                os.close(installed_descriptor)
            os.fsync(generations_descriptor)
        finally:
            if temporary_descriptor is not None:
                os.close(temporary_descriptor)
            if temporary_name is not None:
                cleanup_descriptor = _open_directory_at(
                    generations_descriptor, temporary_name, absent_ok=True
                )
                if cleanup_descriptor is not None:
                    try:
                        for child in os.listdir(cleanup_descriptor):
                            os.unlink(child, dir_fd=cleanup_descriptor)
                        os.fsync(cleanup_descriptor)
                    finally:
                        os.close(cleanup_descriptor)
                    os.rmdir(temporary_name, dir_fd=generations_descriptor)
            os.close(generations_descriptor)
        return selected_id

    def publish_generation(
        self,
        files: Mapping[str, bytes],
        manifest: Mapping[str, object],
        *,
        generation_id: str | None = None,
    ) -> str:
        with self.locked() as mutation:
            return mutation.publish_generation(
                files, manifest, generation_id=generation_id
            )

    def _write_json_at(
        self, root_descriptor: int, name: str, value: Mapping[str, object]
    ) -> None:
        payload = _canonical_json(value)
        _atomic_write_at(root_descriptor, name, payload)

    def write_state(self, value: Mapping[str, object]) -> None:
        with self.locked() as mutation:
            mutation.write_state(value)

    def write_receipt(self, value: Mapping[str, object]) -> None:
        """Compatibility spelling for the canonical known-good state receipt."""
        self.write_state(value)

    def write_transaction(self, value: Mapping[str, object]) -> None:
        with self.locked() as mutation:
            mutation.write_transaction(value)

    def write_journal(self, value: Mapping[str, object]) -> None:
        """Compatibility spelling for the transaction journal."""
        self.write_transaction(value)

    def _read_json(self, name: str) -> object | None:
        with self._root_descriptor(create=False) as root_descriptor:
            if root_descriptor is None:
                return None
            return _read_json_at(root_descriptor, name)

    def read_state(self) -> object | None:
        return self._read_json("state.json")

    def read_receipt(self) -> object | None:
        """Compatibility spelling for the canonical known-good state receipt."""
        return self.read_state()

    def read_transaction(self) -> object | None:
        return self._read_json("transaction.json")

    def read_journal(self) -> object | None:
        """Compatibility spelling for the transaction journal."""
        return self.read_transaction()

    def _prune_unreferenced_generations_at(
        self, root_descriptor: int, *, retain: tuple[str, ...] = ()
    ) -> tuple[str, ...]:
        protected = set(retain)
        if any(not isinstance(item, str) or _DIGEST.fullmatch(item) is None for item in protected):
            raise ValueError("generation_id_invalid")
        state = _read_json_at(root_descriptor, "state.json")
        transaction = _read_json_at(root_descriptor, "transaction.json")
        protected.update(_retention_references(state, transaction))
        removed: list[str] = []
        generations_descriptor = _open_directory_at(root_descriptor, "generations")
        assert generations_descriptor is not None
        try:
            validated: list[tuple[str, str, int, int, tuple[str, ...]]] = []
            names = sorted(os.listdir(generations_descriptor))
            if len(names) > 256:
                raise ValueError("repository_unsafe")
            for name in names:
                generation_id = "sha256:" + name
                if _DIGEST.fullmatch(generation_id) is None:
                    raise ValueError("repository_unsafe")
                generation_descriptor = _open_directory_at(generations_descriptor, name)
                assert generation_descriptor is not None
                try:
                    directory_facts = os.fstat(generation_descriptor)
                    manifest_payload = _read_owned_file_at(
                        generation_descriptor, "manifest.json", expected_mode=0o600
                    )
                    assert manifest_payload is not None
                    if _digest(manifest_payload) != generation_id:
                        raise ValueError("generation_immutable")
                    try:
                        manifest_record = json.loads(manifest_payload)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        raise ValueError("generation_immutable") from None
                    if (
                        not isinstance(manifest_record, dict)
                        or set(manifest_record) != {"schema", "manifest", "files"}
                        or manifest_record.get("schema") != 1
                        or not isinstance(manifest_record.get("files"), dict)
                        or _canonical_json(manifest_record) != manifest_payload
                    ):
                        raise ValueError("generation_immutable")
                    file_records = manifest_record["files"]
                    children = os.listdir(generation_descriptor)
                    if set(children) != {"manifest.json", *file_records.keys()}:
                        raise ValueError("generation_immutable")
                    for child, record in file_records.items():
                        if (
                            re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", child) is None
                            or not isinstance(record, dict)
                            or set(record) != {"sha256", "mode"}
                            or record.get("mode") != "0400"
                            or not isinstance(record.get("sha256"), str)
                            or re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None
                        ):
                            raise ValueError("generation_immutable")
                        payload = _read_owned_file_at(
                            generation_descriptor, child, expected_mode=0o400
                        )
                        assert payload is not None
                        if hashlib.sha256(payload).hexdigest() != record["sha256"]:
                            raise ValueError("generation_immutable")
                    validated.append((
                        name, generation_id, directory_facts.st_dev,
                        directory_facts.st_ino, tuple(children),
                    ))
                finally:
                    os.close(generation_descriptor)
            for name, generation_id, expected_dev, expected_ino, children in validated:
                if generation_id in protected:
                    continue
                generation_descriptor = _open_directory_at(generations_descriptor, name)
                assert generation_descriptor is not None
                try:
                    facts = os.fstat(generation_descriptor)
                    if facts.st_dev != expected_dev or facts.st_ino != expected_ino:
                        raise ValueError("repository_unsafe")
                    for child in children:
                        os.unlink(child, dir_fd=generation_descriptor)
                    os.fsync(generation_descriptor)
                finally:
                    os.close(generation_descriptor)
                os.rmdir(name, dir_fd=generations_descriptor)
                removed.append(generation_id)
            os.fsync(generations_descriptor)
        finally:
            os.close(generations_descriptor)
        return tuple(removed)
