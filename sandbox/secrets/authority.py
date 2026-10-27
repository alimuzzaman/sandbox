"""Bind provider credentials to the complete normalized automation authority."""

from __future__ import annotations

from collections.abc import Mapping
import copy
import hashlib
import hmac
import json
from pathlib import Path

from .adapter_manifest import source_kind
from .models import SecretBrokerError


_SOURCE_FIELDS = (
    "type", "endpoint", "projectId", "environment", "path", "key",
    "clientId", "mcpModes", "timeoutSeconds",
)
_PROFILE_FIELDS = (
    "source", "key", "argv", "destination", "timeoutSeconds",
    "maxOutputBytes", "mcp",
)


def _fingerprint(alias: str, source: Mapping, profiles: Mapping, project_root: object,
                 ownership_consumers=()) -> str:
    selected_profiles = []
    for name in sorted(profiles):
        profile = profiles[name]
        if isinstance(profile, Mapping) and profile.get("source") == alias:
            selected_profiles.append({
                "name": name,
                **{field: copy.deepcopy(profile.get(field)) for field in _PROFILE_FIELDS},
            })
    payload = {
        "version": 1,
        "source": alias,
        "project_root": str(Path(project_root).expanduser().resolve()),
        "descriptor": {field: copy.deepcopy(source.get(field)) for field in _SOURCE_FIELDS},
        "profiles": selected_profiles,
    }
    if ownership_consumers:
        # Only present when an ownership block consumes this source, so every
        # pre-existing a1- digest stays byte-identical.
        payload["ownershipConsumers"] = sorted(
            (copy.deepcopy(dict(item)) for item in ownership_consumers),
            key=_canonical,
        )
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")
    return "a1-" + hashlib.sha256(encoded).hexdigest()


def bind_provider_authorities(sources: Mapping, profiles: Mapping, *, project_root: object,
                              ownership: Mapping | None = None) -> dict:
    """Copy sources and attach an internal digest to every provider source.

    The digest covers the canonical project root, every normalized source field,
    and every fixed profile that can consume the item. It is not accepted from user configuration.
    OS-credential lookup includes it, so a config change cannot silently reuse
    previously approved bootstrap material. When ``ownership`` names a
    source-origin consumer of a provider source, the digest also binds that
    consumer and the complete ownership map hash; without one it is unchanged.
    """
    result = copy.deepcopy(dict(sources or {}))
    consumers = _ownership_consumers(ownership)
    for alias, descriptor in result.items():
        if source_kind(descriptor) == "infisical" and isinstance(descriptor, dict):
            descriptor["authorityDigest"] = _fingerprint(
                alias, descriptor, profiles, project_root,
                consumers.get(alias, ()),
            )
    return result


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")


def _canonical_root(project_root: object) -> str:
    return str(Path(project_root).expanduser().resolve())


def _ownership_config(ownership: Mapping):
    # Lazy: projects without an ownership block never import the package.
    from .ownership.models import OwnershipConfig
    return OwnershipConfig.from_normalized(ownership)


def _ownership_consumers(ownership: Mapping | None) -> dict[str, tuple[dict, ...]]:
    """Source-origin ownership consumers per source alias (metadata only)."""
    if ownership is None:
        return {}
    config = _ownership_config(ownership)
    grouped: dict[str, list[dict]] = {}
    for entry in config.entries:
        if entry.origin != "source":
            continue
        grouped.setdefault(entry.source, []).append({
            "profile": entry.profile,
            "key": entry.key,
            "consumer": entry.consumer,
            "ownershipSha256": config.map_sha256,
        })
    return {alias: tuple(items) for alias, items in grouped.items()}


def _authority_mismatch() -> SecretBrokerError:
    return SecretBrokerError(
        "authority_mismatch", "secret ownership grant does not match its authority",
    )


def _client_for(profile, capability: str) -> str:
    if capability == "read":
        client_id = profile.destination.read_client_id
    elif capability == "create":
        client_id = profile.destination.create_client_id
    else:
        raise _authority_mismatch()
    if client_id is None:
        raise SecretBrokerError(
            "credential_scope_invalid",
            "secret ownership profile has no client for this capability",
        )
    return client_id


def ownership_digest(profile_name: str, ownership: Mapping, capability: str, *,
                     project_root: object) -> str:
    """Return the ``o1-`` authority digest for one profile and capability.

    The payload is non-secret metadata only: canonical project root, the
    profile's domain, full destination (gateway included), the capability's
    client id, every entry of the profile, all its mapped destination names
    and the hash of the complete canonical ownership block. No source value
    contributes.
    """
    config = _ownership_config(ownership)
    profile = config.profiles.get(profile_name)
    if profile is None:
        raise SecretBrokerError("profile_unknown", "secret ownership profile is not configured")
    entries = [entry for entry in config.entries if entry.profile == profile_name]
    payload = {
        "version": 1,
        "kind": "ownership",
        "capability": capability,
        "project_root": _canonical_root(project_root),
        "profile": profile_name,
        "domain": profile.domain,
        "destination": profile.destination.as_normalized(),
        "clientId": _client_for(profile, capability),
        "entries": [entry.as_normalized() for entry in entries],
        "names": sorted({entry.name for entry in entries}),
        "ownershipSha256": config.map_sha256,
    }
    return "o1-" + hashlib.sha256(_canonical(payload)).hexdigest()


def validate_ownership_grant(grant) -> None:
    """Recompute a grant's authority before any credential lookup or HTTP.

    A caller-supplied digest is never trusted by itself: the complete
    canonical ownership block and project root carried by the grant are
    re-validated, the digest recomputed, and the profile, capability, client,
    destination (gateway included) and name subset checked against it.
    """
    from .ownership.models import Destination, Grant
    if not isinstance(grant, Grant) or not isinstance(grant.destination, Destination):
        raise _authority_mismatch()
    if grant.capability not in ("read", "create"):
        raise _authority_mismatch()
    if (not isinstance(grant.project_root, str) or not grant.project_root
            or not Path(grant.project_root).is_absolute()
            or _canonical_root(grant.project_root) != grant.project_root):
        raise _authority_mismatch()
    if not isinstance(grant.ownership, Mapping):
        raise _authority_mismatch()
    config = _ownership_config(grant.ownership)
    profile = config.profiles.get(grant.profile)
    if profile is None:
        raise _authority_mismatch()
    if grant.destination != profile.destination:
        raise _authority_mismatch()
    if grant.client_id != _client_for(profile, grant.capability):
        raise _authority_mismatch()
    allowed = {entry.name for entry in config.entries if entry.profile == grant.profile}
    names = grant.names
    if (not isinstance(names, tuple) or not names
            or any(not isinstance(name, str) for name in names)
            or len(set(names)) != len(names) or not set(names) <= allowed):
        raise _authority_mismatch()
    expected = ownership_digest(
        grant.profile, grant.ownership, grant.capability,
        project_root=grant.project_root,
    )
    if (not isinstance(grant.authority_digest, str)
            or not grant.authority_digest.isascii()
            or not hmac.compare_digest(expected, grant.authority_digest)):
        raise _authority_mismatch()


__all__ = ["bind_provider_authorities", "ownership_digest", "validate_ownership_grant"]
