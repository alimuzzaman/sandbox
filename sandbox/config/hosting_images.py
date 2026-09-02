"""Pure project-intent and machine-policy providers for OCI image trust.

Callers own all file loading and layer selection.  These normalizers accept
already separated mappings and return immutable value objects.  In particular,
the project channel can select and narrow a policy but cannot define its
primary service or any other machine authority.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from sandbox.hosting.images.models import (
    ImageContractError,
    ProjectImageIntent,
    ReleaseReceipt,
    _TrustedMachinePolicy,
    _issue_trusted_machine_policy,
    canonical_json,
)


MAX_POLICIES = 32


class HostingImageConfigError(ValueError):
    """Stable config-boundary wrapper around a value-contract refusal."""

    def __init__(self, code: str, location: str) -> None:
        self.code = code
        self.location = location
        super().__init__(code)


def _translate(exc: ImageContractError) -> HostingImageConfigError:
    return HostingImageConfigError(exc.code, exc.location)


def normalize_project_image_intent(value: object) -> ProjectImageIntent | None:
    """Normalize one optional untrusted project channel without adding authority."""
    if value is None:
        return None
    try:
        canonical_json(value)
        return ProjectImageIntent.from_mapping(value)
    except ImageContractError as exc:
        raise _translate(exc) from None


def normalize_machine_image_policies(value: object) -> MappingProxyType:
    """Normalize the machine-owned named policy collection.

    A policy's selector must equal its collection key.  This prevents a lookup
    alias from becoming an unnoticed second authority identity.
    """
    if value is None:
        return MappingProxyType({})
    if type(value) is not dict:
        raise HostingImageConfigError("input_invalid", "policy")
    if len(value) > MAX_POLICIES:
        raise HostingImageConfigError("input_too_large", "policy")
    result: dict[str, _TrustedMachinePolicy] = {}
    try:
        canonical_json(value)
        for selector, raw in sorted(value.items()):
            policy = _issue_trusted_machine_policy(raw)
            if selector != policy.policy.policy_selector:
                raise ImageContractError("policy_mismatch", "policy")
            result[selector] = policy
    except ImageContractError as exc:
        raise _translate(exc) from None
    return MappingProxyType(result)


def normalize_release_receipt(value: object) -> ReleaseReceipt:
    """Issue the exact immutable untrusted receipt channel at its boundary."""
    try:
        canonical_json(value)
        return ReleaseReceipt.from_mapping(value)
    except ImageContractError as exc:
        raise _translate(exc) from None


def project_image_intent_provider(result: dict[str, Any]) -> ProjectImageIntent | None:
    """Normalize only the primary project file's preserved raw declaration."""
    if type(result) is not dict:
        raise HostingImageConfigError("input_invalid", "project")
    raw = result.get("_hosting_images_raw")
    if type(raw) is not dict or set(raw) != {"declared", "project_primary"}:
        raise HostingImageConfigError("input_invalid", "project")
    if type(raw["declared"]) is not bool:
        raise HostingImageConfigError("input_invalid", "project")
    if not raw["declared"]:
        return None
    if raw["project_primary"] is None:
        raise HostingImageConfigError("input_invalid", "project")
    return normalize_project_image_intent(raw["project_primary"])


def machine_image_policy_provider(result: dict[str, Any]) -> MappingProxyType:
    """Machine-config provider for ``hosting.images.policies``.

    Unknown sibling hosting configuration is left to its owning provider.  The
    Feature 049 block itself is closed.
    """
    hosting = result.get("hosting")
    if hosting is None:
        return MappingProxyType({})
    if type(hosting) is not dict:
        raise HostingImageConfigError("input_invalid", "policy")
    if "images" not in hosting:
        return MappingProxyType({})
    images = hosting["images"]
    if type(images) is not dict or set(images) != {"policies"}:
        raise HostingImageConfigError("input_invalid", "policy")
    return normalize_machine_image_policies(images["policies"])


def resolve_machine_image_policy(
    intent: ProjectImageIntent,
    policies: MappingProxyType,
) -> _TrustedMachinePolicy:
    """Resolve an untrusted selector against trusted machine topology."""
    if type(intent) is not ProjectImageIntent or type(policies) is not MappingProxyType:
        raise HostingImageConfigError("input_invalid", "policy")
    policy = policies.get(intent.policy_selector)
    if type(policy) is not _TrustedMachinePolicy:
        raise HostingImageConfigError("policy_mismatch", "project.policy_selector")
    return policy


__all__ = (
    "HostingImageConfigError", "machine_image_policy_provider",
    "normalize_machine_image_policies", "normalize_project_image_intent",
    "normalize_release_receipt", "project_image_intent_provider",
    "resolve_machine_image_policy",
)
