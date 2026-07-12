from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .errors import RecoveryError
from .models import RecoveryProfile

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_SOURCE_TYPES = {"control-plane", "database", "filesystem", "git"}
_CAPTURE_MODES = {"declarative", "logical", "full", "partial", "provenance"}
_FIELDS = {
    "id", "scope", "source_type", "allowed_roots", "sources", "capture_mode",
    "consistency", "excludes", "sensitivity", "restore_target", "verification",
    "retention_class", "dependencies", "metadata",
}


@dataclass(frozen=True)
class RecoveryCatalog:
    schema_version: int
    profiles: tuple[RecoveryProfile, ...]

    def by_id(self) -> dict[str, RecoveryProfile]:
        return {profile.profile_id: profile for profile in self.profiles}


def _strings(value, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise RecoveryError(f"{field} must be a list of non-empty strings", "invalid_catalog")
    return tuple(value)


def load_catalog(path: str | Path) -> RecoveryCatalog:
    try:
        document = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"could not load recovery catalog: {exc}", "invalid_catalog") from exc
    if document.get("schema_version") != 1 or not isinstance(document.get("profiles"), list):
        raise RecoveryError("recovery catalog schema_version 1 and profiles are required", "invalid_catalog")
    profiles = []
    seen = set()
    for raw in document["profiles"]:
        if not isinstance(raw, dict) or set(raw) - _FIELDS:
            raise RecoveryError("profile contains unknown fields", "invalid_catalog")
        profile_id = raw.get("id")
        if not isinstance(profile_id, str) or not _ID.fullmatch(profile_id) or profile_id in seen:
            raise RecoveryError("profile id is invalid or duplicated", "invalid_catalog")
        if raw.get("source_type") not in _SOURCE_TYPES or raw.get("capture_mode") not in _CAPTURE_MODES:
            raise RecoveryError(f"profile {profile_id} has an unknown adapter or mode", "invalid_catalog")
        for value in raw.values():
            if isinstance(value, str) and ("\n" in value or value.startswith(("sh:", "bash:"))):
                raise RecoveryError(f"profile {profile_id} contains command text", "invalid_catalog")
        profile = RecoveryProfile(
            profile_id, str(raw.get("scope") or ""), raw["source_type"],
            _strings(raw.get("allowed_roots", []), "allowed_roots"),
            _strings(raw.get("sources", []), "sources"), raw["capture_mode"],
            str(raw.get("consistency") or ""), _strings(raw.get("excludes", []), "excludes"),
            str(raw.get("sensitivity") or "encrypted"), str(raw.get("restore_target") or ""),
            str(raw.get("verification") or ""), str(raw.get("retention_class") or "standard"),
            _strings(raw.get("dependencies", []), "dependencies"), raw.get("metadata") or {},
        )
        seen.add(profile_id)
        profiles.append(profile)
    ids = {profile.profile_id for profile in profiles}
    for profile in profiles:
        missing = set(profile.dependencies) - ids
        if missing:
            raise RecoveryError(f"profile {profile.profile_id} has unknown dependencies", "invalid_catalog")
    visiting, visited = set(), set()
    by_id = {profile.profile_id: profile for profile in profiles}
    def walk(profile_id):
        if profile_id in visiting:
            raise RecoveryError("recovery profile dependency cycle", "invalid_catalog")
        if profile_id in visited:
            return
        visiting.add(profile_id)
        for dependency in by_id[profile_id].dependencies:
            walk(dependency)
        visiting.remove(profile_id); visited.add(profile_id)
    for profile_id in sorted(ids):
        walk(profile_id)
    return RecoveryCatalog(1, tuple(profiles))
