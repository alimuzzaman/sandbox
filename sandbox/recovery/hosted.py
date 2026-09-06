"""Typed controller boundary for hosted recovery materialization.

The recovery package owns publication, while a controller that owns the hosted
runtime supplies source observations and native capture receipts.  This module
does not discover Docker, database, or filesystem paths and deliberately has no
default transport; production wiring must provide one through ``context``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from .errors import RecoveryError
from .materialize import MaterializationAdapter, SourceBinding
from .models import ArtifactPlan, RecoveryPlan


_FORMATS = {
    "control-plane": {"declarations"},
    "database": {"postgresql-custom", "mariadb-sql"},
    "filesystem": {"tar"},
    "git": {"git-bundle", "git-provenance"},
}


def _safe_atom(value: object) -> bool:
    return (isinstance(value, str) and bool(value)
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


@dataclass(frozen=True)
class HostedObservation:
    """Controller observation bound to one remote source generation."""

    binding: SourceBinding
    covered_sources: Mapping[str, tuple[str, ...]]

    def validate(self, remote: str, plan: RecoveryPlan) -> None:
        self.binding.validate(remote)
        expected = set(plan.profiles)
        actual = set(self.covered_sources) if isinstance(self.covered_sources, Mapping) else set()
        if actual != expected:
            raise RecoveryError("hosted materialization coverage is incomplete", "incomplete_materialization")
        for artifact in plan.artifacts:
            values = self.covered_sources.get(artifact.profile_id)
            if (not isinstance(values, tuple) or not values
                    or len(values) != len(set(values))
                    or any(not _safe_atom(item) for item in values)
                    or set(values) != set(artifact.sources)):
                raise RecoveryError("hosted materialization coverage is incomplete", "incomplete_materialization")


@dataclass(frozen=True)
class HostedCaptureReceipt:
    """Native-format capture receipt returned by the hosted controller."""

    profile_id: str
    artifact_id: str
    request_id: str
    files: tuple[Path, ...]
    covered_sources: tuple[str, ...]
    native_format: str
    format_validated: bool = True

    def validate(self, artifact: ArtifactPlan, request_id: str) -> None:
        expected_formats = _FORMATS.get(artifact.source_type, set())
        if (self.profile_id != artifact.profile_id or self.artifact_id != artifact.artifact_id
                or self.request_id != request_id or not isinstance(self.files, tuple)
                or not self.files or any(not isinstance(path, Path) for path in self.files)
                or not isinstance(self.covered_sources, tuple) or not self.covered_sources
                or len(self.covered_sources) != len(set(self.covered_sources))
                or any(not _safe_atom(item) for item in self.covered_sources)
                or set(self.covered_sources) != set(artifact.sources)
                or self.native_format not in expected_formats or self.format_validated is not True):
            raise RecoveryError("hosted capture receipt is invalid", "invalid_materialization_receipt")


class HostedRecoveryController(Protocol):
    """Controller-owned observation and capture operations.

    Implementations must use the supported remote execution and secret broker
    services.  They return local, owner-controlled paths in ``destination``;
    they never return credentials or rely on caller-provided host paths.
    """

    def observe(self, remote: str, plan: RecoveryPlan) -> HostedObservation: ...

    def capture(self, remote: str, artifact: ArtifactPlan, destination: Path,
                binding: SourceBinding, request_id: str) -> HostedCaptureReceipt: ...


class HostedRecoveryMaterializer(MaterializationAdapter):
    """Adapt a typed hosted controller to the generic scoped coordinator."""

    def __init__(self, controller: HostedRecoveryController) -> None:
        self.controller = controller

    @staticmethod
    def _request_id(remote: str, artifact: ArtifactPlan, binding: SourceBinding) -> str:
        # Include the complete immutable artifact declaration.  A replay must
        # resolve to the same controller operation only when both the source
        # binding and the requested capture shape are identical.
        declaration = {
            "profile_id": artifact.profile_id,
            "artifact_id": artifact.artifact_id,
            "source_type": artifact.source_type,
            "allowed_roots": artifact.allowed_roots,
            "sources": artifact.sources,
            "capture_mode": artifact.capture_mode,
            "consistency": artifact.consistency,
            "excludes": artifact.excludes,
            "restore_target": artifact.restore_target,
            "verification": artifact.verification,
            "dependencies": artifact.dependencies,
        }
        payload = json.dumps({
            "remote": remote,
            "binding": {
                "machine_identity": binding.machine_identity,
                "revision": binding.revision,
                "source_digest": binding.source_digest,
            },
            "artifact": declaration,
        }, sort_keys=True, separators=(",", ":"))
        return "recovery-" + hashlib.sha256(payload.encode()).hexdigest()

    def observe(self, remote: str, plan: RecoveryPlan) -> SourceBinding:
        try:
            observation = self.controller.observe(remote, plan)
        except RecoveryError as exc:
            # Do not reflect controller transport/path diagnostics in the
            # public result envelope.
            raise RecoveryError("hosted source observation failed", "materialization_observe_failed") from exc
        except Exception as exc:
            raise RecoveryError("hosted source observation failed", "materialization_observe_failed") from exc
        if not isinstance(observation, HostedObservation):
            raise RecoveryError("hosted source observation is invalid", "invalid_source_binding")
        observation.validate(remote, plan)
        return observation.binding

    def capture(self, remote: str, artifact: ArtifactPlan, destination: Path,
                binding: SourceBinding) -> tuple[Path, ...]:
        request_id = self._request_id(remote, artifact, binding)
        try:
            receipt = self.controller.capture(remote, artifact, destination, binding, request_id)
        except RecoveryError as exc:
            # Controller messages may contain transport paths or provider
            # diagnostics. Keep the public recovery envelope stable and
            # secret/path-free even when the controller returns a typed error.
            raise RecoveryError("hosted capture failed", "materialization_capture_failed") from exc
        except Exception as exc:
            raise RecoveryError("hosted capture failed", "materialization_capture_failed") from exc
        if not isinstance(receipt, HostedCaptureReceipt):
            raise RecoveryError("hosted capture receipt is invalid", "invalid_materialization_receipt")
        receipt.validate(artifact, request_id)
        return receipt.files


__all__ = [
    "HostedCaptureReceipt", "HostedObservation", "HostedRecoveryController",
    "HostedRecoveryMaterializer",
]
