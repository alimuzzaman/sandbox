"""Human-only, expiring approvals bound to the exact ownership authority.

An approval is created only from an interactive foreground terminal and is
never extended or rewritten (FR-034). Agents and MCP callers run inside an
unexpired approval; they cannot create or re-create one. Same-UID processes
that fake a terminal are a documented trust limit, not a claimed control.
"""

from __future__ import annotations

import hmac
import json
import math
import os
import pwd
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping, TextIO

from sandbox.secrets.models import SecretBrokerError
from .journal import (
    ensure_private_dir, is_qualified_id, load_controller_id, new_qualified_id,
    qualified_controller, read_private_file, write_private_file,
    _unique_pairs, _reject_constant,
)
from .models import Approval, OwnershipConfig, SourceContinuity, StorageAdmission, canonical_json

MAX_APPROVAL_SECONDS = 3600
_APPROVAL_KEYS = frozenset(Approval.__dataclass_fields__)
_CONTINUITY_KEYS = frozenset(SourceContinuity.__dataclass_fields__)
_TOKEN_EXPIRY_KEYS = frozenset((
    "version", "approval_id", "controller_id", "operator_uid", "operator_login",
    "profile", "operation",
    "map_sha256", "destination_sha256", "authority_digest",
    "token_expires_at", "effective_expires_at", "bound_at",
))


def _mismatch(message: str = "approval does not match this operation") -> SecretBrokerError:
    return SecretBrokerError("approval_mismatch", message)


def require_human_terminal(tty_in: TextIO, tty_out: TextIO) -> None:
    """Both streams are TTYs and this process is the terminal's foreground job."""
    ready = False
    try:
        if tty_in.isatty() and tty_out.isatty():
            descriptor = tty_in.fileno()
            ready = (os.isatty(descriptor) and os.tcgetpgrp(descriptor) == os.getpgrp()
                     and os.tcgetpgrp(tty_out.fileno()) == os.getpgrp())
    except (OSError, ValueError, AttributeError):
        ready = False
    if not ready:
        raise SecretBrokerError(
            "approval_human_only",
            "approval requires an operator at an interactive foreground terminal",
        )


def profile_aliases(config: OwnershipConfig, profile: str) -> tuple[str, ...]:
    return tuple(sorted({entry.source for entry in config.entries if entry.profile == profile}))


def profile_names(config: OwnershipConfig, profile: str) -> tuple[str, ...]:
    return tuple(sorted({entry.name for entry in config.entries if entry.profile == profile}))


def _continuity_from(value: object) -> SourceContinuity:
    if not isinstance(value, dict) or set(value) != _CONTINUITY_KEYS:
        raise _mismatch()
    if (not isinstance(value["alias"], str) or type(value["reliable"]) is not bool
            or any(type(value[key]) is not int for key in (
                "dev", "ino", "size", "mtime_ns", "ctime_ns", "nlink", "uid", "mode"))
            or not isinstance(value["names_sha256"], str)):
        raise _mismatch()
    return SourceContinuity(**value)


def _approval_from(raw: bytes) -> Approval:
    try:
        data = json.loads(raw.decode("ascii"), object_pairs_hook=_unique_pairs,
                          parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError):
        raise _mismatch() from None
    if not isinstance(data, dict) or set(data) != _APPROVAL_KEYS:
        raise _mismatch()
    if not isinstance(data["sources"], list):
        raise _mismatch()
    data["sources"] = tuple(_continuity_from(item) for item in data["sources"])
    if len({item.alias for item in data["sources"]}) != len(data["sources"]):
        raise _mismatch()
    if data.get("operation") not in ("migrate", "cleanup") or data.get("surface") != "cli_tty":
        raise _mismatch()
    for field in ("operator_uid",):
        if not isinstance(data[field], int) or isinstance(data[field], bool):
            raise _mismatch()
    for field in ("created_at", "expires_at"):
        if type(data[field]) not in (int, float) or not math.isfinite(data[field]):
            raise _mismatch()
    if (not 0 < data["expires_at"] - data["created_at"] <= MAX_APPROVAL_SECONDS
            or not is_qualified_id(data["approval_id"], "ap")
            or qualified_controller(data["approval_id"]) != data["controller_id"]
            or any(not isinstance(data[field], str) for field in (
                "operator_login", "controller_id", "profile", "map_sha256",
                "destination_sha256", "authority_digest"))):
        raise _mismatch()
    expiry = data["evidence_expires_at"]
    if expiry is not None and (type(expiry) not in (int, float) or not math.isfinite(expiry)
                               or data["expires_at"] > expiry):
        raise _mismatch()
    if data["evidence_sha256"] is not None and not isinstance(data["evidence_sha256"], str):
        raise _mismatch()
    return Approval(**data)


