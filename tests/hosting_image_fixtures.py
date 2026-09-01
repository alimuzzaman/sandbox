"""Public, secret-free builders for Feature 049 pure contract tests."""

from __future__ import annotations

from copy import deepcopy

from sandbox.hosting.images import machine_policy_digest, receipt_payload_digest
from sandbox.config.hosting_images import (
    normalize_machine_image_policies,
    normalize_project_image_intent,
    normalize_release_receipt,
)


MANIFEST_DIGEST = "sha256:" + "1" * 64
CONFIG_DIGEST = "sha256:" + "2" * 64
SOURCE_REVISION = "3" * 40
BUILD_IDENTITY = "sha256:" + "5" * 64
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"


def receipt_payload_mapping(**changes):
    value = {
        "schema_version": 1,
        "repository": "acme/widget",
        "manifest_digest": MANIFEST_DIGEST,
        "config_digest": CONFIG_DIGEST,
        "platform": {"os": "linux", "architecture": "amd64"},
        "manifest_media_type": OCI_MANIFEST,
        "source_repository": "acme/widget-source",
        "source_revision": SOURCE_REVISION,
        "build_identity": BUILD_IDENTITY,
        "provenance": {
            "builder_id": "sha256:" + "6" * 64,
            "workflow_id": "sha256:" + "7" * 64,
            "invocation_id": "sha256:" + "8" * 64,
            "materials_digest": "sha256:" + "4" * 64,
        },
        "signature_mode": "not_required",
    }
    value.update(deepcopy(changes))
    return value


def receipt_mapping(*, payload_changes=None, **changes):
    payload = receipt_payload_mapping(**(payload_changes or {}))
    value = {
        "payload": payload,
        "payload_digest": receipt_payload_digest(payload),
    }
    value.update(deepcopy(changes))
    return value


def project_intent_mapping(**changes):
    value = {
        "schema_version": 1,
        "policy_selector": "production-widget",
        "declared_services": ["db", "migrate", "web", "worker"],
        "persistent_services": ["web", "worker"],
        "one_shot_services": ["migrate"],
    }
    value.update(deepcopy(changes))
    return value


def policy_mapping(*, receipt=None, **changes):
    receipt = receipt or receipt_mapping()
    payload = receipt["payload"]
    value = {
        "schema_version": 1,
        "authority_id": "machine-policy/controller-a",
        "policy_selector": "production-widget",
        "policy_revision": 7,
        "target_scope": {
            "remote": "production-a", "project": "widget", "environment": "production",
        },
        "repository": payload["repository"],
        "approved_receipt_payload_digest": receipt["payload_digest"],
        "image": {
            "registry": "ghcr.io", "repository": payload["repository"],
            "manifest_digest": payload["manifest_digest"],
            "config_digest": payload["config_digest"],
            "platform": deepcopy(payload["platform"]),
            "manifest_media_type": payload["manifest_media_type"],
        },
        "source_repository": payload["source_repository"],
        "source_revision": payload["source_revision"],
        "build_identity": payload["build_identity"],
        "provenance": deepcopy(payload["provenance"]),
        "signature_mode": "not_required",
        "primary_service": "web",
        "allowed_persistent_services": ["web", "worker"],
        "allowed_one_shot_services": ["migrate", "seed"],
    }
    supplied_digest = changes.pop("policy_digest", None)
    value.update(deepcopy(changes))
    value["policy_digest"] = supplied_digest or machine_policy_digest(value)
    return value


def valid_channel_mappings():
    receipt = receipt_mapping()
    return policy_mapping(receipt=receipt), project_intent_mapping(), receipt


def valid_channels():
    policy_raw, project_raw, receipt_raw = valid_channel_mappings()
    return channel_objects(policy_raw, project_raw, receipt_raw)


def channel_objects(policy_raw, project_raw, receipt_raw):
    policies = normalize_machine_image_policies({"production-widget": policy_raw})
    project = normalize_project_image_intent(project_raw)
    return (
        next(iter(policies.values())),
        project,
        normalize_release_receipt(receipt_raw),
    )


