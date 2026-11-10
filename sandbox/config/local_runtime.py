"""Pure machine-level local-runtime selection (spec 082).

A controller either runs local Docker instances (`docker`, the default) or has
no local container runtime at all (`none`: remote-only, or a machine without
Docker). The setting is machine-scoped: it lives under `controller.local_runtime`
in `sandbox.local.yml`, and `SANDBOX_LOCAL_RUNTIME` overrides it for one process.

This module reads no files, starts no process and mutates nothing; callers hand
it the raw layers and receive a validated selection.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, NamedTuple


LocalRuntimeMode = Literal["docker", "none"]

DEFAULT_MODE: LocalRuntimeMode = "docker"
MODES: tuple[LocalRuntimeMode, ...] = ("docker", "none")
ENV_VARIABLE = "SANDBOX_LOCAL_RUNTIME"
CONFIG_PATH = "controller.local_runtime"


class LocalRuntimeConfigError(ValueError):
    """A local-runtime setting was rejected before any host contact."""

    def __init__(self, message: str, code: str = "invalid_local_runtime") -> None:
        super().__init__(message)
        self.code = code


class LocalRuntimeSelection(NamedTuple):
    """The effective local-runtime mode and the layer that chose it."""

    mode: LocalRuntimeMode
    source: str

    @property
    def remote_only(self) -> bool:
        return self.mode == "none"

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "source": self.source,
                "remote_only": self.remote_only}


def normalize_local_runtime(raw: object) -> LocalRuntimeMode:
    """Validate one `local_runtime` value; absent means the default."""
    if raw is None:
        return DEFAULT_MODE
    if not isinstance(raw, str) or raw.strip().lower() not in MODES:
        raise LocalRuntimeConfigError(
            f"{CONFIG_PATH} must be one of {', '.join(MODES)} (got {raw!r})")
    return raw.strip().lower()  # type: ignore[return-value]


def machine_local_runtime(result: Mapping[str, Any]) -> LocalRuntimeMode:
    """Read and validate `controller.local_runtime` from a machine document."""
    controller = result.get("controller")
    if controller is None:
        if "controller" in result:
            raise LocalRuntimeConfigError(
                "machine controller configuration must be an object")
        return DEFAULT_MODE
    if not isinstance(controller, Mapping):
        raise LocalRuntimeConfigError(
            "machine controller configuration must be an object")
    return normalize_local_runtime(controller.get("local_runtime"))


def resolve_local_runtime(*, env: Mapping[str, str] | None = None,
                          machine: Mapping[str, Any] | None = None
                          ) -> LocalRuntimeSelection:
    """Environment, then machine-local configuration, then the default.

    An empty environment value is ignored; any other unknown value is an error
    rather than a silent fall back to Docker.
    """
    value = (env or {}).get(ENV_VARIABLE)
    if isinstance(value, str) and value.strip():
        return LocalRuntimeSelection(normalize_local_runtime(value), "environment")
    if isinstance(machine, Mapping):
        controller = machine.get("controller")
        if isinstance(controller, Mapping) and controller.get("local_runtime") is not None:
            return LocalRuntimeSelection(machine_local_runtime(machine),
                                         "machine_override")
        machine_local_runtime(machine)  # reject a malformed block
    return LocalRuntimeSelection(DEFAULT_MODE, "default")


REMOTE_ONLY_REFUSAL = (
    "this controller is set to remote-only (controller.local_runtime: none"
    "{source}), so local Docker instances are not available. Use --remote, or "
    "remove controller.local_runtime from sandbox.local.yml "
    f"(and unset {ENV_VARIABLE}) to run them here."
)


def remote_only_message(selection: LocalRuntimeSelection) -> str:
    """One user-facing refusal naming the switch and how to clear it."""
    source = f", from {ENV_VARIABLE}" if selection.source == "environment" else ""
    return REMOTE_ONLY_REFUSAL.format(source=source)


__all__ = [
    "CONFIG_PATH", "DEFAULT_MODE", "ENV_VARIABLE", "LocalRuntimeConfigError",
    "LocalRuntimeMode", "LocalRuntimeSelection", "MODES", "REMOTE_ONLY_REFUSAL",
    "machine_local_runtime", "normalize_local_runtime", "remote_only_message",
    "resolve_local_runtime",
]
