"""Exact multi-image runtime validation for immutable activation v2."""

from __future__ import annotations

from typing import Any

from .models import ActivationContractError, SECRET_FIELDS, _digest, _safe_mapping, activation_digest
from .v2_models import VerifiedActivationGenerationV2


PLATFORM = {"os": "linux", "architecture": "amd64"}


def plan_set_bindings(plan) -> tuple[dict[str, Any], ...]:
    images = {item.name: item for item in plan.receipt.images}
    variables = dict(plan.policy.activation_environment_bindings)
    rows = []
    for service, image_name in plan.policy.service_image_bindings:
        if service not in plan.policy.persistent_services:
            continue
        image = images[image_name]
        rows.append({"service": service, "image": image_name,
                     "image_ref": image.image_ref,
                     "environment_variable": variables[image_name]})
    return tuple(rows)


def validate_rendered_topology_v2(rendered: object, *, request) -> dict[str, Any]:
    if type(rendered) is not dict or set(rendered) != {
            "services", "orphans", "runtime_epoch", "configuration_digest"} \
            or type(rendered["services"]) is not dict or rendered["orphans"] != []:
        raise ActivationContractError("topology_mismatch")
    proof = request.proof_set
    target = proof["target"]
    snapshot = request.compose_snapshot
    if (rendered["runtime_epoch"] != target["daemon_identity"]
            or rendered["configuration_digest"] != snapshot.configuration_digest):
        raise ActivationContractError("topology_mismatch")
    bindings = {item["service"]: item for item in plan_set_bindings(request.plan_set)}
    if set(rendered["services"]) != set(bindings):
        raise ActivationContractError("topology_mismatch")
    required = frozenset({
        "image", "build", "pull_policy", "platform", "dependencies",
        "topology_identity", "compose_config_hash", "configuration_digest",
    })
    safe = {}
    for service, value in rendered["services"].items():
        row = _safe_mapping(value, forbidden=SECRET_FIELDS)
        if (set(row) != required or row["image"] != bindings[service]["image_ref"]
                or row["build"] is not None or row["pull_policy"] != "never"
                or row["platform"] != PLATFORM or type(row["dependencies"]) is not list
                or row["topology_identity"] != proof["observation"]["observation_digest"]):
            raise ActivationContractError("topology_mismatch")
        _digest(row["compose_config_hash"]); _digest(row["configuration_digest"])
        safe[service] = row
    return {**rendered, "services": safe}


