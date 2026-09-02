"""Closed synthetic Feature 051 fixtures. No live registry/remote/edge access."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from sandbox.hosting.images import validate_verified_image_plan
from sandbox.hosting.images.activation.models import (
    ActivationAuthorityBinding, ActivationPolicy, ActivationRequest,
    ForwardRollbackSubject, RollbackCompatibilityGrant, activation_digest,
)
from sandbox.hosting.images.staging_models import StagedImageProof
from tests.hosting_image_fixtures import (
    local_observation, stage_request, staging_policy, verified_plan_mapping,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
ROLLBACK_VERIFICATION_DIGEST = "sha256:" + "c" * 64


def staged_proof():
    policy = staging_policy(); request = stage_request(policy=policy)
    return StagedImageProof.create(request, policy, local_observation(policy), 1)


def init_declaration():
    value = {"name": "migrate", "service": "migrate", "command": ["migrate", "--once"],
             "mounts": ["type=volume,source=data,target=/data"], "networks": ["default"],
             "environment_keys": ["DATABASE_URL"], "privileged": False,
             "dependencies": [], "timeout_seconds": 60, "configuration_digest": DIGEST_A}
    return value


def activation_policy(*, zero_init=False):
    proof = staged_proof()
    plan = validate_verified_image_plan(verified_plan_mapping())
    compose_projection = [{"service": name, "image": plan.image.repository_qualified_digest,
        "build": None, "pull_policy": "never", "platform": plan.image.platform.as_mapping(),
                   "dependencies": [], "topology_identity": proof.observed_identity["topology_digest"],
                   "compose_config_hash": DIGEST_B,
                   "configuration_digest": DIGEST_A} for name in ("web", "worker")]
    edge_routes = [{"hostname": "example.test", "mode": "serve", "target": None,
                    "primary": True, "healthcheck_path": "/health"}]
    edge_digest = activation_digest(
        "sandbox.hosting.images.activation-edge-routes.v1", edge_routes)
    values = {"schema_version": 1, "authority_id": "activation-policy/controller-a",
              "authority_revision": "activation-policy-v1", "target": proof.target.as_mapping(),
              "selected_services": ["web", "worker"],
              "compose_projection": compose_projection,
              "init_declarations": [] if zero_init else [init_declaration()],
              "runtime_capability_revision": "docker-compose-v2",
              "compose_capability_revision": "compose-no-build-pull-never-v1",
              "edge_policy_digest": edge_digest, "edge_required": True,
              "edge_route_plan": edge_routes, "edge_route_digest": edge_digest,
              "mutation_owner_revision": "shared-target-owner-v1",
              "state_revision": "activation-state-v1",
              "accepted_plan_schema": 1, "accepted_proof_schema": 1}
    digest = activation_digest("sandbox.hosting.images.activation-policy.v1", values)
    return ActivationPolicy.from_mapping({**values, "policy_digest": digest})


def authority_binding(*, policy=None):
    plan = validate_verified_image_plan(verified_plan_mapping()); proof = staged_proof()
    policy = policy or activation_policy()
    values = {"schema_version": 1, "authority_id": "activation-authority/controller-a",
              "authority_revision": "activation-authority-v1",
              "plan_digest": plan.plan_digest, "proof_digest": proof.proof_digest,
              "stage_request_id": proof.request_id, "stage_request_digest": proof.request_digest,
              "staging_policy_digest": proof.staging_policy_digest,
              "staging_generation": proof.staging_generation,
              "stage_ledger_authority": "feature-050-stage-ledger-v2",
              "stage_ledger_revision": 7, "target": proof.target.as_mapping(),
              "delivery_identity_projection": plan.delivery_identity_projection.as_mapping(),
              "policy_digest": policy.policy_digest,
              "rollback_grant_authority_id": "rollback-authority/controller-a",
              "rollback_grant_authority_revision": "rollback-authority-v1",
              "rollback_grant_verification_digest": ROLLBACK_VERIFICATION_DIGEST}
    digest = activation_digest("sandbox.hosting.images.activation-authority.v1", values)
    return ActivationAuthorityBinding.from_mapping({**values, "binding_digest": digest})


def activation_request(*, operation="activate", zero_init=False, generation=0,
                       request_id="activation-a", policy=None, subject=None, grant=None):
    plan = validate_verified_image_plan(verified_plan_mapping()); proof = staged_proof()
    policy = policy or activation_policy(zero_init=zero_init); binding = authority_binding(policy=policy)
    subject = subject or rollback_subject(
        policy, plan=plan, proof=proof, binding=binding, generation=generation)
    grant = grant or rollback_grant(subject)
    return ActivationRequest.create(
        request_id=request_id, operation=operation, expected_generation=generation,
        policy_digest=policy.policy_digest, plan=plan, proof=proof,
        authority_binding_digest=binding.binding_digest,
        rollback_subject_digest=subject.subject_digest,
        rollback_grant_digest=grant.grant_digest, confirmed=True)


def rollback_subject(policy=None, *, plan=None, proof=None, binding=None, generation=0):
    policy = policy or activation_policy()
    plan = plan or validate_verified_image_plan(verified_plan_mapping())
    proof = proof or staged_proof()
    binding = binding or authority_binding(policy=policy)
    genesis = activation_digest("sandbox.hosting.images.activation-genesis.v1",
        {"target": proof.target.as_mapping(), "generation": generation})
    body = {"target": proof.target.as_mapping(),
            "rollback_target_generation_digest": genesis,
            "candidate_plan_digest": plan.plan_digest,
            "candidate_proof_digest": proof.proof_digest,
            "activation_authority_digest": binding.binding_digest,
            "configuration_digest": DIGEST_B,
            "topology_digest": proof.observed_identity["topology_digest"],
            "init_data_contract_digest": DIGEST_B,
            "policy_revision": "activation-policy-v1"}
    return ForwardRollbackSubject(**body, subject_digest=activation_digest(
        "sandbox.hosting.images.forward-rollback-subject.v1", body))


def rollback_grant(subject=None):
    subject = subject or rollback_subject()
    unsigned = {"authority_id": "rollback-authority/controller-a",
            "authority_revision": "rollback-authority-v1", "issued_at": 1,
            "policy_revision": "activation-policy-v1", "subject": subject.as_mapping(),
            "expires_at": None, "revoked": False}
    proof = "fixture-authority-proof"
    body = {**unsigned, "authority_proof": proof}
    return RollbackCompatibilityGrant(
        body["authority_id"], body["authority_revision"], body["issued_at"],
        body["policy_revision"], subject, proof,
        activation_digest("sandbox.hosting.images.rollback-grant.v1", body), None, False)


def admission_deadline():
    return (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class ForbiddenWitnesses:
    def __init__(self):
        self.calls = []
    def __getattr__(self, name):
        def forbidden(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"forbidden capability reached: {name}")
        return forbidden


class FakeRollbackGrantVerifier:
    verification_digest = ROLLBACK_VERIFICATION_DIGEST
    def verify(self, grant):
        return grant.authority_proof == "fixture-authority-proof"


class FakeRuntime:
    def __init__(self):
        self.calls = []; self.started = 0; self.replacements = 0
        self.render_selectors = []
    def render_topology(self, **kwargs):
        self.calls.append("render")
        self.render_selectors.append(dict(kwargs))
        image = next(iter(kwargs["image_overrides"].values()))
        return {"runtime_epoch": "daemon-a", "orphans": [],
            "configuration_digest": DIGEST_B, "services": {
            name: {"image": image, "build": None, "pull_policy": "never",
                   "platform": {"os": "linux", "architecture": "amd64"},
                   "dependencies": [], "topology_identity": staged_proof().observed_identity["topology_digest"],
                   "compose_config_hash": DIGEST_B,
                   "configuration_digest": DIGEST_A}
            for name in kwargs["selected_services"]}}
    def observe_local_image(self, **kwargs):
        proof = staged_proof(); return {**proof.observed_identity,
            "target_identity_start": proof.target.target_identity,
            "target_identity_end": proof.target.target_identity}
    def observe_running(self, **kwargs):
        plan = validate_verified_image_plan(verified_plan_mapping())
        return {"target_epoch_start": "machine-a", "target_epoch_end": "machine-a",
                "target_identity_start": "target-a", "target_identity_end": "target-a",
                "runtime_epoch_start": "daemon-a", "runtime_epoch_end": "daemon-a",
                "services": [{"service": name,
                    "compose_project": kwargs["compose_project"],
                    "runtime_identity": f"container-{name}",
                    "declared_image": plan.image.repository_qualified_digest,
                    "repository_digest": plan.image.repository_qualified_digest,
                    "local_image_id": plan.image.config_digest,
                    "config_digest": plan.image.config_digest,
                    "platform": plan.image.platform.as_mapping(),
                    "topology_identity": staged_proof().observed_identity["topology_digest"],
                    "compose_config_hash": DIGEST_B, "healthy": True}
                    for name in kwargs["services"]]}
    def create_init(self, **kwargs):
        self.calls.append("create")
        class Handle: identity = "init-a"
        return Handle()
    def inspect_init(self, handle):
        declaration = init_declaration(); plan = validate_verified_image_plan(verified_plan_mapping())
        return {"image": plan.image.repository_qualified_digest,
                "local_image_id": plan.image.config_digest,
                "platform": plan.image.platform.as_mapping(), "command": declaration["command"],
                "mounts": declaration["mounts"], "networks": declaration["networks"],
                "environment_keys": declaration["environment_keys"], "privileged": False,
                "dependencies": [], "target": staged_proof().target.as_mapping(),
                "runtime_epoch": "daemon-a"}
    def start_init(self, handle): self.started += 1; self.calls.append("start")
    def wait_init(self, handle, **kwargs):
        return {"exit_code": 0, "terminated": True, "output_bytes": 0, "cancelled": False}
    def remove_init(self, handle, **kwargs): self.calls.append("remove"); return True
    def cancel_init(self, handle): self.calls.append("cancel"); return True
    def wait_terminated(self, handle, **kwargs): return True
    def replace_services(self, **kwargs): self.replacements += 1; self.calls.append("replace")


class FakeEdge:
    def __init__(self, lookup="not_entered"):
        self.lookup_result = lookup; self.calls = []
    def observe_plan(self):
        policy = activation_policy()
        return {"routes": list(policy.edge_route_plan), "route_digest": policy.edge_route_digest}
    def lookup(self, request_id, request_digest): self.calls.append("lookup"); return self.lookup_result
    def apply(self, request_id, request_digest, **evidence):
        self.calls.append("apply")
        body = {"request_id": request_id, "request_digest": request_digest,
                "terminal": True, **evidence}
        return {**body, "receipt_digest": activation_digest(
            "sandbox.hosting.images.activation-edge-receipt.v1", body)}
    def observe_generation(self, request_id, request_digest, **evidence):
        self.calls.append("observe-generation")
        body = {"request_id": request_id, "request_digest": request_digest,
                "terminal": True, "observed_only": True, **evidence}
        return {**body, "receipt_digest": activation_digest(
            "sandbox.hosting.images.activation-edge-receipt.v1", body)}


class FakeActivationRepository:
    def __init__(self, *, current=None, previous=None):
        self.events = []; self.released = 0
        self.state = {"generation": 0, "current": current, "previous": previous,
            "active": None, "results": {}, "tombstones": {}, "recovery_results": {},
            "recovery_provisional": None, "schema_version": 1,
            "reserved_terminal_bytes": 0}
        self.accepted_terminal_leases = set()
    @contextmanager
    def operation_transaction(self, target):
        self.events.append("owner-enter")
        try:
            yield self
        finally:
            self.events.append("owner-exit")
    def lookup_terminal(self, target, *, request_id, request_digest):
        stored = self.state["results"].get(request_id)
        if stored is None: return None
        if stored.get("request_digest") != request_digest: raise ValueError("request_conflict")
        if request_id in self.accepted_terminal_leases:
            self.accepted_terminal_leases.remove(request_id); self.released += 1
        return stored
    def accept(self, request, **kwargs):
        admission = kwargs["admission_validator"]()
        if not admission.ok:
            return admission.code, None, None
        transaction = activation_digest("sandbox.hosting.images.activation-transaction.v1",
                                        {"request": request.request_digest})
        self.state["active"] = {"transaction_digest": transaction,
            "request_id": request.request_id, "request_digest": request.request_digest,
            "operation": request.operation, "phase": "accepted", "effect_entered": False,
            "proof_pin": {"lease_id": "lease-a", "holder": f"activation-owner/{request.request_id}",
                          "phase": "accepted", "proof_digest": request.proof.proof_digest,
                          "host_acceptance_receipt": "host-acceptance/" + "a" * 64},
            "init_receipts": [], "init_steps": [], "edge_required": kwargs.get("edge_required", True),
            "running_observation": None, "edge_result": None,
            "candidate_generation": None}
        class Lease:
            target_identity = request.proof.target.target_identity
            holder = f"activation-owner/{request.request_id}"
            proof_digest = request.proof.proof_digest
            acceptance_receipt = "host-acceptance/" + "a" * 64
        self.events.append("accept"); return "accepted", None, Lease()
    def snapshot(self, target): return self.state
    def transition(self, target, request, phase, **values):
        self.events.append(phase); self.state["active"]["phase"] = phase
        step = values.pop("init_step", None)
        self.state["active"].update(values)
        if step is not None:
            if step["index"] == len(self.state["active"]["init_steps"]):
                self.state["active"]["init_steps"].append(step)
            else: self.state["active"]["init_steps"][step["index"]] = step
        if "init_receipt" in values: self.state["active"]["init_receipts"].append(values["init_receipt"])
        return self.state
    def commit(self, target, request, result, generation=None):
        self.events.append("commit")
        if generation is not None:
            self.state["previous"] = self.state["current"]
            self.state["current"] = generation; self.state["generation"] += 1
        self.state["results"][request.request_id] = result.as_mapping()
        return self.state
    def release_terminal_pin(self, lease, **kwargs): self.released += 1


class CrashHarness:
    POINTS = ("create", "inspect", "effect_entered", "start", "wait", "cleanup", "receipt")
    def __init__(self, point): self.point = point; self.events = []
    def reach(self, point):
        self.events.append(point)
        if point == self.point: raise RuntimeError(f"crash:{point}")


class RaceHarness:
    CAPABILITIES = ("activate", "adopt", "rollback", "image-recover", "apply", "sync",
                    "login-url", "edge-continue", "failed-apply-recover", "image-stage")
    def __init__(self): self.owner = None; self.effects = []
    @contextmanager
    def acquire(self, capability):
        if self.owner is not None: raise TimeoutError("operation_busy")
        self.owner = capability
        try: yield
        finally: self.owner = None
