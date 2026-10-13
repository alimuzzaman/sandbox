"""Provider-neutral interfaces and bounded outcomes for secret adapters."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..models import SecretBrokerError


MAX_PROVIDER_TIMEOUT_SECONDS = 1_800
MAX_PROVIDER_RESPONSE_BYTES = 1_048_576
MAX_PROVIDER_VALUE_BYTES = 65_536


_OUTCOMES: dict[str, tuple[str, str]] = {
    "provider_unsupported": (
        "the configured provider API contract is unsupported",
        "check the registered endpoint and supported provider release",
    ),
    "credential_backend_unavailable": (
        "the approved local credential store is unavailable",
        "start or reconnect the approved user credential session",
    ),
    "credential_locked": (
        "the approved local credential store is locked",
        "unlock the credential store in the user session, then retry",
    ),
    "credential_missing": (
        "the controller credential is not present in the approved local store",
        "place this controller's credential in its approved local store",
    ),
    "credential_store_failed": (
        "the approved local credential store operation failed",
        "check the local credential store and user session",
    ),
    "endpoint_unreachable": (
        "the registered provider endpoint could not be reached",
        "check private network reachability and the registered endpoint",
    ),
    "endpoint_untrusted": (
        "the registered provider endpoint identity could not be verified",
        "check the endpoint certificate and the operating system trust store",
    ),
    "authentication_denied": (
        "the provider rejected this controller credential",
        "check this controller's independently provisioned provider credential",
    ),
    "provider_denied": (
        "the provider denied access to the registered project item",
        "check the provider identity's project-level read permission",
    ),
    "item_unavailable": (
        "the provider did not return the registered item",
        "check the registered selector and provider project access",
    ),
    "provider_rate_limited": (
        "the provider rate limit blocked this operation",
        "wait before starting a new operation",
    ),
    "provider_failed": (
        "the provider could not complete this operation",
        "check provider availability and its supported API release",
    ),
    "response_invalid": (
        "the provider response did not match the registered item contract",
        "check provider compatibility and the exact registered selector",
    ),
    "response_too_large": (
        "the provider response exceeded the supported size limit",
        "reduce the provider response size or use a smaller secret value",
    ),
    "operation_timeout": (
        "the provider operation exceeded its configured time bound",
        "check private network and provider availability before retrying",
    ),
    "capacity_exhausted": (
        "the provider cannot supply a distinct authorized identity or credential",
        "provision a distinct provider identity and controller credential",
    ),
}


class ProviderError(SecretBrokerError):
    """Stable provider failure with a constant message and optional remedy."""

    def __init__(self, code: str) -> None:
        outcome = _OUTCOMES.get(code)
        if outcome is None:
            code = "provider_failed"
            outcome = _OUTCOMES[code]
        message, remedy = outcome
        super().__init__(code, message, retryable=False, remedy=remedy)


@dataclass(frozen=True)
class ProviderPolicy:
    """The single non-secret selector and origin for one provider source."""

    endpoint: str
    project_id: str
    environment: str
    path: str
    key: str
    client_id: str
    timeout_seconds: int = 10

    @classmethod
    def from_descriptor(cls, descriptor: object) -> "ProviderPolicy":
        """Adapt the configuration contract's camel-case or Python field names."""

        def field_value(*names: str, default: object = None) -> object:
            values: list[object] = []
            for name in names:
                if isinstance(descriptor, Mapping):
                    if name in descriptor:
                        values.append(descriptor[name])
                elif hasattr(descriptor, name):
                    values.append(getattr(descriptor, name))
            if len(values) > 1 and any(value != values[0] for value in values[1:]):
                raise ProviderError("provider_unsupported")
            return values[0] if values else default

        try:
            return cls(
                endpoint=field_value("endpoint"),
                project_id=field_value("project_id", "projectId"),
                environment=field_value("environment"),
                path=field_value("path", "secret_path", "secretPath"),
                key=field_value("key", "secret_name", "secretName"),
                client_id=field_value("client_id", "clientId"),
                timeout_seconds=field_value(
                    "timeout_seconds", "timeoutSeconds", default=10,
                ),
            )  # type: ignore[arg-type]
        except ProviderError:
            raise
        except Exception:
            invalid = True
        if invalid:
            raise ProviderError("provider_unsupported")


class Deadline:
    """One monotonic deadline shared by credential lookup and all HTTP work."""

    __slots__ = ("_end", "_clock")

    def __init__(self, timeout_seconds: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > MAX_PROVIDER_TIMEOUT_SECONDS
        ):
            raise ProviderError("provider_unsupported")
        self._clock = clock
        self._end = clock() + float(timeout_seconds)

    def remaining(self) -> float:
        remaining = self._end - self._clock()
        if remaining <= 0:
            raise ProviderError("operation_timeout")
        return remaining

    def check(self) -> None:
        self.remaining()


@dataclass(frozen=True)
class CapabilityStatus:
    """Value-free endpoint availability observation."""

    provider: str = "infisical"
    state: str = "unknown"
    endpoint_reachable: bool | None = None
    error_code: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "state": self.state,
            "endpoint_reachable": self.endpoint_reachable,
            "error_code": self.error_code,
        }


@dataclass
class HttpResponse:
    """A status and capped mutable body; headers and error bodies are discarded."""

    status: int
    body: bytearray = field(default_factory=bytearray, repr=False)

    def discard(self) -> None:
        for index in range(len(self.body)):
            self.body[index] = 0
        self.body.clear()


@runtime_checkable
class CredentialHandle(Protocol):
    """Handle for one controller-local credential; implementations honor deadline."""

    def get_secret(self, deadline: Deadline) -> bytes | bytearray | memoryview:
        """Return a credential from the approved local store for this operation."""


@runtime_checkable
class ProviderTransport(Protocol):
    """Injected transport used only for one bounded provider operation."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | bytearray | None,
        deadline: Deadline,
        capture_body: bool = True,
    ) -> HttpResponse:
        """Perform one HTTPS request without redirects, proxies, or retries."""
