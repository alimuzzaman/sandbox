from typing import Any, Callable, Mapping, Protocol

class ProxyManager(Protocol):
    def plan(self, hostname: str, port: int) -> Mapping[str, Any]: ...
    def apply(self, plan: Mapping[str, Any]) -> None: ...
    def remove(self, hostname: str) -> None: ...


class CallbackProxyManager:
    """Small transactional adapter over an existing proxy implementation."""

    def __init__(self, *, apply_route: Callable, remove_route: Callable) -> None:
        self._apply_route = apply_route
        self._remove_route = remove_route

    def plan(self, hostname: str, port: int) -> Mapping[str, Any]:
        if not hostname or port < 1 or port > 65535:
            raise ValueError("valid hostname and port are required")
        return {"hostname": hostname, "port": port}

    def apply(self, plan: Mapping[str, Any]) -> None:
        hostname = str(plan["hostname"])
        try:
            self._apply_route(hostname, int(plan["port"]))
        except Exception:
            self._remove_route(hostname)
            raise

    def remove(self, hostname: str) -> None:
        self._remove_route(hostname)
