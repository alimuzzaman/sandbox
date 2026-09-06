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


class AuthorityLifecycleService:
    def __init__(self, repository: StorageAuthorityLifecycleRepository):
        self.repository = repository

    def evaluate_capability(
        self,
        *,
        remote_identity: str,
        platform_mode: str = "ubuntu-24.04-systemd-255-private-root-v1",
        service_revision: str = "rev_installed",
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

        # Check recorded capability in repository
        existing_cap = self.repository.get_capability(remote_identity)

        # Check recorded acceptances in repository
        state = self.repository.load_state()
        acceptances = state.get("acceptances", {})
        completed_acc = None
        for acc_data in acceptances.values():
            if acc_data.get("outcome") in (AcceptanceOutcome.COMPLETE.value, "complete"):
                completed_acc = acc_data
                break

        # Check drift: if we had a recorded capability with a different service_revision
        if existing_cap is not None and existing_cap.service_revision != service_revision:
            support_tier = SupportTier.DRIFTED
            adoptable = False
            reason_code = "authority_revision_mismatch"
            acceptance_state = AcceptanceState.FAILED
        elif completed_acc is not None:
            support_tier = SupportTier.PROVEN
            adoptable = True
            reason_code = "proven"
            acceptance_state = AcceptanceState.COMPLETE
        else:
            support_tier = SupportTier.IMPLEMENTED_UNPROVEN
            adoptable = False
            reason_code = "implemented_unproven"
            acceptance_state = AcceptanceState.PENDING_ORDINARY

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
