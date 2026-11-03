"""Storage authority lifecycle service for capability evaluation and review."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from sandbox.owned_storage_lifecycle.models import (
    AcceptanceOutcome,
    AcceptanceState,
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


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _revocation_applies(item, remote_identity, promotion_id, binding) -> bool:
    """A revocation that cannot be read counts: it must not grant anything."""
    if not isinstance(item, dict) or not _text(item.get("remote_identity")):
        return True
    if item["remote_identity"] != remote_identity:
        return False
    selectors = [item.get("promotion_id"), item.get("authority_binding_id")]
    if not any(_text(value) for value in selectors):
        return True
    return selectors[0] == promotion_id or selectors[1] == binding


def _authority_check(state, remote_identity, service_revision, source_revision, now):
    """Return (acceptance, reason) for this remote and these observed revisions.

    Every link must hold: complete acceptance -> its promotion -> this remote, a
    supported adoptable promotion, the same binding, unexpired, unrevoked, and both
    observed revisions. Missing or malformed data never proves anything.
    """
    observed = _text(service_revision) and _text(source_revision)
    promotions = state.get("promotions")
    acceptances = state.get("acceptances")
    revocations = state.get("revocations", {})
    if not isinstance(promotions, dict) or not isinstance(acceptances, dict):
        return None, "implemented_unproven"
    revocations = list(revocations.values()) if isinstance(revocations, dict) else [None]
    reasons = set()
    for acceptance in acceptances.values():
        if (not isinstance(acceptance, dict)
                or acceptance.get("outcome") != AcceptanceOutcome.COMPLETE.value):
            continue
        promotion_id = acceptance.get("promotion_id")
        if not _text(promotion_id):
            continue
        promotion = promotions.get(promotion_id)
        if not isinstance(promotion, dict) or promotion.get("remote_identity") != remote_identity:
            continue
        binding = promotion.get("authority_binding_id")
        if any(_revocation_applies(item, remote_identity, promotion_id, binding)
               for item in revocations):
            reasons.add("authority_revoked")
            continue
        if (promotion.get("promotion_id") != promotion_id
                or not _text(binding) or binding != acceptance.get("authority_binding_id")
                or promotion.get("phase") != PromotionPhase.SUPPORTED.value
                or promotion.get("adoptable") is not True):
            reasons.add("authority_binding_mismatch")
            continue
        if not _unexpired(promotion.get("expires_at"), now):
            reasons.add("authority_expired")
            continue
        if not observed:
            continue
        if (not _text(promotion.get("service_revision"))
                or not _text(promotion.get("source_revision"))
                or promotion.get("service_revision") != service_revision
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
        service_revision: Optional[str] = None,
        source_revision: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read-only: one snapshot decides, and nothing is written."""
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

        try:
            state = self.repository.load_state()
        except (LifecycleError, OSError):
            # Unreadable state proves nothing and is left in place for an operator.
            completed_acc, reason_code = None, "authority_unavailable"
        else:
            # Taken after the read: the lock wait must not outlast a promotion's expiry.
            completed_acc, reason_code = _authority_check(
                state, remote_identity, service_revision, source_revision,
                datetime.datetime.now(datetime.timezone.utc))
        if completed_acc is not None:
            support_tier = SupportTier.PROVEN
            adoptable = True
            acceptance_state = AcceptanceState.COMPLETE
        elif reason_code == "authority_revision_mismatch":
            support_tier = SupportTier.DRIFTED
            adoptable = False
            acceptance_state = AcceptanceState.FAILED
        elif reason_code == "authority_unavailable":
            support_tier = SupportTier.UNAVAILABLE
            adoptable = False
            acceptance_state = AcceptanceState.FAILED
        elif reason_code == "implemented_unproven":
            support_tier = SupportTier.IMPLEMENTED_UNPROVEN
            adoptable = False
            acceptance_state = AcceptanceState.PENDING_ORDINARY
        else:
            support_tier = SupportTier.IMPLEMENTED_UNPROVEN
            adoptable = False
            acceptance_state = AcceptanceState.FAILED

        # Nothing here observes these primitives, so only a proven chain may vouch for them.
        check_state = "pass" if support_tier == SupportTier.PROVEN else "unknown"
        checks = {name: check_state for name in (
            "dedicated_identity", "private_root", "caller_non_mutation", "canonical_peer_auth",
            "controller_process_identity", "mount_controller_identity",
            "descriptor_only_mount_channel", "no_arbitrary_path_protocol",
            "no_replace_publication", "durable_restart_recovery", "private_workload_mount",
            "read_only_generation_mount", "active_reference_observation",
            "identity_bound_final_removal", "bounded_secret_free_evidence",
            "packaging_revision_parity",
        )}
        if support_tier == SupportTier.DRIFTED:
            checks["packaging_revision_parity"] = "fail"
        # No review decision is inspected here.
        checks["human_review"] = "unknown"

        accepted = completed_acc or {}
        report = {
            "ok": reason_code != "authority_unavailable",
            "capability": "owned-storage-authority-v1",
            "remote_identity": remote_identity,
            "platform_mode": platform_mode,
            "support_tier": support_tier.value,
            "adoptable": adoptable,
            "service_revision": service_revision,
            "evidence_id": accepted.get("evidence_id"),
            "ordinary_evidence_id": accepted.get("acceptance_id"),
            "acceptance_state": acceptance_state.value if acceptance_state else None,
            "promotion_id": accepted.get("promotion_id"),
            "authority_binding_id": accepted.get("authority_binding_id"),
            "binding_generation": accepted.get("lifecycle_generation"),
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
        if not report["ok"]:
            # The check itself could not run: a caller must not read this as an answer.
            report["code"] = reason_code
            report["message"] = "storage authority state is unreadable"
        return report

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
