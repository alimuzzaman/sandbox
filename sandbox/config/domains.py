"""Normalize clean-hostname intent without erasing configuration provenance."""

from __future__ import annotations

import re
from typing import Any, Mapping


_DOMAIN_KEYS = frozenset({"enabled", "hostname", "strategy", "wildcard", "tld", "ingress"})
_STRATEGY = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _mapping(value: object, source: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{source} domains configuration must be an object")
    result = dict(value)
    unknown = sorted(set(result) - _DOMAIN_KEYS)
    if unknown:
        raise ValueError(f"unknown domains key: {unknown[0]}")
    return result


def normalize_hostname(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("domains hostname must be a non-empty hostname")
    candidate = value.strip().rstrip(".")
    if candidate.startswith("*."):
        raise ValueError("domains hostname must not contain a wildcard")
    try:
        ascii_name = candidate.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("domains hostname is not valid IDNA") from exc
    labels = ascii_name.split(".")
    if len(labels) < 2 or len(ascii_name) > 253 or any(
        len(label) > 63 or not _LABEL.fullmatch(label) for label in labels
    ):
        raise ValueError("domains hostname must be a valid fully-qualified name")
    return ascii_name


def normalize_tld(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("domains tld must be a non-empty DNS label")
    candidate = value.strip().lower().lstrip(".")
    if "." in candidate or not _LABEL.fullmatch(candidate):
        raise ValueError("domains tld must be one valid DNS label")
    return candidate


def suffix_class(hostname: str | None, tld: str) -> str:
    suffix = hostname.rsplit(".", 1)[-1] if hostname else tld
    if suffix == "test":
        return "test"
    if suffix == "local":
        return "mdns_reserved"
    if suffix in {"tst", "localhost", "invalid"}:
        return "legacy_private"
    return "public"


def _selected(
    key: str,
    project: Mapping[str, Any],
    machine: Mapping[str, Any],
    default: Any,
) -> tuple[Any, str]:
    if key in machine:
        return machine[key], "machine_override"
    if key in project:
        return project[key], "project"
    return default, "default"


def normalize_domain_policy(result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return one strict resolver policy plus independently reportable sources.

    Schema providers attach ``_domains_raw`` before legacy defaults are merged.  This
    function intentionally ignores a defaulted top-level ``tld`` unless the raw layer
    says it was explicit.
    """
    resolved = dict(result or {})
    raw = resolved.get("_domains_raw") or {}
    if not isinstance(raw, Mapping):
        raise ValueError("domain provenance must be an object")
    project = _mapping(raw.get("project"), "project")
    machine = _mapping(raw.get("machine_override"), "machine override")

    persisted = resolved.get("_persisted_hostname")
    if persisted is None and resolved.get("domain") and not (
        "hostname" in project or "hostname" in machine
    ):
        persisted = resolved.get("domain")

    configured_hostname, hostname_source = _selected(
        "hostname", project, machine, None,
    )
    if persisted:
        hostname = normalize_hostname(persisted)
        hostname_source = "persisted"
    elif configured_hostname is not None:
        hostname = normalize_hostname(configured_hostname)
    else:
        hostname = None

    configured_tld, tld_source = _selected("tld", project, machine, None)
    if hostname:
        inferred_tld = hostname.rsplit(".", 1)[-1]
        tld = normalize_tld(configured_tld) if configured_tld is not None else inferred_tld
        if configured_tld is not None and tld != inferred_tld:
            raise ValueError("domains hostname and tld disagree")
        if hostname_source == "persisted":
            tld_source = "persisted"
    else:
        tld = normalize_tld(configured_tld) if configured_tld is not None else "test"
        if configured_tld is not None and hostname_source == "default":
            hostname_source = tld_source

    classification = suffix_class(hostname, tld)
    if classification == "mdns_reserved" and hostname_source != "persisted":
        raise ValueError("new .local hostnames are reserved for mDNS; use .test")

    enabled, enabled_source = _selected("enabled", project, machine, False)
    wildcard, wildcard_source = _selected("wildcard", project, machine, False)
    strategy, strategy_source = _selected("strategy", project, machine, None)
    ingress, ingress_source = _selected("ingress", project, machine, None)
    if not isinstance(enabled, bool):
        raise ValueError("domains enabled must be a boolean")
    if not isinstance(wildcard, bool):
        raise ValueError("domains wildcard must be a boolean")
    if strategy is not None and (
        not isinstance(strategy, str) or not _STRATEGY.fullmatch(strategy)
    ):
        raise ValueError("domains strategy must be a lowercase adapter id")
    if ingress is not None and (
        not isinstance(ingress, str)
        or (ingress != "disabled" and not _STRATEGY.fullmatch(ingress))
    ):
        raise ValueError("domains ingress must be a lowercase adapter id or disabled")

    explicit = bool(project or machine)
    return {
        "enabled": enabled,
        "hostname": hostname,
        "tld": tld,
        "strategy": strategy,
        "ingress": ingress,
        "wildcard": wildcard,
        "suffixClass": classification,
        "hostnameSource": hostname_source,
        "tldSource": tld_source,
        "strategySource": strategy_source,
        "ingressSource": ingress_source,
        "enabledSource": enabled_source,
        "wildcardSource": wildcard_source,
        "explicit": explicit,
        "migrationState": "required" if classification == "mdns_reserved" else "none",
    }


def raw_domain_layer(document: Mapping[str, Any] | None) -> dict[str, Any]:
    """Extract only supported domain keys from one un-defaulted config document."""
    if not document:
        return {}
    if not isinstance(document, Mapping):
        raise ValueError("project configuration must be an object")
    nested = document.get("domains")
    result = _mapping(nested, "project") if nested is not None else {}
    if "domain" in document and "hostname" not in result:
        result["hostname"] = document["domain"]
    if "hostname" in document and "hostname" not in result:
        result["hostname"] = document["hostname"]
    if "tld" in document and "tld" not in result:
        result["tld"] = document["tld"]
    return result
