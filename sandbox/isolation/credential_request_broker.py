"""Explicit, bounded application-layer broker for Credential Vault requests.

The broker is deliberately separate from :mod:`egress_broker`: the existing
egress broker authorizes TCP CONNECT, while this module authorizes one exact
HTTP operation and applies a credential only inside a trusted callback.  The
default dependencies refuse use until a caller supplies proof, egress, and an
upstream implementation explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Callable, Mapping
import re
import threading
import time
import uuid
from typing import Any

from sandbox.secrets.models import SecretBrokerError

from .credential_binding import (
    ALLOWED_AUTH_FORMS, ALLOWED_METHODS, CredentialBinding, canonical_host,
    canonical_path,
)
from .credential_upstream import CredentialUpstreamError


MAX_REQUEST_HEADERS = 64 * 1024
MAX_REQUEST_BODY = 1 * 1024 * 1024
MAX_RESPONSE_BODY = 4 * 1024 * 1024
MAX_CONCURRENT_REQUESTS = 16
MAX_CONNECT_SECONDS = 5
MAX_TOTAL_SECONDS = 30
MAX_IDLE_SECONDS = 5

_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_SAFE_HEADER_VALUE = re.compile(r"^[^\x00-\x1f\x7f]*$")
_SENSITIVE_HEADERS = frozenset({
    "authorization", "proxy-authorization", "proxy-authenticate",
    "host", "connection", "keep-alive", "transfer-encoding",
    "te", "trailer", "upgrade", "content-length", "x-api-key",
    "api-key", "proxy-connection",
})


def _safe_code(value: Any, fallback: str = "broker_failed") -> str:
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


class CredentialBrokerError(RuntimeError):
    """Stable, bounded broker refusal with no source or upstream diagnostics."""

    def __init__(self, code: str, message: str, *, retryable: bool = False,
                 correlation_id: str | None = None) -> None:
        self.code = _safe_code(code)
        self.message = _safe_message(message, "credential broker request failed")
        self.retryable = bool(retryable)
        self.correlation_id = correlation_id
        super().__init__(self.message)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "correlation_id": self.correlation_id,
        }


def _correlation(value: Any) -> str:
    if value is None or value == "":
        return f"corr-{uuid.uuid4().hex}"
    if not isinstance(value, str) or not _CORRELATION_ID.fullmatch(value):
        raise CredentialBrokerError("correlation_invalid", "request correlation ID is invalid")
    return value


def _header_value(value: Any, *, response: bool = False) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError:
            raise CredentialBrokerError(
                "response_header_invalid" if response else "request_header_invalid",
                "header value is not valid ASCII",
            ) from None
    if not isinstance(value, str) or len(value) > MAX_REQUEST_HEADERS:
        raise CredentialBrokerError(
            "response_header_invalid" if response else "request_header_invalid",
            "header value is invalid",
        )
    if not _SAFE_HEADER_VALUE.fullmatch(value):
        raise CredentialBrokerError(
            "response_header_invalid" if response else "request_header_invalid",
            "header value contains control characters",
        )
    return value


def _normalize_headers(value: Any, *, response: bool = False) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CredentialBrokerError(
            "response_headers_invalid" if response else "request_headers_invalid",
            "headers must be an object",
        )
    normalized: dict[str, str] = {}
    total = 0
    for raw_name, raw_value in value.items():
        if not isinstance(raw_name, str) or not _HEADER_NAME.fullmatch(raw_name):
            raise CredentialBrokerError(
                "response_header_invalid" if response else "request_header_invalid",
                "header name is invalid",
            )
        name = raw_name.lower()
        if name in normalized:
            raise CredentialBrokerError(
                "response_duplicate_header" if response else "request_duplicate_header",
                "duplicate header names are not allowed",
            )
        if not response and name in _SENSITIVE_HEADERS:
            raise CredentialBrokerError(
                "request_header_denied", "guest authentication or hop-by-hop header is denied",
            )
        if isinstance(raw_value, (str, bytes)) and len(raw_value) > MAX_REQUEST_HEADERS:
            raise CredentialBrokerError(
                "response_headers_too_large" if response else "request_headers_too_large",
                "headers exceed the broker limit",
            )
        text = _header_value(raw_value, response=response)
        total += len(raw_name.encode("ascii")) + len(text.encode("utf-8")) + 4
        if total > MAX_REQUEST_HEADERS:
            raise CredentialBrokerError(
                "response_headers_too_large" if response else "request_headers_too_large",
                "headers exceed the broker limit",
            )
        normalized[name] = text
    return normalized


@dataclass(frozen=True, repr=False)
class BrokerRequest:
    """Canonical request shape accepted by the explicit broker."""

    binding_id: str
    binding_version: int
    scheme: str
    host: str
    port: int
    method: str
    path: str
    headers: Mapping[str, str]
    body: bytes = b""
    content_type: str | None = None
    deadline_ms: int = MAX_TOTAL_SECONDS * 1000
    correlation_id: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BrokerRequest":
        fields = {
            "binding_id", "binding_version", "scheme", "host", "port", "method",
            "path", "headers", "body", "content_type", "deadline_ms", "correlation_id",
        }
        if not isinstance(value, Mapping) or not set(value) <= fields:
            raise CredentialBrokerError("request_shape_invalid", "request fields are invalid")
        required = {"binding_id", "binding_version", "scheme", "host", "port", "method", "path"}
        if not required <= set(value):
            raise CredentialBrokerError("request_shape_invalid", "required request fields are missing")
        binding_id = value["binding_id"]
        if not isinstance(binding_id, str) or not _CORRELATION_ID.fullmatch(binding_id):
            raise CredentialBrokerError("binding_invalid", "credential binding identity is invalid")
        version = value["binding_version"]
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise CredentialBrokerError("binding_invalid", "credential binding version is invalid")
        scheme = value["scheme"]
        if not isinstance(scheme, str) or scheme.lower() != "https":
            raise CredentialBrokerError("request_scope_invalid", "request scope must use HTTPS")
        try:
            host = canonical_host(value["host"])
            path = canonical_path(value["path"])
        except (TypeError, ValueError):
            raise CredentialBrokerError("request_scope_invalid", "request scope is invalid") from None
        port = value["port"]
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise CredentialBrokerError("request_scope_invalid", "request port is invalid")
        method = value["method"]
        if not isinstance(method, str):
            raise CredentialBrokerError("request_method_invalid", "request method is invalid")
        method = method.upper()
        if method not in ALLOWED_METHODS:
            raise CredentialBrokerError("request_method_invalid", "request method is not approved")
        body = value.get("body", b"")
        if isinstance(body, bytearray):
            body = bytes(body)
        elif isinstance(body, memoryview):
            body = body.tobytes()
        if not isinstance(body, bytes):
            raise CredentialBrokerError("request_body_invalid", "request body must be bytes")
        if len(body) > MAX_REQUEST_BODY:
            raise CredentialBrokerError("request_body_too_large", "request body exceeds the broker limit")
        content_type = value.get("content_type")
        if content_type is not None:
            content_type = _header_value(content_type)
            if len(content_type) > 1024:
                raise CredentialBrokerError("request_content_type_invalid", "request content type is invalid")
        deadline = value.get("deadline_ms", MAX_TOTAL_SECONDS * 1000)
        if isinstance(deadline, bool) or not isinstance(deadline, int) or not 1 <= deadline <= MAX_TOTAL_SECONDS * 1000:
            raise CredentialBrokerError("request_deadline_invalid", "request deadline exceeds the broker limit")
        correlation_id = _correlation(value.get("correlation_id"))
        headers = _normalize_headers(value.get("headers", {}))
        return cls(
            binding_id=binding_id, binding_version=version, scheme="https", host=host,
            port=port, method=method, path=path, headers=headers, body=body,
            content_type=content_type, deadline_ms=deadline, correlation_id=correlation_id,
        )

    def __repr__(self) -> str:
        return (
            "BrokerRequest("
            f"binding_id={self.binding_id!r}, version={self.binding_version}, "
            f"host={self.host!r}, method={self.method!r}, path={self.path!r}, "
            f"body_bytes={len(self.body)})"
        )


@dataclass(frozen=True, repr=False)
class BrokerResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    correlation_id: str

    def __repr__(self) -> str:
        return (
            "BrokerResponse("
            f"status={self.status}, headers={len(self.headers)}, "
            f"body_bytes={len(self.body)}, correlation_id={self.correlation_id!r})"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "headers": dict(self.headers),
            "body": self.body,
            "correlation_id": self.correlation_id,
        }


def _redact(body: bytes, credential: bytes) -> bytes:
    if credential:
        body = body.replace(credential, b"<redacted>")
    return body


def _redact_header(value: str, credential: bytes) -> str:
    if not credential:
        return value
    try:
        text = credential.decode("utf-8")
    except UnicodeDecodeError:
        return value
    return value.replace(text, "<redacted>") if text else value


def _response(value: Any, *, correlation_id: str, credential: bytes) -> BrokerResponse:
    if isinstance(value, BrokerResponse):
        status, headers, body = value.status, value.headers, value.body
    elif isinstance(value, Mapping):
        status, headers, body = value.get("status"), value.get("headers", {}), value.get("body", b"")
        if value.get("redirected") is True:
            raise CredentialBrokerError("redirect_denied", "upstream redirects are denied")
    else:
        raise CredentialBrokerError("upstream_response_invalid", "upstream response is invalid")
    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
        raise CredentialBrokerError("upstream_response_invalid", "upstream response status is invalid")
    if 300 <= status <= 399:
        raise CredentialBrokerError("redirect_denied", "upstream redirects are denied")
    normalized_headers = _normalize_headers(headers, response=True)
    normalized_headers = {
        key: _redact_header(text, credential) for key, text in normalized_headers.items()
        if key not in {"authorization", "proxy-authorization"}
    }
    if isinstance(body, bytearray):
        body = bytes(body)
    elif isinstance(body, memoryview):
        body = body.tobytes()
    if not isinstance(body, bytes):
        raise CredentialBrokerError("upstream_response_invalid", "upstream response body is invalid")
    if len(body) > MAX_RESPONSE_BODY:
        raise CredentialBrokerError("response_body_too_large", "upstream response exceeds the broker limit")
    return BrokerResponse(
        status=status, headers=normalized_headers, body=_redact(body, credential),
        correlation_id=correlation_id,
    )


class CredentialRequestBroker:
    """Authorize one exact request per managed-native instance."""

    def __init__(
        self,
        instance_id: str,
        resolver,
        binding_loader: Callable[[str], CredentialBinding | None],
        *,
        proof: Callable[[CredentialBinding], Any] | None = None,
        egress: Callable[[CredentialBinding], Any] | None = None,
        upstream: Any = None,
        owner: str | None = None,
        max_concurrent: int = MAX_CONCURRENT_REQUESTS,
        clock: Callable[[], datetime] | None = None,
        drain_seconds: float = MAX_IDLE_SECONDS,
    ) -> None:
        if not isinstance(instance_id, str) or not _CORRELATION_ID.fullmatch(instance_id):
            raise ValueError("credential broker instance identity is invalid")
        if resolver is None or not callable(getattr(resolver, "issue", None)):
            raise ValueError("credential broker resolver is required")
        if not callable(binding_loader):
            raise ValueError("credential broker binding loader is required")
        if owner is not None and (not isinstance(owner, str) or not _CORRELATION_ID.fullmatch(owner)):
            raise ValueError("credential broker owner is invalid")
        if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int) \
                or not 1 <= max_concurrent <= MAX_CONCURRENT_REQUESTS:
            raise ValueError("credential broker concurrency limit is invalid")
        if not isinstance(drain_seconds, (int, float)) or not 0 < drain_seconds <= MAX_IDLE_SECONDS:
            raise ValueError("credential broker drain deadline is invalid")
        self.instance_id = instance_id
        self.resolver = resolver
        self.binding_loader = binding_loader
        self.proof = proof or (lambda _binding: False)
        self.egress = egress or (lambda _binding: False)
        self.upstream = upstream
        self.owner = owner
        self.max_concurrent = max_concurrent
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.drain_seconds = float(drain_seconds)
        self._condition = threading.Condition()
        self._active = 0
        self._closed = False
        self._closed_bindings: set[str] = set()

    @property
    def active_sessions(self) -> int:
        with self._condition:
            return self._active

    @property
    def admission_open(self) -> bool:
        with self._condition:
            return not self._closed

    def _now(self) -> datetime:
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise CredentialBrokerError("broker_clock_invalid", "broker clock is unavailable")
        return now.astimezone(timezone.utc)

    def _check_proof(self, binding: CredentialBinding) -> None:
        try:
            observed = self.proof(binding)
            if hasattr(observed, "admissible"):
                allowed = bool(observed.admissible)
            elif isinstance(observed, Mapping):
                allowed = observed.get("admissible") is True
            else:
                allowed = observed is True
        except Exception:
            allowed = False
        if not allowed:
            raise CredentialBrokerError("proof_unavailable", "credential isolation proof is not admissible")

    def _check_egress(self, binding: CredentialBinding) -> None:
        try:
            observed = self.egress(binding)
            if hasattr(observed, "allowed"):
                allowed = bool(observed.allowed)
            elif isinstance(observed, Mapping):
                # Proof admission and network admission are separate gates.
                # Never let a proof-shaped ``admissible`` field satisfy the
                # egress check by accident.
                allowed = observed.get("allowed") is True
            else:
                allowed = observed is True
        except Exception:
            allowed = False
        if not allowed:
            raise CredentialBrokerError("egress_not_authorized", "credential egress is not authorized")

    def _load_binding(self, request: BrokerRequest) -> CredentialBinding:
        try:
            binding = self.binding_loader(request.binding_id)
        except Exception:
            binding = None
        if not isinstance(binding, CredentialBinding):
            raise CredentialBrokerError("binding_unknown", "credential binding is unknown", correlation_id=request.correlation_id)
        if binding.binding_id != request.binding_id or binding.instance_id != self.instance_id:
            raise CredentialBrokerError("binding_instance_denied", "credential binding is not owned by this instance", correlation_id=request.correlation_id)
        if binding.version != request.binding_version:
            raise CredentialBrokerError("binding_version_conflict", "credential binding version does not match", correlation_id=request.correlation_id)
        if binding.state != "ready":
            raise CredentialBrokerError("binding_not_ready", "credential binding is not ready", correlation_id=request.correlation_id)
        if binding.is_expired(now=self._now()):
            raise CredentialBrokerError("binding_expired", "credential binding has expired", correlation_id=request.correlation_id)
        if self.owner is not None and binding.owner != self.owner:
            raise CredentialBrokerError("binding_owner_denied", "credential binding owner is not authorized", correlation_id=request.correlation_id)
        if request.scheme != binding.scheme or request.host != binding.host or request.port != binding.port \
                or request.method != binding.method or request.path != binding.path:
            raise CredentialBrokerError("request_scope_mismatch", "request scope does not match binding", correlation_id=request.correlation_id)
        if binding.auth_form not in ALLOWED_AUTH_FORMS:
            raise CredentialBrokerError("auth_form_denied", "credential authentication profile is not approved", correlation_id=request.correlation_id)
        return binding

    def _acquire(self, binding_id: str, correlation_id: str) -> None:
        with self._condition:
            if self._closed:
                raise CredentialBrokerError("broker_closed", "credential broker admission is closed", correlation_id=correlation_id)
            if binding_id in self._closed_bindings:
                raise CredentialBrokerError("binding_revoked", "credential binding admission is closed", correlation_id=correlation_id)
            if self._active >= self.max_concurrent:
                raise CredentialBrokerError("concurrency_limit", "credential broker concurrency limit reached", retryable=True, correlation_id=correlation_id)
            self._active += 1

    def _release(self) -> None:
        with self._condition:
            self._active = max(0, self._active - 1)
            self._condition.notify_all()

    def _call_upstream(self, binding: CredentialBinding, request: BrokerRequest, credential: bytes) -> Any:
        if self.upstream is None:
            raise CredentialBrokerError("upstream_unavailable", "credential upstream is unavailable", correlation_id=request.correlation_id)
        try:
            if callable(self.upstream):
                return self.upstream(binding, request, credential)
            return self.upstream.request(binding, request, credential)
        except CredentialBrokerError:
            raise
        except CredentialUpstreamError as exc:
            raise CredentialBrokerError(
                _safe_code(getattr(exc, "code", None), "upstream_failed"),
                _safe_message(getattr(exc, "message", None), "credential upstream request failed"),
                retryable=bool(getattr(exc, "retryable", False)),
                correlation_id=request.correlation_id,
            ) from None
        except SecretBrokerError:
            raise CredentialBrokerError("upstream_failed", "credential upstream request failed", correlation_id=request.correlation_id) from None
        except (TimeoutError, OSError):
            raise CredentialBrokerError(
                "upstream_timeout", "credential upstream request timed out",
                retryable=True, correlation_id=request.correlation_id,
            ) from None
        except Exception:
            raise CredentialBrokerError("upstream_failed", "credential upstream request failed", correlation_id=request.correlation_id) from None

    def _request_with_lease_supplier(
        self,
        value: Mapping[str, Any] | BrokerRequest,
        *,
        transport_identity: str | None,
        lease_supplier: Callable[[CredentialBinding], Any],
    ) -> BrokerResponse:
        if isinstance(value, BrokerRequest):
            request = BrokerRequest.from_mapping({
                "binding_id": value.binding_id,
                "binding_version": value.binding_version,
                "scheme": value.scheme,
                "host": value.host,
                "port": value.port,
                "method": value.method,
                "path": value.path,
                "headers": value.headers,
                "body": value.body,
                "content_type": value.content_type,
                "deadline_ms": value.deadline_ms,
                "correlation_id": value.correlation_id,
            })
        else:
            request = BrokerRequest.from_mapping(value)
        if transport_identity != self.instance_id:
            raise CredentialBrokerError("transport_denied", "request transport is not bound to this instance", correlation_id=request.correlation_id)
        binding = self._load_binding(request)
        self._check_proof(binding)
        self._check_egress(binding)
        self._acquire(binding.binding_id, request.correlation_id)
        try:
            try:
                lease = lease_supplier(binding)
            except CredentialBrokerError:
                raise
            except SecretBrokerError as exc:
                raise CredentialBrokerError(
                    _safe_code(getattr(exc, "code", None), "source_unavailable"),
                    "credential source is unavailable",
                    retryable=bool(getattr(exc, "retryable", False)),
                    correlation_id=request.correlation_id,
                ) from None
            except Exception:
                raise CredentialBrokerError("lease_unavailable", "credential lease is unavailable", correlation_id=request.correlation_id) from None

            def consume(credential: bytes) -> dict[str, Any]:
                try:
                    result = self._call_upstream(binding, request, credential)
                    return {"response": _response(
                        result, correlation_id=request.correlation_id, credential=bytes(credential),
                    )}
                except CredentialBrokerError as exc:
                    return {"error": exc.as_dict()}
                except Exception:
                    return {"error": CredentialBrokerError(
                        "upstream_failed", "credential upstream request failed",
                        correlation_id=request.correlation_id,
                    ).as_dict()}

            try:
                outcome = lease.consume(consume)
            except SecretBrokerError as exc:
                raise CredentialBrokerError(
                    _safe_code(getattr(exc, "code", None), "lease_failed"),
                    "credential lease failed",
                    retryable=bool(getattr(exc, "retryable", False)),
                    correlation_id=request.correlation_id,
                ) from None
            except Exception:
                raise CredentialBrokerError("lease_failed", "credential lease failed", correlation_id=request.correlation_id) from None
            if not isinstance(outcome, Mapping):
                raise CredentialBrokerError("lease_result_invalid", "credential lease result is invalid", correlation_id=request.correlation_id)
            if isinstance(outcome.get("error"), Mapping):
                error = outcome["error"]
                raise CredentialBrokerError(
                    _safe_code(error.get("code")),
                    _safe_message(error.get("message"), "credential upstream request failed"),
                    retryable=bool(error.get("retryable", False)),
                    correlation_id=request.correlation_id,
                )
            response = outcome.get("response")
            if not isinstance(response, BrokerResponse):
                raise CredentialBrokerError("lease_result_invalid", "credential lease result is invalid", correlation_id=request.correlation_id)
            return response
        finally:
            self._release()

    def request(self, value: Mapping[str, Any] | BrokerRequest, *, transport_identity: str | None = None) -> BrokerResponse:
        return self._request_with_lease_supplier(
            value, transport_identity=transport_identity,
            lease_supplier=self.resolver.issue,
        )

    def request_with_lease(
        self,
        value: Mapping[str, Any] | BrokerRequest,
        lease: Any,
        *,
        transport_identity: str | None = None,
    ) -> BrokerResponse:
        """Execute one descriptor-supplied lease under this broker's admission state."""
        if lease is None or not callable(getattr(lease, "consume", None)):
            raise CredentialBrokerError("lease_unavailable", "credential lease is unavailable")
        return self._request_with_lease_supplier(
            value, transport_identity=transport_identity,
            lease_supplier=lambda _binding: lease,
        )

    def handle(self, value: Mapping[str, Any] | BrokerRequest, *, transport_identity: str | None = None) -> dict[str, Any]:
        """Return the bounded public envelope used by a local broker adapter."""
        correlation_id = None
        try:
            correlation_id = value.correlation_id if isinstance(value, BrokerRequest) else (
                value.get("correlation_id") if isinstance(value, Mapping) else None
            )
            response = self.request(value, transport_identity=transport_identity)
            return {"ok": True, **response.as_dict()}
        except CredentialBrokerError as exc:
            if (isinstance(correlation_id, str) and _CORRELATION_ID.fullmatch(correlation_id)
                    and exc.correlation_id is None):
                exc.correlation_id = correlation_id
            return {"ok": False, "error": exc.as_dict()}
        except Exception:
            return {"ok": False, "error": CredentialBrokerError(
                "broker_failed", "credential broker request failed",
            ).as_dict()}

    def close_binding(self, binding_id: str, *, binding_version: int | None = None) -> int:
        if not isinstance(binding_id, str) or not _CORRELATION_ID.fullmatch(binding_id):
            raise ValueError("credential binding identity is invalid")
        with self._condition:
            self._closed_bindings.add(binding_id)
        invalidate = getattr(self.resolver, "invalidate", None)
        if not callable(invalidate):
            return 0
        try:
            return int(invalidate(binding_id, binding_version=binding_version))
        except Exception:
            return 0

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def drain(self, timeout_seconds: float | None = None) -> bool:
        timeout = self.drain_seconds if timeout_seconds is None else timeout_seconds
        if not isinstance(timeout, (int, float)) or timeout < 0 or timeout > MAX_IDLE_SECONDS:
            raise ValueError("credential broker drain deadline is invalid")
        deadline = time.monotonic() + float(timeout)
        with self._condition:
            while self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True


__all__ = [
    "BrokerRequest", "BrokerResponse", "CredentialBrokerError",
    "CredentialRequestBroker", "MAX_CONNECT_SECONDS", "MAX_CONCURRENT_REQUESTS",
    "MAX_IDLE_SECONDS", "MAX_REQUEST_BODY", "MAX_REQUEST_HEADERS",
    "MAX_RESPONSE_BODY", "MAX_TOTAL_SECONDS",
]