class RuntimeObserverV2:
    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def prove_all_local(self, request) -> tuple[dict[str, Any], ...]:
        target = request.proof_set["target"]
        expected = request.proof_set["observation"]["images"]
        proven = []
        for image in expected:
            observed = self.adapter.observe_local_image(
                target=target, repository_digest=image["repo_digest"],
                config_digest=image["config_digest"])
            comparable = {
                "repository": image["repository"].split("/", 1)[1],
                "repo_digest": image["repo_digest"],
                "config_digest": image["config_digest"], "local_image_id": image["local_image_id"],
                "platform": PLATFORM, "target_epoch_start": target["machine_identity"],
                "target_epoch_end": target["machine_identity"],
                "target_identity_start": target["target_identity"],
                "target_identity_end": target["target_identity"],
                "daemon_epoch_start": target["daemon_identity"],
                "daemon_epoch_end": target["daemon_identity"],
            }
            if type(observed) is not dict or any(
                    observed.get(key) != value for key, value in comparable.items()):
                raise ActivationContractError("local_image_mismatch")
            proven.append({"name": image["name"], "image_ref": image["repo_digest"],
                           "config_digest": image["config_digest"],
                           "platform": PLATFORM, "local_image_id": image["local_image_id"]})
        return tuple(proven)

    def prove_generation_local(self, generation: VerifiedActivationGenerationV2) -> None:
        if type(generation) is not VerifiedActivationGenerationV2:
            raise ActivationContractError("local_image_mismatch")
        for image in generation.images:
            observed = self.adapter.observe_local_image(
                target=generation.target, repository_digest=image["image_ref"],
                config_digest=image["config_digest"])
            expected = {"repository": image["image_ref"].split("@", 1)[0].split("/", 1)[1],
                        "repo_digest": image["image_ref"],
                        "config_digest": image["config_digest"],
                        "local_image_id": image["local_image_id"],
                        "platform": image["platform"],
                        "target_epoch_start": generation.target["machine_identity"],
                        "target_epoch_end": generation.target["machine_identity"],
                        "target_identity_start": generation.target["target_identity"],
                        "target_identity_end": generation.target["target_identity"],
                        "daemon_epoch_start": generation.target["daemon_identity"],
                        "daemon_epoch_end": generation.target["daemon_identity"]}
            if type(observed) is not dict or any(observed.get(k) != v for k, v in expected.items()):
                raise ActivationContractError("local_image_mismatch")

    def observe(self, *, target: dict[str, str], compose_project: str,
                bindings: tuple[dict[str, Any], ...], images: tuple[dict[str, Any], ...],
                topology_digest: str, compose_config_hashes: dict[str, str],
                edge_identity: str, snapshot_digest: str) -> dict[str, Any]:
        selected = tuple(item["service"] for item in bindings)
        by_service = {item["service"]: item for item in bindings}
        by_image = {item["name"]: item for item in images}
        image_identities = {
            service: {
                "image_ref": by_image[binding["image"]]["image_ref"],
                "config_digest": by_image[binding["image"]]["config_digest"],
                "local_image_id": by_image[binding["image"]]["local_image_id"],
            }
            for service, binding in by_service.items()
        }
        raw = self.adapter.observe_running_v2(
            target=target, services=selected, compose_project=compose_project,
            topology_digest=topology_digest,
            compose_config_hashes=compose_config_hashes,
            snapshot_digest=snapshot_digest, image_identities=image_identities)
        if type(raw) is not dict or set(raw) != {
                "target_epoch_start", "target_epoch_end", "target_identity_start",
                "target_identity_end", "runtime_epoch_start", "runtime_epoch_end", "services"}:
            raise ActivationContractError("runtime_mismatch")
        if (raw["target_epoch_start"] != raw["target_epoch_end"]
                or raw["target_epoch_start"] != target["machine_identity"]
                or raw["target_identity_start"] != raw["target_identity_end"]
                or raw["target_identity_start"] != target["target_identity"]
                or raw["runtime_epoch_start"] != raw["runtime_epoch_end"]
                or raw["runtime_epoch_start"] != target["daemon_identity"]):
            raise ActivationContractError("runtime_mismatch")
        rows = raw["services"]
        if type(rows) is not list or len(rows) != len(bindings):
            raise ActivationContractError("runtime_mismatch")
        safe = []
        for value in rows:
            row = _safe_mapping(value, forbidden=SECRET_FIELDS)
            binding = by_service.get(row.get("service")); image = by_image.get(
                binding["image"] if binding else None)
            if (binding is None or image is None
                    or row.get("declared_image") != binding["image_ref"]
                    or row.get("repository_digest") != binding["image_ref"]
                    or row.get("local_image_id") != image["local_image_id"]
                    or row.get("config_digest") != image["config_digest"]
                    or row.get("platform") != image["platform"]
                    or row.get("topology_identity") != topology_digest
                    or row.get("compose_project") != compose_project
                    or row.get("compose_config_hash") != compose_config_hashes.get(row.get("service"))
                    or row.get("healthy") is not True):
                raise ActivationContractError("runtime_mismatch")
            safe.append(row)
        if {item["service"] for item in safe} != set(selected):
            raise ActivationContractError("runtime_mismatch")
        body = {"schema_version": 2, "target": target,
                "target_epoch_start": raw["target_epoch_start"],
                "target_epoch_end": raw["target_epoch_end"],
                "target_identity_start": raw["target_identity_start"],
                "target_identity_end": raw["target_identity_end"],
                "runtime_epoch_start": raw["runtime_epoch_start"],
                "runtime_epoch_end": raw["runtime_epoch_end"],
                "services": sorted(safe, key=lambda item: item["service"]),
                "topology_digest": topology_digest, "edge_identity": edge_identity}
        return {**body, "observation_digest": activation_digest(
            "sandbox.hosting.images.running-observation.v2", body)}


__all__ = ("PLATFORM", "RuntimeObserverV2", "plan_set_bindings",
           "validate_rendered_topology_v2")
