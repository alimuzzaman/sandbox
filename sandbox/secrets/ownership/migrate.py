"""Create-only migration of one ownership profile (normative 12-step order).

Values are bytearrays held only in this process and the worker, wiped after
use. Nothing value-derived (no revision, HMAC or hash of a value) is written,
journaled, audited or printed.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from pathlib import Path
from typing import Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from .manifest import OwnershipAdapter

from sandbox.secrets.models import SecretBrokerError
from .approval import ApprovalStore, profile_aliases
from .journal import (
    Journal, destination_sha256, new_qualified_id, read_private_file, write_private_file,
)
from .models import (
    Existence, Grant, MapEntry, NameState, OwnershipContext, RevocationOutcome,
    SourceContinuity, canonical_json,
)

MAX_RUN_SECONDS = 3600
ATTESTATION = "operator_attested"
_EXIT = {
    2: frozenset((
        "ownership_not_configured", "profile_unknown", "classification_blocked",
        "source_unregistered", "source_unsupported", "other_domain", "ownership_keys_required",
        "selection_invalid", "input_invalid", "option_conflict", "command_invalid",
        "consumer_collision", "source_key_missing", "duplicate_key", "syntax_unsupported",
        "source_missing", "source_too_large", "escalation_unsupported", "audit_unavailable",
    )),
    3: frozenset((
        "approval_required", "approval_expired", "approval_mismatch", "approval_human_only",
        "authority_mismatch", "evidence_required", "evidence_inadmissible", "evidence_expired",
        "source_changed", "continuity_unreliable", "credential_missing",
        "credential_scope_invalid", "journal_locked", "journal_foreign_controller",
        "journal_missing_owner_elsewhere", "journal_corrupt", "cleanup_ineligible",
        "tty_required", "key_missing",
        "source_scope_denied", "source_unsafe",
    )),
    4: frozenset((
        "provider_contract_unconfirmed", "provider_policy_requires_approval",
        "existence_unknown", "pre_existing", "provider_failed", "response_invalid",
        "value_unsupported", "operation_timeout",
    )),
    5: frozenset(("run_stopped",)),
    6: frozenset(("cleanup_unknown",)),
}
_STOP_STATES = frozenset((NameState.CONFLICT, NameState.UNRESOLVED, NameState.ACCEPTANCE_UNKNOWN))
_UNKNOWN_PRIOR = frozenset((NameState.ATTEMPTED, NameState.ACCEPTANCE_UNKNOWN, NameState.CREATED))
_SETTLED = frozenset((NameState.VERIFIED, NameState.RECONCILED))


def exit_for(code: str) -> int:
    for status, codes in _EXIT.items():
        if code in codes:
            return status
    return 1


def wipe(values: Mapping[str, bytearray] | None) -> None:
    for buffer in (values or {}).values():
        if isinstance(buffer, bytearray):
            buffer[:] = b"\0" * len(buffer)


def canonical_root(ctx: OwnershipContext) -> str:
    return str(Path(ctx.project_root).resolve())


def profile_entries(ctx: OwnershipContext, profile: str) -> tuple[MapEntry, ...]:
    if profile not in ctx.config.profiles:
        raise SecretBrokerError("profile_unknown", "ownership profile is not configured")
    return tuple(sorted((e for e in ctx.config.entries if e.profile == profile),
                        key=lambda entry: entry.name))


def authority_for(ctx: OwnershipContext, profile: str, capability: str) -> str:
    from sandbox.secrets.authority import ownership_digest
    return ownership_digest(profile, ctx.config.as_normalized(), capability,
                            project_root=canonical_root(ctx))


def build_grant(ctx: OwnershipContext, profile: str, capability: str,
                names: tuple[str, ...]) -> Grant:
    """Grant with complete canonical authority, validated in the parent before
    any credential access (the worker validates again)."""
    from sandbox.secrets.authority import validate_ownership_grant
    destination = ctx.config.profiles[profile].destination
    client = destination.create_client_id if capability == "create" else destination.read_client_id
    if not client:
        raise SecretBrokerError("credential_scope_invalid", "ownership profile lacks that client")
    grant = Grant(
        profile=profile, capability=capability, destination=destination, client_id=client,
        authority_digest=authority_for(ctx, profile, capability), names=tuple(names),
        project_root=canonical_root(ctx), ownership=ctx.config.as_normalized(),
    )
    validate_ownership_grant(grant)
    return grant


def resolve_adapter(adapter, *, capability: str):
    if adapter is not None:
        return adapter
    from .manifest import OWNERSHIP_MANIFEST, load_ownership_adapter
    manifest = OWNERSHIP_MANIFEST.get("infisical-v4") or {}
    if capability not in tuple(manifest.get("capabilities") or ()):
        raise SecretBrokerError("provider_contract_unconfirmed", "provider capability is unavailable")
    if capability == "create" and manifest.get("contract_confirmed") is not True:
        raise SecretBrokerError("provider_contract_unconfirmed", "provider contract is unconfirmed")
    return load_ownership_adapter("infisical-v4")


def classify_profile(ctx: OwnershipContext, profile: str,
                     existence: Mapping[str, Existence] | None = None,
                     *, proven_removed: frozenset[str] = frozenset()):
    """Names-only classification: (report, continuities, source_names)."""
    from .classify import classify
    from .source_view import read_names
    profile_entries(ctx, profile)
    continuities: dict[str, SourceContinuity] = {}
    source_names: dict[str, tuple[str, ...]] = {}
    for alias in profile_aliases(ctx.config, profile):
        try:
            continuity, names = read_names(ctx.registry, alias)
        except SecretBrokerError as exc:
            if exc.code == "source_unsupported":
                continue      # classify reports the alias as source_unsupported
            raise
        continuities[alias] = continuity
        source_names[alias] = names
    report = classify(ctx.config, profile, source_names, existence, proven_removed=proven_removed)
    return report, continuities, source_names


def payload(code: str, profile: str, *, ok: bool, states: Mapping[str, object] | None = None,
            **extra) -> dict:
    grouped: dict[str, list[str]] = {}
    for name, state in sorted((states or {}).items()):
        key = state.value if hasattr(state, "value") else str(state)
        grouped.setdefault(key, []).append(name)
    body = {
        "ok": ok, "code": code, "profile": profile,
        "counts": {state: len(names) for state, names in sorted(grouped.items())},
        "names": grouped,
    }
    body.update({key: value for key, value in extra.items() if value is not None})
    body["_exit"] = 0 if ok else exit_for(code)
    return body


def report_payload(report, code: str, *, ok: bool, **extra) -> dict:
    data = report.as_dict()
    body = {"ok": ok, "code": code, "profile": data["profile"], "counts": data["counts"],
            "names": data["names"]}
    body.update({key: value for key, value in extra.items() if value is not None})
    body["_exit"] = 0 if ok else exit_for(code)
    return body


def write_completion_record(root: Path, *, run_id: str, operation: str, profile: str,
                            approval_id: str | None, started_at: float, end_reason: str,
                            names: Mapping[str, list[str]], revocation: str,
                            next_action: str | None) -> None:
    record = {
        "run_id": run_id, "operation": operation, "profile": profile,
        "approval_id": approval_id, "started_at": started_at, "ended_at": time.time(),
        "end_reason": end_reason,
        "counts": {state: len(items) for state, items in sorted(names.items())},
        "names": {state: list(items) for state, items in sorted(names.items())},
        "revocation": revocation, "next_action": next_action,
        "storage_attestation": ATTESTATION,
    }
    write_private_file(Path(root) / "runs" / f"{run_id}.json", canonical_json(record))


def last_run_record(root: Path, profile: str) -> dict | None:
    import json
    runs = Path(root) / "runs"
    if not runs.is_dir() or runs.is_symlink():
        return None
    latest = None
    for path in runs.glob("run_*.json"):
        try:
            record = json.loads(read_private_file(path).decode("ascii"))
        except (FileNotFoundError, UnicodeDecodeError, ValueError, SecretBrokerError):
            continue
        if (isinstance(record, dict) and record.get("profile") == profile
                and type(record.get("ended_at")) in (int, float)
                and math.isfinite(record["ended_at"])):
            if latest is None or record.get("ended_at", 0) > latest.get("ended_at", 0):
                latest = record
    return latest


def load_admission(ctx: OwnershipContext, profile: str, evidence_sha256: str):
    """Re-admit the stored evidence copy bound by its sha256."""
    from .evidence import admit_evidence
    if not isinstance(evidence_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
        raise SecretBrokerError("evidence_required", "storage evidence is required")
    path = Path(ctx.runtime_root) / "evidence" / f"{evidence_sha256}.json"
    try:
        raw = read_private_file(path, limit=1024 * 1024)
    except FileNotFoundError:
        raise SecretBrokerError("evidence_required", "stored storage evidence was not found") from None
    if hashlib.sha256(raw).hexdigest() != evidence_sha256:
        raise SecretBrokerError("evidence_inadmissible", "stored storage evidence changed")
    admission = admit_evidence(path, ctx.config.profiles[profile].destination, now=time.time())
    if admission.evidence_sha256 != evidence_sha256:
        raise SecretBrokerError("evidence_inadmissible", "stored storage evidence changed")
    return admission


def read_entry_values(ctx: OwnershipContext, entries, continuities) -> dict[str, bytearray]:
    """Source values keyed by destination name; continuity re-checked per alias."""
    from .source_view import read_values
    values: dict[str, bytearray] = {}
    try:
        by_alias: dict[str, list[MapEntry]] = {}
        for entry in entries:
            by_alias.setdefault(entry.source, []).append(entry)
        for alias, group in sorted(by_alias.items()):
            read = read_values(ctx.registry, alias, sorted({entry.key for entry in group}),
                               continuities[alias])
            try:
                for entry in group:
                    values[entry.name] = bytearray(read[entry.key])
            finally:
                wipe(read)
        return values
    except BaseException:
        wipe(values)
        raise


def _comment(ctx: OwnershipContext, run_id: str) -> str:
    # Owner guidance for a foreign controller; no authority, no value.
    return f"sandbox-ownership controller={ctx.controller_id} run={run_id}"


def _reconcile(ctx, profile, names, values, adapter, deadline, *, settled=(),
               revocations=None, approval=None, store=None, continuities=None,
               dest_sha=None, approval_digest=None) -> tuple[dict, str]:
    """Exact-item check of journaled unknown names under a READ grant."""
    grant = build_grant(ctx, profile, "read", tuple(sorted(names)))
    outcome: dict[str, NameState | None] = {}
    persisted_exp = deadline
    if store is not None and approval is not None and hasattr(store, "_load_token_expiry_sidecar"):
        sidecar = store._load_token_expiry_sidecar(approval)
        if sidecar is not None:
            persisted_exp = min(persisted_exp, float(sidecar.get("effective_expires_at", deadline)))
    worker_deadline = min(deadline, persisted_exp)
    if time.time() >= worker_deadline:
        raise SecretBrokerError("approval_expired", "approval credential lifetime has expired")
    session = adapter.open_session(grant, deadline=worker_deadline)
    try:
        with session:
            effective_exp = worker_deadline
            if store is not None and approval is not None:
                token_expires_at = session.token_expires_at
                effective_exp = store.bind_token_expiry(approval, token_expires_at)
                if continuities is not None and dest_sha is not None and approval_digest is not None:
                    store.require_current(approval, operation="migrate", profile=profile, config=ctx.config,
                                          continuities=continuities, destination_sha256=dest_sha,
                                          authority_digest=approval_digest, now=time.time())
            token_exp = session.token_expires_at if (session.token_expires_at is not None and not isinstance(session.token_expires_at, bool) and isinstance(session.token_expires_at, (int, float)) and math.isfinite(session.token_expires_at)) else float("inf")
            effective_clock = min(deadline, float(effective_exp), float(token_exp))

            if time.time() >= effective_clock:
                raise SecretBrokerError("approval_expired", "approval credential lifetime has expired")

            existence = session.exists(list(grant.names))
            for name in grant.names:
                seen = existence.get(name)
                if time.time() >= effective_clock:
                    raise SecretBrokerError("approval_expired", "approval credential lifetime has expired")
                if seen is Existence.ABSENT:
                    outcome[name] = NameState.UNRESOLVED if name in settled else NameState.PENDING
                elif seen is Existence.PRESENT:
                    if time.time() >= effective_clock:
                        raise SecretBrokerError("approval_expired", "approval credential lifetime has expired")
                    equal = session.readback_equals(name, values[name])
                    outcome[name] = NameState.RECONCILED if equal is True else NameState.UNRESOLVED
                else:
                    outcome[name] = NameState.UNRESOLVED
    finally:
        if revocations is not None:
            revocations.append(revocation_outcome(session))
    return outcome, revocation_status(session)


def revocation_outcome(session) -> RevocationOutcome:
    try:
        outcome = session.revocation()
        if isinstance(outcome, RevocationOutcome):
            status = outcome.status if outcome.status in ("revoked", "not_applicable") else "cleanup_unknown"
            return RevocationOutcome(status, outcome.token_expires_at, outcome.next_action)
    except Exception:
        pass
    return RevocationOutcome("cleanup_unknown", None, None)


def revocation_status(session) -> str:
    return revocation_outcome(session).status


def run_migration(service_ctx: OwnershipContext, profile: str, approval_id: str | None, *,
                  confirm: bool, timeout_seconds: int, adapter: OwnershipAdapter | None = None) -> dict:
    ctx = service_ctx
    if (not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool)
            or not 1 <= timeout_seconds <= MAX_RUN_SECONDS):
        raise SecretBrokerError("input_invalid", "timeout must be between 1 and 3600 seconds")
    # 1-2: config and names-only classification; blocking refuses with 0 writes.
    entries = profile_entries(ctx, profile)
    report, continuities, _ = classify_profile(ctx, profile)
    if report.blocking:
        return report_payload(report, "classification_blocked", ok=False)
    # 3: dry run (existence unchecked, no approval needed).
    if not confirm:
        return report_payload(report, "dry_run", ok=True, storage_attestation=ATTESTATION)
    # 4: approval bound to continuity, map, destination, authority and expiry.
    store = ApprovalStore(ctx.runtime_root)
    if not approval_id:
        raise SecretBrokerError("approval_required", "an operator approval is required")
    approval = store.load(approval_id)
    destination = ctx.config.profiles[profile].destination
    dest_sha = destination_sha256(destination)
    create_digest = authority_for(ctx, profile, "create")
    store.require_current(approval, operation="migrate", profile=profile, config=ctx.config,
                          continuities=continuities, destination_sha256=dest_sha,
                          authority_digest=create_digest, now=time.time())
    # 5-6: stored evidence and provider capability.
    load_admission(ctx, profile, approval.evidence_sha256)
    adapter = resolve_adapter(adapter, capability="create")
    names = tuple(entry.name for entry in entries)
    by_name = {entry.name: entry for entry in entries}
    # 7: exclusive journal lock before any value read.
    journal = Journal.open(ctx.runtime_root, profile, dest_sha,
                           controller_id=ctx.controller_id, authority_digest=create_digest)
    run_id = new_qualified_id("run", ctx.controller_id)
    started = time.time()
    values: dict[str, bytearray] = {}
    revocations: list[RevocationOutcome | str] = []
    end_reason, code, next_action = "completed", "migrated", None
    session = None
    try:
        prior = journal.states()
        if not set(prior) <= set(names):
            raise SecretBrokerError("authority_mismatch", "journal names exceed the current map")
        if any(state in (NameState.CONFLICT, NameState.UNRESOLVED) for state in prior.values()):
            end_reason, code = "unresolved_prior", "run_stopped"
            next_action = "resolve the conflicting or unresolved names before resuming"
            return _finish(ctx, journal, run_id, approval, profile, started, end_reason, code,
                           next_action, revocations, operation="migrate")
        deadline = min(approval.expires_at, started + timeout_seconds)
        unknown = sorted(name for name, state in prior.items()
                         if state in _UNKNOWN_PRIOR | _SETTLED)
        if unknown:
            # Resume (FR-029/030): reconcile journaled names first, exact names only.
            values.update(read_entry_values(ctx, [by_name[n] for n in unknown], continuities))
            outcome, _ = _reconcile(ctx, profile, unknown, values, adapter, deadline,
                                    settled={n for n in unknown if prior[n] in _SETTLED},
                                    revocations=revocations,
                                    approval=approval, store=store,
                                    continuities=continuities, dest_sha=dest_sha,
                                    approval_digest=create_digest)
            for name, state in outcome.items():
                journal.append(run_id, approval.approval_id, name, state, "resume_exact_check")
            if any(state is NameState.UNRESOLVED for state in outcome.values()):
                end_reason, code = "unresolved", "run_stopped"
                next_action = "inspect unresolved names at the destination; no blind retry"
                return _finish(ctx, journal, run_id, approval, profile, started, end_reason,
                               code, next_action, revocations, operation="migrate")
        # 8: create session, parent-validated grant over all profile names.
        grant = build_grant(ctx, profile, "create", names)
        store.require_current(approval, operation="migrate", profile=profile, config=ctx.config,
                              continuities=continuities, destination_sha256=dest_sha,
                              authority_digest=create_digest, now=time.time())
        persisted_exp = approval.expires_at
        if hasattr(store, "_load_token_expiry_sidecar"):
            sidecar = store._load_token_expiry_sidecar(approval)
            if sidecar is not None:
                persisted_exp = min(persisted_exp, float(sidecar.get("effective_expires_at", approval.expires_at)))
        create_deadline = min(deadline, persisted_exp)
        if time.time() >= create_deadline:
            raise SecretBrokerError("approval_expired", "create authority expired")
        session = adapter.open_session(grant, deadline=create_deadline)
        try:
            with session:
                effective_exp = store.bind_token_expiry(approval, session.token_expires_at)
                store.require_current(approval, operation="migrate", profile=profile, config=ctx.config,
                                      continuities=continuities, destination_sha256=dest_sha,
                                      authority_digest=create_digest, now=time.time())
                if session.policy_requires_approval() is True:
                    raise SecretBrokerError("provider_policy_requires_approval",
                                            "destination policy requires change approval")
                token_exp = session.token_expires_at if (session.token_expires_at is not None and not isinstance(session.token_expires_at, bool) and math.isfinite(session.token_expires_at)) else float("inf")
                effective = min(deadline, float(effective_exp), float(token_exp))
                # 9: value-free existence of every name.
                existence = session.exists(list(names))
                if any(existence.get(name) not in (Existence.ABSENT, Existence.PRESENT)
                       for name in names):
                    raise SecretBrokerError("existence_unknown",
                                            "destination existence could not be determined")
                current = journal.states()
                present = [n for n in names if existence[n] is Existence.PRESENT]
                if journal.fresh and present:
                    for name in present:
                        journal.append(run_id, approval.approval_id, name,
                                       NameState.PRE_EXISTING, "preflight")
                    owners = getattr(session, "owners", {})
                    foreign = sorted({owners[name] for name in present\
                                      if name in owners and owners[name] != ctx.controller_id})
                    if foreign:
                        next_action = "resume on owning controller " + ", ".join(foreign)
                        raise SecretBrokerError("journal_missing_owner_elsewhere",
                                                "destination carries a foreign controller marker")
                    raise SecretBrokerError("pre_existing",
                                            "destination already holds mapped names")
                pending = []
                for name in names:
                    state = current.get(name)
                    if state in _SETTLED:
                        if existence[name] is not Existence.PRESENT:
                            journal.append(run_id, approval.approval_id, name,
                                           NameState.UNRESOLVED, "settled_missing")
                            end_reason, code = "unresolved", "run_stopped"
                    elif existence[name] is Existence.PRESENT:
                        journal.append(run_id, approval.approval_id, name,
                                       NameState.CONFLICT, "present_on_resume")
                        end_reason, code = "conflict", "run_stopped"
                    else:
                        pending.append(name)
                if code == "run_stopped":
                    next_action = "resolve conflicting or missing names before resuming"
                else:
                    # 10: values (continuity re-checked), then the WHOLE batch validated.
                    for name in pending:
                        if journal.states().get(name) is not NameState.PENDING:
                            journal.append(run_id, approval.approval_id, name, NameState.PENDING)
                    needed = [by_name[n] for n in pending if n not in values]
                    values.update(read_entry_values(ctx, needed, continuities))
                    adapter.validate_create_inputs(grant, {n: values[n] for n in pending})
                    # 11: one create per name, journaled before release.
                    for name in pending:
                        now = time.time()
                        if now >= effective or now >= approval.expires_at:
                            end_reason, code = "deadline", "run_stopped"
                            next_action = "approve again and resume; remaining names stay pending"
                            break

                        def before_send(name=name) -> None:
                            from .source_view import read_names
                            fresh_continuities = {}
                            for s in approval.sources:
                                c, _ = read_names(ctx.registry, s.alias)
                                fresh_continuities[s.alias] = c
                            store.require_current(
                                approval,
                                operation="migrate",
                                profile=profile,
                                config=ctx.config,
                                continuities=fresh_continuities,
                                destination_sha256=dest_sha,
                                authority_digest=create_digest,
                                now=time.time(),
                            )
                            if time.time() >= effective or time.time() >= approval.expires_at:
                                raise SecretBrokerError("approval_expired", "create authority expired")
                            journal.append(run_id, approval.approval_id, name,
                                           NameState.ATTEMPTED)

                        outcome = session.create(name, values[name],
                                                 comment=_comment(ctx, run_id),
                                                 before_send=before_send)
                        state = NameState(outcome.state)
                        if state in (NameState.CREATED, NameState.VERIFIED):
                            journal.append(run_id, approval.approval_id, name, NameState.CREATED)
                            equal = session.readback_equals(name, values[name])
                            final = NameState.VERIFIED if equal is True else NameState.UNRESOLVED
                            journal.append(run_id, approval.approval_id, name, final,
                                           None if equal is True else "readback_mismatch")
                            if final is NameState.UNRESOLVED:
                                end_reason, code = "unresolved", "run_stopped"
                                break
                            continue
                        journal.append(run_id, approval.approval_id, name, state,
                                       outcome.reason if outcome.reason in (
                                           "conflict", "approval", "timeout", "pre_send",
                                       ) else None)
                        if state in _STOP_STATES:
                            end_reason, code = state.value, "run_stopped"
                            next_action = "inspect stopped names; resume reconciles exact names"
                            break
                        if state is NameState.FAILED:
                            end_reason, code = "failed", "provider_failed"
                            break
        finally:
            revocations.append(revocation_outcome(session))
        return _finish(ctx, journal, run_id, approval, profile, started, end_reason, code,
                       next_action, revocations, operation="migrate")
    except SecretBrokerError as exc:
        action = next_action or ("approve again and resume; remaining names stay pending" if exc.code == "approval_expired" else None)
        return _finish(ctx, journal, run_id, approval, profile, started, exc.code, exc.code,
                       action, revocations, operation="migrate", ok=False)
    except BaseException:
        return _finish(ctx, journal, run_id, approval, profile, started, "interrupted",
                       "run_stopped", "resume reconciles exact journaled names; do not retry blindly",
                       revocations, operation="migrate", ok=False)
    finally:
        wipe(values)
        journal.close()


def _finish(ctx, journal, run_id, approval, profile, started, end_reason, code, next_action,
            revocations, *, operation, ok=None, states=None) -> dict:
    statuses = []
    uncertain_finite_expiries = []
    has_unknown_expiry = False

    for item in (revocations or ()):
        if isinstance(item, RevocationOutcome):
            status = item.status if item.status in ("revoked", "not_applicable") else "cleanup_unknown"
            statuses.append(status)
            if status == "cleanup_unknown":
                if (item.token_expires_at is not None and not isinstance(item.token_expires_at, bool)
                        and isinstance(item.token_expires_at, (int, float)) and math.isfinite(item.token_expires_at)):
                    uncertain_finite_expiries.append(float(item.token_expires_at))
                else:
                    has_unknown_expiry = True
        elif isinstance(item, str):
            status = item if item in ("revoked", "not_applicable") else "cleanup_unknown"
            statuses.append(status)
            if status == "cleanup_unknown":
                has_unknown_expiry = True
        else:
            statuses.append("cleanup_unknown")
            has_unknown_expiry = True

    if not statuses:
        revocation = "not_applicable"
    elif all(s == "revoked" for s in statuses):
        revocation = "revoked"
    elif all(s in ("revoked", "not_applicable") for s in statuses):
        revocation = "not_applicable" if all(s == "not_applicable" for s in statuses) else "revoked"
    else:
        revocation = "cleanup_unknown"

    states = journal.states() if states is None else states
    success = code in ("migrated", "cleaned") if ok is None else ok

    if revocation == "cleanup_unknown":
        if has_unknown_expiry or not uncertain_finite_expiries:
            exp_str = "expiry unknown"
        else:
            exp_str = f"expires at {int(max(uncertain_finite_expiries))}"
        token_action = f"derived access token revocation not confirmed; account for its remaining lifetime ({exp_str})"
        if success:
            code, success = "cleanup_unknown", False
        if next_action:
            next_action = f"{token_action}; {next_action}"
        else:
            next_action = token_action

    operator_action = "native bootstrap entries and Cloudflare credentials remain operator-managed"
    if next_action:
        if operator_action not in next_action:
            next_action = f"{next_action}; {operator_action}"
    else:
        next_action = operator_action

    body = payload(code, profile, ok=success, states=states, run_id=run_id,
                   approval_id=approval.approval_id if approval else None, revocation=revocation,
                   next_action=next_action, storage_attestation=ATTESTATION)
    try:
        write_completion_record(ctx.runtime_root, run_id=run_id, operation=operation,
                                profile=profile, approval_id=approval.approval_id if approval else None,
                                started_at=started, end_reason=end_reason, names=body["names"],
                                revocation=revocation, next_action=next_action)
    except Exception:
        body["persistence"] = "persistence_failed"
        body["completion_record"] = "persistence_failed"
        if body.get("ok"):
            body["operation_code"] = body["code"]
            body["ok"] = False
            body["code"] = "provider_failed"
            body["_exit"] = exit_for("provider_failed")
    return body


__all__ = [
    "ATTESTATION", "MAX_RUN_SECONDS", "authority_for", "build_grant", "canonical_root",
    "classify_profile", "exit_for", "last_run_record", "load_admission", "payload",
    "profile_entries", "read_entry_values", "report_payload", "resolve_adapter",
    "revocation_outcome", "revocation_status",
    "run_migration", "wipe", "write_completion_record",
]
