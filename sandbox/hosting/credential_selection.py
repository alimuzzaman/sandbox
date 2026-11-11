"""Pure, nonsecret declarations for disabled server credential groundwork.

No parsed declaration establishes registration, availability or authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping
import re

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_FIELDS = ("credential_project", "credential_source", "credential_source_revision",
           "credential_profile", "credential_profile_revision")
_SELECTION_FIELDS = frozenset(("credential_scope", "remote", *_FIELDS))
INVALID = "server_credentials_invalid_selection"
UNSUPPORTED = "server_credentials_unsupported"
STATIC_VALIDATED = "server_credentials_static_validated"
INVENTORY_UNAVAILABLE = "server_credentials_inventory_unavailable"


class DeclarationError(ValueError):
    def __init__(self):
        super().__init__("Invalid server credential selection or nonsecret declaration.")


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise DeclarationError()
    return value


@dataclass(frozen=True)
class PersonalLocalSelection:
    scope: Literal["personal-local"] = field(default="personal-local", init=False)


@dataclass(frozen=True)
class ServerSelection:
    remote: str
    project: str
    source: str
    source_revision: str
    profile: str
    profile_revision: str
    scope: Literal["server"] = field(default="server", init=False)

    def __post_init__(self):
        for value in (self.remote, self.project, self.source, self.source_revision,
                      self.profile, self.profile_revision):
            _identifier(value)


Selection = PersonalLocalSelection | ServerSelection


def decode_selection(raw: Mapping[str, object]) -> Selection:
    if not isinstance(raw, Mapping) or set(raw) - _SELECTION_FIELDS:
        raise DeclarationError()
    scope = raw.get("credential_scope")
    if scope is None:
        scope = "personal-local"
    if scope == "personal-local":
        if any(raw.get(name) is not None for name in _FIELDS):
            raise DeclarationError()
        return PersonalLocalSelection()
    if scope != "server":
        raise DeclarationError()
    return ServerSelection(_identifier(raw.get("remote")),
                           *(_identifier(raw.get(name)) for name in _FIELDS))


def selection_from_args(args) -> Selection:
    return decode_selection({name: getattr(args, name, None) for name in _SELECTION_FIELDS})


@dataclass(frozen=True, order=True)
class CredentialMapping:
    kind: Literal["provider", "generated"]
    source_key: str
    destination: str
    consumer: str

    def __post_init__(self):
        if self.kind not in ("provider", "generated"):
            raise DeclarationError()
        for value in (self.source_key, self.destination):
            if not isinstance(value, str) or not _KEY.fullmatch(value):
                raise DeclarationError()
        _identifier(self.consumer)


def decode_mappings(rows: object) -> tuple[CredentialMapping, ...]:
    if not isinstance(rows, (list, tuple)) or len(rows) > 256:
        raise DeclarationError()
    result = []
    destinations = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"kind", "source_key", "destination", "consumer"}:
            raise DeclarationError()
        item = CredentialMapping(**row)
        if item.destination in destinations:
            raise DeclarationError()
        destinations.add(item.destination)
        result.append(item)
    return tuple(sorted(result))


def parse_declarations(manifest: object, environment: str | None) -> tuple[CredentialMapping, ...]:
    """Read only ordinary required/generated maps; never perform full validation."""
    if not isinstance(manifest, dict):
        raise DeclarationError()
    environments = manifest.get("environments")
    if not isinstance(environments, dict) or not 1 <= len(environments) <= 256:
        raise DeclarationError()
    if environment is None:
        if len(environments) != 1:
            raise DeclarationError()
        environment = next(iter(environments))
    _identifier(environment)
    env = environments.get(environment)
    if not isinstance(env, dict):
        raise DeclarationError()
    forbidden = {"actor", "grant", "owner_context", "owner-context", "admission", "capability"}
    if forbidden.intersection(manifest) or forbidden.intersection(env):
        raise DeclarationError()
    compose = env.get("compose")
    if not isinstance(compose, dict):
        raise DeclarationError()
    consumer = _identifier(compose.get("service"))
    secrets = env.get("secrets", {})
    if not isinstance(secrets, dict) or set(secrets) - {"required", "generated", "values"}:
        raise DeclarationError()
    # Even empty inline values are unsupported; do not inspect or render them.
    if "values" in secrets:
        raise DeclarationError()
    rows = []
    for name, kind in (("required", "provider"), ("generated", "generated")):
        mappings = secrets.get(name, {})
        if not isinstance(mappings, dict) or len(mappings) > 256:
            raise DeclarationError()
        for destination, source_key in mappings.items():
            rows.append(dict(kind=kind, source_key=source_key,
                             destination=destination, consumer=consumer))
    return decode_mappings(rows)


@dataclass(frozen=True)
class StaticValidationResult:
    selection: ServerSelection
    mappings: tuple[CredentialMapping, ...]
    code: Literal["server_credentials_static_validated"] = field(default=STATIC_VALIDATED, init=False)
    declarations: Literal["valid"] = field(default="valid", init=False)
    availability: Literal["unverified"] = field(default="unverified", init=False)
    execution: Literal["disabled"] = field(default="disabled", init=False)


@dataclass(frozen=True)
class UnsupportedResult:
    code: Literal["server_credentials_unsupported", "server_credentials_invalid_selection",
                  "server_credentials_inventory_unavailable"] = UNSUPPORTED
    execution: Literal["disabled"] = field(default="disabled", init=False)
    availability: Literal["unverified"] = field(default="unverified", init=False)

    def __post_init__(self):
        if self.code not in (UNSUPPORTED, INVALID, INVENTORY_UNAVAILABLE):
            raise DeclarationError()
