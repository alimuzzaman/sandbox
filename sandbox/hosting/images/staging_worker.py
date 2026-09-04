"""Fixed helper orchestration and coherent observation validation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from sandbox.transports.remote_hosting_images import RegisteredRemoteImageTransport

from .staging_models import (
    LocalImageObservation, StageRequest, StagingContractError, StagingPolicy,
)


class StageWorkerError(RuntimeError):
    def __init__(self, code: str, *, process: dict | None = None,
                 cleanup: dict | None = None,
                 pull_failure: dict | None = None) -> None:
        self.code = code
        self.process = process
        self.cleanup = cleanup
        self.pull_failure = pull_failure
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class StageDeliveryFailure:
    """Secret-free delivery error returned through the broker callback seam.

    ``BrokerLease.consume`` deliberately does not let callback exceptions cross
    the credential boundary.  Staging still needs to distinguish a remote
    helper failure from a broker failure, so the callback converts only these
    known, already-bounded errors into a structured value.  No exception,
    traceback, or helper output is retained here.
    """

    kind: Literal["remote", "worker"]
    code: str
    process: dict | None = None
    cleanup: dict | None = None
    pull_failure: dict | None = None


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


class StageWorkerV2:
    """One helper invocation for all images in one verified plan set."""

    def __init__(self, transport: RegisteredRemoteImageTransport, *, timeout_seconds: int = 2100) -> None:
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def prepare(self, request, policy):
        from .staging_v2 import StageRequestSet, StagingPolicySet
        if type(request) is not StageRequestSet or type(policy) is not StagingPolicySet:
            raise StageWorkerError("observation_invalid")
        plan = request.plan_set
        images = [{"name": item.name, "repository": item.repository,
                   "repository_qualified_digest": item.image_ref,
                   "manifest_digest": item.manifest_digest,
                   "config_digest": item.config_digest, "platform": item.platform}
                  for item in plan.receipt.images]
        bindings = [{"service": row["service"], "image": row["image"],
                     "image_ref": row["image_ref"]}
                    for row in plan.as_mapping()["service_image_bindings"]]
        frame = {"schema_version": 2,
                 "unit_name": unit_name(request.request_id, request.request_digest),
                 "request_id": request.request_id,
                 "plan_set_digest": plan.plan_set_digest, "images": images,
                 "service_image_bindings": bindings, "target": policy.target.as_mapping(),
                 "helper": policy.helper.as_mapping()}
        channel = self.transport.prepare(
            plan.policy.target_scope.remote, frame, timeout_seconds=self.timeout_seconds)
        return _PreparedWorkerV2(request, policy, channel, frame)


class _PreparedWorker:
    def __init__(self, worker, request, policy, channel, frame):
        self.worker = worker; self.request = request; self.policy = policy
        self.channel = channel; self.frame = frame

    def deliver(self, credential: bytes) -> tuple[LocalImageObservation, dict, dict]:
        request = self.request; policy = self.policy
        projection = request.plan.delivery_identity_projection
        response = self.channel.deliver(credential)
        if response.schema_version != 1:
            raise StageWorkerError("observation_invalid")
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


class _PreparedWorkerV2:
    def __init__(self, request, policy, channel, frame):
        self.request = request; self.policy = policy
        self.channel = channel; self.frame = frame

    def deliver(self, credential: bytes):
        from .staging_v2 import BatchObservation
        response = self.channel.deliver(credential)
        if response.schema_version != 2:
            raise StageWorkerError("observation_invalid")
        if type(response.payload) is not dict or not {"process", "cleanup"} <= set(response.payload):
            raise StageWorkerError("observation_invalid")
        process = response.payload["process"]; cleanup = response.payload["cleanup"]
        expected_unit = self.frame["unit_name"]
        if type(process) is not dict or process.get("unit_name") != expected_unit \
                or process.get("delegated") is not False \
                or process.get("escape_allowed") is not False \
                or type(process.get("cgroup")) is not str \
                or expected_unit not in process["cgroup"] \
                or process.get("unit_inactive") is not True \
                or process.get("cgroup_empty_or_removed") is not True:
            raise StageWorkerError("process_unproven", process=process, cleanup=cleanup)
        if cleanup != {"complete": True}:
            raise StageWorkerError("cleanup_unproven", process=process, cleanup=cleanup)
        if not response.ok:
            fields = {"process", "cleanup"}
            pull_failure = None
            if response.code == "pull_failed":
                fields.add("pull_failure")
                raw_failure = response.payload.get("pull_failure")
                names = {item["name"] for item in self.frame["images"]}
                if type(raw_failure) is not dict \
                        or set(raw_failure) != {"image", "class"} \
                        or type(raw_failure.get("image")) is not str \
                        or raw_failure.get("image") not in names \
                        or type(raw_failure.get("class")) is not str \
                        or raw_failure.get("class") not in {
                            "denied", "not_found", "network", "timeout", "no_space", "daemon"}:
                    raise StageWorkerError("observation_invalid", process=process, cleanup=cleanup)
                pull_failure = dict(raw_failure)
            if set(response.payload) != fields:
                raise StageWorkerError("observation_invalid", process=process, cleanup=cleanup)
            raise StageWorkerError(response.code, process=process, cleanup=cleanup,
                                   pull_failure=pull_failure)
        if set(response.payload) != {"observation", "process", "cleanup"}:
            raise StageWorkerError("observation_invalid")
        try:
            observation = BatchObservation.from_mapping(response.payload["observation"])
        except (TypeError, StagingContractError):
            raise StageWorkerError("observation_invalid") from None
        expected = {item.name: item for item in self.request.plan_set.receipt.images}
        actual = {item.name: item for item in observation.images}
        if observation.target != self.policy.target or set(actual) != set(expected):
            raise StageWorkerError("observation_invalid")
        for name, image in expected.items():
            item = actual[name]
            if (item.repository, item.repo_digest, item.config_digest, item.platform) != (
                    image.repository, image.image_ref, image.config_digest, image.platform):
                raise StageWorkerError("observation_invalid")
        return observation, process, cleanup

    def cancel(self) -> dict:
        return self.channel.cancel()
