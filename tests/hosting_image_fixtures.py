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


# Feature 050 production ownership is intentionally explicit. These modules did
# not exist before the implementation-order waiver recorded in quickstart.md.
STAGING_PRODUCTION_FILES = (
    "sandbox/hosting/images/staging_models.py",
    "sandbox/hosting/images/staging_policy.py",
    "sandbox/hosting/images/staging_repository.py",
    "sandbox/hosting/images/staging_service.py",
    "sandbox/hosting/images/staging_worker.py",
    "sandbox/hosting/images/staging_helper.py",
    "sandbox/transports/remote_hosting_images.py",
)


def staging_target():
    from sandbox.hosting.images.staging_models import StagingTarget
    return StagingTarget("machine-a", "target-a", "daemon-a")


def staging_policy():
    from sandbox.hosting.images import validate_verified_image_plan
    from sandbox.hosting.images.staging_models import (
        HelperIdentity, StagingPolicy, staging_digest,
    )
    plan = validate_verified_image_plan(verified_plan_mapping())
    helper = HelperIdentity("sha256:" + "9" * 64, "sandbox-image-stage-helper-v1",
                            "a" * 40, "systemd-cgroup-v2-stage-v1")
    target = staging_target()
    projection = plan.delivery_identity_projection
    values = {"schema_version": 1, "plan_digest": plan.plan_digest,
              "target": target.as_mapping(), "helper": helper.as_mapping(),
              "broker_recipient": f"ghcr-repository-read:{projection.image.repository}@{projection.image.manifest_digest}",
              "broker_binding_id": "binding-a", "broker_binding_version": 3,
              "credential_reference_revision": "credential-revision-a",
              "operation": "ghcr.repository.read",
              "capability_revision": "stage-capability-v1",
              "delivery_identity_projection": projection.as_mapping()}
    digest = staging_digest("sandbox.hosting.images.staging-policy.v1", values)
    return StagingPolicy(1, digest, plan.plan_digest, target, helper,
                         values["broker_recipient"], "binding-a", 3,
                         "credential-revision-a", "ghcr.repository.read",
                         "stage-capability-v1", projection)


def stage_request(*, request_id="stage-request-a", generation=0, policy=None):
    from sandbox.hosting.images import validate_verified_image_plan
    from sandbox.hosting.images.staging_models import StageRequest
    policy = policy or staging_policy()
    return StageRequest.create(request_id=request_id, expected_generation=generation,
                               plan=validate_verified_image_plan(verified_plan_mapping()),
                               staging_policy_digest=policy.policy_digest,
                               target=policy.target, confirmed=True)


def local_observation(policy=None):
    from sandbox.hosting.images.staging_models import LocalImageObservation, staging_digest
    policy = policy or staging_policy(); projection = policy.projection
    registry = {"anonymous_exact_manifest": "denied",
                "authenticated_exact_manifest": "succeeded"}
    registry["observation_digest"] = staging_digest(
        "sandbox.hosting.images.registry-observation.v1", registry)
    values = {"target_epoch_start": "machine-a", "target_epoch_end": "machine-a",
              "daemon_epoch_start": "daemon-a", "daemon_epoch_end": "daemon-a",
              "target": policy.target.as_mapping(), "repository": projection.image.repository,
              "repo_digest": projection.image.repository_qualified_digest,
              "config_digest": projection.image.config_digest,
              "platform": projection.image.platform.as_mapping(),
              "local_image_id": projection.image.config_digest,
              "topology_digest": staging_digest("sandbox.hosting.images.topology.v1",
                                                 projection.topology.as_mapping()),
              "observed_topology": projection.topology.as_mapping(), **registry}
    identity = staging_digest("sandbox.hosting.images.local-observation.v1", values)
    return LocalImageObservation(identity, values["target_epoch_start"],
                                 values["target_epoch_end"], values["daemon_epoch_start"],
                                 values["daemon_epoch_end"], policy.target,
                                 values["repository"], values["repo_digest"],
                                 values["config_digest"], values["platform"],
                                 values["local_image_id"], values["topology_digest"],
                                 values["observed_topology"],
                                 registry["anonymous_exact_manifest"],
                                 registry["authenticated_exact_manifest"],
                                 registry["observation_digest"])


