"""Fixed helper orchestration and coherent observation validation."""

from __future__ import annotations

import hashlib
import re

from sandbox.transports.remote_hosting_images import RegisteredRemoteImageTransport

from .staging_models import (
    LocalImageObservation, StageRequest, StagingContractError, StagingPolicy,
)


class StageWorkerError(RuntimeError):
    def __init__(self, code: str, *, process: dict | None = None,
                 cleanup: dict | None = None) -> None:
        self.code = code
        self.process = process
        self.cleanup = cleanup
        super().__init__(code)


def unit_name(request_id: str, request_digest: str) -> str:
    token = hashlib.sha256(f"{request_id}\0{request_digest}".encode()).hexdigest()[:32]
    return f"sandbox-image-stage-{token}.service"


class StageWorker:
    def __init__(self, transport: RegisteredRemoteImageTransport, *, timeout_seconds: int = 900) -> None:
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def prepare(self, request: StageRequest, policy: StagingPolicy):
        projection = request.plan.delivery_identity_projection
        frame = {"schema_version": 1, "unit_name": unit_name(request.request_id, request.request_digest),
                 "request_id": request.request_id,
                 "repository": projection.image.repository,
                 "repository_qualified_digest": projection.image.repository_qualified_digest,
                 "manifest_digest": projection.image.manifest_digest,
                 "config_digest": projection.image.config_digest,
                 "platform": projection.image.platform.as_mapping(),
                 "topology": projection.topology.as_mapping(), "target": policy.target.as_mapping(),
                 "helper": policy.helper.as_mapping()}
        channel = self.transport.prepare(
            projection.target_scope.remote, frame, timeout_seconds=self.timeout_seconds)
        return _PreparedWorker(self, request, policy, channel, frame)


class _PreparedWorker:
    def __init__(self, worker, request, policy, channel, frame):
        self.worker = worker; self.request = request; self.policy = policy
        self.channel = channel; self.frame = frame

    def deliver(self, credential: bytes) -> tuple[LocalImageObservation, dict, dict]:
        request = self.request; policy = self.policy
        projection = request.plan.delivery_identity_projection
        response = self.channel.deliver(credential)
        if type(response.payload) is not dict or not {"process", "cleanup"} <= set(response.payload):
            raise StageWorkerError("observation_invalid")
        process = response.payload["process"]; cleanup = response.payload["cleanup"]
        expected_unit = self.frame["unit_name"]
        if type(process) is not dict or process.get("unit_name") != expected_unit \
                or process.get("delegated") is not False or process.get("escape_allowed") is not False \
                or type(process.get("cgroup")) is not str or expected_unit not in process["cgroup"] \
                or process.get("unit_inactive") is not True \
                or process.get("cgroup_empty_or_removed") is not True:
            raise StageWorkerError("process_unproven", process=process, cleanup=cleanup)
        if cleanup != {"complete": True}:
            raise StageWorkerError("cleanup_unproven", process=process, cleanup=cleanup)
        if not response.ok:
            raise StageWorkerError(response.code, process=process, cleanup=cleanup)
        if set(response.payload) != {"observation", "process", "cleanup"}:
            raise StageWorkerError("observation_invalid")
        raw = response.payload["observation"]
        try:
            observation = LocalImageObservation(**raw)
        except (TypeError, StagingContractError):
            raise StageWorkerError("observation_invalid") from None
        if observation.target != policy.target \
                or observation.repository != projection.image.repository \
                or observation.repo_digest != projection.image.repository_qualified_digest \
                or observation.config_digest != projection.image.config_digest \
                or observation.local_image_id != observation.config_digest \
                or observation.platform != projection.image.platform.as_mapping():
            raise StageWorkerError("observation_invalid")
        return observation, process, cleanup

    def cancel(self) -> dict:
        return self.channel.cancel()
