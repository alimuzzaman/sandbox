"""Server-first recovery capture, status, promotion and retention (spec 058).

``ServerCaptureService`` orchestrates a capture that runs as a detached job on
a registered remote's server, reads its retained state back, and later
promotes the finished archive to Drive through the existing 023 encryption and
manifest-last pipeline.  All remote work goes through an injected transport
(``sandbox.transports.remote_server_capture``); this module never opens SSH,
Docker or database connections itself.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import time
from typing import Callable, Mapping

from .capture import _valid_set_id
from .errors import RecoveryError, result
from .hosted import HostedRecoveryMaterializer
from .integrity import sha256_file
from .materialize import SourceBinding
from .planner import build_plan


PHASES = ("preflight", "inventory", "dump", "files", "verify", "archive", "receipt")
STATES = ("queued", "running", "complete", "failed", "incomplete", "retired")
SUPPORTED_PROFILES = frozenset({"control-plane", "amarsonar-bangla-prod"})
CAPTURE_PROFILE = "amarsonar-bangla-prod"
CONTROL_PROFILE = "control-plane"
ARCHIVE_ARTIFACT = "amarsonar-bangla-prod/amarsonar-bangla.tar"
DECLARATION_ARTIFACT = "control-plane/control-plane-declarations.json"
CHUNK_BYTES = 16 * 1024 * 1024
DEFAULT_RETENTION_DAYS = 7
RETIRABLE = ("promoted", "failed", "incomplete")
DB_PASSWORD_ENV = "SANDBOX_RECOVERY_DB_PASSWORD"


def refusal(message: str, code: str, **data) -> RecoveryError:
    """Build a typed refusal carrying bounded, non-secret result data."""
    error = RecoveryError(message, code)
    error.data = data
    return error


def slot_for(remote: str, backup_id: str) -> str:
    """Slot key = ``capture-`` + sha256 of the remote and backup id (R4)."""
    payload = json.dumps({"schema_version": 1, "remote": remote, "backup_id": backup_id},
                         sort_keys=True, separators=(",", ":"))
    return "capture-" + hashlib.sha256(payload.encode()).hexdigest()


def capture_request_id(base_request_id: str, declarations_sha256: str) -> str:
    """Bind hosted source identity and the complete control-plane declaration."""
    payload = json.dumps({"base_request_id": base_request_id,
                          "declarations_sha256": declarations_sha256},
                         sort_keys=True, separators=(",", ":"))
    return "recovery-" + hashlib.sha256(payload.encode()).hexdigest()


def phase_index(phase: str | None) -> int:
    """Order of a capture phase; ``-1`` before the first phase."""
    return PHASES.index(phase) if phase in PHASES else -1


def derive_state(facts: Mapping) -> str:
    """Derive the reported state from the helper's raw slot facts (R3).

    ``incomplete`` is never stored: it is a non-terminal state whose job lock
    is free, a ``complete`` state without a valid receipt, or a slot with no
    state record and no live job.  Must match ``derive`` in the helper.
    """
    raw = facts.get("state")
    state = raw.get("state") if isinstance(raw, Mapping) else raw
    if state is None:
        return "queued" if not facts.get("lock_free") else "incomplete"
    if state in ("retired", "failed"):
        return state
    if state == "complete":
        return "complete" if facts.get("receipt_valid") else "incomplete"
    if state in ("queued", "running"):
        return "incomplete" if facts.get("lock_free") else state
    return "incomplete"


def review_state(facts: Mapping) -> str:
    """State as shown to retention review: a promoted complete capture is ``promoted``."""
    derived = derive_state(facts)
    if derived == "complete" and facts.get("promoted"):
        return "promoted"
    return derived


def retention_days(config: Mapping | None, remote: str | None) -> int:
    """Resolve the per-remote retention bound (R12): default 7, range 1-365."""
    recovery = (config or {}).get("recovery") if isinstance(config, Mapping) else None
    recovery = recovery if isinstance(recovery, Mapping) else {}
    value = recovery.get("server_capture_retention_days", DEFAULT_RETENTION_DAYS)
    remotes = recovery.get("remotes")
    if remote and isinstance(remotes, Mapping) and isinstance(remotes.get(remote), Mapping):
        value = remotes[remote].get("server_capture_retention_days", value)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 365:
        raise RecoveryError("server capture retention bound must be 1-365 days",
                            "invalid_retention_policy")
    return value


def retention_view(state: str, promoted: bool, completed_at, days: int, now: float) -> dict:
    """Retention flag for one capture; only complete unpromoted captures carry a bound."""
    age = None
    if isinstance(completed_at, (int, float)) and not isinstance(completed_at, bool):
        age = max(0.0, now - completed_at)
    exceeded = bool(state == "complete" and not promoted and age is not None
                    and age > days * 86400)
    return {"retention_days": days, "age_seconds": age, "retention_exceeded": exceeded}


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _owned_dir(path: Path) -> Path:
    if path.is_symlink():
        raise RecoveryError("recovery state directory is invalid", "invalid_staging_root")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise RecoveryError("recovery state directory is not owner-controlled", "invalid_staging_root")
    if stat.S_IMODE(info.st_mode) & 0o077:
        os.chmod(path, 0o700)
    return path


def _write_private(path: Path, payload: bytes) -> None:
    temporary = path.with_name(path.name + ".pending")
    temporary.unlink(missing_ok=True)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    temporary.replace(path)


class ServerCaptureService:
    """Start, observe, promote and retire server-held captures."""

    def __init__(self, catalog, transport, *, environment: Mapping[str, str] | None = None,
                 config: Mapping | Callable[[], Mapping] | None = None,
                 clock: Callable[[], float] | None = None,
                 state_root: str | Path | None = None, drive=None, capture=None) -> None:
        self.catalog = catalog
        self.transport = transport
        self._environment = environment if environment is not None else os.environ
        self._config = config
        self._clock = clock or time.time
        root = Path(state_root) if state_root else (
            Path(os.environ.get("SANDBOX_HOME", Path.home() / "sandbox")) / "recovery")
        self.promote_root = root / "promote"
        self.drive = drive
        self.capture = capture

    # -- shared helpers ----------------------------------------------------

    def _config_mapping(self) -> Mapping:
        config = self._config() if callable(self._config) else self._config
        return config if isinstance(config, Mapping) else {}

    def retention_days(self, remote: str) -> int:
        return retention_days(self._config_mapping(), remote)

    @staticmethod
    def _envelope_error(action: str, remote, exc: Exception, data: dict | None = None) -> dict:
        if not isinstance(exc, RecoveryError):
            exc = RecoveryError("server capture operation failed", "server_capture_failed")
        merged = dict(getattr(exc, "data", None) or {})
        merged.update(data or {})
        return result(False, action, remote=remote, data=merged, error=exc)

    @staticmethod
    def _require_ids(remote, backup_id) -> None:
        if not remote:
            raise RecoveryError("--remote is required", "missing_remote")
        if not backup_id:
            raise RecoveryError("--backup-id is required", "missing_backup_id")
        if not _valid_set_id(backup_id) or len(backup_id) > 128:
            raise RecoveryError("recovery set id is invalid", "invalid_set_id")

    def _bindings(self, profiles: tuple[str, ...]) -> dict:
        by_id = self.catalog.by_id()
        if any(profile not in by_id for profile in profiles):
            raise RecoveryError("recovery profile is unknown", "unknown_profile")
        return {profile: {"dependencies": list(by_id[profile].dependencies),
                          "restore_target": by_id[profile].restore_target,
                          "allowed_roots": list(by_id[profile].allowed_roots)}
                for profile in profiles}

    def _local_transfer_bytes(self, slot: str) -> int | None:
        part = self.promote_root / slot / "archive.part"
        try:
            info = part.lstat()
        except OSError:
            return None
        return info.st_size if stat.S_ISREG(info.st_mode) else None

    def _blocking(self, remote: str, listing: Mapping) -> list[str]:
        days = self.retention_days(remote)
        now = self._clock()
        blocking = []
        for item in listing.get("slots") or ():
            state = derive_state(item)
            view = retention_view(state, bool(item.get("promoted")), item.get("completed_at"),
                                  days, now)
            if view["retention_exceeded"] and item.get("backup_id"):
                blocking.append(item["backup_id"])
        return sorted(blocking)

    # -- capture start (US1) -----------------------------------------------

    def start(self, remote: str | None, backup_id: str | None, profiles, *,
              confirm: bool = False) -> dict:
        action = "capture"
        try:
            if not confirm:
                raise RecoveryError("recovery capture requires explicit confirmation",
                                    "confirmation_required")
            self._require_ids(remote, backup_id)
            if not profiles:
                raise RecoveryError("at least one --profile is required", "missing_profiles")
            plan = build_plan(self.catalog, tuple(profiles))
            if (not set(plan.profiles) <= SUPPORTED_PROFILES
                    or CAPTURE_PROFILE not in plan.profiles):
                raise RecoveryError("recovery profile is not supported by server capture",
                                    "unsupported_materialization")
            password = self._environment.get(DB_PASSWORD_ENV)
            if not isinstance(password, str) or not password:
                raise RecoveryError("database credential is not available",
                                    "missing_database_credential")
            source = self.transport.observe(remote)
            binding = SourceBinding(remote, source.machine_identity, source.revision,
                                    source.source_digest)
            artifact = next(item for item in plan.artifacts if item.profile_id == CAPTURE_PROFILE)
            control = next(item for item in plan.artifacts if item.profile_id == CONTROL_PROFILE)
            declarations = (json.dumps(
                self.transport.declaration(remote, control, source, backup_id),
                sort_keys=True, separators=(",", ":")) + "\n").encode()
            declarations_sha256 = hashlib.sha256(declarations).hexdigest()
            request_id = capture_request_id(
                HostedRecoveryMaterializer._request_id(remote, artifact, binding, backup_id),
                declarations_sha256)
            slot = slot_for(remote, backup_id)
            listing = self.transport.list(remote)
            existing = any(item.get("slot") == slot for item in listing.get("slots") or ())
            if not existing:
                blocking = self._blocking(remote, listing)
                if blocking:
                    raise refusal("an unpromoted server capture is past its retention bound",
                                  "retention_exceeded", blocking=blocking)
            request = {
                "schema_version": 1, "slot": slot, "remote": remote, "backup_id": backup_id,
                "backup_operation_id": backup_id, "request_id": request_id,
                "profile_id": artifact.profile_id, "artifact_id": artifact.artifact_id,
                "profiles": list(plan.profiles),
                "source_binding": {"machine_identity": binding.machine_identity,
                                   "revision": binding.revision,
                                   "source_digest": binding.source_digest},
                "declarations_sha256": declarations_sha256,
            }
            outcome = self.transport.start(remote, slot, request, password, declarations)
        except RecoveryError as exc:
            return self._envelope_error(action, remote, exc)
        except (OSError, TypeError, ValueError, KeyError, StopIteration) as exc:
            return self._envelope_error(action, remote, RecoveryError(
                "server capture start failed", "server_capture_failed"))
        if outcome.get("existing"):
            facts = outcome.get("status") or {}
            stored = facts.get("request") or {}
            state = derive_state(facts)
            raw = facts.get("state") or {}
            data = {"backup_id": backup_id, "request_id": stored.get("request_id", request_id),
                    "slot": slot, "state": state, "phase": raw.get("phase"),
                    "accepted_at": raw.get("accepted_at", stored.get("accepted_at")),
                    "existing": True}
        else:
            state = outcome.get("state") or "queued"
            data = {"backup_id": backup_id, "request_id": request_id, "slot": slot,
                    "state": state, "phase": outcome.get("phase"),
                    "accepted_at": outcome.get("accepted_at"), "existing": False}
        return result(True, action, remote=remote, status=state, data=data)

    # -- status (US2) ------------------------------------------------------

    def _view(self, remote: str, backup_id: str, facts: Mapping) -> dict:
        request = facts.get("request") or {}
        raw = facts.get("state") or {}
        receipt = facts.get("receipt") or {}
        promoted = facts.get("promoted") or None
        state = derive_state(facts)
        days = self.retention_days(remote)
        view = retention_view(state, bool(promoted), receipt.get("completed_at"), days,
                              self._clock())
        residue = facts.get("residue_bytes")
        detail = raw.get("detail")
        safe_detail = {}
        if isinstance(detail, Mapping):
            for name in ("need_bytes", "available_bytes", "shortfall_bytes"):
                value = detail.get(name)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    safe_detail[name] = value
            for name in ("missing_from_dump", "not_in_inventory"):
                values = detail.get(name)
                if isinstance(values, list):
                    safe_detail[name] = [value[:256] for value in values[:200]
                                         if isinstance(value, str)]
        return {
            "backup_id": backup_id, "request_id": request.get("request_id"),
            "state": state, "phase": raw.get("phase"),
            "reason": raw.get("reason"), "detail": safe_detail or None,
            "accepted_at": raw.get("accepted_at", request.get("accepted_at")),
            "started_at": raw.get("started_at"), "ended_at": raw.get("ended_at"),
            "archive": ({"sha256": receipt.get("archive_sha256"), "size": receipt.get("archive_size")}
                        if receipt else None),
            "members": receipt.get("members") if receipt else None,
            "inventory_summary": receipt.get("inventory_summary") if receipt else None,
            "promoted": bool(promoted),
            "promoted_set_id": promoted.get("set_id") if isinstance(promoted, Mapping) else None,
            "retention_days": days, "retention_exceeded": view["retention_exceeded"],
            "residue_bytes": residue if residue else None,
            "local_transfer_bytes": self._local_transfer_bytes(slot_for(remote, backup_id)),
        }

    def status(self, remote: str | None, backup_id: str | None) -> dict:
        action = "status"
        try:
            self._require_ids(remote, backup_id)
            facts = self.transport.status(remote, slot_for(remote, backup_id))
            view = self._view(remote, backup_id, facts)
        except RecoveryError as exc:
            return self._envelope_error(action, remote, exc)
        except (OSError, TypeError, ValueError, KeyError) as exc:
            return self._envelope_error(action, remote, RecoveryError(
                "server capture status failed", "server_capture_failed"))
        return result(True, action, remote=remote, status=view["state"], data=view)

    # -- listing and retention view (US5) -----------------------------------

    def captures(self, remote: str) -> dict:
        """Server captures and legacy one-shot archives for one remote (raises)."""
        listing = self.transport.list(remote)
        days = self.retention_days(remote)
        now = self._clock()
        captures = []
        for item in listing.get("slots") or ():
            state = derive_state(item)
            promoted = bool(item.get("promoted"))
            view = retention_view(state, promoted, item.get("completed_at"), days, now)
            reference = item.get("completed_at") or item.get("accepted_at")
            age = (max(0.0, now - reference)
                   if isinstance(reference, (int, float)) and not isinstance(reference, bool)
                   else None)
            captures.append({
                "backup_id": item.get("backup_id"), "state": state, "age_seconds": age,
                "archive_size": item.get("archive_size"), "promoted": promoted,
                "retention_exceeded": view["retention_exceeded"],
                "retirable": review_state(item) in RETIRABLE,
                "residue_bytes": item.get("residue_bytes") or None,
            })
        legacy = [{"name": item.get("name"), "size": item.get("size")}
                  for item in listing.get("legacy") or ()]
        return {"server_captures": captures, "legacy_server_archives": legacy,
                "retention_days": days, "truncated": bool(listing.get("truncated"))}

    def retention(self, remote: str) -> dict:
        try:
            data = self.captures(remote)
        except RecoveryError as exc:
            return self._envelope_error("retention", remote, exc)
        return result(True, "retention", remote=remote, status="planned",
                      data=dict(data, requires_confirmation=True))

    def retire(self, remote: str | None, backup_id: str | None, *, confirm: bool = False) -> dict:
        """Retire one promoted, failed or incomplete capture (FR-034)."""
        action = "retention"
        try:
            if not confirm or not backup_id:
                raise RecoveryError("retire requires --confirm and --backup-id",
                                    "confirmation_required")
            self._require_ids(remote, backup_id)
            slot = slot_for(remote, backup_id)
            candidate = self.transport.retire_plan(remote, slot)
            reviewed = candidate.get("state")
            if reviewed not in RETIRABLE:
                raise refusal("server capture is not retirable", "not_retirable",
                              state=reviewed)
            outcome = self.transport.retire(remote, slot, candidate)
        except RecoveryError as exc:
            return self._envelope_error(action, remote, exc)
        except (OSError, TypeError, ValueError, KeyError) as exc:
            return self._envelope_error(action, remote, RecoveryError(
                "server capture retire failed", "server_capture_failed"))
        return result(True, action, remote=remote, status="retired", data={
            "backup_id": backup_id, "retired_at": outcome.get("retired_at"),
            "removed_bytes": outcome.get("removed_bytes"), "previous_state": reviewed})

    # -- promotion (US3, US4) ----------------------------------------------

    def _pending_ciphertext(self, backup_id: str) -> Path | None:
        root = getattr(self.capture, "pending_root", None)
        if root is None:
            return None
        path = Path(root) / f"{backup_id}.archive.tar.gpg"
        return path if path.is_file() and not path.is_symlink() else None

    def _drive_paths(self, backup_id: str) -> list[str]:
        prefix = f"sets/{backup_id}/"
        paths = []
        for item in self.drive.list(""):
            path = item.get("Path") if isinstance(item, Mapping) else None
            if isinstance(path, str) and path.startswith(prefix):
                paths.append(path)
        return paths

    def _mark(self, remote: str, slot: str, request_id: str, set_id: str,
              manifest: Mapping) -> bool:
        marker = {"request_id": request_id, "set_id": set_id,
                  "ciphertext_sha256": manifest.get("ciphertext_sha256"),
                  "promoted_at": _iso(self._clock())}
        try:
            self.transport.mark_promoted(remote, slot, marker)
        except RecoveryError:
            return False
        return True

    def _verified_manifest(self, backup_id: str) -> dict:
        from .restore import verify_manifest
        return verify_manifest(self.drive, backup_id)

    def _resume_pending(self, remote: str, backup_id: str, pending: Path) -> dict:
        """Finish a local pending ciphertext only if Drive does not already
        hold this backup id from somewhere else (FR-028 before FR-029).

        A published manifest whose ciphertext hash equals the pending file is
        this same capture: the upload completed and only the local cleanup was
        lost, so the leftovers are removed and nothing is uploaded. Any other
        manifest under the id is ``set_id_conflict`` before any transfer. A
        set with objects but no manifest is the upload this pending file was
        in the middle of, and is resumed.
        """
        if f"sets/{backup_id}/manifest.json" in self._drive_paths(backup_id):
            manifest = self._verified_manifest(backup_id)
            if manifest.get("ciphertext_sha256") != sha256_file(pending):
                raise RecoveryError("Drive holds a different set under this backup id",
                                    "set_id_conflict")
            pending.unlink(missing_ok=True)
            (pending.parent / f"{backup_id}.manifest.json").unlink(missing_ok=True)
            server_capture = (manifest.get("provenance") or {}).get("server_capture") or {}
            marked = False
            if server_capture.get("request_id"):
                marked = self._mark(remote, slot_for(remote, backup_id),
                                    server_capture["request_id"], backup_id, manifest)
            return result(True, "promote", remote=remote, status="already_published", data={
                "set_id": backup_id, "manifest": manifest, "from_pending": True,
                "request_id": server_capture.get("request_id"),
                "archive_sha256": server_capture.get("archive_sha256"),
                "server_marked": marked})
        return self._finish_pending(remote, backup_id)

    def _finish_pending(self, remote: str, backup_id: str) -> dict:
        manifest = self.capture.publish_pending(backup_id, bindings_for=self._bindings)
        server_capture = (manifest.get("provenance") or {}).get("server_capture")
        marked = False
        if isinstance(server_capture, Mapping) and server_capture.get("request_id"):
            marked = self._mark(remote, slot_for(remote, backup_id),
                                server_capture["request_id"], backup_id, manifest)
        return result(True, "promote", remote=remote, status="published", data={
            "set_id": backup_id, "manifest": manifest, "from_pending": True,
            "request_id": (server_capture or {}).get("request_id"),
            "archive_sha256": (server_capture or {}).get("archive_sha256"),
            "server_marked": marked})

    def _transfer(self, remote: str, slot: str, request_id: str, digest: str,
                  size: int) -> tuple[Path, int]:
        directory = _owned_dir(_owned_dir(self.promote_root) / slot)
        part = directory / "archive.part"
        progress_path = directory / "progress.json"
        progress = None
        try:
            if progress_path.is_file() and not progress_path.is_symlink():
                progress = json.loads(progress_path.read_text())
        except (OSError, ValueError):
            progress = None
        received = 0
        part_size = part.lstat().st_size if part.is_file() and not part.is_symlink() else -1
        if (isinstance(progress, dict) and progress.get("request_id") == request_id
                and progress.get("archive_sha256") == digest
                and progress.get("archive_size") == size
                and isinstance(progress.get("received"), int)
                and 0 <= progress["received"] <= min(size, part_size)):
            received = progress["received"]
        else:
            part.unlink(missing_ok=True)
            progress_path.unlink(missing_ok=True)
            os.close(os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
        os.chmod(part, 0o600)
        free = shutil.disk_usage(directory).free
        need = 3 * size - received
        if free < need:
            raise refusal("operator free space is below what promote needs", "insufficient_space",
                          need_bytes=need, available_bytes=free, shortfall_bytes=need - free)
        resumed_from = received
        with open(part, "r+b") as stream:
            stream.truncate(received)
            stream.seek(received)
            while received < size:
                length = min(CHUNK_BYTES, size - received)
                data = self.transport.read_chunk(remote, slot, received, length)
                if len(data) != length:
                    raise RecoveryError("transferred chunk is incomplete", "transfer_mismatch")
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                received += length
                _write_private(progress_path, json.dumps({
                    "request_id": request_id, "archive_sha256": digest,
                    "archive_size": size, "received": received}, sort_keys=True).encode())
        if part.lstat().st_size != size or sha256_file(part) != digest:
            part.unlink(missing_ok=True)
            progress_path.unlink(missing_ok=True)
            try:
                self.transport.check_integrity(remote, slot)
            except RecoveryError:
                pass
            raise RecoveryError("transferred archive does not match the capture receipt",
                                "transfer_mismatch")
        return part, resumed_from

    def promote(self, remote: str | None, backup_id: str | None, *, confirm: bool = False) -> dict:
        action = "promote"
        slot = None
        try:
            if not confirm:
                raise RecoveryError("recovery promote requires explicit confirmation",
                                    "confirmation_required")
            self._require_ids(remote, backup_id)
            if not self._environment.get("RECOVERY_PASSPHRASE"):
                raise RecoveryError("RECOVERY_PASSPHRASE is not available", "missing_passphrase")
            if self.drive is None or self.capture is None:
                raise RecoveryError("recovery Drive destination is not configured",
                                    "recovery_not_configured")
            pending = self._pending_ciphertext(backup_id)
            if pending is not None:
                return self._resume_pending(remote, backup_id, pending)
            slot = slot_for(remote, backup_id)
            facts = self.transport.status(remote, slot)
            request = facts.get("request") or {}
            summary = facts.get("receipt") or {}
            paths = self._drive_paths(backup_id)
            if f"sets/{backup_id}/manifest.json" in paths:
                manifest = self._verified_manifest(backup_id)
                server_capture = (manifest.get("provenance") or {}).get("server_capture") or {}
                if (request.get("request_id") and summary.get("archive_sha256")
                        and server_capture.get("request_id") == request["request_id"]
                        and server_capture.get("archive_sha256") == summary["archive_sha256"]):
                    marked = self._mark(remote, slot, request["request_id"], backup_id, manifest)
                    return result(True, action, remote=remote, status="already_published", data={
                        "set_id": backup_id, "manifest": manifest,
                        "request_id": request["request_id"],
                        "archive_sha256": summary["archive_sha256"], "server_marked": marked})
                raise RecoveryError("Drive holds a different set under this backup id",
                                    "set_id_conflict")
            if paths:
                raise RecoveryError("Drive holds an incomplete set for this backup id",
                                    "incomplete_remote_set")
            state = derive_state(facts)
            if state != "complete":
                raise refusal("server capture is not complete", "capture_not_complete",
                              state=state)
            receipt = self.transport.read_receipt(remote, slot)
            digest, size = receipt.get("archive_sha256"), receipt.get("archive_size")
            if (receipt.get("request_id") != request.get("request_id")
                    or digest != summary.get("archive_sha256") or size != summary.get("archive_size")
                    or not isinstance(size, int) or size < 1):
                raise RecoveryError("server capture receipt changed", "transfer_mismatch")
            part, resumed_from = self._transfer(remote, slot, request["request_id"], digest, size)
            declarations = self.transport.read_declaration(remote, slot).encode("utf-8")
            if hashlib.sha256(declarations).hexdigest() != receipt.get("declarations_sha256"):
                raise RecoveryError("control-plane declaration does not match the receipt",
                                    "transfer_mismatch")
            manifest = self._publish(backup_id, request, receipt, part, declarations)
            marked = self._mark(remote, slot, request["request_id"], backup_id, manifest)
            shutil.rmtree(self.promote_root / slot, ignore_errors=True)
        except RecoveryError as exc:
            extra = {}
            if slot is not None and self._local_transfer_bytes(slot) is not None:
                extra["local_transfer_bytes"] = self._local_transfer_bytes(slot)
            return self._envelope_error(action, remote, exc, extra)
        except (OSError, TypeError, ValueError, KeyError) as exc:
            return self._envelope_error(action, remote, RecoveryError(
                "server capture promote failed", "server_capture_failed"))
        return result(True, action, remote=remote, status="published", data={
            "set_id": backup_id, "manifest": manifest, "request_id": request["request_id"],
            "archive_sha256": digest, "resumed_from_bytes": resumed_from,
            "server_marked": marked})

    def _publish(self, backup_id: str, request: Mapping, receipt: Mapping, part: Path,
                 declarations: bytes) -> dict:
        root = getattr(self.capture, "materialization_root", None)
        if root is None:
            raise RecoveryError("owned materialization root is not configured",
                                "recovery_not_configured")
        base = _owned_dir(Path(root))
        stage = base / f"promote-{slot_for(request.get('remote', ''), backup_id)}"
        shutil.rmtree(stage, ignore_errors=True)
        _owned_dir(stage)
        try:
            archive = _owned_dir(stage / CAPTURE_PROFILE) / "amarsonar-bangla.tar"
            os.link(part, archive)
            declaration = _owned_dir(stage / CONTROL_PROFILE) / "control-plane-declarations.json"
            _write_private(declaration, declarations)
            profiles = tuple(request.get("profiles") or (CONTROL_PROFILE, CAPTURE_PROFILE))
            plan = build_plan(self.catalog, profiles)
            binding = request.get("source_binding") or {}
            inventory = receipt.get("inventory") or {}
            provenance = {
                "remote": request.get("remote"),
                "machine_identity": binding.get("machine_identity"),
                "revision": binding.get("revision"),
                "source_digest": binding.get("source_digest"),
                "capture_contract_version": 2, "backup_operation_id": backup_id,
                "server_capture": {
                    "schema_version": 1, "request_id": request.get("request_id"),
                    "backup_operation_id": backup_id,
                    "archive_sha256": receipt.get("archive_sha256"),
                    "archive_size": receipt.get("archive_size"),
                    "members": receipt.get("members"),
                    "declarations_sha256": receipt.get("declarations_sha256"),
                    "inventory_summary": inventory.get("summary"),
                    "captured_at": [receipt.get("started_at"), receipt.get("completed_at")],
                    "promoted_at": _iso(self._clock()),
                },
            }
            return self.capture.publish_files(
                backup_id, {ARCHIVE_ARTIFACT: archive, DECLARATION_ARTIFACT: declaration},
                profiles=plan.profiles, provenance=provenance,
                profile_bindings=self._bindings(plan.profiles))
        finally:
            shutil.rmtree(stage, ignore_errors=True)


__all__ = [
    "CHUNK_BYTES", "DEFAULT_RETENTION_DAYS", "PHASES", "ServerCaptureService",
    "derive_state", "phase_index", "refusal", "retention_days", "retention_view",
    "review_state", "slot_for",
]