EXPECTED_RECEIPT_DIGEST = "sha256:13cb67b3cf4997fab99b50a2343d1d8ecb3975a8d4c2c1ac7aa4d6f2fa581b78"
EXPECTED_POLICY_DIGEST = "sha256:91b467b049a37763b3351ab435721b350bec0c2542888080573585d6df072fc7"
EXPECTED_PLAN_DIGEST = "sha256:fd883c02ad8dd7d7bfe20a9b168e95315d7c1bcc8235d1e3c78c2a3cf916fd68"
EXPECTED_PLAN_CANONICAL_BYTES = (
    b'{"authority":{"authority_id":"machine-policy/controller-a","policy_digest":"sha256:91b467b049a37763b3351ab435721b350bec0c2542888080573585d6df072fc7","policy_revision":7,"target_scope":{"environment":"production","project":"widget","remote":"production-a"}},'
    b'"delivery_identity_projection":{"config_digest":"sha256:2222222222222222222222222222222222222222222222222222222222222222","intended_visibility":"private","manifest_digest":"sha256:1111111111111111111111111111111111111111111111111111111111111111","manifest_media_type":"application/vnd.oci.image.manifest.v1+json","platform":{"architecture":"amd64","os":"linux"},"registry":"ghcr.io","repository":"acme/widget","repository_qualified_digest":"ghcr.io/acme/widget@sha256:1111111111111111111111111111111111111111111111111111111111111111","target_scope":{"environment":"production","project":"widget","remote":"production-a"},"topology":{"one_shot_services":["migrate"],"persistent_services":["web","worker"]}},'
    b'"image":{"config_digest":"sha256:2222222222222222222222222222222222222222222222222222222222222222","manifest_digest":"sha256:1111111111111111111111111111111111111111111111111111111111111111","manifest_media_type":"application/vnd.oci.image.manifest.v1+json","platform":{"architecture":"amd64","os":"linux"},"registry":"ghcr.io","repository":"acme/widget"},'
    b'"plan_digest":"sha256:fd883c02ad8dd7d7bfe20a9b168e95315d7c1bcc8235d1e3c78c2a3cf916fd68","receipt":{"build_identity":"sha256:5555555555555555555555555555555555555555555555555555555555555555","payload_digest":"sha256:13cb67b3cf4997fab99b50a2343d1d8ecb3975a8d4c2c1ac7aa4d6f2fa581b78","provenance":{"builder_id":"sha256:6666666666666666666666666666666666666666666666666666666666666666","invocation_id":"sha256:8888888888888888888888888888888888888888888888888888888888888888","materials_digest":"sha256:4444444444444444444444444444444444444444444444444444444444444444","workflow_id":"sha256:7777777777777777777777777777777777777777777777777777777777777777"},"source_repository":"acme/widget-source","source_revision":"3333333333333333333333333333333333333333"},"schema_version":1,"signature_mode":"not_required","topology":{"one_shot_services":["migrate"],"persistent_services":["web","worker"]}}'
)


def verified_plan_mapping():
    """Independent fixed vector; never asks production verification for expected data."""
    payload = receipt_payload_mapping()
    topology = {"persistent_services": ["web", "worker"], "one_shot_services": ["migrate"]}
    image = {
        "registry": "ghcr.io", "repository": "acme/widget",
        "manifest_digest": MANIFEST_DIGEST, "config_digest": CONFIG_DIGEST,
        "platform": {"os": "linux", "architecture": "amd64"},
        "manifest_media_type": OCI_MANIFEST,
    }
    target = {"remote": "production-a", "project": "widget", "environment": "production"}
    projection = {
        "target_scope": deepcopy(target), **deepcopy(image),
        "repository_qualified_digest": f"ghcr.io/acme/widget@{MANIFEST_DIGEST}",
        "topology": deepcopy(topology), "intended_visibility": "private",
    }
    return {
        "schema_version": 1,
        "authority": {
            "authority_id": "machine-policy/controller-a", "policy_revision": 7,
            "policy_digest": EXPECTED_POLICY_DIGEST, "target_scope": deepcopy(target),
        },
        "receipt": {
            "payload_digest": EXPECTED_RECEIPT_DIGEST,
            "source_repository": payload["source_repository"],
            "source_revision": payload["source_revision"],
            "build_identity": payload["build_identity"],
            "provenance": deepcopy(payload["provenance"]),
        },
        "image": deepcopy(image), "delivery_identity_projection": projection,
        "topology": deepcopy(topology), "signature_mode": "not_required",
        "plan_digest": EXPECTED_PLAN_DIGEST,
    }


def reverse_objects(value):
    if isinstance(value, dict):
        return {key: reverse_objects(item) for key, item in reversed(tuple(value.items()))}
    if isinstance(value, list):
        return [reverse_objects(item) for item in value]
    return value
