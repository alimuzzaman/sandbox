"""Small reviewed client for the explicit Credential Vault request contract."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from sandbox.isolation.credential_binding import CredentialBinding
from sandbox.isolation.credential_request_broker import (
    MAX_REQUEST_BODY, MAX_REQUEST_HEADERS,
)


_CORRELATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_INSTANCE = re.compile(r"^sb-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DENIED = frozenset({
    "authorization", "proxy-authorization", "proxy-authenticate", "x-api-key",
    "api-key", "host", "content-length", "transfer-encoding", "connection",
    "keep-alive", "te", "trailer", "upgrade", "proxy-connection",
})


class CredentialConsumerError(ValueError):
    """Client-side shape refusal without a broker/source value."""


class ExplicitCredentialConsumer:
    """Construct binding-scoped requests and return the broker's safe envelope."""

    def __init__(self, broker, *, instance_id: str) -> None:
        if broker is None or not callable(getattr(broker, "handle", None)):
            raise ValueError("credential consumer requires a request broker")
        if not isinstance(instance_id, str) or not _INSTANCE.fullmatch(instance_id):
            raise ValueError("credential consumer instance identity is invalid")
        self.broker = broker
        self.instance_id = instance_id

    @staticmethod
    def _headers(headers: Any) -> dict[str, str]:
        if headers is None:
            return {}
        if not isinstance(headers, Mapping):
            raise CredentialConsumerError("consumer headers are invalid")
        result = {}
        total = 0
        for key, value in headers.items():
            if not isinstance(key, str) or not key or key.lower() in _DENIED:
                raise CredentialConsumerError("consumer security header is denied")
            if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise CredentialConsumerError("consumer header is invalid")
            name = key.lower()
            if name in result:
                raise CredentialConsumerError("consumer duplicate header is denied")
            result[name] = value
            try:
                name_bytes = len(key.encode("ascii", "strict"))
            except UnicodeEncodeError:
                raise CredentialConsumerError("consumer header is invalid") from None
            total += name_bytes + len(value.encode("utf-8")) + 4
            if total > MAX_REQUEST_HEADERS:
                raise CredentialConsumerError("consumer headers exceed the limit")
        return result

    def request(self, binding: CredentialBinding, *, body: bytes = b"", headers=None,
                content_type: str | None = None, deadline_ms: int = 30_000,
                correlation_id: str | None = None) -> dict[str, Any]:
        if not isinstance(binding, CredentialBinding):
            raise CredentialConsumerError("consumer binding is invalid")
        if isinstance(body, bytearray):
            body = bytes(body)
        elif isinstance(body, memoryview):
            body = body.tobytes()
        if not isinstance(body, bytes) or len(body) > MAX_REQUEST_BODY:
            raise CredentialConsumerError("consumer body exceeds the limit")
        if isinstance(deadline_ms, bool) or not isinstance(deadline_ms, int) or not 1 <= deadline_ms <= 30_000:
            raise CredentialConsumerError("consumer deadline is invalid")
        if correlation_id is None:
            correlation_id = "consumer-request"
        if not isinstance(correlation_id, str) or not _CORRELATION.fullmatch(correlation_id):
            raise CredentialConsumerError("consumer correlation ID is invalid")
        if content_type is not None and (not isinstance(content_type, str) or
                                          any(ord(char) < 32 or ord(char) == 127 for char in content_type)):
            raise CredentialConsumerError("consumer content type is invalid")
        request = {
            "binding_id": binding.binding_id,
            "binding_version": binding.version,
            "scheme": binding.scheme,
            "host": binding.host,
            "port": binding.port,
            "method": binding.method,
            "path": binding.path,
            "headers": self._headers(headers),
            "body": body,
            "content_type": content_type,
            "deadline_ms": deadline_ms,
            "correlation_id": correlation_id,
        }
        return self.broker.handle(request, transport_identity=self.instance_id)


__all__ = ["CredentialConsumerError", "ExplicitCredentialConsumer"]
