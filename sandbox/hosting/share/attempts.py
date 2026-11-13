"""Spec 088 controller-only, token-free share recovery records.

Callers preallocate every link id, then call prepare BEFORE remote effects. Hold
``locked()`` across cleanup and mint; call begin_mint before key installation OR a gate mint request.
Unknown acceptance must be reconciled by id, never retried with a replacement id.

Wire contract: version 3 writes original-settlement evidence. Version 2 is read
conservatively with no settlement evidence; version 1 refuses without migration.
Grant.cleanup holds only the canonical public gate DNS hostname (HTTPS origin is
computed), original runtime incarnation, bounded Compose argv inputs, and optional
WP service/plugin path/key id/expiry/protocol. No manifest, command, bearer/token hash,
signing key, email, or companion URL is accepted. Paths identify locations only;
this module never reads them. Callers reconstruct _compose_prefix from files,
override_path, env_path and project_name; the fixed gate service is sandbox-login.
WordPress removal uses dirname(container_path) and the existing fixed filename
prefix plus key_id, never an arbitrary glob or a caller-supplied command.

Before using cleanup_for, the controller must independently prove that these
Compose inputs still address the recorded copy_id or immutable runtime fingerprint
for BOTH gate and WP services. A manifest hash/slug/commit is not that proof. No
proof after state loss means cleanup_pending until authoritative cleanup or the
recorded protocol expiry; NEVER resolve a replacement by current parent/slug.
Hold the parent lock, then sorted target locks through that proof and effects.
Gate revocation and WP key absence are independent acknowledgements; only both
close the grant. Supported sandbox-share-login.v1 enforces key expiry for every
unused nonce, so elapsed grant/key expiry safely discharges unused capabilities.
Established application sessions are outside this repository's revocation scope.
This module does no remote work and never generates or accepts bearer material.
"""
from __future__ import annotations

import errno
import fcntl
import ipaddress
import json
import os
import re
import stat
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterator, Literal

Phase = Literal["prepared", "minting", "active", "revoking", "cleanup_pending", "revoked", "expired"]
Disposition = Literal["planned", "unknown", "live", "revoked", "expired"]


class AttemptError(ValueError):
    """Fixed messages only; rejected input never enters diagnostics."""


@dataclass(frozen=True, order=True)
class Target:
    remote: str
    project: str
    environment: str


@dataclass(frozen=True)
class Parent:
    target: Target
    stack: str


@dataclass(frozen=True)
class RuntimeIdentity:
    # copy_id is the host lifetime incarnation, NOT a slug or delivered revision.
    # runtime_fingerprint must cover immutable identities of ALL addressed services.
    kind: Literal["copy_id", "runtime_fingerprint"]
    value: str


@dataclass(frozen=True)
class ComposeCleanup:
    project_name: str
    # Fully joined absolute source paths, in original Compose precedence order.
    files: tuple[str, ...]
    override_path: str
    env_path: str


@dataclass(frozen=True)
class WordPressCleanup:
    service: str
    container_path: str
    key_id: str
    # This exact plugin protocol checks key expiry before accepting any nonce.
    expires_at: int
    protocol: Literal["sandbox-share-login.v1"] = "sandbox-share-login.v1"


@dataclass(frozen=True)
class CleanupDescriptor:
    gate_hostname: str
    identity: RuntimeIdentity
    compose: ComposeCleanup
    wordpress: WordPressCleanup | None = None

    @property
    def gate_origin(self) -> str:
        return "https://" + self.gate_hostname


@dataclass(frozen=True)
class Grant:
    target: Target
    entry: str
    id: str
    expires_at: int
    disposition: Disposition = "planned"
    cleanup: CleanupDescriptor | None = None
    gate_revoked: bool = False
    wordpress_revoked: bool = False
    original_settled: bool = False


