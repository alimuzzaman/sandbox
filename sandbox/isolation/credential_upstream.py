"""Pinned, bounded HTTPS transport for the Credential Vault broker.

The request broker owns admission and lease lifetime.  This module owns only
the last hop: resolve a reviewed DNS name, connect to one of the public
addresses observed for that request, validate TLS for the original hostname,
and apply the binding's registered authentication profile.  It deliberately
does not follow redirects or expose a generic proxy interface.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import http.client
import ipaddress
import re
import socket
import ssl
import time
from typing import Any

from .credential_binding import ALLOWED_AUTH_FORMS, CredentialBinding, canonical_host, canonical_path


MAX_CONNECT_SECONDS = 5.0
MAX_TOTAL_SECONDS = 30.0
MAX_IDLE_SECONDS = 5.0
MAX_RESPONSE_BODY = 4 * 1024 * 1024
MAX_REQUEST_BODY = 1 * 1024 * 1024
MAX_REQUEST_HEADERS = 64 * 1024
MAX_CREDENTIAL_BYTES = 65_536

_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_SAFE_HEADER_VALUE = re.compile(r"^[\x20-\x7e\x80-\xff]*$")
_CREDENTIAL_VALUE = re.compile(r"^[\x21-\x7e]+$")
_SENSITIVE_HEADERS = frozenset({
    "authorization", "proxy-authorization", "proxy-authenticate", "x-api-key",
    "api-key", "host", "content-length", "transfer-encoding", "connection",
    "keep-alive", "te", "trailer", "upgrade", "proxy-connection",
})
ALLOWED_RESPONSE_HEADERS = frozenset({
    "cache-control", "content-language", "content-type", "etag",
    "last-modified", "retry-after",
})


def _safe_code(value: Any, fallback: str = "upstream_failed") -> str:
    if isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", value):
        return value
    return fallback


def _safe_message(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    value = value.replace("\r", " ").replace("\n", " ")
    if not value or len(value) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return fallback
    return value


class CredentialUpstreamError(RuntimeError):
    """Bounded, non-secret upstream failure."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = _safe_code(code)
        self.message = _safe_message(message, "credential upstream request failed")
        self.retryable = bool(retryable)
        super().__init__(self.message)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _safe_header(value: Any, *, name: str) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("latin-1")
        except UnicodeDecodeError:
            raise CredentialUpstreamError("request_header_invalid", "request header is invalid") from None
    if not isinstance(value, str) or len(value) > MAX_REQUEST_HEADERS or not _SAFE_HEADER_VALUE.fullmatch(value):
        raise CredentialUpstreamError("request_header_invalid", "request header is invalid")
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        raise CredentialUpstreamError("request_header_invalid", "request header is invalid") from None
    if name in _SENSITIVE_HEADERS:
        raise CredentialUpstreamError("request_header_denied", "request security header is denied")
    return value


