import unittest

from sandbox.hosting.images.activation.models import ActivationRequest
from sandbox.hosting.images.activation.policy import create_forward_rollback_subject
from sandbox.hosting.images.activation.runtime_observer import RuntimeObserver
from sandbox.hosting.images.activation.service import ActivationService
from tests.fixtures.hosting_image_activation import (
    DIGEST_B, FakeActivationRepository, FakeEdge, FakeRuntime, activation_policy,
    activation_request, authority_binding, rollback_grant, rollback_subject,
    FakeRollbackGrantVerifier,
)


def service(repository, runtime=None, edge=None):
    runtime = runtime or FakeRuntime()
    return ActivationService(repository=repository, runtime_adapter=runtime,
                             runtime_observer=RuntimeObserver(runtime),
                             edge_adapter=edge or FakeEdge(),
                             rollback_grant_verifier=FakeRollbackGrantVerifier()), runtime


def execute(instance, request, policy, binding, subject, grant):
    return instance.execute(
        request, policy, binding, rollback_subject=subject, rollback_grant=grant,
        admission_deadline="2999-01-01T00:00:00Z",
        compose_files=("compose.yml",), compose_project="widget",
        configuration_digest=DIGEST_B, init_data_contract_digest=DIGEST_B)


def prepared_rollback():
    repository = FakeActivationRepository(); instance, runtime = service(repository)
    policy = activation_policy(); binding = authority_binding(policy=policy)
    first = activation_request(policy=policy)
    first_subject = rollback_subject(policy); first_grant = rollback_grant(first_subject)
    assert execute(instance, first, policy, binding, first_subject, first_grant)["ok"]
    second_base = activation_request(generation=1, request_id="activation-b", policy=policy)
    subject = create_forward_rollback_subject(
        target=second_base.proof.target.as_mapping(),
        rollback_target_generation_digest=repository.state["current"]["generation_digest"],
        candidate_plan_digest=second_base.plan.plan_digest,
        candidate_proof_digest=second_base.proof.proof_digest,
        activation_authority_digest=binding.binding_digest,
        configuration_digest=DIGEST_B,
        topology_digest=second_base.proof.observed_identity["topology_digest"],
        init_data_contract_digest=DIGEST_B,
        policy_revision=policy.authority_revision)
    grant = rollback_grant(subject)
    second = activation_request(
        generation=1, request_id="activation-b", policy=policy,
        subject=subject, grant=grant)
    assert execute(instance, second, policy, binding, subject, grant)["ok"]
    request = ActivationRequest.create(
        request_id="rollback-a", operation="rollback", expected_generation=2,
        policy_digest=policy.policy_digest, plan=second.plan, proof=second.proof,
        authority_binding_digest=binding.binding_digest,
        rollback_subject_digest=subject.subject_digest,
        rollback_grant_digest=grant.grant_digest, confirmed=True)
    return repository, instance, runtime, policy, binding, subject, grant, request


