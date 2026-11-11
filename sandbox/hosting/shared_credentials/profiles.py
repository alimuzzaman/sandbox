"""Immutable registered hosting declarations, never command-execution grants.

secrets.hostingProfiles maps safe names to exact source/action/environment,
sourceCommit/artifactDigest/manifestDigest and ordered mappings. Mappings have
exactly the CredentialMapping fields, including kind, consumer identity and
revision, destination slot and purpose. No URL, argv, role or shell is allowed.
The hprof1 revision hashes NONSECRET structure only. It is distinct from the
secret service's keyed, value-derived opaque source revision. Config edits
require a new revision and current server reference resolution at every effect.
Full owner/sudo policy belongs to the owner executor; these profiles add no role.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType

from .models import CredentialMapping, CredentialValidationError

_ID = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = {"source", "action", "environment", "sourceCommit", "artifactDigest",
           "manifestDigest", "mappings"}


def normalize_hosting_profiles(raw, sources) -> dict:
    result = {}
    for name, descriptor in raw.items():
        if (not isinstance(name, str) or not _ID.fullmatch(name)
                or type(descriptor) is not dict or set(descriptor) != _FIELDS):
            raise ValueError("hosting profile has invalid fields")
        source = descriptor["source"]
        if not isinstance(source, str) or source not in sources:
            raise ValueError("hosting profile requires an explicitly registered source")
        if descriptor["action"] not in ("stack_up", "host_apply"):
            raise ValueError("hosting profile action is invalid")
        if (not isinstance(descriptor["environment"], str)
                or not _ID.fullmatch(descriptor["environment"])):
            raise ValueError("hosting profile environment is invalid")
        commit = descriptor["sourceCommit"]
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
            raise ValueError("hosting profile source commit is invalid")
        for key in ("artifactDigest", "manifestDigest"):
            if not isinstance(descriptor[key], str) or not _HEX.fullmatch(descriptor[key]):
                raise ValueError("hosting profile artifact selector is invalid")
        mappings = descriptor["mappings"]
        if type(mappings) is not list or not 0 < len(mappings) <= 128:
            raise ValueError("hosting profile mappings are invalid")
        try:
            parsed = tuple(CredentialMapping.parse(item) for item in mappings)
        except CredentialValidationError:
            raise ValueError("hosting profile mapping is invalid") from None
        slots = [(item.consumer_id, item.destination) for item in parsed]
        if len(set(slots)) != len(slots):
            raise ValueError("hosting profile destination is duplicated")
        # Reuse selector rules without constructing a command/use grant.
        from sandbox.config.secrets import _env_name
        from sandbox.secrets.formats import validate_selector, SecretFormatError
        source_descriptor = sources[source]
        for item in parsed:
            if item.kind != "provider":
                continue
            if source_descriptor.get("type", "file") == "infisical":
                if item.source_key != source_descriptor["key"]:
                    raise ValueError("hosting provider selector must match its source")
            elif source_descriptor.get("format", "dotenv") == "dotenv":
                _env_name(item.source_key, "hosting source key")
            else:
                try:
                    validate_selector(item.source_key)
                except (SecretFormatError, TypeError):
                    raise ValueError("hosting source selector is invalid") from None
        result[name] = {**descriptor, "mappings": [item.as_dict() for item in parsed]}
    return result


@dataclass(frozen=True, slots=True)
class HostingProfile:
    name: str
    source: str
    action: str
    environment: str
    source_commit: str
    artifact_digest: str
    manifest_digest: str
    mappings: tuple[CredentialMapping, ...]
    revision: str

    def matches(self, intent) -> bool:
        return (self.name == intent.profile_ref and self.revision == intent.profile_revision
                and self.source == intent.source_ref and self.action == intent.action
                and self.environment == intent.environment and self.source_commit == intent.source_commit
                and self.artifact_digest == intent.artifact_digest
                and self.manifest_digest == intent.manifest_digest and self.mappings == intent.mappings)


def registered_profiles(normalized) -> MappingProxyType:
    profiles = {}
    for name, item in normalized.items():
        revision = "hprof1-" + hashlib.sha256(json.dumps(
            {"name": name, **item}, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False).encode("ascii")).hexdigest()
        profiles[name] = HostingProfile(name, item["source"], item["action"], item["environment"],
            item["sourceCommit"], item["artifactDigest"], item["manifestDigest"],
            tuple(CredentialMapping.parse(value) for value in item["mappings"]), revision)
    return MappingProxyType(profiles)


__all__ = ["HostingProfile", "normalize_hosting_profiles", "registered_profiles"]
