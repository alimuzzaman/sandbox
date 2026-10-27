"""Separate, human-approved source cleanup using fresh READ comparisons."""
from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manifest import OwnershipAdapter

from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.writer import remove_keys
from .approval import ApprovalStore
from .cleanup_progress import CleanupProgress
from .journal import Journal, destination_sha256, new_qualified_id, read_journal_states
from .models import NameState, OwnershipContext, SourceContinuity
from .migrate import (
    _finish, authority_for, build_grant, classify_profile, payload, profile_entries,
    read_entry_values, resolve_adapter, revocation_outcome, wipe,
)


def run_cleanup(service_ctx: OwnershipContext, profile: str, approval_id: str | None, *,
                confirm: bool, adapter: OwnershipAdapter | None = None) -> dict:
    ctx = service_ctx
    entries = profile_entries(ctx, profile)
    names = {entry.name for entry in entries}
    destination = ctx.config.profiles[profile].destination
    dest_sha = destination_sha256(destination)
    create_digest = authority_for(ctx, profile, "create")

    # Preflight read-only checks: no journal lock, no file mutation, in-memory recovery only.
    states = read_journal_states(ctx.runtime_root, profile, dest_sha)
    progress = CleanupProgress.load_or_recover(
        ctx.runtime_root, profile, dest_sha,
        controller_id=ctx.controller_id,
        authority_digest=create_digest,
        map_sha256=ctx.config.map_sha256,
        registry=ctx.registry,
        config=ctx.config,
        persist=False,
    )
    proven_removed = progress.proven_removed_names()
    report, continuities, _ = classify_profile(ctx, profile, proven_removed=proven_removed)

    eligible = (not report.blocking and bool(names) and states is not None
                and set(states) == names and all(state in (NameState.VERIFIED, NameState.RECONCILED)
                                                for state in states.values()))
    if not eligible:
        return payload("cleanup_ineligible", profile, ok=False, states=states or {},
                       next_action="keep source keys; every mapped name must be verified or reconciled")

    if not confirm:
        return payload("cleanup_plan", profile, ok=True, states=states,
                       next_action="a separate foreground human cleanup approval is required; "
                                   "the destination must be quiescent")

    store = ApprovalStore(ctx.runtime_root)
    approval = store.load(approval_id)
    read_digest = authority_for(ctx, profile, "read")
    store.require_current(approval, operation="cleanup", profile=profile, config=ctx.config,
                          continuities=continuities, destination_sha256=dest_sha,
                          authority_digest=read_digest, now=time.time())

    # Confirmed cleanup: acquire existing migration Journal lock before any progress mutation or writes.
    journal = Journal.open(ctx.runtime_root, profile, dest_sha, controller_id=ctx.controller_id,
                           authority_digest=create_digest)
    run_id, started = new_qualified_id("run", ctx.controller_id), time.time()
    result_states = dict(states)
    revocations = []
    code, reason, action = "cleaned", "completed", "final comparisons are observations; keep the destination quiescent"
    session = None
    try:
        if journal.states() != states:
            raise SecretBrokerError("cleanup_ineligible", "journal changed before cleanup")

        # RELOAD progress under lock (do not use pre-lock cached object)
        progress = CleanupProgress.load_or_recover(
            ctx.runtime_root, profile, dest_sha,
            controller_id=ctx.controller_id,
            authority_digest=create_digest,
            map_sha256=ctx.config.map_sha256,
            registry=ctx.registry,
            config=ctx.config,
            persist=False,
        )
        proven_removed = progress.proven_removed_names()
        for name in proven_removed:
            result_states[name] = "removed"

        # Capture fresh source continuities under lock
        current_report, fresh_continuities, _ = classify_profile(ctx, profile, proven_removed=proven_removed)
        if current_report.blocking:
            raise SecretBrokerError("cleanup_ineligible", "source mapping changed before cleanup")

        # Require current human approval with fresh continuities under lock
        store.require_current(approval, operation="cleanup", profile=profile, config=ctx.config,
                              continuities=fresh_continuities, destination_sha256=dest_sha,
                              authority_digest=read_digest, now=time.time())
        continuities = fresh_continuities

        # Persist validated recovery now that continuities + approval are confirmed under lock
        progress.persist_recovery()

        # Open worker session with READ capability only
        adapter = resolve_adapter(adapter, capability="read")
        grant = build_grant(ctx, profile, "read", tuple(sorted(names)))
        persisted_exp = approval.expires_at
        if hasattr(store, "_load_token_expiry_sidecar"):
            sidecar = store._load_token_expiry_sidecar(approval)
            if sidecar is not None:
                persisted_exp = min(persisted_exp, float(sidecar.get("effective_expires_at", approval.expires_at)))
        cleanup_deadline = min(approval.expires_at, persisted_exp)
        if time.time() >= cleanup_deadline:
            raise SecretBrokerError("approval_expired", "cleanup authority expired")
        session = adapter.open_session(grant, deadline=cleanup_deadline)
        with session:
            effective = cleanup_deadline
            if hasattr(store, "bind_token_expiry"):
                effective = store.bind_token_expiry(approval, session.token_expires_at)
            if session.token_expires_at is not None and not isinstance(session.token_expires_at, bool) and math.isfinite(session.token_expires_at):
                effective = min(effective, float(session.token_expires_at))
            effective = min(effective, cleanup_deadline)

            store.require_current(approval, operation="cleanup", profile=profile, config=ctx.config,
                                  continuities=continuities, destination_sha256=dest_sha,
                                  authority_digest=read_digest, now=time.time())
            store.consume(approval)

            for entry in entries:
                if entry.name in proven_removed:
                    continue
                if time.time() >= effective:
                    raise SecretBrokerError("approval_expired", "cleanup authority expired")

                policy = ctx.registry.policy(entry.source)
                values = read_entry_values(ctx, (entry,), continuities)
                try:
                    equal = session.readback_equals(entry.name, values[entry.name])
                    if equal is not True:
                        journal.append(run_id, approval.approval_id, entry.name,
                                       NameState.UNRESOLVED, "cleanup_readback_mismatch")
                        result_states[entry.name] = NameState.UNRESOLVED
                        code, reason = "run_stopped", "cleanup_unverified"
                        action = "keep remaining source keys; destination comparison was not confirmed"
                        break

                    if time.time() >= effective:
                        raise SecretBrokerError("approval_expired", "cleanup authority expired")

                    def before_replace(staged: SourceContinuity, _entry=entry) -> None:
                        from .source_view import read_names
                        progress.prepare(_entry.name, _entry.key, _entry.source, staged, approval.approval_id)
                        now_check = time.time()
                        if now_check >= effective or now_check >= approval.expires_at:
                            raise SecretBrokerError("approval_expired", "cleanup authority expired")
                        for s in approval.sources:
                            c, _ = read_names(ctx.registry, s.alias)
                            if not c.reliable or not c.same_as(continuities[s.alias]):
                                raise SecretBrokerError("source_changed", "source was modified concurrently")

                    new_cont = remove_keys(
                        policy.path, (entry.key,), expected=continuities[entry.source],
                        before_replace=before_replace,
                        deadline=min(effective, approval.expires_at),
                    )
                    continuities[entry.source] = new_cont
                    progress.checkpoint_removed(
                        entry.name, entry.key, entry.source, new_cont.dev, new_cont.ino, approval.approval_id,
                        names_sha256=new_cont.names_sha256,
                    )
                    result_states[entry.name] = "removed"
                finally:
                    wipe(values)
    except SecretBrokerError as exc:
        code, reason, action = exc.code, exc.code, "keep remaining source keys; obtain a fresh cleanup approval"
    except BaseException:
        code, reason, action = "run_stopped", "interrupted", "keep remaining source keys; inspect cleanup status"
    finally:
        if session is not None:
            revocations.append(revocation_outcome(session))
    try:
        return _finish(ctx, journal, run_id, approval, profile, started, reason, code, action,
                       revocations, operation="cleanup", states=result_states)
    finally:
        journal.close()


__all__ = ["run_cleanup"]
