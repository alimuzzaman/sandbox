"""Minimal fixed HTTP contract for stock Caddy ``forward_auth``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from .catalog import ActivationCatalog
from .service import ActivationService


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes = b""


class ActivationHTTPApplication:
    def __init__(self, catalog: ActivationCatalog, service: ActivationService,
                 resume: Callable[[object, int], bool]) -> None:
        self.catalog, self.service, self.resume = catalog, service, resume
        for route in catalog.routes():
            policy = route.policy
            from .coordinator import ActivationPolicy
            service.register(route.route_id, ActivationPolicy(
                mode=str(policy["mode"]), wake_on_request=True,
                idle_after_seconds=int(policy["idleAfterSeconds"]),
                wake_timeout_seconds=int(policy["wakeTimeoutSeconds"]),
                stop_grace_seconds=int(policy["stopGraceSeconds"]),
                max_pending_requests=int(policy["maxPendingRequests"]),
            ))

    @staticmethod
    def _response(status: int, *, retry: int | None = None) -> HTTPResponse:
        headers = {"Cache-Control": "no-store", "Content-Length": "0"}
        if retry is not None:
            headers["Retry-After"] = str(max(1, retry))
        return HTTPResponse(status, headers)

    def handle(self, method: str, path: str, headers: Mapping[str, str], body: bytes = b"") -> HTTPResponse:
        if path == "/healthz" and method == "GET" and not body:
            return self._response(204)
        if path != "/v1/activate" or "?" in path:
            return self._response(404)
        if method != "GET":
            return self._response(405)
        if body or headers.get("Content-Length", "0") not in {"", "0"}:
            return self._response(400)
        route_id = headers.get("X-Sandbox-Route-ID", "")
        route = self.catalog.get(route_id)
        auth = headers.get("Authorization", "")
        if route is None or not auth.startswith("Bearer ") or not route.authorized(auth[7:]):
            return self._response(404)
        result = self.service.activate(
            route.route_id,
            resume=lambda _route_id, timeout: self.resume(route, timeout),
        )
        if result.ok:
            return self._response(204)
        return self._response(503, retry=2)


__all__ = ["ActivationHTTPApplication", "HTTPResponse"]