def _normalize_request_headers(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CredentialUpstreamError("request_headers_invalid", "request headers are invalid")
    result: dict[str, str] = {}
    total = 0
    for raw_name, raw_value in value.items():
        if not isinstance(raw_name, str) or not _HEADER_NAME.fullmatch(raw_name):
            raise CredentialUpstreamError("request_header_invalid", "request header is invalid")
        name = raw_name.lower()
        if name in result:
            raise CredentialUpstreamError("request_duplicate_header", "duplicate request headers are denied")
        result[name] = _safe_header(raw_value, name=name)
        total += len(raw_name.encode("ascii")) + len(result[name].encode("latin-1")) + 4
        if total > MAX_REQUEST_HEADERS:
            raise CredentialUpstreamError("request_headers_too_large", "request headers exceed the upstream limit")
    return result


def _credential_header(binding: CredentialBinding, credential: bytes) -> tuple[str, str]:
    if not isinstance(binding, CredentialBinding) or binding.auth_form not in ALLOWED_AUTH_FORMS:
        raise CredentialUpstreamError("auth_form_denied", "credential authentication profile is not approved")
    if isinstance(credential, bytearray):
        credential = bytes(credential)
    if isinstance(credential, memoryview):
        credential = credential.tobytes()
    if not isinstance(credential, bytes) or not credential or len(credential) > MAX_CREDENTIAL_BYTES:
        raise CredentialUpstreamError("credential_invalid", "credential material is invalid")
    try:
        text = credential.decode("ascii")
    except UnicodeDecodeError:
        raise CredentialUpstreamError("credential_invalid", "credential material is invalid") from None
    if not _CREDENTIAL_VALUE.fullmatch(text):
        raise CredentialUpstreamError("credential_invalid", "credential material is invalid")
    if binding.auth_form in {"bearer", "authorization_bearer"}:
        return "authorization", "Bearer " + text
    return "x-api-key", text


def _public_address(value: Any) -> str:
    try:
        address = ipaddress.ip_address(str(value))
    except (TypeError, ValueError):
        raise CredentialUpstreamError("dns_invalid", "upstream DNS result is invalid") from None
    # The managed-native network grant model is IPv4 public-CIDR based.  Do not
    # silently widen the credential path to IPv6 or special-use destinations.
    if address.version != 4 or not address.is_global:
        raise CredentialUpstreamError("dns_not_public", "upstream DNS result is not a public address")
    return str(address)


def _resolve_public(resolver: Callable[[str], Iterable[str]], host: str, forbidden) -> tuple[str, ...]:
    if not callable(resolver):
        raise CredentialUpstreamError("dns_unavailable", "upstream DNS resolver is unavailable", retryable=True)
    try:
        values = resolver(host)
        if isinstance(values, (str, bytes)):
            raise ValueError
        addresses = tuple(dict.fromkeys(_public_address(value) for value in values))
    except CredentialUpstreamError:
        raise
    except Exception:
        raise CredentialUpstreamError("dns_unavailable", "upstream DNS resolution failed", retryable=True) from None
    if not addresses:
        raise CredentialUpstreamError("dns_empty", "upstream DNS returned no public address", retryable=True)
    if forbidden is not None:
        try:
            if callable(forbidden):
                try:
                    denied = forbidden(host, addresses)
                except TypeError:
                    # Existing egress policy observers expose a zero-argument
                    # forbidden-network callback.  Keep that seam compatible
                    # without turning an observer error into an allow.
                    denied = forbidden()
            else:
                denied = forbidden
            if isinstance(denied, (str, bytes)):
                denied = (denied,)
            networks = tuple(ipaddress.ip_network(value, strict=False) for value in (denied or ()))
        except Exception:
            raise CredentialUpstreamError("dns_policy_invalid", "upstream DNS policy is unavailable") from None
        for address in addresses:
            if any(ipaddress.ip_address(address) in network for network in networks):
                raise CredentialUpstreamError("dns_denied", "upstream DNS address is not authorized")
    return addresses


class _PinnedHttpsTransport:
    """Small HTTP/1.1 transport over an already-pinned TLS socket."""

    def __init__(self, sock: socket.socket, host: str, port: int, *, max_response_body: int) -> None:
        self.sock = sock
        self.host = host
        self.port = port
        self.max_response_body = max_response_body

    def request(self, method: str, path: str, headers: Mapping[str, str], body: bytes, timeout: float):
        self.sock.settimeout(timeout)
        lines = [f"{method} {path} HTTP/1.1\r\n"]
        for name, value in headers.items():
            lines.append(f"{name}: {value}\r\n")
        lines.append("\r\n")
        self.sock.sendall("".join(lines).encode("latin-1") + body)
        response = http.client.HTTPResponse(self.sock)
        try:
            response.begin()
            length = response.getheader("Content-Length")
            if length is not None:
                try:
                    declared = int(length)
                except (TypeError, ValueError):
                    raise CredentialUpstreamError("response_invalid", "upstream response length is invalid") from None
                if declared < 0 or declared > self.max_response_body:
                    raise CredentialUpstreamError("response_body_too_large", "upstream response exceeds the broker limit")
            body_value = response.read(self.max_response_body + 1)
            if len(body_value) > self.max_response_body:
                raise CredentialUpstreamError("response_body_too_large", "upstream response exceeds the broker limit")
            return {"status": response.status, "headers": dict(response.getheaders()), "body": body_value}
        except CredentialUpstreamError:
            raise
        except (OSError, http.client.HTTPException, socket.timeout, TimeoutError):
            raise CredentialUpstreamError(
                "upstream_indeterminate", "credential upstream outcome is indeterminate",
            ) from None
        finally:
            response.close()

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class VerifiedHttpsUpstream:
    """Originate one exact, certificate-validated HTTPS request."""

    def __init__(
        self,
        *,
        resolver: Callable[[str], Iterable[str]],
        connector: Callable[[str, str, int, float, ssl.SSLContext], Any] | None = None,
        ssl_context: ssl.SSLContext | None = None,
        forbidden=None,
        max_response_body: int = MAX_RESPONSE_BODY,
        max_request_body: int = MAX_REQUEST_BODY,
        max_request_headers: int = MAX_REQUEST_HEADERS,
        connect_seconds: float = MAX_CONNECT_SECONDS,
        total_seconds: float = MAX_TOTAL_SECONDS,
        idle_seconds: float = MAX_IDLE_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not callable(resolver):
            raise ValueError("credential upstream resolver is required")
        for value, maximum, label in (
            (max_response_body, MAX_RESPONSE_BODY, "response body"),
            (max_request_body, MAX_REQUEST_BODY, "request body"),
            (max_request_headers, MAX_REQUEST_HEADERS, "request headers"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ValueError(f"credential upstream {label} limit is invalid")
        for value, maximum, label in (
            (connect_seconds, MAX_CONNECT_SECONDS, "connect"),
            (total_seconds, MAX_TOTAL_SECONDS, "total"),
            (idle_seconds, MAX_IDLE_SECONDS, "idle"),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= maximum:
                raise ValueError(f"credential upstream {label} deadline is invalid")
        if connect_seconds > total_seconds:
            raise ValueError("credential upstream connect deadline exceeds total deadline")
        if ssl_context is not None:
            if not isinstance(ssl_context, ssl.SSLContext) or not ssl_context.check_hostname \
                    or ssl_context.verify_mode != ssl.CERT_REQUIRED:
                raise ValueError("credential upstream TLS validation is not enabled")
        if connector is not None and not callable(connector):
            raise ValueError("credential upstream connector is invalid")
        self.resolver = resolver
        self.connector = connector or self._connect
        self.ssl_context = ssl_context or ssl.create_default_context()
        self.forbidden = forbidden
        self.max_response_body = max_response_body
        self.max_request_body = max_request_body
        self.max_request_headers = max_request_headers
        self.connect_seconds = float(connect_seconds)
        self.total_seconds = float(total_seconds)
        self.idle_seconds = float(idle_seconds)
        self.clock = clock or time.monotonic

    def _connect(self, address: str, host: str, port: int, timeout: float, context: ssl.SSLContext):
        raw = None
        try:
            raw = socket.create_connection((address, port), timeout=timeout)
            raw.settimeout(timeout)
            wrapped = context.wrap_socket(raw, server_hostname=host)
            raw = None
            return _PinnedHttpsTransport(wrapped, host, port, max_response_body=self.max_response_body)
        except (OSError, ssl.SSLError, socket.timeout, TimeoutError):
            raise CredentialUpstreamError("tls_connect_failed", "credential upstream TLS connection failed", retryable=True) from None
        finally:
            if raw is not None:
                try:
                    raw.close()
                except OSError:
                    pass

    def _request_headers(self, request: Any, binding: CredentialBinding, credential: bytes) -> dict[str, str]:
        headers = _normalize_request_headers(_attr(request, "headers", {}))
        # The guest supplies application headers only.  The transport owns all
        # routing/framing/security headers so a caller cannot smuggle a second
        # destination, credential, or hop-by-hop instruction.
        path = _attr(request, "path")
        body = _attr(request, "body", b"")
        content_type = _attr(request, "content_type", None)
        try:
            host = canonical_host(_attr(request, "host"))
            canonical_path(path)
        except (TypeError, ValueError):
            raise CredentialUpstreamError("destination_invalid", "upstream request destination is invalid") from None
        if isinstance(body, bytearray):
            body = bytes(body)
        elif isinstance(body, memoryview):
            body = body.tobytes()
        if not isinstance(body, bytes) or len(body) > self.max_request_body:
            raise CredentialUpstreamError("request_body_too_large", "upstream request body exceeds the broker limit")
        headers["host"] = host
        headers["content-length"] = str(len(body))
        if content_type is not None:
            headers["content-type"] = _safe_header(content_type, name="content-type")
        name, value = _credential_header(binding, credential)
        headers[name] = value
        total = sum(len(key.encode("ascii")) + len(value.encode("latin-1")) + 4 for key, value in headers.items())
        if total > self.max_request_headers:
            raise CredentialUpstreamError("request_headers_too_large", "upstream request headers exceed the broker limit")
        return headers

    def request(self, binding: CredentialBinding, request: Any, credential: bytes):
        if not isinstance(binding, CredentialBinding):
            raise CredentialUpstreamError("binding_invalid", "credential binding is invalid")
        try:
            host = canonical_host(_attr(request, "host"))
            path = canonical_path(_attr(request, "path"))
        except (TypeError, ValueError):
            raise CredentialUpstreamError("destination_invalid", "upstream request destination is invalid") from None
        scheme = _attr(request, "scheme", "https")
        method = _attr(request, "method", "")
        if (not isinstance(scheme, str) or scheme.lower() != "https" or
                host != binding.host or _attr(request, "port") != binding.port or
                not isinstance(method, str) or method.upper() != binding.method or path != binding.path):
            raise CredentialUpstreamError("destination_mismatch", "upstream request destination does not match binding")
        body = _attr(request, "body", b"")
        if isinstance(body, bytearray):
            body = bytes(body)
        elif isinstance(body, memoryview):
            body = body.tobytes()
        if not isinstance(body, bytes) or len(body) > self.max_request_body:
            raise CredentialUpstreamError("request_body_too_large", "upstream request body exceeds the broker limit")
        headers = self._request_headers(request, binding, credential)
        requested_deadline = _attr(request, "deadline_ms", int(self.total_seconds * 1000))
        if isinstance(requested_deadline, bool) or not isinstance(requested_deadline, int) or requested_deadline < 1:
            raise CredentialUpstreamError("request_deadline_invalid", "upstream request deadline is invalid")
        total = min(self.total_seconds, requested_deadline / 1000.0)
        started = self.clock()
        deadline = started + total
        addresses = _resolve_public(self.resolver, host, self.forbidden)
        # Resolver output is pinned for this request.  There is no second DNS
        # lookup between policy validation and socket creation.
        address = addresses[0]
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise CredentialUpstreamError("upstream_timeout", "credential upstream request timed out", retryable=True)
        timeout = min(self.connect_seconds, self.idle_seconds, remaining)
        transport = None
        try:
            try:
                transport = self.connector(address, host, binding.port, timeout, self.ssl_context)
            except CredentialUpstreamError:
                raise
            except (OSError, ssl.SSLError, socket.timeout, TimeoutError):
                raise CredentialUpstreamError("tls_connect_failed", "credential upstream TLS connection failed", retryable=True) from None
            except Exception:
                raise CredentialUpstreamError("upstream_connect_failed", "credential upstream connection failed", retryable=True) from None
            if transport is None or not callable(getattr(transport, "request", None)):
                raise CredentialUpstreamError("upstream_transport_invalid", "credential upstream transport is invalid")
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise CredentialUpstreamError("upstream_timeout", "credential upstream request timed out", retryable=True)
            try:
                result = transport.request(
                    method.upper(), path, headers, body,
                    min(self.idle_seconds, remaining),
                )
            except CredentialUpstreamError:
                raise CredentialUpstreamError(
                    "upstream_indeterminate", "credential upstream outcome is indeterminate",
                ) from None
            except (OSError, socket.timeout, TimeoutError, http.client.HTTPException):
                raise CredentialUpstreamError(
                    "upstream_indeterminate", "credential upstream outcome is indeterminate",
                ) from None
            except Exception:
                raise CredentialUpstreamError(
                    "upstream_indeterminate", "credential upstream outcome is indeterminate",
                ) from None
            if not isinstance(result, Mapping):
                raise CredentialUpstreamError("response_invalid", "upstream response is invalid")
            status = result.get("status")
            if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
                raise CredentialUpstreamError("response_invalid", "upstream response status is invalid")
            if 300 <= status <= 399 or result.get("redirected") is True:
                raise CredentialUpstreamError("redirect_denied", "upstream redirects are denied")
            response_body = result.get("body", b"")
            if isinstance(response_body, bytearray):
                response_body = bytes(response_body)
            elif isinstance(response_body, memoryview):
                response_body = response_body.tobytes()
            if not isinstance(response_body, bytes):
                raise CredentialUpstreamError("response_invalid", "upstream response body is invalid")
            if len(response_body) > self.max_response_body:
                raise CredentialUpstreamError("response_body_too_large", "upstream response exceeds the broker limit")
            response_headers = result.get("headers", {})
            if not isinstance(response_headers, Mapping):
                raise CredentialUpstreamError("response_invalid", "upstream response headers are invalid")
            safe_headers: dict[str, str] = {}
            declared_length = None
            for raw_name, raw_value in response_headers.items():
                if not isinstance(raw_name, str) or not _HEADER_NAME.fullmatch(raw_name):
                    continue
                name = raw_name.lower()
                if name == "content-length":
                    try:
                        declared_length = _safe_header(raw_value, name="response")
                    except CredentialUpstreamError:
                        raise CredentialUpstreamError(
                            "response_invalid", "upstream response length is invalid",
                        ) from None
                    continue
                if name not in ALLOWED_RESPONSE_HEADERS:
                    continue
                try:
                    safe_headers[name] = _safe_header(raw_value, name="response")
                except CredentialUpstreamError:
                    continue
            if declared_length is not None:
                try:
                    if int(declared_length) < 0 or int(declared_length) > self.max_response_body:
                        raise CredentialUpstreamError(
                            "response_body_too_large", "upstream response exceeds the broker limit",
                        )
                except ValueError:
                    raise CredentialUpstreamError(
                        "response_invalid", "upstream response length is invalid",
                    ) from None
            return {"status": status, "headers": safe_headers, "body": response_body}
        finally:
            close = getattr(transport, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


__all__ = [
    "ALLOWED_RESPONSE_HEADERS", "CredentialUpstreamError", "VerifiedHttpsUpstream", "MAX_CONNECT_SECONDS",
    "MAX_IDLE_SECONDS", "MAX_REQUEST_BODY", "MAX_REQUEST_HEADERS", "MAX_RESPONSE_BODY",
    "MAX_TOTAL_SECONDS",
]
