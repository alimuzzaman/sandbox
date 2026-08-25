"""Minimal fixed HTTP contract for stock Caddy ``forward_auth``."""

from __future__ import annotations

from dataclasses import dataclass
import threading
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
                 resume: Callable[[object, int], bool],
                 catalog_provider: Callable[[], ActivationCatalog] | None = None) -> None:
        self.catalog, self.service, self.resume = catalog, service, resume
        self._catalog_provider = catalog_provider
        self._catalog_lock = threading.RLock()
        self._register_catalog(catalog)

    def _register_catalog(self, catalog: ActivationCatalog) -> None:
        """Publish route policies without exposing route credentials.

        The activation process is supervised and can outlive instance
        creation/deletion.  Registering the current snapshot into the shared
        coordinator keeps newly discovered routes usable while preserving
        in-flight state for route IDs that remain present.
        """
        self.catalog = catalog
        for route in catalog.routes():
            policy = route.policy
            from .coordinator import ActivationPolicy
            self.service.register(route.route_id, ActivationPolicy(
                mode=str(policy["mode"]), wake_on_request=True,
                idle_after_seconds=int(policy["idleAfterSeconds"]),
                wake_timeout_seconds=int(policy["wakeTimeoutSeconds"]),
                stop_grace_seconds=int(policy["stopGraceSeconds"]),
                max_pending_requests=int(policy["maxPendingRequests"]),
            ))

    def _refresh_catalog(self) -> bool:
        provider = self._catalog_provider
        if provider is None:
            return True
        try:
            catalog = provider()
            if not isinstance(catalog, ActivationCatalog):
                raise TypeError("activation catalog provider returned an invalid catalog")
        except Exception:
            # A failed or invalid registry/config read must never leave an old
            # credential-bearing route authorized.  Activation requests remain
            # a generic deny below; the scheduler pins on the same condition.
            with self._catalog_lock:
                self._register_catalog(ActivationCatalog(()))
            return False
        with self._catalog_lock:
            self._register_catalog(catalog)
        return True

    @staticmethod
    def _response(status: int, *, retry: int | None = None) -> HTTPResponse:
        headers = {"Cache-Control": "no-store", "Content-Length": "0"}
        if retry is not None:
            headers["Retry-After"] = str(max(1, retry))
        return HTTPResponse(status, headers)

    def handle(self, method: str, path: str, headers: Mapping[str, str], body: bytes = b"") -> HTTPResponse:
        if path == "/healthz" and method == "GET" and not body:
            # Keep liveness independent of registry/config I/O. Supervisors
            # use a sub-second probe here; catalog freshness is enforced on
            # authenticated activation requests and by the scheduler.
            return self._response(204)
        if path != "/v1/activate" or "?" in path:
            return self._response(404)
        if method != "GET":
            return self._response(405)
        if body or headers.get("Content-Length", "0") not in {"", "0"}:
            return self._response(400)
        self._refresh_catalog()
        with self._catalog_lock:
            catalog = self.catalog
        route_id = headers.get("X-Sandbox-Route-ID", "")
        route = catalog.get(route_id)
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