class FakeBroker:
    def __init__(self, credential=b"synthetic-stage-canary"):
        self.credential = credential; self.calls = []
    def consume_for_stage(self, **kwargs):
        self.calls.append({key: value for key, value in kwargs.items() if key != "consumer"})
        return kwargs["consumer"](self.credential)
    def prepare_for_stage(self, **kwargs):
        self.calls.append(dict(kwargs))
        credential = self.credential
        class Lease:
            def __init__(self): self.used = False
            def consume(self, consumer):
                if self.used: raise RuntimeError("used")
                self.used = True; return consumer(credential)
            def invalidate(self): self.used = True
        return Lease()


class FakePreparedWorker:
    def __init__(self, policy): self.policy = policy; self.calls = []; self.frame = {"unit_name": "sandbox-image-stage-fake.service"}
    def deliver(self, credential):
        self.calls.append(len(credential))
        return local_observation(self.policy), {
            "unit_name": self.frame["unit_name"],
            "cgroup": ("/user.slice/user-1000.slice/user@1000.service/app.slice/"
                       + self.frame["unit_name"]), "delegated": False,
            "escape_allowed": False, "unit_inactive": True, "cgroup_empty_or_removed": True,
        }, {"complete": True}
    def cancel(self):
        self.calls.append("cancel")
        return {"unit_inactive": True, "cgroup_empty_or_removed": True,
                "cleanup_complete": True}


class FakeWorker:
    def __init__(self): self.calls = []; self.prepared = None
    def prepare(self, request, policy):
        self.calls.append((request.request_id, policy.policy_digest))
        self.prepared = FakePreparedWorker(policy)
        return self.prepared


def description_drift_transport(helper_mapping, schema_version):
    """Real registered transport seam with one active mismatched incumbent."""
    import io
    import json
    import os
    import subprocess
    from sandbox.transports.remote_hosting_images import RegisteredRemoteImageTransport
    commands = []
    class Process:
        stdin = io.BytesIO(); stdout = io.BytesIO(); stderr = io.BytesIO()
        def read_ready(self, _timeout): return b"READY\n"
        def kill(self): self.killed = True
    process = Process()
    class Sender:
        def __init__(self): self.prepares = 0
        def prepare(self, _remote, argv, **_kwargs):
            self.prepares += 1; process.argv = argv; return process
    sender = Sender()
    def observe(_remote, command, timeout):
        commands.append(command)
        if command == "id -u":
            return subprocess.CompletedProcess((), 0, stdout=str(os.geteuid()) + "\n")
        if command.startswith("sha256sum"):
            return subprocess.CompletedProcess((), 0, stdout="9" * 64 + "  helper\n")
        if "manifest" in command:
            return subprocess.CompletedProcess((), 0, stdout=json.dumps(
                {"schema_version": schema_version, **helper_mapping}))
        unit = next((item.split("=", 1)[1] for item in getattr(process, "argv", ())
                     if item.startswith("--unit=")), "unknown.service")
        cgroup = (f"/user.slice/user-{os.geteuid()}.slice/user@{os.geteuid()}.service/"
                  f"app.slice/{unit}")
        if "--property=ProtectControlGroups" in command:
            return subprocess.CompletedProcess((), 0, stdout=(
                f"ActiveState=active\nDescription=incumbent\nControlGroup={cgroup}\n"
                "KillMode=control-group\nDelegate=no\nNoNewPrivileges=yes\n"
                "RestrictSUIDSGID=yes\nProtectControlGroups=yes\n"))
        if "--property=LoadState" in command:
            return subprocess.CompletedProcess((), 0, stdout=(
                f"LoadState=loaded\nActiveState=active\nDescription=incumbent\n"
                f"ControlGroup={cgroup}\n"))
        return subprocess.CompletedProcess((), 0, stdout="")
    transport = RegisteredRemoteImageTransport(
        remote_lookup=lambda _name: {"provisioned": True}, ssh_private_frame=sender,
        unit_observer=observe, resolve_home=lambda _remote: "/home/alim/sandbox")
    return transport, sender, commands
