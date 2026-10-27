"""Resolve an explicit profile subset under declared consumer bindings."""
from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manifest import OwnershipAdapter

from sandbox.secrets.adapter_manifest import source_kind
from sandbox.secrets.models import MAX_OUTPUT_BYTES, MAX_TIMEOUT_SECONDS, SecretBrokerError
from sandbox.secrets.policy import validate_destination
from sandbox.secrets.runner import refuse_escalation, validate_child_request
from .models import MapEntry, OwnershipConfig, OwnershipContext, ScopedRunRequest
from .migrate import build_grant, profile_entries, resolve_adapter, revocation_outcome, revocation_status, wipe
from .source_view import read_names, read_values

_PROVIDER_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _target(config: OwnershipConfig, entry: MapEntry) -> tuple | None:
    profile = config.profiles.get(entry.profile)
    if profile is None:
        return None
    dest = profile.destination
    return (dest.endpoint, dest.project_id, dest.environment, dest.path, entry.name)


def _domain(config: OwnershipConfig, entry: MapEntry) -> str | None:
    profile = config.profiles.get(entry.profile)
    return None if profile is None else profile.domain


def resolve_scoped_bindings(service_ctx: OwnershipContext, request: ScopedRunRequest,
                            adapter: OwnershipAdapter | None = None) -> dict[str, str]:
    ctx = service_ctx
    if not request.keys:
        raise SecretBrokerError("ownership_keys_required", "explicit ownership keys are required")
    if (not isinstance(request.keys, tuple)
            or any(type(key) is not str for key in request.keys)
            or len(set(request.keys)) != len(request.keys)):
        raise SecretBrokerError("selection_invalid", "ownership selection is invalid")

    if request.profile not in ctx.config.profiles:
        raise SecretBrokerError("profile_unknown", "ownership profile is not configured")
    profile_cfg = ctx.config.profiles[request.profile]

    entries = profile_entries(ctx, request.profile)
    by_name: dict[str, MapEntry] = {}
    for entry in entries:
        if entry.name in by_name:
            raise SecretBrokerError("classification_blocked", "profile has duplicate mapped names")
        if not _PROVIDER_NAME.fullmatch(entry.name):
            raise SecretBrokerError("classification_blocked", "mapped name must be an uppercase environment identifier")
        if entry.origin not in ("source", "destination"):
            raise SecretBrokerError("classification_blocked", "entry origin must be source or destination")
        by_name[entry.name] = entry

    if not set(request.keys) <= set(by_name):
        domain = profile_cfg.domain
        foreign_names = {
            entry.name for entry in ctx.config.entries
            if entry.profile in ctx.config.profiles
            and ctx.config.profiles[entry.profile].domain != domain
        }
        if any(name in foreign_names for name in request.keys):
            raise SecretBrokerError("other_domain", "selected keys belong to another domain")
        raise SecretBrokerError("selection_invalid", "unknown ownership keys selected")

    selected = [by_name[name] for name in request.keys]

    # Consumer collisions across full profile
    profile_consumers: dict[str, int] = {}
    for entry in entries:
        profile_consumers[entry.consumer] = profile_consumers.get(entry.consumer, 0) + 1
    if any(count > 1 for count in profile_consumers.values()):
        raise SecretBrokerError("consumer_collision", "profile has duplicate consumer environment variables")

    # Validate destination / consumer names for selected entries
    for entry in selected:
        validate_destination(entry.consumer)

    # Source key mapping checks (cross-domain, undecided, multiply-assigned)
    by_key: dict[tuple[str, str], list[MapEntry]] = {}
    for entry in ctx.config.entries:
        by_key.setdefault((entry.source, entry.key), []).append(entry)

    for entry in selected:
        mapped = by_key.get((entry.source, entry.key), [])
        domains = {_domain(ctx.config, e) for e in mapped} - {None}
        if len(domains) > 1:
            raise SecretBrokerError("other_domain", "source key is mapped across different domains")
        if any(e.profile == "undecided" for e in mapped):
            raise SecretBrokerError("classification_blocked", "source key is mapped to undecided")
        if len(mapped) > 1:
            raise SecretBrokerError("classification_blocked", "source key is multiply assigned")

    # Scope and target checks
    scope = (profile_cfg.destination.endpoint, profile_cfg.destination.project_id)
    foreign_scope = any(
        other.domain != profile_cfg.domain
        and (other.destination.endpoint, other.destination.project_id) == scope
        for name, other in ctx.config.profiles.items() if name != request.profile
    )
    if foreign_scope:
        raise SecretBrokerError("other_domain", "destination scope shared across different domains")

    targets: dict[tuple, int] = {}
    for entry in ctx.config.entries:
        tgt = _target(ctx.config, entry)
        if tgt is not None:
            targets[tgt] = targets.get(tgt, 0) + 1
    for entry in selected:
        tgt = _target(ctx.config, entry)
        if tgt is not None and targets.get(tgt, 0) > 1:
            raise SecretBrokerError("classification_blocked", "duplicate destination target")

    # Registry policy registration and dotenv type for all sources referenced by profile
    for alias in sorted({entry.source for entry in entries if entry.origin == "source"}):
        try:
            policy = ctx.registry.policy(alias)
        except SecretBrokerError as exc:
            if exc.code == "source_unknown":
                raise SecretBrokerError(
                    "source_unregistered", "secret source alias is not registered",
                    remedy="register the source under secrets.sources before mapping it",
                ) from None
            raise
        if source_kind(policy) != "file" or getattr(policy, "format", None) != "dotenv":
            raise SecretBrokerError(
                "source_unsupported",
                "secret ownership supports registered dotenv file sources only",
            )

    # Argv, escalation, and bounds checks precede any source read
    validate_child_request(request.argv, {entry.consumer: "validation-only" for entry in selected})
    refuse_escalation(request.argv)
    if (type(request.timeout_seconds) is not int or not 1 <= request.timeout_seconds <= MAX_TIMEOUT_SECONDS
            or type(request.max_output_bytes) is not int
            or not 1 <= request.max_output_bytes <= MAX_OUTPUT_BYTES):
        raise SecretBrokerError("command_invalid", "ownership child bounds are invalid")

    remote = [entry for entry in selected if entry.origin == "destination"]
    grant = build_grant(ctx, request.profile, "read", tuple(sorted(e.name for e in remote))) if remote else None
    if remote:
        adapter = resolve_adapter(adapter, capability="read")
    buffers, result = {}, {}
    try:
        for alias in sorted({entry.source for entry in selected if entry.origin == "source"}):
            group = [entry for entry in selected if entry.source == alias and entry.origin == "source"]
            continuity, _ = read_names(ctx.registry, alias)
            read = read_values(ctx.registry, alias, sorted({entry.key for entry in group}), continuity)
            try:
                for entry in group:
                    buffers[entry.name] = bytearray(read[entry.key])
            finally:
                wipe(read)
        if remote:
            session = adapter.open_session(grant, deadline=time.time() + request.timeout_seconds)
            try:
                with session:
                    read = session.read(grant.names)
                    try:
                        if set(read) != set(grant.names) or any(not isinstance(v, bytearray) for v in read.values()):
                            raise SecretBrokerError("response_invalid", "destination read was incomplete")
                        for name in grant.names:
                            buffers[name] = bytearray(read[name])
                    finally:
                        wipe(read)
            except SecretBrokerError as exc:
                outcome = revocation_outcome(session)
                if outcome.status == "cleanup_unknown":
                    exc.revocation = "cleanup_unknown"
                    exc.revocation_outcome = outcome
                raise
            outcome = revocation_outcome(session)
            if outcome.status != "revoked":
                err = SecretBrokerError("cleanup_unknown", "derived token revocation was not confirmed")
                err.revocation = "cleanup_unknown"
                err.revocation_outcome = outcome
                raise err
        invalid = False
        for entry in selected:
            try:
                text = buffers[entry.name].decode("utf-8", "strict")
            except UnicodeDecodeError:
                invalid = True
                break
            if not text or "\x00" in text:
                invalid = True
                break
            result[entry.consumer] = text
        if invalid:
            result.clear()
            raise SecretBrokerError("value_unsupported", "selected value cannot be delivered losslessly")
        return result
    finally:
        wipe(buffers)


__all__ = ["resolve_scoped_bindings"]
