"""Verified, bounded standard-library HTTPS transport for provider calls."""

from __future__ import annotations

import http.client
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping

from .base import (
    Deadline,
    HttpResponse,
    MAX_PROVIDER_RESPONSE_BYTES,
    ProviderError,
)


_STATUS_PATH = "/api/status"
_LOGIN_PATH = "/api/v1/auth/universal-auth/login"
_SECRET_PREFIX = "/api/v4/secrets/"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _safe_origin_and_route(url: str, method: str) -> tuple[str, str]:
    if not isinstance(url, str) or any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
        raise ProviderError("endpoint_untrusted")
    invalid_url = False
    parsed = None
    port = None
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        invalid_url = True
        host = None
    if invalid_url or parsed is None:
        raise ProviderError("endpoint_untrusted")
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.netloc
        or (parsed.netloc.endswith(":") and port is None)
        or (port is not None and not 1 <= port <= 65_535)
    ):
        raise ProviderError("endpoint_untrusted")
    if parsed.path == _STATUS_PATH and method == "GET" and not parsed.query:
        route = "status"
    elif parsed.path == _LOGIN_PATH and method == "POST" and not parsed.query:
        route = "login"
    elif (
        parsed.path.startswith(_SECRET_PREFIX)
        and method == "GET"
        and "/" not in parsed.path[len(_SECRET_PREFIX):]
        and bool(parsed.query)
    ):
        route = "secret"
    else:
        raise ProviderError("provider_unsupported")
    origin = urllib.parse.urlunsplit(("https", parsed.netloc, "", "", ""))
    return origin, route


def _failure_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if code in {
        "provider_unsupported", "endpoint_unreachable", "endpoint_untrusted",
        "response_too_large", "operation_timeout", "provider_failed",
    }:
        return code
    observed: BaseException | None = exc
    for _ in range(4):
        if observed is None:
            break
        if isinstance(observed, (ssl.SSLError, ssl.CertificateError)):
            return "endpoint_untrusted"
        if isinstance(observed, (TimeoutError, socket.timeout)):
            return "operation_timeout"
        if isinstance(observed, urllib.error.URLError):
            observed = observed.reason if isinstance(observed.reason, BaseException) else None
            continue
        if isinstance(observed, (socket.gaierror, ConnectionError, OSError, http.client.HTTPException)):
            return "endpoint_unreachable"
        observed = observed.__cause__ or observed.__context__
    return "provider_failed"


def _set_socket_timeout(response: object, seconds: float) -> None:
    """Refresh the underlying socket bound before each body read when available."""

    try:
        sock = response.fp.raw._sock  # type: ignore[attr-defined]
        sock.settimeout(seconds)
    except (AttributeError, OSError, ValueError):
        # The initial opener timeout remains in force for non-CPython responses.
        return


# Fixed and non-identifying. Without it urllib sends its default agent, which
# edge proxies such as Cloudflare's browser integrity check refuse with 403.
USER_AGENT = "sandbox-secret-manager/1"


class BoundedHTTPTransport:
    """HTTPS-only opener with OS trust, no proxy inheritance, and a 1 MiB cap."""

    def __init__(self) -> None:
        context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
            urllib.request.HTTPSHandler(context=context, check_hostname=True),
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | bytearray | None = None,
        deadline: Deadline,
        capture_body: bool = True,
    ) -> HttpResponse:
        method = method.upper() if isinstance(method, str) else ""
        _origin, route = _safe_origin_and_route(url, method)
        if not isinstance(capture_body, bool):
            raise ProviderError("provider_unsupported")
        request_headers = dict(headers or {})
        if any(
            not isinstance(name, str)
            or not isinstance(value, str)
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in name + value)
            for name, value in request_headers.items()
        ):
            raise ProviderError("provider_unsupported")
        if route == "status" and (request_headers or body is not None):
            raise ProviderError("provider_unsupported")
        if route == "login" and (
            "Authorization" in request_headers
            or not isinstance(body, (bytes, bytearray))
        ):
            raise ProviderError("provider_unsupported")
        if route == "secret" and (
            "Authorization" not in request_headers
            or body is not None
        ):
            raise ProviderError("provider_unsupported")
        if body is not None and not isinstance(body, (bytes, bytearray)):
            raise ProviderError("provider_unsupported")

        req = urllib.request.Request(
            url, data=body, headers={**request_headers, "User-Agent": USER_AGENT}, method=method,
        )
        response = None
        result: HttpResponse | None = None
        failure: str | None = None
        try:
            try:
                response = self._opener.open(req, timeout=deadline.remaining())
            except urllib.error.HTTPError as exc:
                status = exc.code
                exc.close()
                if 300 <= status < 400:
                    failure = "endpoint_untrusted"
                else:
                    # Error bodies are intentionally not read or retained.
                    result = HttpResponse(status=status)
            except Exception as exc:
                failure = _failure_code(exc)

            if failure is None and result is None and response is not None:
                status = int(response.status)
                if not 200 <= status < 300:
                    result = HttpResponse(status=status)
                elif not capture_body:
                    result = HttpResponse(status=status)
                else:
                    body_buffer = bytearray()
                    try:
                        while True:
                            remaining = deadline.remaining()
                            _set_socket_timeout(response, remaining)
                            chunk = response.read(min(65_536, MAX_PROVIDER_RESPONSE_BYTES + 1 - len(body_buffer)))
                            if not chunk:
                                break
                            body_buffer.extend(chunk)
                            if len(body_buffer) > MAX_PROVIDER_RESPONSE_BYTES:
                                failure = "response_too_large"
                                break
                        if failure is None:
                            deadline.check()
                            result = HttpResponse(status=status, body=body_buffer)
                        else:
                            _wipe(body_buffer)
                            chunk = b""
                    except Exception as exc:
                        _wipe(body_buffer)
                        chunk = b""
                        failure = _failure_code(exc)
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
                response = None

        if failure is not None:
            raise ProviderError(failure)
        if result is None:
            raise ProviderError("provider_failed")
        return result


def _wipe(buffer: bytearray) -> None:
    for index in range(len(buffer)):
        buffer[index] = 0
    buffer.clear()
