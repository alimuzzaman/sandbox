"""Fail-closed catalog of routes eligible for request-triggered wake."""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import re
from types import MappingProxyType
from typing import Mapping

from sandbox.config.instance_lifecycle import normalize_instance_lifecycle


_ROUTE = re.compile(r"^[A-Za-z0-9_.:-]{16,128}$")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_HOST = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class ActivationCatalogError(ValueError):
    pass


@dataclass(frozen=True)
class ActivationRoute:
    route_id: str
    token: str
    hostname: str
    project_root: str
    label: str
    kind: str
    backend_port: int
    policy: Mapping[str, object]
    instance: str | None = None

    def authorized(self, bearer: str) -> bool:
        return isinstance(bearer, str) and hmac.compare_digest(self.token, bearer)


class ActivationCatalog:
    def __init__(self, routes: tuple[ActivationRoute, ...], issues: tuple[str, ...] = ()) -> None:
        by_id: dict[str, ActivationRoute] = {}
        by_host: dict[str, ActivationRoute] = {}
        for route in routes:
            if route.route_id in by_id or route.hostname in by_host:
                raise ActivationCatalogError("activation route collision")
            by_id[route.route_id] = route
            by_host[route.hostname] = route
        self._by_id = MappingProxyType(by_id)
        self._by_host = MappingProxyType(by_host)
        self._issues = tuple(issues)

    def get(self, route_id: str) -> ActivationRoute | None:
        return self._by_id.get(route_id)

    def for_host(self, hostname: str) -> ActivationRoute | None:
        return self._by_host.get(hostname.lower() if isinstance(hostname, str) else "")

    def routes(self) -> tuple[ActivationRoute, ...]:
        return tuple(self._by_id.values())

    def issues(self) -> tuple[str, ...]:
        """Non-secret, route-local metadata issues quarantined from the catalog."""
        return self._issues


def build_catalog(records: Mapping[str, Mapping[str, object]],
                  wordpress_instances: Mapping[str, Mapping[str, object]] | None = None) -> ActivationCatalog:
    """Build from registry snapshots plus registry-owned WordPress instance blocks.

    Non-opted-in entries are ignored. An opted-in malformed entry invalidates
    the candidate catalog so callers can keep the previously active Caddyfile.
    """
    wp = wordpress_instances or {}
    routes: list[ActivationRoute] = []
    issues: list[str] = []
    for record in records.values():
        if not isinstance(record, Mapping):
            continue
        instance = record.get("instance")
        block = wp.get(str(instance), {}) if instance else {}
        lifecycle_raw = block.get("instance_lifecycle", record.get("instanceLifecycle"))
        # Default adoption happens through normal ensure/apply reconciliation,
        # which persists the normalized marker and route identity atomically.
        # Merely upgrading Sandbox must never place a legacy/public registry
        # row behind request wake or make it eligible for later suspension.
        if not isinstance(lifecycle_raw, Mapping):
            continue
        policy = normalize_instance_lifecycle(lifecycle_raw)
        if policy["mode"] != "idle_stop" or not policy["wakeOnRequest"]:
            continue
        kind = record.get("kind", "wordpress")
        if kind not in {"wordpress", "compose"} or block.get("server") == "herd":
            raise ActivationCatalogError("activation runtime is unsupported")
        if kind == "wordpress" and (block.get("wp_config") or {}).get("DISABLE_WP_CRON") is not True:
            # A deliberate background-work policy pins this instance on. It is
            # an ordinary ineligible route, not corruption of unrelated routes.
            continue
        aliases = block.get("aliases", record.get("aliases"))
        if aliases:
            raise ActivationCatalogError("activation routes do not support aliases")
        hostname = block.get("domain", record.get("domain"))
        port = block.get("wordpress_port", record.get("http_port"))
        credentials = block.get("activation_route", record.get("activation_route"))
        root, label = record.get("root"), record.get("label", "default")
        if (not isinstance(hostname, str) or not _HOST.fullmatch(hostname.lower()) or
                isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535 or
                not isinstance(root, str) or not root or not isinstance(label, str) or not label):
            # A stale registry row must not disable request wake for every
            # otherwise valid instance. An incomplete route is quarantined and
            # remains unreachable through the activation authority; callers can
            # surface this count without exposing paths or credentials.
            issues.append("activation route metadata is invalid")
            continue
        if not isinstance(credentials, Mapping):
            raise ActivationCatalogError("activation route credentials are invalid")
        route_id, token = credentials.get("id"), credentials.get("token")
        if not isinstance(route_id, str) or not _ROUTE.fullmatch(route_id) or not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise ActivationCatalogError("activation route credentials are invalid")
        routes.append(ActivationRoute(route_id, token, hostname.lower(), root, label,
                                      str(kind), port, MappingProxyType(dict(policy)),
                                      str(instance) if instance else None))
    return ActivationCatalog(tuple(routes), tuple(issues))


__all__ = ["ActivationCatalog", "ActivationCatalogError", "ActivationRoute", "build_catalog"]
