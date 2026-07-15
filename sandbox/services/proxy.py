from typing import Any, Callable, Mapping, Protocol

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
        if not hostname or port < 1 or port > 65535:
            raise ValueError("valid hostname and port are required")
        plan = {"hostname": hostname, "port": port}
        if self._validate_plan is not None:
            self._validate_plan(plan)
        return plan

    def apply(self, plan: Mapping[str, Any]) -> None:
        if self._validate_plan is not None:
            self._validate_plan(plan)
        hostname = str(plan["hostname"])
        try:
            self._apply_route(hostname, int(plan["port"]))
        except Exception:
            try:
                self._remove_route(hostname)
            except Exception:
                # Applying a route is the primary failure; rollback is best effort
                # and must not hide the operation that requires operator action.
                pass
            raise

    def remove(self, hostname: str) -> None:
        self._remove_route(hostname)