@dataclass(frozen=True)
class Attempt:
    id: str
    parent: Parent
    created_at: int
    expires_at: int
    phase: Phase
    # Parent first, then up to four companions. Lock order is separate.
    grants: tuple[Grant, ...]

    @property
    def target_order(self) -> tuple[Target, ...]:
        """Acquire remote target locks in this order, never parent-first."""
        return tuple(sorted({grant.target for grant in self.grants}))

    def still_live(self, now: int) -> tuple[Grant, ...]:
        """Include gate or key uncertainty until protocol-guaranteed expiry."""
        _timestamp(now)
        return tuple(grant for grant in self.grants
                     if grant.disposition in ("unknown", "live") and not _expired_safely(grant, now))


_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}")
_STACK = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_ENTRY = re.compile(r"[a-z][a-z0-9-]{0,31}")
_ATTEMPT_ID = re.compile(r"a-[0-9a-f]{32}")
_LINK_ID = re.compile(r"l-(?:[0-9a-f]{12}|[0-9a-f]{32})")
_PHASES = {
    "prepared": {"minting", "cleanup_pending", "revoked", "expired"},
    "minting": {"active", "cleanup_pending", "expired"},
    "active": {"revoking", "expired"},
    "revoking": {"cleanup_pending", "revoked", "expired"},
    "cleanup_pending": {"revoking", "revoked", "expired"},
    "revoked": set(), "expired": set(),
}
_MAX_BYTES = 1024 * 1024
_MAX_ATTEMPTS = 256  # Refuse when full; never discard unresolved identities.


def _require(condition: bool) -> None:
    if not condition:
        raise AttemptError("invalid share attempt record")


def _timestamp(value: object) -> None:
    _require(type(value) is int and 0 <= value <= 2**53 - 1)


def _name(value: object, pattern: re.Pattern = _LABEL) -> None:
    _require(type(value) is str and pattern.fullmatch(value) is not None)


def _object(value: object, keys: set[str]) -> dict:
    _require(type(value) is dict and set(value) == keys)
    return value


def _target(value: object) -> Target:
    value = _object(value, {"remote", "project", "environment"})
    for item in value.values():
        _name(item)
    return Target(**value)


def _parent(value: object) -> Parent:
    value = _object(value, {"target", "stack"})
    _name(value["stack"], _STACK)
    return Parent(_target(value["target"]), value["stack"])


def _path(value: object) -> None:
    # Nonsecret cleanup locations only. No shell metacharacters, traversal, or
    # option-like relative paths; values are data and MUST still be argv-quoted.
    _require(type(value) is str and 1 < len(value) <= 1024
             and re.fullmatch(r"/[A-Za-z0-9_./-]+", value) is not None
             and all(part not in ("", ".", "..") for part in value.split("/")[1:]))


def _identity(value: object) -> RuntimeIdentity:
    value = _object(value, {"kind", "value"})
    _require(type(value["kind"]) is str and value["kind"] in ("copy_id", "runtime_fingerprint"))
    _name(value["value"], re.compile(r"[0-9a-f]{8}" if value["kind"] == "copy_id" else r"[0-9a-f]{64}"))
    return RuntimeIdentity(**value)


def _cleanup(value: object, link_id: str, expires_at: int) -> CleanupDescriptor | None:
    if value is None:
        return None
    value = _object(value, {"gate_hostname", "identity", "compose", "wordpress"})
    hostname = value["gate_hostname"]
    _require(type(hostname) is str and len(hostname) <= 253)
    labels = hostname.split(".")
    _require(len(labels) >= 2 and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                     for label in labels)
             and labels[-1] not in ("local", "localhost", "tst", "internal"))
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise AttemptError("invalid share attempt record")
    identity = _identity(value["identity"])
    compose = _object(value["compose"], {"project_name", "files", "override_path", "env_path"})
    _name(compose["project_name"], re.compile(r"[a-z0-9][a-z0-9_-]{0,127}"))
    _require(type(compose["files"]) in (list, tuple) and 1 <= len(compose["files"]) <= 8)
    for path in (*compose["files"], compose["override_path"], compose["env_path"]):
        _path(path)
    invocation = ComposeCleanup(compose["project_name"], tuple(compose["files"]),
                                compose["override_path"], compose["env_path"])
    wordpress = value["wordpress"]
    if wordpress is not None:
        wordpress = _object(wordpress, {"service", "container_path", "key_id", "expires_at", "protocol"})
        _name(wordpress["service"], re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}"))
        _path(wordpress["container_path"])
        _name(wordpress["key_id"], re.compile(r"(?:[0-9a-f]{12}|[0-9a-f]{32})"))
        _timestamp(wordpress["expires_at"])
        _require(wordpress["key_id"] == link_id[2:] and wordpress["expires_at"] == expires_at
                 and wordpress["protocol"] == "sandbox-share-login.v1")
        wordpress = WordPressCleanup(**wordpress)
    return CleanupDescriptor(hostname, identity, invocation, wordpress)


