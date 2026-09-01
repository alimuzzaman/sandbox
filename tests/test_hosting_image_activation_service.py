import unittest

from sandbox.hosting.images.activation.runtime_observer import RuntimeObserver
from sandbox.hosting.images.activation.service import ActivationService
from tests.fixtures.hosting_image_activation import (
    DIGEST_B, FakeActivationRepository, FakeEdge, FakeRuntime, activation_policy,
    activation_request, authority_binding, rollback_grant, rollback_subject,
)


def service(repository, runtime=None, edge=None):
    runtime = runtime or FakeRuntime()
    return ActivationService(repository=repository, runtime_adapter=runtime,
                             runtime_observer=RuntimeObserver(runtime),
                             edge_adapter=edge or FakeEdge()), runtime


class ActivationServiceTests(unittest.TestCase):
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
            rollback_grant=grant, admission_deadline="2999-01-01T00:00:00+00:00",
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
            rollback_grant=rollback_grant(subject), admission_deadline="2999-01-01T00:00:00+00:00",
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
            rollback_grant=grant, admission_deadline="2999-01-01T00:00:00+00:00",
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
                    admission_deadline="2999-01-01T00:00:00+00:00", compose_files=(),
                    compose_project="widget", configuration_digest=DIGEST_B,
                    init_data_contract_digest=DIGEST_B)
                self.assertFalse(result["ok"]); self.assertEqual(runtime.replacements, 0)

    def test_rollback_never_has_registry_broker_credential_pull_build_surface(self):
        instance, _ = service(FakeActivationRepository())
        forbidden = {"registry", "broker", "credential", "pull", "build", "tag", "prune"}
        self.assertFalse(forbidden & set(vars(instance)))


if __name__ == "__main__": unittest.main()