class ActivationServiceTests(unittest.TestCase):
    def test_runtime_is_reobserved_after_edge_receipt_before_commit(self):
        class ChangedAfterEdge(FakeRuntime):
            def __init__(self):
                super().__init__(); self.observations = 0
            def observe_running(self, **kwargs):
                value = super().observe_running(**kwargs)
                self.observations += 1
                if self.observations == 2:
                    value["services"][0]["runtime_identity"] = "container-replaced"
                return value
        repository = FakeActivationRepository(); runtime = ChangedAfterEdge()
        instance, _ = service(repository, runtime=runtime)
        policy = activation_policy(); binding = authority_binding(policy=policy)
        request = activation_request(policy=policy)
        subject = rollback_subject(policy); grant = rollback_grant(subject)
        result = execute(instance, request, policy, binding, subject, grant)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "runtime_mismatch")
        self.assertEqual(runtime.observations, 2)
        self.assertEqual(repository.state["generation"], 0)

    def test_self_issued_rollback_grant_is_not_machine_authority(self):
        from dataclasses import replace
        repository = FakeActivationRepository(); instance, runtime = service(repository)
        policy = activation_policy(); binding = authority_binding(policy=policy)
        subject = rollback_subject(policy); legitimate = rollback_grant(subject)
        forged_body = {**legitimate.body_mapping(), "authority_proof": "attacker-proof"}
        from sandbox.hosting.images.activation.models import (
            RollbackCompatibilityGrant, activation_digest,
        )
        forged = RollbackCompatibilityGrant(
            authority_id=forged_body["authority_id"],
            authority_revision=forged_body["authority_revision"],
            issued_at=forged_body["issued_at"], policy_revision=forged_body["policy_revision"],
            subject=subject, authority_proof=forged_body["authority_proof"],
            expires_at=forged_body["expires_at"], revoked=forged_body["revoked"],
            grant_digest=activation_digest(
                "sandbox.hosting.images.rollback-grant.v1", forged_body))
        result = execute(instance, activation_request(policy=policy), policy, binding,
                         subject, forged)
        self.assertEqual(result["code"], "rollback_grant_mismatch")
        self.assertEqual(runtime.calls, [])
    def test_exact_terminal_replay_precedes_changed_policy_grant_and_custody(self):
        repository = FakeActivationRepository(); instance, runtime = service(repository)
        request = activation_request(); terminal = {
            "schema_version": 1, "ok": True, "result_class": "success", "code": "committed",
            "operation": "activate", "request_id": request.request_id,
            "request_digest": request.request_digest, "starting_generation": 0,
            "resulting_generation": 1, "transaction_digest": DIGEST_B,
            "generation_digest": DIGEST_B, "observation_digest": DIGEST_B}
        repository.state["results"][request.request_id] = terminal
        repository.accepted_terminal_leases.add(request.request_id)
        result = instance.execute(request, activation_policy(), object(),
            rollback_subject=object(), rollback_grant=object(), admission_deadline="expired",
            compose_files=(), compose_project="changed", configuration_digest=DIGEST_B,
            init_data_contract_digest=DIGEST_B)
        self.assertEqual(result, terminal)
        self.assertNotIn("accept", repository.events)
        self.assertEqual(runtime.calls, [])
        self.assertEqual(repository.released, 1)
        self.assertNotIn(request.request_id, repository.accepted_terminal_leases)
        self.assertEqual(instance.execute(request, activation_policy(), object(),
            rollback_subject=object(), rollback_grant=object(), admission_deadline="expired",
            compose_files=(), compose_project="changed", configuration_digest=DIGEST_B,
            init_data_contract_digest=DIGEST_B), terminal)
        self.assertEqual(repository.released, 1)

    def test_exact_activation_runs_init_replace_observe_edge_and_atomic_commit(self):
        repository = FakeActivationRepository(); subject = rollback_subject(); grant = rollback_grant()
        instance, runtime = service(repository)
        request = activation_request(); policy = activation_policy(); binding = authority_binding(policy=policy)
        result = instance.execute(request, policy, binding, rollback_subject=subject,
            rollback_grant=grant, admission_deadline="2999-01-01T00:00:00Z",
            compose_files=("compose.yml",), compose_project="widget",
            configuration_digest=DIGEST_B, init_data_contract_digest=DIGEST_B)
        self.assertTrue(result["ok"], result)
        self.assertEqual(runtime.started, 1); self.assertEqual(runtime.replacements, 1)
        self.assertEqual(repository.released, 1)
        self.assertEqual(repository.events.count("commit"), 1)

    def test_two_init_steps_persist_independent_effect_and_receipt_slots(self):
        repository = FakeActivationRepository(); instance, _ = service(repository)
        policy = activation_policy()
        declarations = (policy.init_declarations[0], {**policy.init_declarations[0], "name": "migrate-b"})
        from sandbox.hosting.images.activation.models import ActivationPolicy, activation_digest
        narrowed_values = policy.identity_mapping()
        narrowed_values["init_declarations"] = list(declarations)
        narrowed_values["policy_digest"] = activation_digest(
            "sandbox.hosting.images.activation-policy.v1", narrowed_values)
        narrowed = ActivationPolicy.from_mapping(narrowed_values)
        binding = authority_binding(policy=narrowed); request = activation_request(policy=narrowed)
        subject = rollback_subject(narrowed)
        result = instance.execute(request, narrowed, binding, rollback_subject=subject,
            rollback_grant=rollback_grant(subject), admission_deadline="2999-01-01T00:00:00Z",
            compose_files=("compose.yml",), compose_project="widget",
            configuration_digest=DIGEST_B, init_data_contract_digest=DIGEST_B)
        self.assertTrue(result["ok"])
        self.assertEqual([step["index"] for step in repository.state["active"]["init_steps"]
                         if repository.state.get("active")], [0, 1])

    def test_zero_init_adoption_proves_exact_state_with_zero_effects(self):
        repository = FakeActivationRepository(); instance, runtime = service(repository)
        policy = activation_policy(zero_init=True); binding = authority_binding(policy=policy)
        request = activation_request(operation="adopt", zero_init=True, policy=policy)
        subject = rollback_subject(policy); grant = rollback_grant(subject)
        result = instance.execute(request, policy, binding, rollback_subject=subject,
            rollback_grant=grant, admission_deadline="2999-01-01T00:00:00Z",
            compose_files=("machine-bound.compose.yml",), compose_project="widget",
            configuration_digest=DIGEST_B,
            init_data_contract_digest=DIGEST_B, edge_required=True)
        self.assertTrue(result["ok"], result); self.assertEqual(runtime.started, 0)
        self.assertEqual(runtime.replacements, 0); self.assertNotIn("apply", instance.edge_adapter.calls)
        self.assertEqual(runtime.render_selectors[0]["compose_files"],
                         ("machine-bound.compose.yml",))
        self.assertEqual(runtime.render_selectors[0]["project_name"], "widget")

    def test_init_bearing_adoption_and_first_generation_rollback_refuse_before_effect(self):
        for operation, zero_init in (("adopt", False), ("rollback", True)):
            with self.subTest(operation=operation):
                repository = FakeActivationRepository(); instance, runtime = service(repository)
                request = activation_request(operation=operation, zero_init=zero_init)
                policy = activation_policy(zero_init=zero_init); binding = authority_binding(policy=policy)
                result = instance.execute(request, policy, binding,
                    rollback_subject=rollback_subject(), rollback_grant=rollback_grant(),
                    admission_deadline="2999-01-01T00:00:00Z", compose_files=(),
                    compose_project="widget", configuration_digest=DIGEST_B,
                    init_data_contract_digest=DIGEST_B)
                self.assertFalse(result["ok"]); self.assertEqual(runtime.replacements, 0)

    def test_rollback_never_has_registry_broker_credential_pull_build_surface(self):
        instance, _ = service(FakeActivationRepository())
        forbidden = {"registry", "broker", "credential", "pull", "build", "tag", "prune"}
        self.assertFalse(forbidden & set(vars(instance)))

    def test_rollback_refuses_changed_local_identity_before_runtime_effect(self):
        repository, instance, runtime, policy, binding, subject, grant, request = prepared_rollback()
        original = runtime.observe_local_image
        def changed(**kwargs):
            value = original(**kwargs)
            value.update(config_digest="sha256:" + "e" * 64,
                         local_image_id="sha256:" + "e" * 64,
                         daemon_epoch_start="daemon-replaced",
                         daemon_epoch_end="daemon-replaced")
            return value
        runtime.observe_local_image = changed
        result = execute(instance, request, policy, binding, subject, grant)
        self.assertFalse(result["ok"]); self.assertEqual(result["code"], "local_image_mismatch")
        self.assertEqual(runtime.replacements, 2)
        self.assertNotIn("runtime_pending", repository.events[-3:])

    def test_rollback_refuses_changed_rendered_topology_before_runtime_effect(self):
        repository, instance, runtime, policy, binding, subject, grant, request = prepared_rollback()
        original = runtime.render_topology
        def changed(**kwargs):
            value = original(**kwargs)
            value["services"]["web"]["configuration_digest"] = "sha256:" + "e" * 64
            return value
        runtime.render_topology = changed
        renders_before = len(runtime.render_selectors)
        result = execute(instance, request, policy, binding, subject, grant)
        self.assertFalse(result["ok"]); self.assertEqual(result["code"], "topology_mismatch")
        self.assertEqual(runtime.replacements, 2)
        self.assertEqual(len(runtime.render_selectors), renders_before + 1)
        self.assertNotIn("runtime_pending", repository.events[-3:])

    def test_rollback_refuses_changed_compose_project_before_runtime_effect(self):
        repository, instance, runtime, policy, binding, subject, grant, request = prepared_rollback()
        replacements_before = runtime.replacements
        result = instance.execute(
            request, policy, binding, rollback_subject=subject, rollback_grant=grant,
            admission_deadline="2999-01-01T00:00:00Z",
            compose_files=("compose.yml",), compose_project="foreign-project",
            configuration_digest=DIGEST_B, init_data_contract_digest=DIGEST_B)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "rollback_grant_mismatch")
        self.assertEqual(runtime.replacements, replacements_before)
        self.assertNotIn("runtime_pending", repository.events[-3:])

    def test_rollback_commits_fresh_runtime_projection_from_exact_previous_generation(self):
        repository, instance, runtime, policy, binding, subject, grant, request = prepared_rollback()
        restored_projection = repository.state["previous"]["compose_projection"]
        original = runtime.observe_running
        def fresh(**kwargs):
            value = original(**kwargs)
            for row in value["services"]:
                row["runtime_identity"] = "fresh-" + row["service"]
            return value
        runtime.observe_running = fresh
        result = execute(instance, request, policy, binding, subject, grant)
        self.assertTrue(result["ok"], result)
        stored = repository.state["current"]
        self.assertEqual(stored["compose_projection"], restored_projection)
        self.assertEqual(
            {row["runtime_identity"] for row in stored["service_projection"]},
            {"fresh-web", "fresh-worker"})
        self.assertEqual(stored["service_projection"],
                         repository.state["active"]["running_observation"]["services"])


if __name__ == "__main__": unittest.main()
