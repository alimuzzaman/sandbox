"""Normalize registered secret sources and fixed child-use profiles."""

from __future__ import annotations

from collections.abc import Mapping
import copy
from pathlib import Path, PurePosixPath
import re


_SAFE_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_MCP_MODE_ORDER = ("keys", "metadata", "validate", "masked", "use")
_MCP_MODES = frozenset(_MCP_MODE_ORDER)
_SECRET_KEYS = frozenset(("sources", "useProfiles"))
_SOURCE_KEYS = frozenset(("path", "mcpModes"))
_PROFILE_KEYS = frozenset((
    "source", "key", "argv", "destination", "timeoutSeconds",
    "maxOutputBytes", "mcp",
))
_DANGEROUS_DESTINATIONS = frozenset((
    "NODE_OPTIONS", "PYTHONPATH", "PYTHONHOME", "PERL5OPT", "RUBYOPT",
    "BASH_ENV", "ENV", "SHELLOPTS", "PS4", "PROMPT_COMMAND",
    "GIT_ASKPASS", "SSH_ASKPASS",
))


def _mapping(value: object, label: str) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return copy.deepcopy(dict(value))


def _unknown_keys(value: Mapping, allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(value) - allowed, key=repr)
    if unknown:
        raise ValueError(f"{label} has unknown keys: {unknown}")


