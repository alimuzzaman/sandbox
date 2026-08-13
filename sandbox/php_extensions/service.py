"""Service boundary for validating and fingerprinting PHP extension intent.

The service composes the immutable catalog and standalone probe results. It does
not build images, install packages, edit INI files, or execute commands itself.
Those mutations remain owned by runtime adapters and their existing approval
contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from .catalog import (
    DEFAULT_CATALOG,
    CatalogIssue,
    CatalogValidation,
    PhpExtensionCatalog,
    PhpExtensionCatalogError,
    normalize_requirements,
)
from .probe import PlaneComparison, ProbeError, ProbeResult, compare_planes


_SAFE_DIMENSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/+@-]{0,255}$")
_DIGEST = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")
_PROVENANCE_KEYS = frozenset({"name", "version", "source", "digest", "scope", "action"})


class PhpExtensionServiceError(ValueError):
    """Invalid extension intent or unsafe provenance input."""


@dataclass(frozen=True)
class ExtensionProvenance:
    """Secret-free evidence tying a generated extension plan to its inputs."""

    catalog_digest: str
    requirement_digest: str
    parent_image_digest: str | None
    php_version: str
    server: str
    platform: str
    architecture: str
    package_provenance: tuple[Mapping[str, str], ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("catalog_digest", "requirement_digest"):
            _validate_digest(getattr(self, field_name), field_name)
        if self.parent_image_digest is not None:
            _validate_digest(self.parent_image_digest, "parent image digest")
        for field_name in ("php_version", "server", "platform", "architecture"):
            _validate_dimension(getattr(self, field_name), field_name)
        rows: list[dict[str, str]] = []
        for row in self.package_provenance:
            if not isinstance(row, Mapping) or set(row) - _PROVENANCE_KEYS:
                raise PhpExtensionServiceError("package provenance contains unsupported fields")
            normalized: dict[str, str] = {}
            for key, value in row.items():
                if not isinstance(value, str) or not value or not _SAFE_DIMENSION.fullmatch(value):
                    raise PhpExtensionServiceError("package provenance contains unsafe metadata")
                if "://" in value or value.lower().startswith(("sh:", "bash:")):
                    raise PhpExtensionServiceError("package provenance cannot contain URLs or commands")
                if key == "digest":
                    _validate_digest(value, "package provenance digest")
                normalized[str(key)] = value
            rows.append(normalized)
        object.__setattr__(self, "package_provenance", tuple(rows))

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_digest": self.catalog_digest,
            "requirement_digest": self.requirement_digest,
            "parent_image_digest": self.parent_image_digest,
            "php_version": self.php_version,
            "server": self.server,
            "platform": self.platform,
            "architecture": self.architecture,
            "package_provenance": [dict(row) for row in self.package_provenance],
        }


@dataclass(frozen=True)
class ExtensionResolution:
    ok: bool
    requirements: tuple[dict[str, Any], ...]
    digest: str
    catalog: str
    profile: str | None = None
    issues: tuple[CatalogIssue, ...] = ()
    provenance: ExtensionProvenance | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "requirements": [dict(item) for item in self.requirements],
            "digest": self.digest,
            "catalog": self.catalog,
            "profile": self.profile,
            "issues": [issue.to_dict() for issue in self.issues],
            "provenance": self.provenance.to_dict() if self.provenance else None,
        }


@dataclass(frozen=True)
class ExtensionVerification:
    ok: bool
    probes: Mapping[str, ProbeResult]
    comparison: PlaneComparison
    errors: tuple[ProbeError, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "probes": {plane: result.to_dict() for plane, result in self.probes.items()},
            "comparison": self.comparison.to_dict(),
            "errors": [error.to_dict() for error in self.errors],
        }


def _validate_dimension(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or not _SAFE_DIMENSION.fullmatch(value):
        raise PhpExtensionServiceError(f"{label} is invalid")
    lowered = value.lower()
    if any(term in lowered for term in ("password", "secret", "token", "authorization", "cookie")):
        raise PhpExtensionServiceError(f"{label} contains sensitive metadata")
    return value


def _validate_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise PhpExtensionServiceError(f"{label} is invalid")
    return value.lower()


def _config_parts(requirements: object, profile: str | None) -> tuple[object, str | None]:
    if hasattr(requirements, "requirements"):
        model_requirements = getattr(requirements, "requirements")
        model_profile = getattr(requirements, "profile", None)
        return model_requirements, profile if profile is not None else model_profile
    if isinstance(requirements, Mapping):
        model_profile = requirements.get("profile", profile)
        if "requirements" in requirements:
            return requirements["requirements"], model_profile
        if "extensions" in requirements:
            return requirements, model_profile
    return requirements, profile


def normalize_extension_requirements(requirements: object) -> tuple[dict[str, Any], ...]:
    values, _ = _config_parts(requirements, None)
    # The config model has a requirements tuple; direct mapping remains supported
    # by the catalog for small callers and tests.
    return normalize_requirements(values)


def canonical_requirement_payload(requirements: object) -> tuple[dict[str, Any], ...]:
    return normalize_extension_requirements(requirements)


def extension_digest(
    requirements: object,
    *,
    catalog: PhpExtensionCatalog = DEFAULT_CATALOG,
    parent_image_digest: str | None = None,
    php_version: str = "",
    server: str = "",
    platform: str = "",
    architecture: str = "",
) -> str:
    """Compute a stable SHA-256 over all extension image inputs.

    Every dimension is explicit so changing the parent image, PHP version,
    server flavour, platform, or architecture cannot reuse an old build.
    """
    normalized = canonical_requirement_payload(requirements)
    if parent_image_digest is not None:
        parent_image_digest = _validate_digest(parent_image_digest, "parent image digest")
    dimensions = {
        "requirements": normalized,
        "catalog_digest": catalog.digest,
        "parent_image_digest": parent_image_digest,
        "php_version": _validate_dimension(php_version, "PHP version") if php_version else "",
        "server": _validate_dimension(server, "server") if server else "",
        "platform": _validate_dimension(platform, "platform") if platform else "",
        "architecture": _validate_dimension(architecture, "architecture") if architecture else "",
    }
    encoded = json.dumps(dimensions, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


# Common names used by runtime builders and tests.
php_extension_digest = extension_digest
build_extension_digest = extension_digest
deterministic_extension_digest = extension_digest
extension_fingerprint = extension_digest


def build_provenance(
    requirements: object,
    *,
    catalog: PhpExtensionCatalog = DEFAULT_CATALOG,
    parent_image_digest: str | None = None,
    php_version: str,
    server: str,
    platform: str,
    architecture: str,
    package_provenance: tuple[Mapping[str, str], ...] = (),
) -> ExtensionProvenance:
    normalized = canonical_requirement_payload(requirements)
    requirement_payload = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    requirement_digest = "sha256:" + sha256(requirement_payload).hexdigest()
    digest = extension_digest(
        normalized, catalog=catalog, parent_image_digest=parent_image_digest,
        php_version=php_version, server=server, platform=platform, architecture=architecture,
    )
    # Keep the digest in the requirement side of the evidence without copying
    # any raw package output or credentials into the provenance document.
    return ExtensionProvenance(
        catalog_digest=catalog.digest,
        requirement_digest=requirement_digest,
        parent_image_digest=parent_image_digest,
        php_version=php_version,
        server=server,
        platform=platform,
        architecture=architecture,
        package_provenance=package_provenance,
    )


class PhpExtensionService:
    """Validate extension requirements and verify all execution planes."""

    def __init__(self, *, catalog: PhpExtensionCatalog = DEFAULT_CATALOG) -> None:
        self.catalog = catalog

    def resolve(
        self,
        requirements: object,
        *,
        profile: str | None = None,
        require_provisioning: bool = False,
        parent_image_digest: str | None = None,
        php_version: str = "",
        server: str = "",
        platform: str = "",
        architecture: str = "",
        package_provenance: tuple[Mapping[str, str], ...] = (),
    ) -> ExtensionResolution:
        raw_requirements, selected_profile = _config_parts(requirements, profile)
        validation = self.catalog.validate(raw_requirements, profile=selected_profile,
                                           require_provisioning=require_provisioning)
        normalized = validation.requirements
        digest = extension_digest(
            normalized, catalog=self.catalog, parent_image_digest=parent_image_digest,
            php_version=php_version, server=server, platform=platform, architecture=architecture,
        )
        provenance = None
        if all((php_version, server, platform, architecture)):
            provenance = build_provenance(
                normalized, catalog=self.catalog, parent_image_digest=parent_image_digest,
                php_version=php_version, server=server, platform=platform,
                architecture=architecture, package_provenance=package_provenance,
            )
        return ExtensionResolution(
            validation.ok, normalized, digest, self.catalog.digest, selected_profile,
            validation.issues, provenance,
        )

    def validate(self, requirements: object, *, profile: str | None = None,
                 require_provisioning: bool = False) -> ExtensionResolution:
        return self.resolve(requirements, profile=profile,
                            require_provisioning=require_provisioning)

    def verify(
        self,
        requirements: object,
        probes: Mapping[str, ProbeResult],
        *,
        profile: str | None = None,
        require_provisioning: bool = False,
    ) -> ExtensionVerification:
        raw_requirements, selected_profile = _config_parts(requirements, profile)
        resolution = self.resolve(raw_requirements, profile=selected_profile,
                                  require_provisioning=require_provisioning)
        comparison = compare_planes(probes, resolution.requirements, catalog=self.catalog,
                                    profile=selected_profile)
        # ``compare_planes`` carries each probe's structured errors into the
        # comparison envelope; preserve one occurrence per root cause.
        errors: list[ProbeError] = list(comparison.errors)
        errors.extend(ProbeError(issue.code, issue.message, extension=issue.extension)
                      for issue in resolution.issues)
        return ExtensionVerification(not errors and resolution.ok, probes, comparison, tuple(errors))

    def fingerprint(self, requirements: object, **kwargs: Any) -> str:
        return extension_digest(requirements, catalog=self.catalog, **kwargs)

    def provenance(self, requirements: object, **kwargs: Any) -> ExtensionProvenance:
        return build_provenance(requirements, catalog=self.catalog, **kwargs)


__all__ = [
    "ExtensionProvenance", "ExtensionResolution", "ExtensionVerification",
    "PhpExtensionService", "PhpExtensionServiceError", "build_extension_digest",
    "build_provenance", "canonical_requirement_payload", "deterministic_extension_digest",
    "extension_digest", "extension_fingerprint", "normalize_extension_requirements",
    "php_extension_digest",
]