def _wordpress_pending(grant: Grant) -> bool:
    return bool(grant.cleanup and grant.cleanup.wordpress and not grant.wordpress_revoked)


def _expired_safely(grant: Grant, now: int) -> bool:
    # Only the supported WP protocol is parsed, with key expiry == grant expiry.
    # It refuses ALL unused nonces at that boundary, even if the plugin remains.
    return grant.expires_at <= now and (grant.cleanup is not None or grant.disposition == "planned")


def _parse(value: object, *, legacy: bool = False) -> Attempt:
    value = _object(value, {"id", "parent", "created_at", "expires_at", "phase", "grants"})
    _name(value["id"], _ATTEMPT_ID)
    parent = _parent(value["parent"])
    _timestamp(value["created_at"])
    _timestamp(value["expires_at"])
    _require(0 < value["expires_at"] - value["created_at"] <= 30 * 86400)
    _require(type(value["phase"]) is str and value["phase"] in _PHASES)
    _require(type(value["grants"]) in (list, tuple) and 1 <= len(value["grants"]) <= 5)
    grants = []
    for row in value["grants"]:
        row = _object(row, {"target", "entry", "id", "expires_at", "disposition",
                            "cleanup", "gate_revoked", "wordpress_revoked", "original_settled"})
        target = _target(row["target"])
        _name(row["entry"], _ENTRY)
        _name(row["id"], _LINK_ID)
        _timestamp(row["expires_at"])
        _require(row["expires_at"] == value["expires_at"])
        _require(type(row["disposition"]) is str and row["disposition"] in
                 {"planned", "unknown", "live", "revoked", "expired"})
        cleanup = _cleanup(row["cleanup"], row["id"], row["expires_at"])
        _require(all(type(row[key]) is bool for key in
                     ("gate_revoked", "wordpress_revoked", "original_settled")))
        grant = Grant(target, row["entry"], row["id"], row["expires_at"], row["disposition"],
                      cleanup, row["gate_revoked"], row["wordpress_revoked"], row["original_settled"])
        _require(grant.disposition not in ("unknown", "live") or cleanup is not None)
        if grant.disposition == "planned":
            _require(not grant.gate_revoked and not grant.wordpress_revoked and not grant.original_settled)
        if grant.disposition == "revoked":
            _require(grant.gate_revoked and not _wordpress_pending(grant)
                     and (legacy or grant.original_settled))
        if grant.disposition == "expired":
            _require(cleanup is not None or not _wordpress_pending(grant))
        grants.append(grant)
    _require(grants[0].target == parent.target)
    _require(len({grant.target for grant in grants}) == len(grants))
    _require(len({grant.id for grant in grants}) == len(grants))
    _require(all(grant.target.remote == parent.target.remote for grant in grants))
    phase = value["phase"]
    if phase == "prepared":
        _require(all(grant.disposition == "planned" for grant in grants))
    if phase == "active":
        _require(all(grant.disposition == "live" and not grant.gate_revoked
                     and not grant.wordpress_revoked for grant in grants))
    if phase in ("revoked", "expired"):
        _require(all(grant.disposition in ("revoked", "expired") for grant in grants))
    return Attempt(value["id"], parent, value["created_at"], value["expires_at"], phase, tuple(grants))


