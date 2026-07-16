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
    "retention_class", "dependencies", "metadata", "version", "enabled", "schedule_class",
}
_SECRET_FIELD = re.compile(r"(?i)(?:token|password|passphrase|secret|credential|cookie|authorization)")


@dataclass(frozen=True)
class RecoveryCatalog:
    schema_version: int
    profiles: tuple[RecoveryProfile, ...]

    def by_id(self) -> dict[str, RecoveryProfile]:
        return {profile.profile_id: profile for profile in self.profiles}


def _safe_text(value: object) -> bool:
    return (isinstance(value, str) and bool(value) and
            not any(ord(char) < 32 or ord(char) == 127 for char in value) and
            not value.startswith(("sh:", "bash:")))


def _strings(value, field: str) -> tuple[str, ...]:
    if (not isinstance(value, list) or not all(_safe_text(item) for item in value) or
            any(Path(item).is_absolute() or ".." in Path(item).parts for item in value)):
        raise RecoveryError(f"{field} must be a list of non-empty strings", "invalid_catalog")
    return tuple(value)


def _safe_value(value) -> bool:
    if isinstance(value, str):
        return not any(ord(char) < 32 or ord(char) == 127 for char in value) and not value.startswith(("sh:", "bash:"))
    if isinstance(value, dict):
        return all(isinstance(key, str) and _safe_text(key) and not _SECRET_FIELD.search(key)
                   and _safe_value(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return all(_safe_value(item) for item in value)
    return value is None or isinstance(value, (bool, int, float))


def load_catalog(path: str | Path) -> RecoveryCatalog:
    try:
        document = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"could not load recovery catalog: {exc}", "invalid_catalog") from exc
    if (not isinstance(document, dict) or document.get("schema_version") != 1 or
            isinstance(document.get("schema_version"), bool) or
            not isinstance(document.get("profiles"), list)):
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
        for field in ("scope", "consistency", "sensitivity", "restore_target", "verification",
                      "retention_class", "schedule_class"):
            if field in raw and not _safe_text(raw[field]):
                raise RecoveryError(f"profile {profile_id} field {field} must be a string", "invalid_catalog")
        if "version" in raw and (isinstance(raw["version"], bool) or
                                  not isinstance(raw["version"], int) or raw["version"] < 1):
            raise RecoveryError(f"profile {profile_id} version is invalid", "invalid_catalog")
        if "enabled" in raw and not isinstance(raw["enabled"], bool):
            raise RecoveryError(f"profile {profile_id} enabled must be boolean", "invalid_catalog")
        for value in raw.values():
            if not _safe_value(value):
                raise RecoveryError(f"profile {profile_id} contains command text", "invalid_catalog")
        if not isinstance(raw.get("metadata") or {}, dict):
            raise RecoveryError(f"profile {profile_id} metadata must be an object", "invalid_catalog")
        dependencies = _strings(raw.get("dependencies", []), "dependencies")
        if not all(_ID.fullmatch(dependency) for dependency in dependencies):
            raise RecoveryError(f"profile {profile_id} dependencies are invalid", "invalid_catalog")
        profile = RecoveryProfile(
            profile_id, str(raw.get("scope") or ""), raw["source_type"],
            _strings(raw.get("allowed_roots", []), "allowed_roots"),
            _strings(raw.get("sources", []), "sources"), raw["capture_mode"],
            str(raw.get("consistency") or ""), _strings(raw.get("excludes", []), "excludes"),
            str(raw.get("sensitivity") or "encrypted"), str(raw.get("restore_target") or ""),
            str(raw.get("verification") or ""), str(raw.get("retention_class") or "standard"),
            dependencies, raw.get("metadata") or {},
            int(raw.get("version", 1)), bool(raw.get("enabled", True)), str(raw.get("schedule_class", "manual")),
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
