"""Effect-free admission for Feature 050 staging authority."""

from __future__ import annotations

from dataclasses import dataclass

from .models import ImageContractError, validate_verified_image_plan
from .staging_models import StageRequest, StagingContractError, StagingPolicy


@dataclass(frozen=True, slots=True)
class StagingAdmission:
    ok: bool
    code: str
    request: StageRequest | None = None
    policy: StagingPolicy | None = None


def admit_stage_request(request: object, machine_policy: object) -> StagingAdmission:
    """Validate exact existing authorities without re-deciding Feature 049 trust."""
    try:
        if type(request) is not StageRequest:
            return StagingAdmission(False, "plan_invalid")
        policy = machine_policy if type(machine_policy) is StagingPolicy \
            else StagingPolicy.from_mapping(machine_policy)
        plan = validate_verified_image_plan(request.plan)
        projection = plan.delivery_identity_projection
        if request.staging_policy_digest != policy.policy_digest:
            return StagingAdmission(False, "policy_mismatch")
        if plan.plan_digest != policy.plan_digest:
            return StagingAdmission(False, "policy_mismatch")
        if request.target != policy.target:
            return StagingAdmission(False, "target_mismatch")
        if projection.as_mapping() != policy.projection.as_mapping():
            return StagingAdmission(False, "policy_mismatch")
        if projection.image.registry != "ghcr.io":
            return StagingAdmission(False, "policy_mismatch")
        return StagingAdmission(True, "admitted", request, policy)
    except (ImageContractError, StagingContractError, TypeError, ValueError):
        return StagingAdmission(False, "plan_invalid")
