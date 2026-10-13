"""Killable process boundary for complete provider and credential operations."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from ..credentials.base import locator_for_policy
from ..process_group import terminate_process_group
from .base import (
    CapabilityStatus, MAX_PROVIDER_TIMEOUT_SECONDS, MAX_PROVIDER_VALUE_BYTES,
    ProviderError,
)


_ENVIRONMENT = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR", "TMP", "TEMP",
    "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "SECURITYSESSIONID",
})
_MAX_WORKER_OUTPUT = MAX_PROVIDER_VALUE_BYTES + 16_384
_ERROR_CODES = frozenset({
    "provider_unsupported", "credential_backend_unavailable", "credential_locked",
    "credential_missing", "credential_store_failed", "endpoint_unreachable",
    "endpoint_untrusted", "authentication_denied", "provider_denied",
    "item_unavailable", "provider_rate_limited", "provider_failed",
    "response_invalid", "response_too_large", "operation_timeout",
    "capacity_exhausted",
})


def _field(descriptor: object, *names: str, default=None):
    for name in names:
        if isinstance(descriptor, Mapping) and name in descriptor:
            return descriptor[name]
        if hasattr(descriptor, name):
            return getattr(descriptor, name)
    return default


def _timeout(descriptor: object) -> int | float:
    value = _field(descriptor, "timeout_seconds", "timeoutSeconds", default=10)
    if (
        isinstance(value, bool) or not isinstance(value, (int, float))
        or value < 1 or value > MAX_PROVIDER_TIMEOUT_SECONDS
    ):
        raise ProviderError("provider_unsupported")
    return value


def _descriptor(descriptor: object) -> dict:
    names = {
        "alias": ("alias",),
        "type": ("type", "kind"),
        "endpoint": ("endpoint",),
        "projectId": ("projectId", "project_id"),
        "environment": ("environment",),
        "path": ("path", "secretPath", "secret_path"),
        "key": ("key", "secretName", "secret_name"),
        "clientId": ("clientId", "client_id"),
        "mcpModes": ("mcpModes", "mcp_modes"),
        "timeoutSeconds": ("timeoutSeconds", "timeout_seconds"),
        "authorityDigest": ("authorityDigest", "authority_digest"),
    }
    result = {}
    for output, aliases in names.items():
        value = _field(descriptor, *aliases)
        if isinstance(value, (set, frozenset, tuple)):
            value = sorted(value)
        if value is not None:
            result[output] = value
    try:
        encoded = json.dumps(result, ensure_ascii=True, sort_keys=True,
                             separators=(",", ":")).encode("ascii")
    except (TypeError, UnicodeEncodeError):
        raise ProviderError("provider_unsupported") from None
    if len(encoded) > 16_384:
        raise ProviderError("provider_unsupported")
    return result


class BoundedProviderBroker:
    """Run real OS-store/DNS/HTTPS work in one disposable child process."""

    isolates_credential_store = True

    def __init__(self, *, process_factory=subprocess.Popen, clock=time.monotonic) -> None:
        self._process_factory = process_factory
        self._clock = clock

    @staticmethod
    def _command() -> list[str]:
        root = str(Path(__file__).resolve().parents[3])
        code = (
            "import sys;sys.path.insert(0," + repr(root) + ");"
            "from sandbox.secrets.providers.worker import main;main()"
        )
        return [sys.executable, "-I", "-c", code]

    @staticmethod
    def _environment() -> dict[str, str]:
        return {
            key: value for key, value in os.environ.items()
            if key in _ENVIRONMENT or key.startswith("LC_")
        }

    def _call(self, action: str, descriptor: object) -> bytes:
        request = json.dumps(
            {"version": 1, "action": action, "descriptor": _descriptor(descriptor)},
            ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        ).encode("ascii")
        started = self._clock()
        timeout = float(_timeout(descriptor))
        try:
            process = self._process_factory(
                self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=self._environment(),
                start_new_session=True, close_fds=True,
            )
        except (OSError, ValueError):
            raise ProviderError("provider_failed") from None
        output = b""
        cleanup_ok = True
        try:
            remaining = timeout - (self._clock() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            output, _unused = process.communicate(input=request, timeout=remaining)
        except subprocess.TimeoutExpired:
            terminate_process_group(process, grace_seconds=0.0, kill_wait_seconds=0.5)
            try:
                process.communicate(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
            raise ProviderError("operation_timeout") from None
        finally:
            cleanup_ok = terminate_process_group(
                process, grace_seconds=0.0, kill_wait_seconds=0.5,
            )
        if not cleanup_ok:
            raise ProviderError("provider_failed")
        if self._clock() - started > timeout:
            raise ProviderError("operation_timeout")
        if len(output) > _MAX_WORKER_OUTPUT or not output:
            raise ProviderError("provider_failed")
        if output[:1] == b"E":
            try:
                code = output[1:].decode("ascii")
            except UnicodeDecodeError:
                code = "provider_failed"
            raise ProviderError(code if code in _ERROR_CODES else "provider_failed")
        return output

    def resolve_exact(self, descriptor: object, credential) -> bytes:
        # Validate that the complete v2 authority is representable before a
        # worker starts. The worker derives this same locator for the OS-store
        # read; no secret-bearing handle crosses the helper boundary.
        locator_for_policy(descriptor)
        if credential is not None:
            raise ProviderError("credential_store_failed")
        output = self._call("resolve", descriptor)
        if output[:1] != b"V" or len(output) < 5:
            raise ProviderError("provider_failed")
        size = int.from_bytes(output[1:5], "big")
        value = output[5:]
        if size != len(value) or size > MAX_PROVIDER_VALUE_BYTES:
            raise ProviderError("response_invalid")
        return value

    def status(self, descriptor: object) -> CapabilityStatus:
        try:
            output = self._call("status", descriptor)
            if output[:1] != b"S":
                raise ProviderError("provider_failed")
            data = json.loads(output[1:].decode("ascii"))
            return CapabilityStatus(
                state=data.get("state", "unavailable"),
                endpoint_reachable=data.get("endpoint_reachable"),
                error_code=data.get("error_code"),
            )
        except ProviderError as exc:
            return CapabilityStatus(
                state="timed_out" if exc.code == "operation_timeout" else "unavailable",
                endpoint_reachable=False, error_code=exc.code,
            )
        except Exception:
            return CapabilityStatus(
                state="unavailable", endpoint_reachable=False,
                error_code="provider_failed",
            )

    def source_status(self, descriptor: object) -> dict[str, object]:
        try:
            output = self._call("source_status", descriptor)
            if output[:1] != b"S":
                raise ProviderError("provider_failed")
            data = json.loads(output[1:].decode("ascii"))
            if not isinstance(data, dict):
                raise ProviderError("provider_failed")
            return data
        except ProviderError as exc:
            return {
                "state": "timed_out" if exc.code == "operation_timeout" else "unavailable",
                "endpoint_reachable": False,
                "error_code": exc.code,
                "credential_state": "unknown",
                "credential_backend": (
                    "macos_keychain" if sys.platform == "darwin" else "linux_secret_service"
                ),
            }
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
            return {
                "state": "unavailable", "endpoint_reachable": False,
                "error_code": "provider_failed", "credential_state": "unknown",
                "credential_backend": (
                    "macos_keychain" if sys.platform == "darwin" else "linux_secret_service"
                ),
            }


__all__ = ["BoundedProviderBroker"]
