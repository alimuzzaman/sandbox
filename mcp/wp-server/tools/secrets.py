"""Opt-in MCP adapters for least-disclosure secret operations."""
from __future__ import annotations

from sandbox.secrets import SecretBrokerError


_service_factory = None


def _service(project_dir: str):
    if _service_factory is None:
        raise RuntimeError("secret service dependency is not configured")
    return _service_factory(project_dir)


def _safe(callable_):
    try:
        return callable_()
    except SecretBrokerError as exc:
        return {"ok": False, "error": exc.as_dict()}
    except Exception:
        return {
            "ok": False,
            "error": {"code": "operation_failed", "message": "secret operation failed", "retryable": False},
        }


def secret_inspect(project_dir: str, source: str, keys: list[str] | None = None,
                   mode: str = "keys", exact_length: bool = False) -> dict:
    """List names by default, or return bounded metadata/fixed masking when authorized."""
    return _safe(lambda: _service(project_dir).inspect(
        source, keys=keys, mode=mode, exact_length=exact_length, surface="mcp",
    ))


def secret_validate(project_dir: str, source: str, key: str, profile: str) -> dict:
    """Run one reviewed offline shape profile; never retrieve or live-check the value."""
    return _safe(lambda: _service(project_dir).validate(
        source, key, profile, surface="mcp",
    ))


def secret_use_profile(project_dir: str, profile: str) -> dict:
    """Use one preconfigured credential with one fixed bounded child profile."""
    return _safe(lambda: _service(project_dir).use_profile(profile, surface="mcp"))


def register(server, dependencies) -> None:
    global _service_factory
    _service_factory = dependencies.require("secret_service_factory")
    for function in (secret_inspect, secret_validate, secret_use_profile):
        server.tool()(function)