def _parse_token_expiry_sidecar(raw: bytes, approval: Approval) -> dict[str, Any]:
    try:
        data = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError):
        raise _mismatch("corrupt token expiry binding") from None
    if not isinstance(data, dict) or set(data) != _TOKEN_EXPIRY_KEYS:
        raise _mismatch("corrupt token expiry binding")
    if (type(data["version"]) is not int or data["version"] != 1
            or data["approval_id"] != approval.approval_id
            or data["controller_id"] != approval.controller_id
            or type(data["operator_uid"]) is not int or data["operator_uid"] != approval.operator_uid
            or data["operator_login"] != approval.operator_login
            or data["profile"] != approval.profile
            or data["operation"] != approval.operation
            or not hmac.compare_digest(data["map_sha256"], approval.map_sha256)
            or not hmac.compare_digest(data["destination_sha256"], approval.destination_sha256)
            or not hmac.compare_digest(data["authority_digest"], approval.authority_digest)):
        raise _mismatch("corrupt token expiry binding")
    for key in ("token_expires_at", "effective_expires_at", "bound_at"):
        val = data[key]
        if type(val) not in (int, float) or not math.isfinite(val):
            raise _mismatch("corrupt token expiry binding")
    if data["effective_expires_at"] > approval.expires_at:
        raise _mismatch("corrupt token expiry binding")
    return data


class ApprovalStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.directory = self.root / "approvals"

    def _path(self, approval_id: str) -> Path:
        return self.directory / f"{approval_id}.json"

    def _token_expiry_path(self, approval_id: str) -> Path:
        return self.directory / f"{approval_id}.token_expiry.json"

    def _load_token_expiry_sidecar(self, approval: Approval) -> dict[str, Any] | None:
        sidecar_path = self._token_expiry_path(approval.approval_id)
        try:
            raw = read_private_file(sidecar_path, limit=4096)
        except FileNotFoundError:
            return None
        except SecretBrokerError:
            raise _mismatch("corrupt token expiry binding") from None
        except OSError:
            raise _mismatch("corrupt token expiry binding") from None
        return _parse_token_expiry_sidecar(raw, approval)

    def bind_token_expiry(self, approval: Approval, expires_at: float | None) -> float:
        """Bind earliest observed worker token expiry to approval in a private atomic sidecar."""
        if not math.isfinite(approval.expires_at):
            raise _mismatch("invalid approval expiry")
        if (expires_at is None or isinstance(expires_at, bool)
                or not isinstance(expires_at, (int, float))
                or not math.isfinite(expires_at)):
            raise SecretBrokerError("response_invalid", "worker token expiry is missing or invalid")

        sidecar_path = self._token_expiry_path(approval.approval_id)
        existing = self._load_token_expiry_sidecar(approval)
        existing_effective = approval.expires_at if existing is None else float(existing["effective_expires_at"])

        effective = min(existing_effective, float(expires_at), approval.expires_at)

        payload = {
            "version": 1,
            "approval_id": approval.approval_id,
            "controller_id": approval.controller_id,
            "operator_uid": approval.operator_uid,
            "operator_login": approval.operator_login,
            "profile": approval.profile,
            "operation": approval.operation,
            "map_sha256": approval.map_sha256,
            "destination_sha256": approval.destination_sha256,
            "authority_digest": approval.authority_digest,
            "token_expires_at": float(expires_at),
            "effective_expires_at": float(effective),
            "bound_at": time.time(),
        }
        write_private_file(sidecar_path, canonical_json(payload), exclusive=False)
        return effective

    def grant_interactive(self, *, operation, profile, config: OwnershipConfig, registry,
                          destination_sha256: str, authority_digest: str,
                          admission: StorageAdmission | None, minutes: int,
                          tty_in: TextIO, tty_out: TextIO) -> Approval:
        require_human_terminal(tty_in, tty_out)
        if (not isinstance(minutes, int) or isinstance(minutes, bool)
                or not 1 <= minutes <= 60):
            raise SecretBrokerError(
                "input_invalid", "approval lifetime must be an integer between 1 and 60 minutes",
            )
        aliases = profile_aliases(config, profile)
        continuities = []
        for alias in aliases:
            continuity, _ = read_names(registry, alias)
            if not continuity.reliable:
                raise SecretBrokerError(
                    "continuity_unreliable", "source continuity cannot be established reliably",
                )
            continuities.append(continuity)
        now = time.time()
        expires_at = now + (minutes * 60)
        if admission is not None:
            if admission.expires_at <= now:
                raise SecretBrokerError("evidence_expired", "storage evidence has expired")
            expires_at = min(expires_at, admission.expires_at)
        if operation == "migrate" and admission is None:
            raise SecretBrokerError("evidence_required", "migration requires admitted evidence")
        dest = config.profiles[profile].destination
        tty_out.write(
            f"Secret ownership approval requested\n"
            f"  operation:   {operation}\n"
            f"  profile:     {profile}\n"
            f"  destination: {dest.endpoint} ({dest.project_id}/{dest.environment})\n"
            f"  sources:     {', '.join(aliases)}\n"
            f"  valid for:   {minutes} minute(s)\n"
            f"Type 'approve' to grant this operation: "
        )
        tty_out.flush()
        line = tty_in.readline()
        if line.strip() != "approve":
            raise SecretBrokerError("approval_required", "operator declined approval")
        if time.time() >= expires_at:
            raise SecretBrokerError("approval_expired", "approval confirmation expired")
        for approved in continuities:
            fresh, _ = read_names(registry, approved.alias)
            if not fresh.reliable or not fresh.same_as(approved):
                raise SecretBrokerError("source_changed", "a source changed during approval")
        try:
            login = pwd.getpwuid(os.geteuid()).pw_name
        except KeyError:
            login = str(os.geteuid())
        controller_id = load_controller_id(self.root)
        approval = Approval(
            approval_id=new_qualified_id("ap", controller_id), operation=operation,
            operator_uid=os.geteuid(), operator_login=login, controller_id=controller_id,
            profile=profile, sources=tuple(continuities), map_sha256=config.map_sha256,
            destination_sha256=destination_sha256, authority_digest=authority_digest,
            evidence_sha256=admission.evidence_sha256 if admission is not None else None,
            evidence_expires_at=float(admission.expires_at) if admission is not None else None,
            created_at=now, expires_at=expires_at, surface="cli_tty",
        )
        ensure_private_dir(self.root)
        write_private_file(self._path(approval.approval_id), canonical_json(asdict(approval)))
        return approval

    def load(self, approval_id: str) -> Approval:
        if not approval_id:
            raise SecretBrokerError("approval_required", "an operator approval is required")
        if not is_qualified_id(approval_id, "ap"):
            raise _mismatch()
        owner = qualified_controller(approval_id)
        if owner != load_controller_id(self.root):
            raise SecretBrokerError(
                "approval_mismatch", "approval was issued by another controller",
                remedy=f"run from controller {owner}",
            )
        try:
            raw = read_private_file(self._path(approval_id), limit=256 * 1024)
        except FileNotFoundError:
            raise SecretBrokerError("approval_required", "approval was not found") from None
        approval = _approval_from(raw)
        if approval.approval_id != approval_id or approval.controller_id != owner:
            raise _mismatch()
        return approval

    def require_current(self, approval: Approval, *, operation, profile, config: OwnershipConfig,
                        continuities: Mapping[str, SourceContinuity] | Iterable[SourceContinuity],
                        destination_sha256, authority_digest, now: float) -> None:
        if (not math.isfinite(now) or approval.created_at > now
                or not 0 < approval.expires_at - approval.created_at <= MAX_APPROVAL_SECONDS):
            raise _mismatch()
        if now >= approval.expires_at:
            raise SecretBrokerError("approval_expired", "approval has expired")
        if approval.evidence_expires_at is not None and now >= approval.evidence_expires_at:
            raise SecretBrokerError("approval_expired", "approval evidence has expired")

        # R2: check token expiry sidecar if present
        sidecar_data = self._load_token_expiry_sidecar(approval)
        if sidecar_data is not None:
            effective_expires_at = float(sidecar_data["effective_expires_at"])
            if now >= effective_expires_at:
                raise SecretBrokerError("approval_expired", "approval credential lifetime has expired")

        if (approval.operation != operation or approval.profile != profile
                or approval.surface != "cli_tty" or approval.operator_uid != os.geteuid()):
            raise _mismatch()
        try:
            login = pwd.getpwuid(os.geteuid()).pw_name
        except KeyError:
            login = str(os.geteuid())
        if approval.operator_login != login:
            raise _mismatch()
        if approval.controller_id != load_controller_id(self.root):
            raise _mismatch()
        if operation == "cleanup":
            try:
                read_private_file(self.directory / f"{approval.approval_id}.consumed", limit=64)
            except FileNotFoundError:
                pass
            else:
                raise _mismatch("approval was already used")
        if operation == "migrate" and approval.evidence_sha256 is None:
            raise _mismatch()
        if not hmac.compare_digest(approval.map_sha256, config.map_sha256):
            raise _mismatch()
        if not hmac.compare_digest(approval.destination_sha256, destination_sha256):
            raise _mismatch()
        if not hmac.compare_digest(approval.authority_digest, authority_digest):
            raise SecretBrokerError("authority_mismatch", "ownership authority changed since approval")
        current = dict(continuities) if isinstance(continuities, Mapping) else {
            item.alias: item for item in continuities
        }
        approved = {item.alias: item for item in approval.sources}
        if set(current) != set(approved) or set(approved) != set(profile_aliases(config, profile)):
            raise _mismatch()
        for alias, observed in sorted(current.items()):
            if not observed.reliable or not approved[alias].reliable:
                raise SecretBrokerError(
                    "continuity_unreliable", "source continuity cannot be established reliably",
                )
            if not observed.same_as(approved[alias]):
                raise SecretBrokerError("source_changed", "a source changed since approval")

    def consume(self, approval: Approval) -> None:
        """Mark a cleanup approval used; a second use refuses."""
        ensure_private_dir(self.directory)
        try:
            write_private_file(self.directory / f"{approval.approval_id}.consumed", b"consumed\n",
                               exclusive=True)
        except FileExistsError:
            raise _mismatch("approval was already used") from None


__all__ = [
    "ApprovalStore", "MAX_APPROVAL_SECONDS", "profile_aliases", "profile_names",
    "require_human_terminal",
]
