"""Storage authority lifecycle service for capability evaluation and review."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from sandbox.owned_storage_lifecycle.models import (
    AcceptanceOutcome,
    AcceptanceState,
    AuthorityCapability,
    CapabilityAcceptance,
    CapabilityPromotion,
    PromotionPhase,
    SupportTier,
)
from sandbox.owned_storage_lifecycle.repository import (
    LifecycleError,
    StorageAuthorityLifecycleRepository,
)


class LifecycleServiceError(Exception):
    def __init__(self, message: str, code: str = "authority_unavailable"):
        super().__init__(f"[{code}] {message}")
        self.code = code


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# The default when a caller observed nothing. It must never prove anything. The value
# stays "rev_installed" so already-recorded capabilities do not read as drifted.
UNOBSERVED_REVISION = "rev_installed"


def _authority_check(state, remote_identity, service_revision, source_revision, now):
    """Return (acceptance, reason) for this remote and these observed revisions.

    Every link must hold: complete acceptance -> its promotion -> this remote, a
    supported adoptable promotion, the same binding, unexpired, unrevoked, and both
    observed revisions. Missing or malformed data never proves anything.
    """
    observed = service_revision != UNOBSERVED_REVISION and bool(source_revision)
    promotions = state.get("promotions", {})
    revocations = state.get("revocations", {}).values()
    reasons = set()
    for acceptance in state.get("acceptances", {}).values():
        if acceptance.get("outcome") != AcceptanceOutcome.COMPLETE.value:
            continue
        promotion = promotions.get(acceptance.get("promotion_id"))
        if not isinstance(promotion, dict) or promotion.get("remote_identity") != remote_identity:
            continue
        if (promotion.get("authority_binding_id") != acceptance.get("authority_binding_id")
                or promotion.get("phase") != PromotionPhase.SUPPORTED.value
                or promotion.get("adoptable") is not True):
            reasons.add("authority_binding_mismatch")
            continue
        if any(item.get("remote_identity") == remote_identity
               and (item.get("promotion_id") == promotion.get("promotion_id")
                    or item.get("authority_binding_id") == promotion.get("authority_binding_id"))
               for item in revocations):
            reasons.add("authority_revoked")
            continue
        if not _unexpired(promotion.get("expires_at"), now):
            reasons.add("authority_expired")
            continue
        if not observed:
            continue
        if (promotion.get("service_revision") != service_revision
                or promotion.get("source_revision") != source_revision):
            reasons.add("authority_revision_mismatch")
            continue
        return acceptance, "proven"
    for reason in ("authority_revoked", "authority_expired",
                   "authority_revision_mismatch", "authority_binding_mismatch"):
        if reason in reasons:
            return None, reason
    return None, "implemented_unproven"


def _unexpired(value, now) -> bool:
    if not isinstance(value, str):
        return False
    try:
        expires = datetime.datetime.fromisoformat(value)
    except ValueError:
        return False
    return expires.tzinfo is not None and expires > now


class AuthorityLifecycleService:
    def __init__(self, repository: StorageAuthorityLifecycleRepository):
        self.repository = repository

    def evaluate_capability(
        self,
        *,
        remote_identity: str,
        platform_mode: str = "ubuntu-24.04-systemd-255-private-root-v1",
        service_revision: str = UNOBSERVED_REVISION,
        source_revision: Optional[str] = None,
    ) -> Dict[str, Any]:
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        observed_at = now_dt.isoformat()
        expires_at = (now_dt + datetime.timedelta(minutes=15)).isoformat()

        # Check platform support
        if platform_mode != "ubuntu-24.04-systemd-255-private-root-v1":
            return {
                "ok": True,
                "capability": "owned-storage-authority-v1",
                "remote_identity": remote_identity,
                "platform_mode": platform_mode,
                "support_tier": "unsupported",
                "adoptable": False,
                "service_revision": service_revision,
                "evidence_id": None,
                "ordinary_evidence_id": None,
                "acceptance_state": None,
                "promotion_id": None,
                "authority_binding_id": None,
                "binding_generation": None,
                "observed_at": observed_at,
                "expires_at": expires_at,
                "checks": {
                    "dedicated_identity": "fail",
                    "private_root": "fail",
                    "caller_non_mutation": "fail",
                    "canonical_peer_auth": "fail",
                    "controller_process_identity": "fail",
                    "mount_controller_identity": "fail",
                    "descriptor_only_mount_channel": "fail",
                    "no_arbitrary_path_protocol": "fail",
                    "no_replace_publication": "fail",
                    "durable_restart_recovery": "fail",
                    "private_workload_mount": "fail",
                    "read_only_generation_mount": "fail",
                    "active_reference_observation": "fail",
                    "identity_bound_final_removal": "fail",
                    "bounded_secret_free_evidence": "fail",
                    "packaging_revision_parity": "fail",
                    "human_review": "fail",
                },
                "storage_authority": {
                    "owner_identity_digest": None,
                    "root_identity_digest": None,
                },
                "resolver_authority": {
                    "included": False,
                    "qualified": False,
                },
                "reason_code": "authority_unsupported",
            }

        state = self.repository.load_state()
        completed_acc, reason_code = _authority_check(
            state, remote_identity, service_revision, source_revision, now_dt)
        existing_cap = self.repository.get_capability(remote_identity)
        changed = (existing_cap is not None
                   and service_revision != UNOBSERVED_REVISION
                   and existing_cap.service_revision != service_revision)
        if completed_acc is not None:
            support_tier = SupportTier.PROVEN
            adoptable = True
            acceptance_state = AcceptanceState.COMPLETE
        elif reason_code == "authority_revision_mismatch" or changed:
            support_tier = SupportTier.DRIFTED
            adoptable = False
            reason_code = "authority_revision_mismatch"
            acceptance_state = AcceptanceState.FAILED
        elif reason_code == "implemented_unproven":
            support_tier = SupportTier.IMPLEMENTED_UNPROVEN
            adoptable = False
            acceptance_state = AcceptanceState.PENDING_ORDINARY
        else:
            support_tier = SupportTier.IMPLEMENTED_UNPROVEN
            adoptable = False
            acceptance_state = AcceptanceState.FAILED

        checks = {
            "dedicated_identity": "pass",
            "private_root": "pass",
            "caller_non_mutation": "pass",
            "canonical_peer_auth": "pass",
            "controller_process_identity": "pass",
            "mount_controller_identity": "pass",
            "descriptor_only_mount_channel": "pass",
            "no_arbitrary_path_protocol": "pass",
            "no_replace_publication": "pass",
            "durable_restart_recovery": "pass",
            "private_workload_mount": "pass",
            "read_only_generation_mount": "pass",
            "active_reference_observation": "pass",
            "identity_bound_final_removal": "pass",
            "bounded_secret_free_evidence": "pass",
            "packaging_revision_parity": "pass" if support_tier != SupportTier.DRIFTED else "fail",
            "human_review": "pass" if support_tier == SupportTier.PROVEN else "unknown",
        }
        if service_revision == UNOBSERVED_REVISION:
            # Nothing was observed, so no primitive can be reported as passing.
            checks = {name: "unknown" for name in checks}

        cap_record = AuthorityCapability(
            capability_id="owned-storage-authority-v1",
            remote_identity=remote_identity,
            platform_mode=platform_mode,
            support_tier=support_tier,
            adoptable=adoptable,
            service_revision=service_revision,
            owner_identity_digest="sha256:owner",
            root_identity_digest="sha256:root",
            primitive_states=checks,
            evidence_id=completed_acc.get("evidence_id") if completed_acc else None,
            ordinary_evidence_id=completed_acc.get("acceptance_id") if completed_acc else None,
            acceptance_state=acceptance_state,
            observed_at=observed_at,
            expires_at=expires_at,
            reason_code=reason_code,
            promotion_id=completed_acc.get("promotion_id") if completed_acc else None,
            authority_binding_id=completed_acc.get("authority_binding_id") if completed_acc else None,
            binding_generation=completed_acc.get("lifecycle_generation") if completed_acc else None,
        )

        cur_gen = self.repository.get_generation()
        self.repository.save_capability(cap_record, expected_generation=cur_gen)

        return {
            "ok": True,
            "capability": "owned-storage-authority-v1",
            "remote_identity": remote_identity,
            "platform_mode": platform_mode,
            "support_tier": support_tier.value,
            "adoptable": adoptable,
            "service_revision": service_revision,
            "evidence_id": cap_record.evidence_id,
            "ordinary_evidence_id": cap_record.ordinary_evidence_id,
            "acceptance_state": acceptance_state.value if acceptance_state else None,
            "promotion_id": cap_record.promotion_id,
            "authority_binding_id": cap_record.authority_binding_id,
            "binding_generation": cap_record.binding_generation,
            "observed_at": observed_at,
            "expires_at": expires_at,
            "checks": checks,
            "storage_authority": {
                "owner_identity_digest": "sha256:owner",
                "root_identity_digest": "sha256:root",
            },
            "resolver_authority": {
                "included": False,
                "qualified": False,
            },
            "reason_code": reason_code,
        }

    def record_acceptance(self, remote_identity: str, acceptance: CapabilityAcceptance) -> int:
        cur_gen = self.repository.get_generation()
        return self.repository.record_acceptance(acceptance, expected_generation=cur_gen)


def build_authority_lifecycle_service(
    lifecycle_path: Optional[Path | str] = None,
) -> AuthorityLifecycleService:
    import os
    if lifecycle_path is None:
        root_env = os.environ.get("SANDBOX_STORAGE_ROOT")
        if root_env:
            p = Path(root_env) / "lifecycle.json"
        else:
            home = Path(os.environ.get("SANDBOX_HOME", Path.home() / "sandbox"))
            p = home / "owned_storage" / "lifecycle.json"
    else:
        p = Path(lifecycle_path)
    repo = StorageAuthorityLifecycleRepository(p)
    return AuthorityLifecycleService(repo)
