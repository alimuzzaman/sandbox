"""Bounded, cached HTTPS health checks for the panel's server rows (spec 076).

One cache per `sb web` process. `snapshot()` never waits: it returns what is
cached (or "checking") and hands stale hostnames to a small worker pool, so
there is at most one request in flight per hostname and at most one per TTL.
Only strict hostnames from Sandbox records are probed; redirects are never
followed and no credentials are sent.
"""
from __future__ import annotations

import http.client
import ipaddress
import re
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Literal, TypedDict

HealthStatus = Literal["checking", "up", "up_login", "redirect", "client_error",
                       "server_error", "unreachable", "not_checked"]

DEFAULT_TTL_SECONDS = 60
DEFAULT_TIMEOUT_SECONDS = 4
DEFAULT_MAX_HOSTS = 64
DEFAULT_WORKERS = 4
USER_AGENT = "sandbox-panel-health/1"

_HOST_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z")
_PATH_RE = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@/%-]{0,499}\Z")


class HealthResult(TypedDict):
    status: HealthStatus
    http_status: int | None
    checked_at: int | None


CHECKING: HealthResult = {"status": "checking", "http_status": None, "checked_at": None}
NOT_CHECKED: HealthResult = {"status": "not_checked", "http_status": None, "checked_at": None}


def probe_allowed(hostname: str) -> bool:
    """A probe target is a plain DNS name: no wildcard, port, userinfo or IP literal."""
    if not isinstance(hostname, str) or len(hostname) > 253 or not _HOST_RE.fullmatch(hostname):
        return False
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return False


def classify(status: int) -> HealthStatus:
    if 200 <= status < 300:
        return "up"
    if 300 <= status < 400:
        return "redirect"
    if status in (401, 403):
        return "up_login"
    if 400 <= status < 500:
        return "client_error"
    return "server_error"


def probe(hostname: str, path: str, timeout: float, *, wall: Callable[[], float] = time.time,
          connection_class=http.client.HTTPSConnection) -> HealthResult:
    """One GET over verified HTTPS. `http.client` never follows redirects."""
    checked_at = int(wall())
    connection = connection_class(hostname, 443, timeout=timeout,
                                  context=ssl.create_default_context())
    try:
        connection.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept": "*/*",
                                                 "Connection": "close"})
        response = connection.getresponse()
        status = int(response.status)
    except (OSError, http.client.HTTPException, ValueError):
        return {"status": "unreachable", "http_status": None, "checked_at": checked_at}
    finally:
        connection.close()
    return {"status": classify(status), "http_status": status, "checked_at": checked_at}


class HealthCache:
    def __init__(self, *, ttl: float = DEFAULT_TTL_SECONDS, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 max_hosts: int = DEFAULT_MAX_HOSTS, workers: int = DEFAULT_WORKERS,
                 probe_fn: Callable[[str, str, float], HealthResult] = probe,
                 clock: Callable[[], float] = time.monotonic,
                 submit: Callable[[Callable[[], None]], object] | None = None):
        self._ttl = ttl
        self._timeout = timeout
        self._max_hosts = max_hosts
        self._probe = probe_fn
        self._clock = clock
        self._lock = threading.Lock()
        self._results: dict[str, tuple[float, HealthResult]] = {}
        self._in_flight: set[str] = set()
        self._executor: ThreadPoolExecutor | None = None
        self._workers = workers
        self._submit = submit or self._pool_submit

    def _pool_submit(self, job: Callable[[], None]) -> object:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self._workers,
                                                thread_name_prefix="sandbox-panel-health")
        return self._executor.submit(job)

    def _run(self, hostname: str, path: str) -> None:
        try:
            result = self._probe(hostname, path, self._timeout)
        except Exception:
            result = {"status": "unreachable", "http_status": None, "checked_at": None}
        with self._lock:
            self._results[hostname] = (self._clock(), result)
            self._in_flight.discard(hostname)

    def snapshot(self, targets: list[tuple[str, str]]) -> dict[str, HealthResult]:
        """Cached health for each (hostname, path); stale ones are refreshed in the background."""
        out: dict[str, HealthResult] = {}
        due: list[tuple[str, str]] = []
        now = self._clock()
        seen: set[str] = set()
        eligible = 0
        with self._lock:
            for hostname, path in targets:
                if hostname in seen:
                    continue
                seen.add(hostname)
                allowed = probe_allowed(hostname)
                eligible += allowed
                if not allowed or eligible > self._max_hosts:
                    out[hostname] = dict(NOT_CHECKED)  # type: ignore[assignment]
                    continue
                cached = self._results.get(hostname)
                out[hostname] = dict(cached[1]) if cached else dict(CHECKING)  # type: ignore[assignment]
                if (cached is None or now - cached[0] >= self._ttl) and hostname not in self._in_flight:
                    self._in_flight.add(hostname)
                    due.append((hostname, path if _PATH_RE.fullmatch(path or "") else "/"))
        for hostname, path in due:
            try:
                self._submit(lambda hostname=hostname, path=path: self._run(hostname, path))
            except RuntimeError:
                with self._lock:
                    self._in_flight.discard(hostname)
        return out