def _legacy_attempt(record: Attempt) -> Attempt:
    """Discard pre-fence acknowledgements that could precede a delayed writer.

    A v2 begin_mint required a cleanup descriptor. A terminal row without one
    therefore has proven zero effects. Other legacy terminal rows cannot prove
    settlement, even when the whole attempt says revoked or the key was absent.
    """
    grants, ambiguous = [], False
    for grant in record.grants:
        if grant.disposition in ("revoked", "expired"):
            if grant.cleanup is None:
                grant = replace(grant, original_settled=True)
            else:
                ambiguous = True
                grant = replace(grant, disposition="unknown", gate_revoked=False, wordpress_revoked=False)
        elif grant.disposition == "unknown":
            grant = replace(grant, gate_revoked=False, wordpress_revoked=False)
        grants.append(grant)
    return _parse(asdict(replace(record, grants=tuple(grants),
                                 phase="cleanup_pending" if ambiguous else record.phase)))


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


class AttemptRepository:
    """One bounded ledger per parent; no other module reads its JSON.

    Root must be an owner-only directory with no symlink ancestors. Methods
    lock automatically. ``locked()`` is reentrant on this instance in one thread
    and refuses contention rather than waiting indefinitely. Hold it around the
    entire remote cleanup/mint sequence. Target locks follow ``target_order``.
    Terminal records remain listed; this repository deliberately has no delete.
    """

    def __init__(self, root: Path, parent: Parent):
        _require(type(parent) is Parent and type(parent.target) is Target)
        self.parent = _parent(asdict(parent))
        self.root = Path(root).absolute()
        _require(".." not in self.root.parts)
        # Derived only from public identity, never a bearer token or token hash.
        identity = json.dumps(asdict(self.parent), sort_keys=True, separators=(",", ":"))
        self._stem = uuid.uuid5(uuid.NAMESPACE_URL, identity).hex
        self._local = threading.local()
        self._mutex = threading.RLock()

    def _directory(self) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(self.root.anchor, flags)
        try:
            for part in self.root.parts[1:]:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                # Also sync entries created by a process that died before fsync.
                os.fsync(descriptor)
                child = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            info = os.fstat(descriptor)
            _require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    @staticmethod
    def _check_file(descriptor: int) -> None:
        info = os.fstat(descriptor)
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                 and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)

    @contextmanager
    def locked(self) -> Iterator[AttemptRepository]:
        if not self._mutex.acquire(blocking=False):
            raise AttemptError("share attempt parent busy")
        try:
            if getattr(self._local, "directory", None) is not None:
                yield self
                return
            directory = lock = None
            try:
                try:
                    directory = self._directory()
                    lock = os.open(self._stem + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
                                   | os.O_NONBLOCK, 0o600, dir_fd=directory)
                    self._check_file(lock)
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                        raise AttemptError("share attempt parent busy") from None
                    raise AttemptError("share attempt storage unavailable") from None
                self._local.directory = directory
                yield self
            finally:
                self._local.directory = None
                if lock is not None:
                    os.close(lock)
                if directory is not None:
                    os.close(directory)
        finally:
            self._mutex.release()

    def _load(self, *, validate_parent: bool = True) -> tuple[Attempt, ...]:
        try:
            descriptor = os.open(self._stem + ".json", os.O_RDONLY | os.O_NOFOLLOW
                                 | os.O_NONBLOCK, dir_fd=self._local.directory)
        except FileNotFoundError:
            return ()
        try:
            self._check_file(descriptor)
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                body = handle.read(_MAX_BYTES + 1)
            _require(len(body) <= _MAX_BYTES)
            document = json.loads(body, object_pairs_hook=_unique_object)
            document = _object(document, {"version", "attempts"})
            _require(type(document["version"]) is int and document["version"] in (2, 3))
            rows = document["attempts"]
            _require(type(rows) is list and len(rows) <= _MAX_ATTEMPTS)
            if document["version"] == 2:
                # Old gate_revoked bits predate original-writer fencing. Never
                # promote them into completion evidence, including after a save.
                for row in rows:
                    _require(type(row) is dict and type(row.get("grants")) is list)
                    for grant in row["grants"]:
                        _require(type(grant) is dict and "original_settled" not in grant)
                        grant["original_settled"] = False
            records = tuple(_parse(row, legacy=document["version"] == 2) for row in rows)
            if document["version"] == 2:
                records = tuple(_legacy_attempt(row) for row in records)
            if validate_parent:
                _require(all(record.parent == self.parent for record in records))
            _require(len({record.id for record in records}) == len(records))
            return records
        except (UnicodeError, RecursionError, json.JSONDecodeError):
            raise AttemptError("invalid share attempt storage") from None
        finally:
            os.close(descriptor)

    def _write(self, records: tuple[Attempt, ...]) -> None:
        _require(len(records) <= _MAX_ATTEMPTS)
        body = (json.dumps({"version": 3, "attempts": [asdict(row) for row in records]},
                           sort_keys=True, separators=(",", ":")) + "\n").encode()
        _require(len(body) <= _MAX_BYTES)
        directory = self._local.directory
        staged = "." + self._stem + "." + uuid.uuid4().hex + ".tmp"
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staged, self._stem + ".json", src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(staged, dir_fd=directory)
            except FileNotFoundError:
                pass

    def list(self) -> tuple[Attempt, ...]:
        """Include incomplete and terminal attempts; never infer remote state."""
        try:
            with self.locked():
                return self._load()
        except OSError:
            raise AttemptError("share attempt storage unavailable") from None

    @classmethod
    def list_recorded(cls, root: Path) -> tuple[Attempt, ...]:
        """Discover parents from their ledgers, without resolving ANY live target.

        Each ledger is read under its original parent lock. This is a bounded
        inventory, not an atomic cross-parent snapshot. Invalid storage refuses
        the inventory rather than silently reporting no live grants.
        """
        discovery = cls(root, Parent(Target("recorded", "recorded", "recorded"), "recorded"))
        directory = None
        try:
            directory = discovery._directory()
            stems = []
            with os.scandir(directory) as entries:
                for count, entry in enumerate(entries):
                    _require(count < 2048)
                    if re.fullmatch(r"[0-9a-f]{32}\.json", entry.name):
                        stems.append(entry.name[:-5])
                        _require(len(stems) <= 256)
            records = []
            for stem in sorted(stems):
                ledger = cls(root, discovery.parent)
                ledger._stem = stem
                with ledger.locked():
                    rows = ledger._load(validate_parent=False)
                    if rows:
                        parent = rows[0].parent
                        _require(all(row.parent == parent for row in rows)
                                 and cls(root, parent)._stem == stem)
                    records.extend(rows)
            return tuple(records)
        except OSError:
            raise AttemptError("share attempt storage unavailable") from None
        finally:
            if directory is not None:
                os.close(directory)

    @classmethod
    def find_recorded_parent_link(cls, root: Path, link_id: str) -> Attempt | None:
        """Recover original Parent from a stable link id, even after state loss.

        Link-id collisions across parents refuse; the caller must disambiguate
        using a recorded Parent, never select a current same-slug replacement.
        """
        _name(link_id, _LINK_ID)
        matches = tuple(row for row in cls.list_recorded(root) if row.grants[0].id == link_id)
        _require(len(matches) <= 1)
        return matches[0] if matches else None

    def read(self, attempt_id: str) -> Attempt | None:
        _name(attempt_id, _ATTEMPT_ID)
        return next((row for row in self.list() if row.id == attempt_id), None)

    def find_by_parent_link(self, link_id: str) -> Attempt | None:
        """Lookup retained identities using only the recorded Parent, no manifest."""
        _name(link_id, _LINK_ID)
        return next((row for row in self.list() if row.grants[0].id == link_id), None)

    def cleanup_for(self, attempt_id: str, grant_id: str,
                    observed_identity: RuntimeIdentity) -> CleanupDescriptor:
        """Return inputs only after an authoritative runtime incarnation check.

        Caller MUST observe the remote's incarnation covering the gate AND WP
        services under target locks, never derive it from a current slug/manifest.
        Keep that lock through effects. Missing proof means retain uncertainty.
        This equality check is not a remote probe or a command authorization.
        """
        _name(grant_id, _LINK_ID)
        _require(type(observed_identity) is RuntimeIdentity)
        observed_identity = _identity(asdict(observed_identity))
        record = self.read(attempt_id)
        _require(record is not None)
        grant = next((item for item in record.grants if item.id == grant_id), None)
        _require(grant is not None and grant.cleanup is not None
                 and grant.cleanup.identity == observed_identity)
        return grant.cleanup

    def prepare(self, attempt: Attempt) -> Attempt:
        """Persist the entire plan before mint. Same plan/id returns saved state."""
        _require(type(attempt) is Attempt)
        attempt = _parse(asdict(attempt))
        _require(attempt.parent == self.parent and attempt.phase == "prepared")
        with self.locked():
            records = self._load()
            previous = next((row for row in records if row.id == attempt.id), None)
            if previous is not None:
                original = replace(previous, phase="prepared", grants=tuple(
                    replace(grant, disposition="planned", gate_revoked=False, wordpress_revoked=False,
                            original_settled=False)
                    for grant in previous.grants))
                _require(original == attempt)
                return previous
            if any(row.phase not in ("active", "revoked", "expired") for row in records):
                raise AttemptError("share attempt cleanup required")
            # A source id cannot be reused for another attempt on the same gate.
            used = {(grant.target, grant.entry, grant.id) for row in records for grant in row.grants}
            _require(not any((grant.target, grant.entry, grant.id) in used for grant in attempt.grants))
            self._commit(records + (attempt,))
            return attempt

    def _commit(self, records: tuple[Attempt, ...]) -> None:
        try:
            self._write(records)
        except OSError:
            raise AttemptError("share attempt storage unavailable") from None

    def _update(self, attempt_id: str, change) -> Attempt:
        _name(attempt_id, _ATTEMPT_ID)
        with self.locked():
            records = self._load()
            previous = next((row for row in records if row.id == attempt_id), None)
            if previous is None:
                raise AttemptError("share attempt missing")
            updated = _parse(asdict(change(previous)))
            if updated != previous:
                self._commit(tuple(updated if row.id == attempt_id else row for row in records))
            return updated

    def transition(self, attempt_id: str, phase: Phase) -> Attempt:
        _require(type(phase) is str and phase in _PHASES)
        def change(record: Attempt) -> Attempt:
            _require(phase == record.phase or phase in _PHASES[record.phase])
            _require(phase != "expired" or record.phase == "expired")
            return replace(record, phase=phase)
        return self._update(attempt_id, change)

    def begin_mint(self, attempt_id: str, grant_id: str) -> Attempt:
        """Persist unknown BEFORE key installation/mint; require cleanup identity."""
        # Unknown acceptance does not prove the original remote writer stopped.
        # Cleanup must not discharge it through a racing revoke/absence probe.
        _name(grant_id, _LINK_ID)
        def change(record: Attempt) -> Attempt:
            _require(record.phase in ("prepared", "minting"))
            _require(any(grant.id == grant_id for grant in record.grants))
            grants = []
            for grant in record.grants:
                if grant.id == grant_id:
                    # SiteStore mint is not idempotent: duplicate id/hash refuses.
                    # An unknown result must be listed/revoked, never reminted.
                    _require(grant.disposition == "planned" and grant.cleanup is not None)
                    grant = replace(grant, disposition="unknown")
                grants.append(grant)
            return replace(record, phase="minting", grants=tuple(grants))
        return self._update(attempt_id, change)

    def update_grant(self, attempt_id: str, grant_id: str, disposition: Disposition) -> Attempt:
        """Record acceptance or GATE revoke, including repeated cleanup replies.

        Only explicit reconciliation may discharge unknown acceptance. Missing
        parent gate state alone is never a companion revoke confirmation.
        """
        _name(grant_id, _LINK_ID)
        _require(disposition in ("live", "revoked"))
        def change(record: Attempt) -> Attempt:
            _require(any(grant.id == grant_id for grant in record.grants))
            grants = []
            for grant in record.grants:
                if grant.id == grant_id:
                    if grant.disposition == "expired" and disposition == "revoked":
                        grants.append(grant)
                        continue
                    _require(disposition == grant.disposition or
                             (grant.disposition == "unknown" and disposition == "live") or
                             (grant.disposition in ("planned", "unknown", "live") and disposition == "revoked"))
                    if disposition == "revoked":
                        _require(grant.disposition != "unknown" or grant.original_settled)
                        grant = replace(grant, gate_revoked=True, original_settled=True)
                        # Gate revocation alone cannot discharge an installed or
                        # possibly installed WordPress key after lost acceptance.
                        if not _wordpress_pending(grant):
                            grant = replace(grant, disposition="revoked")
                        elif grant.disposition == "planned":
                            grant = replace(grant, disposition="unknown")
                    else:
                        _require(not grant.gate_revoked and not grant.wordpress_revoked)
                        grant = replace(grant, disposition=disposition, original_settled=True)
                grants.append(grant)
            return replace(record, grants=tuple(grants))
        return self._update(attempt_id, change)

    def confirm_original_settled(self, attempt_id: str, grant_id: str) -> Attempt:
        """Only the original invocation's completed-transport callback calls this.

        Gate absence, a saved revoke bit, a timeout, or another request is not
        evidence. This separately versioned fact survives partial compensation.
        """
        _name(grant_id, _LINK_ID)
        def change(record: Attempt) -> Attempt:
            _require(any(grant.id == grant_id for grant in record.grants))
            return replace(record, grants=tuple(
                replace(grant, original_settled=True) if grant.id == grant_id
                and grant.disposition in ("unknown", "live") else grant
                for grant in record.grants))
        return self._update(attempt_id, change)

    def confirm_wordpress_removed(self, attempt_id: str, grant_id: str) -> Attempt:
        """Record a confirmed key absence/removal, independent of gate cleanup.

        A failed or malformed remote reply MUST NOT call this method. Uses the
        recorded key id only; no revoke-all patterns or replacement-target lookup.
        """
        _name(grant_id, _LINK_ID)
        def change(record: Attempt) -> Attempt:
            _require(any(grant.id == grant_id for grant in record.grants))
            grants = []
            for grant in record.grants:
                if grant.id == grant_id:
                    _require(grant.cleanup is not None and grant.cleanup.wordpress is not None)
                    if grant.disposition not in ("revoked", "expired"):
                        _require(grant.disposition in ("unknown", "live"))
                        grant = replace(grant, wordpress_revoked=True)
                        if grant.gate_revoked and grant.original_settled:
                            grant = replace(grant, disposition="revoked")
                grants.append(grant)
            return replace(record, grants=tuple(grants))
        return self._update(attempt_id, change)

    def expire(self, *, now: int) -> tuple[Attempt, ...]:
        """Discharge elapsed grants locally while retaining their identities."""
        _timestamp(now)
        with self.locked():
            records = self._load()
            updated = tuple(replace(row, phase="expired", grants=tuple(
                grant if grant.disposition == "revoked" else replace(grant, disposition="expired")
                for grant in row.grants))
                if all(_expired_safely(grant, now) or grant.disposition in ("revoked", "expired")
                       for grant in row.grants) and row.phase not in ("revoked", "expired") else row
                for row in records)
            if updated != records:
                self._commit(updated)
            return updated
