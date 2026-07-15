from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from sandbox.hermes.backup import HermesBackupService
from sandbox.hermes.gateway import HermesGatewayService
from sandbox.hermes.jobs import HermesJobService
from sandbox.hermes.routing import recommended_route
from sandbox.hermes.state import HermesStateRepository


@dataclass
class HermesService:
    state: Any = None
    routing: Any = None
    jobs: Any = None
    gateway: Any = None
    backup: Any = None


@dataclass(frozen=True)
class HermesCommandService:
    """Transport-neutral command boundary used by compatibility transports."""

    runner: Callable[[list[str], int], Mapping[str, Any]]

    def run(self, arguments: Sequence[str], timeout: int) -> dict[str, Any]:
        if timeout < 1:
            raise ValueError("Hermes command timeout must be positive")
        return dict(self.runner([str(argument) for argument in arguments], timeout))


class _UnavailableJobBackend:
    def _unavailable(self, *_args, **_kwargs):
        raise RuntimeError("Hermes job backend is not composed")
    run = status = cancel = cleanup = _unavailable


class _UnavailableGatewayBackend:
    def _unavailable(self, *_args, **_kwargs):
        raise RuntimeError("Hermes gateway backend is not composed")
    apply_access = apply_route = remove_route = remove_access = _unavailable


class _EmptyArtifactStore:
    def put(self, artifact):
        return dict(artifact)

    def list(self):
        return ()

    def read(self, artifact_id):
        raise KeyError(artifact_id)


def compose_hermes_service(dependencies: Mapping[str, Any]) -> HermesService:
    """Compose bounded services from explicit optional adapters, without I/O."""
    state = dependencies.get("state") or HermesStateRepository(
        dependencies.get("state_path", ".hermes-state.json")
    )
    return HermesService(
        state=state,
        routing=dependencies.get("routing") or recommended_route,
        jobs=dependencies.get("jobs") or HermesJobService(
            dependencies.get("job_backend") or _UnavailableJobBackend()
        ),
        gateway=dependencies.get("gateway") or HermesGatewayService(
            dependencies.get("gateway_backend") or _UnavailableGatewayBackend()
        ),
        backup=dependencies.get("backup") or HermesBackupService(
            dependencies.get("artifact_store") or _EmptyArtifactStore()
        ),
    )
