"""Strict normalization of the ``secrets.ownership`` configuration block.

Structural problems raise ``ValueError`` with fixed messages that never echo
configuration payloads. Ownership decisions (undecided, multiply assigned,
duplicate destination, other domain) are kept verbatim for ``classify``.

The identifier patterns mirror ``sandbox.config.secrets``; they are kept local
because this package imports only the standard library and ``sandbox.secrets``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
import re

from sandbox.secrets.provider_identity import canonical_https_origin


_SAFE_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_PROVIDER_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_PROVIDER_ENVIRONMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SECRET_LIKE_METADATA = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+|"
    r"\b(?:gh[pousr]_|github_pat_|sk_(?:test|live)_|xox[baprs]-))",
    re.IGNORECASE,
)
_DANGEROUS_DESTINATIONS = frozenset((
    "NODE_OPTIONS", "PYTHONPATH", "PYTHONHOME", "PERL5OPT", "RUBYOPT",
    "BASH_ENV", "ENV", "SHELLOPTS", "PS4", "PROMPT_COMMAND",
    "GIT_ASKPASS", "SSH_ASKPASS",
))
_VALUE_FIELDS = frozenset(("value", "secret", "secretValue", "token"))
_OWNERSHIP_KEYS = frozenset(("profiles", "entries"))
_PROFILE_KEYS = frozenset(("domain", "destination"))
_DESTINATION_KEYS = frozenset((
    "endpoint", "projectId", "environment", "path", "readClientId",
    "createClientId", "timeoutSeconds", "gateway",
))
_ENTRY_KEYS = frozenset(("source", "key", "profile", "name", "consumer", "origin"))
_DOMAINS = frozenset(("office", "personal"))
_ORIGINS = frozenset(("source", "destination"))
_GATEWAYS = frozenset(("none", "cloudflare-access"))
_RESERVED_PROFILE = "undecided"
_PERSONAL_ALIAS = "personal"


def _refuse(message: str) -> ValueError:
    return ValueError(f"secret ownership {message}")


def _object(value: object, keys: frozenset[str], label: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise _refuse(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise _refuse(f"{label} has unknown keys")
    unknown = set(value) - keys
    if unknown & _VALUE_FIELDS:
        raise _refuse("configuration must not contain secret values")
    if unknown:
        raise _refuse(f"{label} has unknown keys")
    return value


def _metadata(value: object, pattern: re.Pattern[str], label: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 256
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or not pattern.fullmatch(value)
            or _SECRET_LIKE_METADATA.search(value)):
        raise _refuse(f"{label} must be a bounded non-secret identifier")
    return value


def _slug(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_SLUG.fullmatch(value):
        raise _refuse(f"{label} must be a lowercase safe slug")
    return value


def _endpoint(value: object) -> str:
    if isinstance(value, str) and _SECRET_LIKE_METADATA.search(value):
        raise _refuse("destination endpoint must be a canonical HTTPS origin")
    try:
        return canonical_https_origin(value)
    except (TypeError, ValueError):
        raise _refuse("destination endpoint must be a canonical HTTPS origin") from None


def _path(value: object) -> str:
    if (not isinstance(value, str) or not value.startswith("/") or len(value) > 512
            or "\\" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise _refuse("destination path must be a canonical absolute provider path")
    candidate = PurePosixPath(value)
    if (str(candidate) != value or value != "/" and value.endswith("/")
            or any(part in {"", ".", ".."} for part in candidate.parts[1:])):
        raise _refuse("destination path must be a canonical absolute provider path")
    return value


def _timeout(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 120:
        raise _refuse("destination timeoutSeconds must be between 1 and 120")
    return value


def _client(value: object) -> str | None:
    if value is None:
        return None
    return _metadata(value, _PROVIDER_IDENTIFIER, "destination client id")


def _normalize_destination(value: object) -> dict:
    item = _object(value, _DESTINATION_KEYS, "destination")
    for required in ("endpoint", "projectId", "environment"):
        if required not in item:
            raise _refuse("destination requires endpoint, projectId and environment")
    gateway = item.get("gateway", "none")
    if gateway not in _GATEWAYS:
        raise _refuse("destination gateway must be none or cloudflare-access")
    read_client = _client(item.get("readClientId"))
    create_client = _client(item.get("createClientId"))
    if read_client is not None and read_client == create_client:
        raise _refuse("destination readClientId and createClientId must differ")
    return {
        "endpoint": _endpoint(item["endpoint"]),
        "projectId": _metadata(item["projectId"], _PROVIDER_IDENTIFIER, "destination projectId"),
        "environment": _metadata(
            item["environment"], _PROVIDER_ENVIRONMENT, "destination environment",
        ),
        "path": _path(item.get("path", "/")),
        "readClientId": read_client,
        "createClientId": create_client,
        "timeoutSeconds": _timeout(item.get("timeoutSeconds", 30)),
        "gateway": gateway,
    }


def _consumer(value: object) -> str:
    if not isinstance(value, str) or not _ENV_NAME.fullmatch(value):
        raise _refuse("entry consumer must be a portable environment name")
    canonical = value.upper()
    if (canonical.startswith("LD_") or canonical.startswith("DYLD_")
            or canonical in _DANGEROUS_DESTINATIONS):
        raise _refuse("entry consumer is dangerous")
    return value


def _entry_key(value: object, descriptor: Mapping | None) -> str:
    if isinstance(descriptor, Mapping) and descriptor.get("type", "file") == "infisical":
        if not isinstance(value, str) or value != descriptor.get("key"):
            raise _refuse("entry key must match its Infisical source exactly")
        return value
    source_format = "dotenv" if descriptor is None else descriptor.get("format", "dotenv")
    if source_format == "dotenv":
        if not isinstance(value, str) or not _ENV_NAME.fullmatch(value):
            raise _refuse("entry key must be a portable environment name")
        return value
    from sandbox.secrets.formats import SecretFormatError, validate_selector
    try:
        return validate_selector(value)
    except (SecretFormatError, TypeError):
        raise _refuse("entry key must be a valid source selector") from None


def _normalize_entry(value: object, sources: Mapping, profiles: Mapping) -> dict:
    item = _object(value, _ENTRY_KEYS, "entry")
    if set(item) != _ENTRY_KEYS:
        raise _refuse("entry requires source, key, profile, name, consumer and origin")
    source = item["source"]
    if not isinstance(source, str) or (source != _PERSONAL_ALIAS and source not in sources):
        raise _refuse("entry references an unregistered source alias")
    descriptor = None if source == _PERSONAL_ALIAS else sources[source]
    profile = item["profile"]
    if not isinstance(profile, str) or (profile != _RESERVED_PROFILE and profile not in profiles):
        raise _refuse("entry references an unknown ownership profile")
    origin = item["origin"]
    if origin not in _ORIGINS:
        raise _refuse("entry origin must be source or destination")
    if (origin == "destination" and profile != _RESERVED_PROFILE
            and profiles[profile]["destination"]["readClientId"] is None):
        raise _refuse("destination-origin entry requires a profile readClientId")
    name = item["name"]
    if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
        raise _refuse("entry name must be a portable environment name")
    return {
        "source": source,
        "key": _entry_key(item["key"], descriptor),
        "profile": profile,
        "name": name,
        "consumer": _consumer(item["consumer"]),
        "origin": origin,
    }


def _check_domains(profiles: Mapping[str, dict]) -> None:
    names = sorted(profiles)
    for index, left_name in enumerate(names):
        left = profiles[left_name]
        for right_name in names[index + 1:]:
            right = profiles[right_name]
            if left["domain"] == right["domain"]:
                continue
            left_dest, right_dest = left["destination"], right["destination"]
            if left_dest["projectId"] == right_dest["projectId"]:
                raise _refuse("profiles of different domains must not share projectId")
            left_clients = {left_dest["readClientId"], left_dest["createClientId"]} - {None}
            right_clients = {right_dest["readClientId"], right_dest["createClientId"]} - {None}
            if left_clients & right_clients:
                raise _refuse("profiles of different domains must not share client ids")


def normalize_ownership(project_block: Mapping | None, machine_block: Mapping | None,
                        sources: Mapping, root: object) -> dict | None:
    """Return the canonical ownership block, or ``None`` when neither layer has one.

    ``root`` is accepted for contract symmetry with source normalization; the
    ownership block itself holds no filesystem paths.
    """
    del root  # paths live only in registered sources
    if project_block is not None and machine_block is not None:
        raise _refuse("must be configured in exactly one layer")
    block = project_block if project_block is not None else machine_block
    if block is None:
        return None
    item = _object(block, _OWNERSHIP_KEYS, "configuration")
    raw_profiles = item.get("profiles", {})
    raw_entries = item.get("entries", [])
    if not isinstance(raw_profiles, Mapping):
        raise _refuse("profiles must be an object")
    if not isinstance(raw_entries, list):
        raise _refuse("entries must be a list")
    profiles: dict[str, dict] = {}
    for name, value in raw_profiles.items():
        profile_name = _slug(name, "profile name")
        if profile_name == _RESERVED_PROFILE:
            raise _refuse("profile name undecided is reserved")
        profile = _object(value, _PROFILE_KEYS, "profile")
        if set(profile) != _PROFILE_KEYS:
            raise _refuse("profile requires domain and destination")
        if profile["domain"] not in _DOMAINS:
            raise _refuse("profile domain must be office or personal")
        profiles[profile_name] = {
            "domain": profile["domain"],
            "destination": _normalize_destination(profile["destination"]),
        }
    _check_domains(profiles)
    registered = sources if isinstance(sources, Mapping) else {}
    entries = [_normalize_entry(entry, registered, profiles) for entry in raw_entries]
    entries.sort(key=lambda entry: (
        entry["source"], entry["key"], entry["profile"], entry["name"],
        entry["consumer"], entry["origin"],
    ))
    return {"profiles": dict(sorted(profiles.items())), "entries": entries}


__all__ = ["normalize_ownership"]
