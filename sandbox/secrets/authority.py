"""Bind provider credentials to the complete normalized automation authority."""

from __future__ import annotations

from collections.abc import Mapping
import copy
import hashlib
import json
from pathlib import Path

from .adapter_manifest import source_kind


_SOURCE_FIELDS = (
    "type", "endpoint", "projectId", "environment", "path", "key",
    "clientId", "mcpModes", "timeoutSeconds",
)
_PROFILE_FIELDS = (
    "source", "key", "argv", "destination", "timeoutSeconds",
    "maxOutputBytes", "mcp",
)


def _fingerprint(alias: str, source: Mapping, profiles: Mapping, project_root: object) -> str:
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
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")
    return "a1-" + hashlib.sha256(encoded).hexdigest()


def bind_provider_authorities(sources: Mapping, profiles: Mapping, *, project_root: object) -> dict:
    """Copy sources and attach an internal digest to every provider source.

    The digest covers the canonical project root, every normalized source field,
    and every fixed profile that can consume the item. It is not accepted from user configuration.
    OS-credential lookup includes it, so a config change cannot silently reuse
    previously approved bootstrap material.
    """
    result = copy.deepcopy(dict(sources or {}))
    for alias, descriptor in result.items():
        if source_kind(descriptor) == "infisical" and isinstance(descriptor, dict):
            descriptor["authorityDigest"] = _fingerprint(
                alias, descriptor, profiles, project_root,
            )
    return result


__all__ = ["bind_provider_authorities"]
