import re
from collections.abc import Mapping
from typing import Any, Callable, Protocol

_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def _valid_hostname(hostname: object) -> bool:
    if not isinstance(hostname, str) or not 1 <= len(hostname) <= 253:
        return False
    if hostname.endswith("."):
        hostname = hostname[:-1]
    return bool(hostname) and all(
        len(label) <= 63 and _HOST_LABEL.fullmatch(label)
        for label in hostname.split(".")
    )


def _validate_route(hostname: object, port: object) -> tuple[str, int]:
    if (not _valid_hostname(hostname) or isinstance(port, bool) or
            not isinstance(port, int) or not 1 <= port <= 65535):
        raise ValueError("valid hostname and port are required")
    return hostname, port

class ProxyManager(Protocol):
    def plan(self, hostname: str, port: int) -> Mapping[str, Any]: ...
    def apply(self, plan: Mapping[str, Any]) -> None: ...
    def remove(self, hostname: str) -> None: ...


class CallbackProxyManager:
    """Small transactional adapter over an existing proxy implementation."""

    def __init__(
        self, *, apply_route: Callable, remove_route: Callable,
        validate_plan: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self._apply_route = apply_route
        self._remove_route = remove_route
        self._validate_plan = validate_plan

    def plan(self, hostname: str, port: int) -> Mapping[str, Any]:
        hostname, port = _validate_route(hostname, port)
        plan = {"hostname": hostname, "port": port}
        if self._validate_plan is not None:
            self._validate_plan(plan)
        return plan

    def apply(self, plan: Mapping[str, Any]) -> None:
        if not isinstance(plan, Mapping) or set(plan) != {"hostname", "port"}:
            raise ValueError("proxy plan is invalid")
        hostname, port = _validate_route(plan["hostname"], plan["port"])
        if self._validate_plan is not None:
            self._validate_plan(plan)
        try:
            self._apply_route(hostname, port)
        except Exception:
            try:
                self._remove_route(hostname)
            except Exception:
                # Applying a route is the primary failure; rollback is best effort
                # and must not hide the operation that requires operator action.
                pass
            raise

    def remove(self, hostname: str) -> None:
        _validate_route(hostname, 1)
        self._remove_route(hostname)