def _slug(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_SLUG.fullmatch(value):
        raise ValueError(f"secret {label} must be a lowercase safe slug")
    return value


def _env_name(value: object, label: str) -> str:
    if not isinstance(value, str) or not _ENV_NAME.fullmatch(value):
        raise ValueError(f"secret use profile {label} must be a portable environment name")
    return value


def _source_path(value: object, root: object = None) -> str:
    if (not isinstance(value, str) or not value or value.endswith("/") or "\\" in value or
            any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("secret source path must be a safe project-relative .env* path")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.name.startswith(".env"):
        raise ValueError("secret source path must be a project-relative .env* file")
    if root is not None:
        project_root = Path(root).expanduser().resolve()
        try:
            (project_root / value).resolve().relative_to(project_root)
        except (OSError, ValueError) as exc:
            raise ValueError("secret source path must stay within the project root") from exc
    return value


def _mcp_modes(value: object) -> list[str]:
    if (not isinstance(value, list) or any(not isinstance(mode, str) for mode in value)
            or len(value) != len(set(value)) or set(value) - _MCP_MODES):
        raise ValueError(
            "secret source mcpModes must be a unique list containing only "
            "keys, metadata, validate, masked, and use"
        )
    selected = set(value)
    return [mode for mode in _MCP_MODE_ORDER if mode in selected]


def _normalize_source(alias: object, value: object, root: object) -> tuple[str, dict]:
    name = _slug(alias, "source alias")
    if name == "personal":
        raise ValueError("secret source alias personal is reserved")
    descriptor = _mapping(value, f"secret source {name!r}")
    _unknown_keys(descriptor, _SOURCE_KEYS, f"secret source {name!r}")
    if "path" not in descriptor:
        raise ValueError(f"secret source {name!r} requires path")
    return name, {
        "path": _source_path(descriptor["path"], root),
        "mcpModes": _mcp_modes(descriptor["mcpModes"])
        if "mcpModes" in descriptor else [],
    }


def _bounded_int(value: object, minimum: int, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"secret use profile {label} must be between {minimum} and {maximum}")
    return value


def _fixed_argv(value: object) -> list[str]:
    if (not isinstance(value, list) or not value or
            any(not isinstance(item, str) or not item or
                any(ord(char) < 32 or ord(char) == 127 for char in item)
                for item in value)):
        raise ValueError("secret use profile argv must be a non-empty direct argv list")
    return list(value)


def _destination(value: object) -> str:
    destination = _env_name(value, "destination")
    canonical = destination.upper()
    if (canonical.startswith("LD_") or canonical.startswith("DYLD_") or
            canonical in _DANGEROUS_DESTINATIONS):
        raise ValueError("secret use profile destination is dangerous")
    return destination


def _normalize_profile(name: object, value: object, sources: Mapping[str, dict]) -> tuple[str, dict]:
    profile_name = _slug(name, "profile name")
    descriptor = _mapping(value, f"secret use profile {profile_name!r}")
    _unknown_keys(descriptor, _PROFILE_KEYS, f"secret use profile {profile_name!r}")
    required = ("source", "key", "argv", "destination")
    missing = [key for key in required if key not in descriptor]
    if missing:
        raise ValueError(
            f"secret use profile {profile_name!r} requires {', '.join(missing)}"
        )
    source = descriptor["source"]
    if not isinstance(source, str) or (source != "personal" and source not in sources):
        raise ValueError(f"secret use profile {profile_name!r} references an unknown source")
    key = _env_name(descriptor["key"], "key")
    enabled_for_mcp = descriptor.get("mcp", False)
    if not isinstance(enabled_for_mcp, bool):
        raise ValueError("secret use profile mcp must be a boolean")
    source_modes = [] if source == "personal" else sources[source]["mcpModes"]
    if enabled_for_mcp and "use" not in source_modes:
        raise ValueError(
            f"secret use profile {profile_name!r} requires use in source mcpModes"
        )
    return profile_name, {
        "source": source,
        "key": key,
        "argv": _fixed_argv(descriptor["argv"]),
        "destination": _destination(descriptor["destination"]),
        "timeoutSeconds": _bounded_int(
            descriptor.get("timeoutSeconds", 300), 1, 1800, "timeoutSeconds",
        ),
        "maxOutputBytes": _bounded_int(
            descriptor.get("maxOutputBytes", 1_048_576),
            1, 1_048_576, "maxOutputBytes",
        ),
        "mcp": enabled_for_mcp,
    }


def raw_secret_layer(document: Mapping | None) -> dict:
    """Extract an un-defaulted common secrets layer from one descriptor."""
    if not isinstance(document, Mapping):
        raise ValueError("project configuration must be an object")
    if "secrets" not in document:
        return {}
    return _mapping(document["secrets"], "secrets configuration")


def merge_secret_layers(target: dict, incoming: Mapping) -> None:
    """Merge disjoint machine-local layers without permitting silent replacement."""
    layer = _mapping(incoming, "machine override secrets configuration")
    _unknown_keys(layer, _SECRET_KEYS, "machine override secrets configuration")
    for category, values in layer.items():
        incoming_values = _mapping(values, f"machine secret {category}")
        existing = target.setdefault(category, {})
        if not isinstance(existing, dict):  # defensive for callers outside schema providers
            raise ValueError(f"machine secret {category} must be an object")
        duplicates = sorted(set(existing) & set(incoming_values), key=repr)
        if duplicates:
            raise ValueError(f"duplicate secret {category} across machine layers: {duplicates}")
        existing.update(incoming_values)


def normalize_secret_config(result: Mapping | None) -> dict:
    """Return strict common secret configuration from project and machine layers."""
    resolved = dict(result or {})
    raw = resolved.get("_secrets_raw")
    if raw is None:
        raw = {
            "project": resolved.get("secrets", {}),
            "machine_override": {},
        }
    raw = _mapping(raw, "secret configuration provenance")
    _unknown_keys(
        raw, frozenset(("project", "machine_override")),
        "secret configuration provenance",
    )
    project = _mapping(raw.get("project", {}), "project secrets configuration")
    machine = _mapping(
        raw.get("machine_override", {}), "machine override secrets configuration",
    )
    _unknown_keys(project, _SECRET_KEYS, "project secrets configuration")
    _unknown_keys(machine, _SECRET_KEYS, "machine override secrets configuration")

    project_sources = _mapping(project.get("sources", {}), "project secret sources")
    machine_sources = _mapping(machine.get("sources", {}), "machine secret sources")
    duplicate_sources = sorted(set(project_sources) & set(machine_sources), key=repr)
    if duplicate_sources:
        raise ValueError(f"duplicate secret source aliases across layers: {duplicate_sources}")

    root = resolved.get("root")
    sources = {}
    for alias, descriptor in (*project_sources.items(), *machine_sources.items()):
        normalized_alias, normalized = _normalize_source(alias, descriptor, root)
        sources[normalized_alias] = normalized

    project_profiles = _mapping(
        project.get("useProfiles", {}), "project secret useProfiles",
    )
    machine_profiles = _mapping(
        machine.get("useProfiles", {}), "machine secret useProfiles",
    )
    duplicate_profiles = sorted(set(project_profiles) & set(machine_profiles), key=repr)
    if duplicate_profiles:
        raise ValueError(f"duplicate secret use profile names across layers: {duplicate_profiles}")

    profiles = {}
    for name, descriptor in (*project_profiles.items(), *machine_profiles.items()):
        normalized_name, normalized = _normalize_profile(name, descriptor, sources)
        profiles[normalized_name] = normalized
    return {"sources": sources, "useProfiles": profiles}
