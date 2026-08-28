"""Versioned, canonical acceptance manifest for the Credential Vault proof run.

The manifest is the whole execution plan: which host, which revision, which
units, which sockets, which kernel state, which bounds, which checks, and which
artifacts. Its digest binds all of it, so a plan cannot be edited between
validation and execution without the digest changing.

The schema is exact. An unknown key is a refusal, not an ignored extra: that is
what stops a credential, a source reference, or a request body from riding into
the plan as an "extra field".
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from . import catalog as catalog_module
from . import scanner


MANIFEST_VERSION = 1
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_STRING = 256
MAX_LIST = 64

_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")
_MACHINE_ID = re.compile(r"^sb-[a-f0-9]{12}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_UNIT = re.compile(r"^[A-Za-z0-9@._-]{1,96}\.(?:service|socket|slice|scope)$")
_INTERFACE = re.compile(r"^[A-Za-z0-9._-]{1,15}$")
_ABSTRACT_SOCKET = re.compile(r"^@[A-Za-z0-9._-]{1,96}$")
_ABSOLUTE_PATH = re.compile(r"^/[A-Za-z0-9._/-]{1,255}$")
_CGROUP = re.compile(r"^/[A-Za-z0-9._/-]{1,255}$")
_CHECK_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_ARTIFACT = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")
_KERNEL = re.compile(r"^[0-9][0-9A-Za-z._-]{2,63}$")

SUPPORTED_OS_RELEASE = "ubuntu-24.04"
SUPPORTED_ARCHITECTURES = frozenset({"x86_64", "aarch64"})
APPARMOR_MODES = frozenset({"enforce"})
SECCOMP_MODES = frozenset({"filter"})
NFTABLES_POLICIES = frozenset({"drop"})
CHECK_CATEGORIES = frozenset({
    "revision", "platform", "service_identity", "process_identity", "transport",
    "descriptor", "network", "upstream", "bounds", "lifecycle", "cleanup",
})

_SECTIONS = {
    "source": frozenset({"git_sha", "sandbox_revision"}),
    "target": frozenset({"machine_id", "broker_epoch", "host_label"}),
    "platform": frozenset({"os_release", "kernel_release", "architecture"}),
    "service": frozenset({
        "units", "service_uid", "service_gid", "controller_uid", "controller_gid",
        "executable", "executable_digest", "config_digest", "cgroup",
    }),
    "transport": frozenset({
        "guest_interface", "host_address", "guest_address", "guest_port",
        "lease_socket", "controller_socket",
    }),
    "kernel": frozenset({
        "required_capabilities", "forbidden_capabilities", "apparmor_profile",
        "apparmor_mode", "seccomp_mode", "nftables_table", "nftables_policy",
    }),
    "bounds": frozenset({
        "connect_seconds", "total_seconds", "idle_seconds", "drain_seconds",
        "command_timeout_seconds", "max_request_headers", "max_request_body",
        "max_response_body", "max_concurrent", "max_output_bytes",
    }),
    "cleanup": frozenset({
        "units", "sockets", "interfaces", "cgroups", "nftables_objects", "paths",
    }),
}
_TOP_LEVEL = frozenset({
    "version", "manifest_id", "source", "target", "platform", "service",
    "transport", "kernel", "bounds", "cleanup", "checks", "artifacts",
})
_CHECK_FIELDS = frozenset({"check_id", "category", "required", "description"})
_ARTIFACT_FIELDS = frozenset({"name", "sha256", "max_bytes"})


class ManifestError(ValueError):
    """A bounded refusal carrying a stable code and no free-form detail."""

    def __init__(self, code: str, location: str = "manifest") -> None:
        super().__init__(code)
        self.code = code
        self.location = location[:MAX_STRING]

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "location": self.location}


def _refuse(code: str, location: str = "manifest") -> "ManifestError":
    return ManifestError(code, location)


def _text(value: Any, pattern: re.Pattern[str], location: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_STRING \
            or not pattern.fullmatch(value):
        raise _refuse("field_invalid", location)
    return value


def _bounded_int(value: Any, location: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) \
            or not minimum <= value <= maximum:
        raise _refuse("field_invalid", location)
    return value


def _string_list(value: Any, pattern: re.Pattern[str], location: str,
                 *, minimum: int = 0) -> tuple[str, ...]:
    if not isinstance(value, list) or not minimum <= len(value) <= MAX_LIST:
        raise _refuse("list_invalid", location)
    items = tuple(_text(item, pattern, f"{location}[{index}]")
                  for index, item in enumerate(value))
    if len(set(items)) != len(items):
        raise _refuse("list_duplicate", location)
    return items


def _section(document: dict[str, Any], name: str) -> dict[str, Any]:
    value = document.get(name)
    if not isinstance(value, dict) or frozenset(value) != _SECTIONS[name]:
        raise _refuse("section_invalid", name)
    return value


def canonical_json(document: Any) -> str:
    """One encoding only: sorted keys, no spaces, ASCII escapes."""
    try:
        return json.dumps(document, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise _refuse("encoding_invalid") from exc


def manifest_digest(document: Any) -> str:
    return hashlib.sha256(canonical_json(document).encode("ascii")).hexdigest()


def _validate_ipv4(value: Any, location: str) -> str:
    import ipaddress

    if not isinstance(value, str):
        raise _refuse("field_invalid", location)
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise _refuse("field_invalid", location) from exc
    if not address.is_private or address.is_loopback:
        raise _refuse("address_not_private", location)
    return str(address)


def validate_manifest(document: Any) -> dict[str, Any]:
    """Return the accepted manifest, or raise a bounded ManifestError."""
    if isinstance(document, (bytes, bytearray, str)):
        raw = document.encode("utf-8") if isinstance(document, str) else bytes(document)
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise _refuse("document_oversize")
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _refuse("document_invalid") from exc
    if not isinstance(document, dict) or frozenset(document) != _TOP_LEVEL:
        raise _refuse("schema_unknown_key")
    if document.get("version") != MANIFEST_VERSION:
        raise _refuse("version_unsupported")
    _text(document.get("manifest_id"), _IDENTITY, "manifest_id")

    findings = scanner.scan_document(document, location="manifest")
    if findings:
        raise _refuse("secret_like_material", findings[0]["location"])

    source = _section(document, "source")
    _text(source["git_sha"], _GIT_SHA, "source.git_sha")
    _text(source["sandbox_revision"], _IDENTITY, "source.sandbox_revision")

    target = _section(document, "target")
    _text(target["machine_id"], _MACHINE_ID, "target.machine_id")
    _text(target["broker_epoch"], _IDENTITY, "target.broker_epoch")
    _text(target["host_label"], _IDENTITY, "target.host_label")

    platform = _section(document, "platform")
    if platform["os_release"] != SUPPORTED_OS_RELEASE:
        raise _refuse("platform_unsupported", "platform.os_release")
    _text(platform["kernel_release"], _KERNEL, "platform.kernel_release")
    if platform["architecture"] not in SUPPORTED_ARCHITECTURES:
        raise _refuse("platform_unsupported", "platform.architecture")

    service = _section(document, "service")
    _string_list(service["units"], _UNIT, "service.units", minimum=1)
    for name in ("service_uid", "service_gid", "controller_uid", "controller_gid"):
        _bounded_int(service[name], f"service.{name}", minimum=1, maximum=2 ** 31 - 1)
    if service["service_uid"] == service["controller_uid"]:
        raise _refuse("service_uid_shared", "service.service_uid")
    _text(service["executable"], _ABSOLUTE_PATH, "service.executable")
    _text(service["executable_digest"], _DIGEST, "service.executable_digest")
    _text(service["config_digest"], _DIGEST, "service.config_digest")
    _text(service["cgroup"], _CGROUP, "service.cgroup")

    transport = _section(document, "transport")
    _text(transport["guest_interface"], _INTERFACE, "transport.guest_interface")
    _validate_ipv4(transport["host_address"], "transport.host_address")
    _validate_ipv4(transport["guest_address"], "transport.guest_address")
    _bounded_int(transport["guest_port"], "transport.guest_port",
                 minimum=1024, maximum=65535)
    _text(transport["lease_socket"], _ABSTRACT_SOCKET, "transport.lease_socket")
    _text(transport["controller_socket"], _ABSTRACT_SOCKET,
          "transport.controller_socket")
    if transport["lease_socket"] == transport["controller_socket"]:
        raise _refuse("socket_identity_shared", "transport.controller_socket")
    if transport["host_address"] == transport["guest_address"]:
        raise _refuse("address_identity_shared", "transport.guest_address")

    kernel = _section(document, "kernel")
    required = _string_list(kernel["required_capabilities"], _IDENTITY,
                            "kernel.required_capabilities")
    forbidden = _string_list(kernel["forbidden_capabilities"], _IDENTITY,
                             "kernel.forbidden_capabilities", minimum=1)
    if set(required) & set(forbidden):
        raise _refuse("capability_contradiction", "kernel.required_capabilities")
    _text(kernel["apparmor_profile"], _IDENTITY, "kernel.apparmor_profile")
    if kernel["apparmor_mode"] not in APPARMOR_MODES:
        raise _refuse("field_invalid", "kernel.apparmor_mode")
    if kernel["seccomp_mode"] not in SECCOMP_MODES:
        raise _refuse("field_invalid", "kernel.seccomp_mode")
    _text(kernel["nftables_table"], _IDENTITY, "kernel.nftables_table")
    if kernel["nftables_policy"] not in NFTABLES_POLICIES:
        raise _refuse("field_invalid", "kernel.nftables_policy")

    bounds = _section(document, "bounds")
    limits = {
        "connect_seconds": (1, 5), "total_seconds": (1, 30), "idle_seconds": (1, 5),
        "drain_seconds": (1, 5), "command_timeout_seconds": (1, 120),
        "max_request_headers": (1, 64 * 1024), "max_request_body": (1, 1024 * 1024),
        "max_response_body": (1, 4 * 1024 * 1024), "max_concurrent": (1, 16),
        "max_output_bytes": (1, 64 * 1024),
    }
    for name, (low, high) in limits.items():
        _bounded_int(bounds[name], f"bounds.{name}", minimum=low, maximum=high)
    if bounds["connect_seconds"] > bounds["total_seconds"]:
        raise _refuse("bounds_contradiction", "bounds.connect_seconds")

    cleanup = _section(document, "cleanup")
    _string_list(cleanup["units"], _UNIT, "cleanup.units", minimum=1)
    _string_list(cleanup["sockets"], _ABSTRACT_SOCKET, "cleanup.sockets", minimum=1)
    _string_list(cleanup["interfaces"], _INTERFACE, "cleanup.interfaces", minimum=1)
    _string_list(cleanup["cgroups"], _CGROUP, "cleanup.cgroups", minimum=1)
    _string_list(cleanup["nftables_objects"], _IDENTITY, "cleanup.nftables_objects",
                 minimum=1)
    _string_list(cleanup["paths"], _ABSOLUTE_PATH, "cleanup.paths")

    checks = document.get("checks")
    if not isinstance(checks, list) or not 1 <= len(checks) <= MAX_LIST * 2:
        raise _refuse("list_invalid", "checks")
    seen_checks = set()
    for index, item in enumerate(checks):
        place = f"checks[{index}]"
        if not isinstance(item, dict) or frozenset(item) != _CHECK_FIELDS:
            raise _refuse("schema_unknown_key", place)
        _text(item["check_id"], _CHECK_ID, f"{place}.check_id")
        if item["category"] not in CHECK_CATEGORIES:
            raise _refuse("field_invalid", f"{place}.category")
        definition = catalog_module.CHECKS.get(item["check_id"])
        if definition is None:
            raise _refuse("check_unsupported", f"{place}.check_id")
        if item["category"] != definition.category:
            raise _refuse("check_category_mismatch", f"{place}.category")
        if not isinstance(item["required"], bool):
            raise _refuse("field_invalid", f"{place}.required")
        if item["required"] != definition.required:
            raise _refuse("check_requirement_mismatch", f"{place}.required")
        if not isinstance(item["description"], str) or not item["description"] \
                or len(item["description"]) > MAX_STRING:
            raise _refuse("field_invalid", f"{place}.description")
        if item["check_id"] in seen_checks:
            raise _refuse("list_duplicate", f"{place}.check_id")
        seen_checks.add(item["check_id"])
    if not any(item["required"] for item in checks):
        raise _refuse("no_required_check", "checks")

    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= MAX_LIST:
        raise _refuse("list_invalid", "artifacts")
    seen_artifacts = set()
    for index, item in enumerate(artifacts):
        place = f"artifacts[{index}]"
        if not isinstance(item, dict) or frozenset(item) != _ARTIFACT_FIELDS:
            raise _refuse("schema_unknown_key", place)
        _text(item["name"], _ARTIFACT, f"{place}.name")
        if item["sha256"] is not None:
            _text(item["sha256"], _DIGEST, f"{place}.sha256")
        _bounded_int(item["max_bytes"], f"{place}.max_bytes",
                     minimum=1, maximum=4 * 1024 * 1024)
        if item["name"] in seen_artifacts:
            raise _refuse("list_duplicate", f"{place}.name")
        definition = catalog_module.ARTIFACTS.get(item["name"])
        if definition is None:
            raise _refuse("artifact_unsupported", f"{place}.name")
        if item["max_bytes"] > definition.maximum_bytes:
            raise _refuse("artifact_bound_too_large", f"{place}.max_bytes")
        seen_artifacts.add(item["name"])
    if seen_artifacts != set(catalog_module.ARTIFACTS):
        raise _refuse("artifact_catalog_incomplete", "artifacts")

    # Cleanup must cover every identity created by this exact plan. A caller
    # cannot omit the controller socket or add a foreign cleanup target.
    exact_cleanup = {
        "units": tuple(service["units"]),
        "sockets": (transport["lease_socket"], transport["controller_socket"]),
        "interfaces": (transport["guest_interface"],),
        "cgroups": (service["cgroup"],),
        "nftables_objects": (kernel["nftables_table"],),
        "paths": catalog_module.EXPECTED_CLEANUP_PATHS,
    }
    for name, expected in exact_cleanup.items():
        if tuple(cleanup[name]) != expected:
            raise _refuse("cleanup_coverage_mismatch", f"cleanup.{name}")
    return document


def load_manifest(path: Any) -> dict[str, Any]:
    """Read one owner-controlled manifest file without following a symlink."""
    target = Path(path)
    if target.is_symlink():
        raise _refuse("manifest_symlink", str(target))
    try:
        if not target.is_file():
            raise _refuse("manifest_missing", str(target))
        if target.stat().st_size > MAX_DOCUMENT_BYTES:
            raise _refuse("document_oversize", str(target))
        raw = target.read_bytes()
    except OSError as exc:
        raise _refuse("manifest_unreadable", str(target)) from exc
    document = validate_manifest(raw)
    if canonical_json(document).encode("ascii") != raw.rstrip(b"\n"):
        raise _refuse("encoding_not_canonical", str(target))
    return document


def required_check_ids(manifest: dict[str, Any]) -> tuple[str, ...]:
    return tuple(item["check_id"] for item in manifest["checks"] if item["required"])


def check_ids(manifest: dict[str, Any]) -> tuple[str, ...]:
    return tuple(item["check_id"] for item in manifest["checks"])


def artifact_names(manifest: dict[str, Any]) -> tuple[str, ...]:
    return tuple(item["name"] for item in manifest["artifacts"])


def assert_revision(manifest: dict[str, Any], observed: Any) -> dict[str, Any]:
    """Refuse before any test action when the host is not the planned revision."""
    if not isinstance(observed, dict) \
            or frozenset(observed) != frozenset({"git_sha", "sandbox_revision"}):
        raise _refuse("revision_observation_invalid", "source")
    if observed["git_sha"] != manifest["source"]["git_sha"]:
        raise _refuse("revision_mismatch", "source.git_sha")
    if observed["sandbox_revision"] != manifest["source"]["sandbox_revision"]:
        raise _refuse("revision_mismatch", "source.sandbox_revision")
    return {"ok": True, "code": "revision_verified"}


__all__ = [
    "APPARMOR_MODES", "CHECK_CATEGORIES", "MANIFEST_VERSION", "MAX_DOCUMENT_BYTES",
    "ManifestError", "SUPPORTED_ARCHITECTURES", "SUPPORTED_OS_RELEASE",
    "artifact_names", "assert_revision", "canonical_json", "check_ids",
    "load_manifest", "manifest_digest", "required_check_ids", "validate_manifest",
]
