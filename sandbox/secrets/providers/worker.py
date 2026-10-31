"""Private provider worker. Protocol output is bounded and never diagnostic."""

from __future__ import annotations

import json
import os
import sys

from ..credentials.manifest import create_credential_store
from ..models import SecretBrokerError
from .infisical import InfisicalProvider


_MAX_INPUT = 16_384
_STATUS_RESERVE_SECONDS = 0.25


def _write(payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(sys.stdout.fileno(), view)
        view = view[written:]


def _status_payload(status, **extra) -> bytes:
    data = status.as_dict()
    data.update(extra)
    return b"S" + json.dumps(
        data, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")


def _credential_state(store, descriptor) -> str:
    state = store.status(descriptor)
    value = getattr(state, "value", state)
    return value if value in {"unknown", "missing", "locked", "unavailable", "present", "failed"} else "failed"


def _status_budget(descriptor) -> float:
    value = descriptor.get("timeoutSeconds", 10) if isinstance(descriptor, dict) else 10
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise SecretBrokerError("provider_unsupported", "provider request is invalid")
    return max(0.1, min(float(value) * 0.75, float(value) - _STATUS_RESERVE_SECONDS))


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(_MAX_INPUT + 1)
        if len(raw) > _MAX_INPUT:
            raise SecretBrokerError("provider_unsupported", "provider request is invalid")
        request = json.loads(raw.decode("ascii"))
        if set(request) != {"version", "action", "descriptor"} or request["version"] != 1:
            raise SecretBrokerError("provider_unsupported", "provider request is invalid")
        descriptor = request["descriptor"]
        action = request["action"]
        provider = InfisicalProvider()
        if action == "status":
            payload = _status_payload(provider.status(descriptor))
        elif action == "source_status":
            store = create_credential_store(bootstrap_dependencies=False)
            credential_state = _credential_state(store, descriptor)
            payload = _status_payload(
                provider.status(descriptor, timeout_seconds=_status_budget(descriptor)),
                credential_state=credential_state,
                credential_backend=(
                    "macos_keychain" if sys.platform == "darwin" else "linux_secret_service"
                ),
            )
        elif action == "resolve":
            store = create_credential_store(bootstrap_dependencies=False)
            value = provider.resolve_exact(descriptor, store.get(descriptor))
            payload = b"V" + len(value).to_bytes(4, "big") + value
        else:
            raise SecretBrokerError("provider_unsupported", "provider request is invalid")
    except SecretBrokerError as exc:
        payload = b"E" + exc.code.encode("ascii", errors="ignore")[:64]
    except Exception:
        payload = b"Eprovider_failed"
    try:
        _write(payload)
    except Exception:
        pass


if __name__ == "__main__":
    main()
