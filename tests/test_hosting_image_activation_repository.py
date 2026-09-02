import unittest

from tests.fixtures.hosting_image_activation import activation_request


def recovery_context(request):
    return {"target": request.proof.target.as_mapping(), "compose_project": "widget",
            "selected_services": ["web", "worker"]}


class ActivationRepositoryCodecTests(unittest.TestCase):
    def test_nested_accept_replay_conflict_generation_and_tombstone_mechanisms(self):
        from sandbox.hosting.images.activation.repository import accept_candidate, empty_activation_state
        request = activation_request(); pin = {"lease_id": "activation-lease/" + "a" * 48, "holder": "activation-owner/a",
            "phase": "accepted", "proof_digest": request.proof.proof_digest,
            "host_acceptance_receipt": "host-acceptance/" + "b" * 64}
        status, state, _ = accept_candidate(
            empty_activation_state(), request, holder="activation-owner/a",
            authority_binding_digest=request.authority_binding_digest,
            rollback_subject_digest=request.rollback_subject_digest,
            rollback_grant_digest=request.rollback_grant_digest, proof_pin=pin,
            edge_required=True, recovery_context=recovery_context(request))
        self.assertEqual(status, "accepted")
        self.assertEqual(accept_candidate(
            state, request, holder="activation-owner/a",
            authority_binding_digest=request.authority_binding_digest,
            rollback_subject_digest=request.rollback_subject_digest,
            rollback_grant_digest=request.rollback_grant_digest, proof_pin=pin,
            edge_required=True, recovery_context=recovery_context(request))[0], "replay")

    def test_common_transaction_transition_table_rejects_illegal_commit(self):
        from sandbox.hosting.images.activation.models import validate_transition
        for current, candidate in (("accepted", "committed"), ("preflight", "edge_pending"),
                                   ("runtime_pending", "committed")):
            with self.subTest(current=current, candidate=candidate):
                with self.assertRaises(ValueError):
                    validate_transition(current, candidate, effect_entered=False)

    def test_admission_reserves_terminal_capacity_and_refuses_full_retention(self):
        from sandbox.hosting.images.activation.repository import (
            accept_candidate, empty_activation_state,
        )
        from sandbox.hosting.images.activation.models import MAX_RESULTS, MAX_TOMBSTONES
        request = activation_request(); state = empty_activation_state()
        from sandbox.hosting.images.activation.models import ActivationResult, activation_digest
        pin = {"lease_id": "activation-lease/" + "a" * 48, "holder": "activation-owner/a", "phase": "accepted",
               "proof_digest": "sha256:" + "a" * 64,
               "host_acceptance_receipt": "host-acceptance/" + "b" * 64}
        state["results"] = {}
        for index in range(MAX_RESULTS):
            request_id = f"r-{index}"; digest = activation_digest("test.request", {"id": request_id})
            terminal = ActivationResult(1, False, "refused", "request_conflict", "activate",
                request_id, digest, 0, 0, digest).as_mapping()
            state["results"][request_id] = {"result": terminal, "holder": "activation-owner/a",
                "proof_digest": pin["proof_digest"], "proof_pin": pin}
        state["tombstones"] = {f"t-{index}": {"request_id": f"t-{index}",
            "request_digest": activation_digest("test.tombstone", {"id": index}),
            "result_class": "refused", "code": "request_conflict"}
            for index in range(MAX_TOMBSTONES)}
        status, _, _ = accept_candidate(state, request, holder="activation-owner/a",
            authority_binding_digest=request.authority_binding_digest,
            rollback_subject_digest=request.rollback_subject_digest,
            rollback_grant_digest=request.rollback_grant_digest, proof_pin={}, edge_required=True,
            recovery_context=recovery_context(request))
        self.assertEqual(status, "retention_full")

    def test_codec_rejects_recursive_secret_and_unbounded_authority(self):
        from sandbox.hosting.images.activation.repository import (
            ActivationRepositoryError, decode_activation_state, empty_activation_state,
        )
        state = empty_activation_state(); state["recovery_results"]["r"] = {
            "schema_version": 1, "request_id": "r", "request_digest": "sha256:" + "a" * 64,
            "code": "recovery_conflict", "promoted": False, "starting_generation": 0,
            "resulting_generation": 0, "secret": "forbidden"}
        with self.assertRaises(ActivationRepositoryError): decode_activation_state(state)

    def test_codec_rejects_malformed_tombstone_digest_and_request_identity(self):
        import copy
        from sandbox.hosting.images.activation.repository import (
            ActivationRepositoryError, decode_activation_state, empty_activation_state,
        )
        base = empty_activation_state()
        base["tombstones"]["request-a"] = {
            "request_id": "request-a", "request_digest": "sha256:" + "a" * 64,
            "result_class": "refused", "code": "request_conflict"}
        for mutate in (
                lambda value: value["tombstones"]["request-a"].update(
                    request_digest="sha256:" + "a" * 64 + "-suffix"),
                lambda value: value["tombstones"].update({"bad request":
                    value["tombstones"].pop("request-a")})):
            candidate = copy.deepcopy(base); mutate(candidate)
            with self.assertRaises(ActivationRepositoryError):
                decode_activation_state(candidate)

    def test_recovery_result_schema_is_closed_and_success_is_exact(self):
        from sandbox.hosting.images.activation.repository import (
            ActivationRepositoryError, decode_activation_state, empty_activation_state,
        )
        state = empty_activation_state()
        state["recovery_results"]["recover-a"] = {
            "schema_version": 1, "ok": False, "request_id": "recover-a",
            "activation_request_id": "activate-a",
            "request_digest": "sha256:" + "a" * 64, "code": "recovery_no_effect",
            "promoted": False, "starting_generation": 0, "resulting_generation": 0}
        state["tombstones"]["activate-a"] = {"request_id": "activate-a",
            "request_digest": "sha256:" + "b" * 64, "result_class": "refused",
            "code": "recovery_no_effect"}
        self.assertFalse(decode_activation_state(state)["recovery_results"]["recover-a"]["ok"])
        state["recovery_results"]["recover-a"]["ok"] = True
        with self.assertRaises(ActivationRepositoryError): decode_activation_state(state)

    def test_recovery_capacity_refuses_before_observation_slot_is_written(self):
        from sandbox.hosting.images.activation.models import MAX_RECOVERY_RESULTS
        from sandbox.hosting.images.activation.repository import (
            ActivationRepositoryError, empty_activation_state, ensure_recovery_capacity,
        )
        state = empty_activation_state()
        state["recovery_results"] = {f"recover-{index}": {
            "schema_version": 1, "ok": False, "request_id": f"recover-{index}",
            "activation_request_id": f"activate-{index}",
            "request_digest": "sha256:" + f"{index:064x}", "code": "recovery_conflict",
            "promoted": False, "starting_generation": 0, "resulting_generation": 0}
            for index in range(MAX_RECOVERY_RESULTS)}
        state["tombstones"] = {f"activate-{index}": {
            "request_id": f"activate-{index}",
            "request_digest": "sha256:" + f"{index + 100:064x}",
            "result_class": "refused", "code": "recovery_no_effect"}
            for index in range(MAX_RECOVERY_RESULTS)}
        with self.assertRaisesRegex(ActivationRepositoryError, "retention_full"):
            ensure_recovery_capacity(state)
        self.assertIsNone(state["recovery_provisional"])

    def test_retained_pin_codec_rejects_noncanonical_lease_and_acceptance_identities(self):
        from sandbox.hosting.images.activation.repository import validate_retained_proof_pin
        base = {"lease_id": "activation-lease/" + "a" * 48,
                "holder": "activation-owner/request-a", "phase": "accepted",
                "proof_digest": "sha256:" + "b" * 64,
                "host_acceptance_receipt": "host-acceptance/" + "c" * 64}
        self.assertEqual(validate_retained_proof_pin(base), base)
        for key, value in (("lease_id", "lease-a"),
                           ("proof_digest", "sha256:not-a-digest"),
                           ("host_acceptance_receipt", "acceptance-a")):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    validate_retained_proof_pin({**base, key: value})

    def test_recovered_no_effect_terminal_prevents_original_reacceptance(self):
        from sandbox.hosting.images.activation.models import ActivationResult
        from sandbox.hosting.images.activation.repository import accept_candidate, empty_activation_state
        request = activation_request()
        pin = {"lease_id": "activation-lease/" + "a" * 48,
               "holder": f"activation-owner/{request.request_id}", "phase": "accepted",
               "proof_digest": request.proof.proof_digest,
               "host_acceptance_receipt": "host-acceptance/" + "b" * 64}
        status, state, _ = accept_candidate(empty_activation_state(), request,
            holder=pin["holder"], authority_binding_digest=request.authority_binding_digest,
            rollback_subject_digest=request.rollback_subject_digest,
            rollback_grant_digest=request.rollback_grant_digest, proof_pin=pin,
            edge_required=True, recovery_context=recovery_context(request))
        self.assertEqual(status, "accepted")
        active = state["active"]
        terminal = ActivationResult(1, False, "refused", "recovery_no_effect",
            request.operation, request.request_id, request.request_digest, 0, 0,
            active["transaction_digest"])
        state["results"][request.request_id] = {"result": terminal.as_mapping(),
            "holder": pin["holder"], "proof_digest": pin["proof_digest"], "proof_pin": pin}
        state["active"] = None; state["reserved_terminal_bytes"] = 0
        self.assertEqual(accept_candidate(state, request, holder=pin["holder"],
            authority_binding_digest=request.authority_binding_digest,
            rollback_subject_digest=request.rollback_subject_digest,
            rollback_grant_digest=request.rollback_grant_digest, proof_pin=pin,
            edge_required=True, recovery_context=recovery_context(request))[0], "replay")

    def test_same_request_uncertain_terminal_keeps_active_fence_and_replays_uncertainty(self):
        from sandbox.hosting.images.activation.models import ActivationResult
        from sandbox.hosting.images.activation.repository import (
            accept_candidate, commit_candidate, empty_activation_state,
        )
        request = activation_request()
        pin = {"lease_id": "activation-lease/" + "a" * 48,
               "holder": f"activation-owner/{request.request_id}", "phase": "accepted",
               "proof_digest": request.proof.proof_digest,
               "host_acceptance_receipt": "host-acceptance/" + "b" * 64}
        _, state, _ = accept_candidate(empty_activation_state(), request,
            holder=pin["holder"], authority_binding_digest=request.authority_binding_digest,
            rollback_subject_digest=request.rollback_subject_digest,
            rollback_grant_digest=request.rollback_grant_digest, proof_pin=pin,
            edge_required=True, recovery_context=recovery_context(request))
        active = state["active"]
        uncertain = ActivationResult(1, False, "uncertain", "effect_unknown",
            request.operation, request.request_id, request.request_digest, 0, 0,
            active["transaction_digest"])
        fenced = commit_candidate(state, request, uncertain)
        self.assertIsNotNone(fenced["active"])
        status, _, replay = accept_candidate(fenced, request, holder=pin["holder"],
            authority_binding_digest=request.authority_binding_digest,
            rollback_subject_digest=request.rollback_subject_digest,
            rollback_grant_digest=request.rollback_grant_digest, proof_pin=pin,
            edge_required=True, recovery_context=recovery_context(request))
        self.assertEqual(status, "replay")
        self.assertEqual(replay["result_class"], "uncertain")
        import copy
        from sandbox.hosting.images.activation.repository import ActivationRepositoryError, decode_activation_state
        mutations = (
            lambda value: value["active"].update(phase="runtime_pending"),
            lambda value: value["active"].update(result=None),
            lambda value: value["active"]["proof_pin"].update(
                host_acceptance_receipt="host-acceptance/" + "e" * 64),
            lambda value: value["results"][request.request_id]["result"].update(
                result_class="success", ok=True, code="committed", resulting_generation=1),
        )
        for mutate in mutations:
            candidate = copy.deepcopy(fenced); mutate(candidate)
            with self.subTest(mutate=mutate):
                with self.assertRaises(ActivationRepositoryError):
                    decode_activation_state(candidate)

    def test_exhaustive_recovery_phase_class_matrix_never_overpromotes(self):
        from sandbox.hosting.images.activation.repository import recovery_decision
        phases = ("accepted", "preflight", "init_pending", "runtime_pending",
                  "runtime_proven", "edge_pending", "committed", "refused",
                  "failed", "cancelled", "uncertain")
        classes = ("exact_new", "exact_prior", "neither", "ambiguous")
        for operation in ("activate", "rollback"):
            for phase in phases:
                for classification in classes:
                    transaction = {"operation": operation, "phase": phase,
                        "effect_entered": phase in {"runtime_pending"},
                        "init_receipts": [], "running_observation": None, "edge_result": None,
                        "edge_required": True}
                    code, promote, close = recovery_decision(transaction, classification)
                    with self.subTest(operation=operation, phase=phase, classification=classification):
                        if classification in {"neither", "ambiguous", "exact_prior"}:
                            self.assertFalse(promote)
                        if promote:
                            self.assertEqual(classification, "exact_new")


if __name__ == "__main__": unittest.main()
