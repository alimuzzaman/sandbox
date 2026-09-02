"""The sole pure trust decision for a machine-approved OCI release."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import (
    ApplicationTopology,
    ImageContractError,
    ProjectImageIntent,
    ReleaseReceipt,
    _TrustedMachinePolicy,
    VerifiedImagePlan,
)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Closed success/refusal envelope with no partial-plan state."""

    schema_version: int
    ok: bool
    result_class: str
    locations: tuple[str, ...]
    plan: VerifiedImagePlan | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("verification result schema is invalid")
        if (type(self.ok) is not bool or type(self.result_class) is not str
                or type(self.locations) is not tuple
                or any(type(item) is not str for item in self.locations)):
            raise ValueError("verification result is invalid")
        if self.ok:
            if (self.result_class != "verified"
                    or type(self.plan) is not VerifiedImagePlan or self.locations):
                raise ValueError("verified result is incomplete")
        else:
            refusal = ImageContractError(self.result_class, "input")
            if (self.result_class != refusal.code or self.plan is not None
                    or not self.locations or len(self.locations) > 8
                    or any(ImageContractError(refusal.code, item).location != item
                           for item in self.locations)):
                raise ValueError("refusal result is invalid")

    def as_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "ok": self.ok,
            "result_class": self.result_class,
        }
        if self.ok:
            result["plan"] = self.plan.as_mapping() if self.plan is not None else None
        else:
            result["locations"] = list(self.locations)
        return result


def _refuse(result_class: str, *locations: str) -> VerificationResult:
    """Create a bounded refusal from allowlisted contract values only."""
    try:
        error = ImageContractError(result_class, locations[0] if locations else "input")
    except (IndexError, TypeError):
        error = ImageContractError()
    safe_locations = tuple(sorted({
        ImageContractError(error.code, location).location for location in locations[:8]
    })) or (error.location,)
    return VerificationResult(1, False, error.code, safe_locations)


def _verify_image_plan(
    trusted_policy: _TrustedMachinePolicy,
    project: ProjectImageIntent,
    receipt: ReleaseReceipt,
) -> VerificationResult:
    """Verify three separate channels without exposing an effect dependency.

    The function accepts only the three exact immutable channel values.  It has
    no callback, repository,
    config loader, credential provider, clock, random source, or process seam.
    """
    policy = trusted_policy.policy
    payload = receipt.payload
    if project.policy_selector != policy.policy_selector:
        return _refuse("policy_mismatch", "project.policy_selector")

    if receipt.claimed_payload_digest != payload.payload_digest:
        return _refuse("receipt_mismatch", "receipt.payload_digest")
    if policy.approved_receipt_payload_digest != payload.payload_digest:
        return _refuse("policy_mismatch", "policy.receipt_payload_digest")

    if payload.signature_mode != policy.signature_mode or policy.signature_mode != "not_required":
        return _refuse("signature_mode_unsupported", "receipt.signature_mode")

    if payload.repository != policy.repository:
        return _refuse("receipt_mismatch", "receipt.repository")
    if payload.manifest_digest != policy.image.manifest_digest:
        return _refuse("receipt_mismatch", "receipt.manifest_digest")
    if payload.config_digest != policy.image.config_digest:
        return _refuse("receipt_mismatch", "receipt.config_digest")
    if payload.manifest_media_type != policy.image.manifest_media_type:
        return _refuse("image_invalid", "receipt.manifest_digest")
    if payload.platform != policy.image.platform:
        return _refuse("platform_mismatch", "receipt.platform")

    provenance_mismatches = []
    if payload.source_repository != policy.source_repository:
        provenance_mismatches.append("receipt.source_repository")
    if payload.source_revision != policy.source_revision:
        provenance_mismatches.append("receipt.source_revision")
    if payload.build_identity != policy.build_identity:
        provenance_mismatches.append("receipt.build_identity")
    if payload.provenance != policy.provenance:
        provenance_mismatches.append("receipt.provenance")
    if provenance_mismatches:
        return _refuse("provenance_mismatch", *provenance_mismatches)

    selected_persistent = set(project.topology.persistent_services)
    selected_one_shot = set(project.topology.one_shot_services)
    declared = set(project.declared_services)
    if policy.primary_service not in selected_persistent:
        return _refuse("topology_mismatch", "project.persistent_services")
    if not selected_persistent <= set(policy.allowed_persistent_services):
        return _refuse("topology_mismatch", "project.persistent_services")
    if not selected_one_shot <= set(policy.allowed_one_shot_services):
        return _refuse("topology_mismatch", "project.one_shot_services")
    if not (selected_persistent | selected_one_shot) <= declared:
        return _refuse("topology_mismatch", "project.declared_services")

    plan = VerifiedImagePlan.verified(
        policy=policy, receipt=payload, topology=project.topology,
    )
    return VerificationResult(1, True, "verified", (), plan)


def verify_image_plan(
    trusted_policy: _TrustedMachinePolicy,
    project: ProjectImageIntent,
    receipt: ReleaseReceipt,
) -> VerificationResult:
    """Safe public wrapper around the exact-type, effect-free decision.

    Raw mappings are rejected.  Owning config/receipt boundaries must issue the
    three exact immutable channel types before this function is called.
    """
    try:
        if (type(trusted_policy) is not _TrustedMachinePolicy
                or type(project) is not ProjectImageIntent
                or type(receipt) is not ReleaseReceipt):
            return _refuse("input_invalid", "input")
        return _verify_image_plan(trusted_policy, project, receipt)
    except ImageContractError as exc:
        return _refuse(exc.code, exc.location)
    except Exception:
        # Never retain or expose exception text from an unexpected object.
        return _refuse("input_invalid", "input")


def reject_legacy_image_authority(_legacy_value: object) -> VerificationResult:
    """Keep Feature 047/048 state opaque and explicitly non-authorizing.

    The value is deliberately neither traversed nor copied, so using this
    compatibility adapter cannot read or mutate sibling state.
    """
    return _refuse("plan_invalid", "legacy")


__all__ = ("VerificationResult", "reject_legacy_image_authority", "verify_image_plan")
