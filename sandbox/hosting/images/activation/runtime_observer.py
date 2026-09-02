"""Coherent exact local/running observation for Feature 051."""

from __future__ import annotations

from .models import (
    ActivationContractError, RunningObservation, VerifiedActivationGeneration,
    activation_digest,
)


def validate_rendered_topology(rendered: object, *, selected_services: tuple[str, ...],
                               exact_image: str, exact_platform: dict,
                               exact_topology_digest: str,
                               exact_service_projection: tuple[dict, ...],
                               exact_runtime_epoch: str,
                               allowed_dependencies: tuple[str, ...] = ()) -> dict:
    if type(rendered) is not dict or set(rendered) != {"services", "orphans", "runtime_epoch"} \
            or type(rendered["services"]) is not dict or rendered["orphans"] != [] \
            or rendered["runtime_epoch"] != exact_runtime_epoch:
        raise ActivationContractError("topology_mismatch")
    if set(rendered["services"]) != set(selected_services):
        raise ActivationContractError("topology_mismatch")
    expected_projection = {item.get("service"): {
        key: value for key, value in item.items() if key != "service"}
        for item in exact_service_projection}
    if set(expected_projection) != set(selected_services):
        raise ActivationContractError("topology_mismatch")
    for name, service in rendered["services"].items():
        required = {"image", "build", "pull_policy", "platform", "dependencies",
                    "topology_identity", "configuration_digest"}
        if type(service) is not dict or set(service) != required \
                or service["image"] != exact_image or service["build"] is not None \
                or service["pull_policy"] not in {"never", "missing-refused"} \
                or service["platform"] != exact_platform \
                or service["topology_identity"] != exact_topology_digest \
                or service != expected_projection.get(name) \
                or not set(service["dependencies"]) <= set(allowed_dependencies):
            raise ActivationContractError("topology_mismatch")
        image_name, separator, _digest = service["image"].partition("@")
        if not separator or "@sha256:" not in service["image"] \
                or ":" in image_name.rsplit("/", 1)[-1]:
            raise ActivationContractError("topology_mismatch")
    return rendered


class RuntimeObserver:
    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def prove_local(self, *, target: dict, proof) -> dict:
        observed = self.adapter.observe_local_image(
            target=target, repository_digest=proof.observed_identity["repo_digest"])
        expected = proof.observed_identity
        return self._validate_local(observed, expected)

    def prove_generation_local(self, *, target: dict,
                               generation: VerifiedActivationGeneration) -> dict:
        if type(generation) is not VerifiedActivationGeneration or generation.target != target:
            raise ActivationContractError("local_image_mismatch")
        image = generation.image
        exact_image = image.get("repository_qualified_digest")
        observed = self.adapter.observe_local_image(
            target=target, repository_digest=exact_image)
        expected = {
            "repository": image.get("repository"),
            "repo_digest": exact_image,
            "config_digest": image.get("config_digest"),
            "platform": image.get("platform"),
            "local_image_id": image.get("config_digest"),
            "target_epoch_start": target.get("machine_identity"),
            "target_epoch_end": target.get("machine_identity"),
            "daemon_epoch_start": target.get("daemon_identity"),
            "daemon_epoch_end": target.get("daemon_identity"),
        }
        return self._validate_local(observed, expected)

    @staticmethod
    def _validate_local(observed: object, expected: dict) -> dict:
        fields = ("repository", "repo_digest", "config_digest", "platform", "local_image_id",
                  "target_epoch_start", "target_epoch_end", "daemon_epoch_start", "daemon_epoch_end")
        if type(observed) is not dict or any(observed.get(name) != expected.get(name) for name in fields):
            raise ActivationContractError("local_image_mismatch")
        return observed

    def observe(self, *, target: dict, selected_services: tuple[str, ...],
                exact_image: str, local_image_id: str, config_digest: str,
                platform: dict, topology_digest: str, edge_identity: str) -> RunningObservation:
        raw = self.adapter.observe_running(target=target, services=selected_services)
        if type(raw) is not dict or set(raw) != {
                "target_epoch_start", "target_epoch_end", "runtime_epoch_start",
                "runtime_epoch_end", "services"}:
            raise ActivationContractError("runtime_mismatch")
        if raw["target_epoch_start"] != raw["target_epoch_end"] \
                or raw["runtime_epoch_start"] != raw["runtime_epoch_end"] \
                or raw["target_epoch_start"] != target.get("machine_identity") \
                or raw["runtime_epoch_start"] != target.get("daemon_identity"):
            raise ActivationContractError("runtime_mismatch")
        services = raw["services"]
        if type(services) is not list or len(services) != len(selected_services):
            raise ActivationContractError("runtime_mismatch")
        normalized = []
        for service in services:
            if type(service) is not dict or service.get("service") not in selected_services \
                    or service.get("declared_image") != exact_image \
                    or service.get("repository_digest") != exact_image \
                    or service.get("local_image_id") != local_image_id \
                    or service.get("config_digest") != config_digest \
                    or service.get("platform") != platform \
                    or service.get("topology_identity") != topology_digest \
                    or service.get("healthy") is not True:
                raise ActivationContractError("health_incomplete")
            normalized.append(service)
        if {item["service"] for item in normalized} != set(selected_services):
            raise ActivationContractError("runtime_mismatch")
        body = {"target": target, "target_epoch_start": raw["target_epoch_start"],
                "target_epoch_end": raw["target_epoch_end"],
                "runtime_epoch_start": raw["runtime_epoch_start"],
                "runtime_epoch_end": raw["runtime_epoch_end"],
                "services": sorted(normalized, key=lambda item: item["service"]),
                "topology_digest": topology_digest, "health_complete": True,
                "edge_identity": edge_identity}
        return RunningObservation(**body, observation_digest=activation_digest(
            "sandbox.hosting.images.running-observation.v1", body))
