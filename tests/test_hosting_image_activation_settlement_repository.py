import copy
import base64
import unittest

from sandbox.hosting.images.activation.models import activation_digest
from sandbox.hosting.images.activation.settlement_models import (
    SettlementApproval, SettlementDataAssessment, SettlementObservation, SettlementPlan,
)
from sandbox.hosting.images.activation.settlement_repository import (
    SettlementRepositoryError, settle_candidate as propose_settlement, validate_settlements,
)
from sandbox.hosting.images.staging_models import StagingTarget


def settle_candidate(state, plan, signature_identity):
    signature = base64.b64encode(("-----BEGIN SSH SIGNATURE-----\n" +
        signature_identity + "\n-----END SSH SIGNATURE-----\n").encode()).decode()
    approval = SettlementApproval.create(authority_id="operator", authority_revision="1",
        plan_digest=plan.plan_digest, issued_at=50, expires_at=200, signature=signature)
    return propose_settlement(state, plan, approval, authorized_at=100)


TARGET = StagingTarget("machine-a", "target-a", "daemon-a")
TX = "sha256:" + "a" * 64
REQUEST_DIGEST = "sha256:" + "b" * 64
PROOF_DIGEST = "sha256:" + "c" * 64
CURRENT_DIGEST = "sha256:" + "d" * 64
PIN = {"lease_id": "activation-lease/" + "1" * 48,
       "holder": "activation-owner/activation-a", "phase": "accepted",
       "proof_digest": PROOF_DIGEST,
       "host_acceptance_receipt": "host-acceptance/" + "2" * 64}


def _result():
    return {"schema_version": 1, "ok": False, "result_class": "uncertain",
        "code": "effect_unknown", "operation": "activate",
        "request_id": "activation-a", "request_digest": REQUEST_DIGEST,
        "starting_generation": 0, "resulting_generation": 0,
        "transaction_digest": TX}


def _active():
    return {"schema_version": 1, "transaction_digest": TX,
        "request_id": "activation-a", "request_digest": REQUEST_DIGEST,
        "operation": "activate", "holder": PIN["holder"],
        "starting_generation": 0, "phase": "uncertain", "effect_entered": True,
        "authority_binding_digest": "sha256:" + "e" * 64, "proof_pin": copy.deepcopy(PIN),
        "rollback_subject_digest": "sha256:" + "f" * 64,
        "rollback_grant_digest": "sha256:" + "0" * 64, "init_receipts": [],
        "init_steps": [], "edge_required": False,
        "recovery_context": {"target": TARGET.as_mapping(), "compose_project": "app",
                              "selected_services": ["web"]},
        "running_observation": None, "edge_result": None,
        "candidate_generation": None, "result": _result()}


def _plan(**changes):
    observation = SettlementObservation.create(
        target=TARGET, transaction_digest=TX, generation=0,
        runtime_epoch="daemon-a", container_identities=("1" * 64,),
        preserved_identities=(PROOF_DIGEST,), inventory_digest=CURRENT_DIGEST)
    assessment = SettlementDataAssessment.create(
        target=TARGET, transaction_digest=TX, application_revision="a" * 40,
        backup_receipt_digests=(CURRENT_DIGEST,))
    values = dict(
        request_id="settlement-a", active_request_id="activation-a",
        active_request_digest=REQUEST_DIGEST, transaction_digest=TX,
        target=TARGET, generation=0, current_generation_digest=None,
        observation=observation, data_assessment=assessment)
    values.update(changes)
    return SettlementPlan.create(**values)


def _state():
    return {"schema_version": 1, "generation": 0, "current": None,
            "previous": None, "active": _active(), "results": {
                "activation-a": {"result": _result(), "holder": PIN["holder"],
                                  "proof_digest": PROOF_DIGEST, "proof_pin": copy.deepcopy(PIN)}},
            "tombstones": {}, "recovery_provisional": None,
            "recovery_results": {}, "reserved_terminal_bytes": 16384}


class SettlementRepositoryTests(unittest.TestCase):
    def test_valid_graph_v2_uncertainty_is_retained_verbatim(self):
        from types import SimpleNamespace
        from sandbox.hosting.images.activation.v2_repository import commit_candidate_v2
        from tests.test_hosting_image_activation_v2_cli import recovery_state
        state, _prior = recovery_state(genesis=True)
        active = state["active"]
        request = SimpleNamespace(request_id=active["request_id"],
                                  request_digest=active["request_digest"])
        target = StagingTarget.from_mapping(active["recovery_context"]["target"])
        tx = active["transaction_digest"]
        terminal = {"schema_version": 2, "ok": False, "result_class": "uncertain",
            "code": "effect_unknown", "request_id": request.request_id,
            "request_digest": request.request_digest, "starting_generation": 0,
            "resulting_generation": 0, "generation_digest": None, "transaction_digest": tx}
        state = commit_candidate_v2(state, request, terminal, None)
        observation = SettlementObservation.create(target=target, transaction_digest=tx,
            generation=0, runtime_epoch=target.daemon_identity, container_identities=(),
            preserved_identities=(PROOF_DIGEST,), inventory_digest=CURRENT_DIGEST)
        assessment = SettlementDataAssessment.create(target=target, transaction_digest=tx,
            application_revision="a" * 40, backup_receipt_digests=(CURRENT_DIGEST,))
        plan = SettlementPlan.create(request_id="settle-v2", active_request_id=request.request_id,
            active_request_digest=request.request_digest, transaction_digest=tx, target=target,
            generation=0, current_generation_digest=None, observation=observation,
            data_assessment=assessment)
        status, candidate, record = settle_candidate(state, plan, "sha256:" + "9" * 64)
        self.assertEqual(status, "settled")
        self.assertEqual(record["original_transaction"], state["active"])
        self.assertEqual(candidate["results"], state["results"])
        self.assertEqual(candidate["generation"], 0)

    def test_corruption_cannot_remove_or_rebind_original_uncertainty(self):
        _, settled, _ = settle_candidate(_state(), _plan(), "sha256:" + "9" * 64)
        for mutate in (
            lambda s: s["results"].clear(),
            lambda s: s.update(settlements=None),
            lambda s: s["settlements"]["settlement-a"].update(schema_version=True),
            lambda s: s["settlements"]["settlement-a"]["original_transaction"]["recovery_context"].update(
                target={**TARGET.as_mapping(), "daemon_identity": "different"}),
        ):
            damaged = copy.deepcopy(settled); mutate(damaged)
            with self.assertRaises(ValueError):
                validate_settlements(damaged)

    def test_settlement_request_cannot_reuse_activation_request_id(self):
        state = _state()
        status, unchanged, record = settle_candidate(state,
            _plan(request_id="activation-a"), "sha256:" + "9" * 64)
        self.assertEqual(status, "conflict")
        self.assertEqual(unchanged, state)
        self.assertIsNone(record)

    def test_later_generation_does_not_rewrite_historical_settlement(self):
        from tests.test_hosting_image_activation_settlement_forward import (
            successor_fixture, signed_request, SettlementForwardTests,
        )
        state, request, grant, approval = successor_fixture()
        record = copy.deepcopy(state["settlements"]["settlement-a"])
        result, host, _stage, _runtime = SettlementForwardTests()._execute(
            state, signed_request(request, approval), grant, allowed=True)
        self.assertTrue(result["ok"], result)
        checked = validate_settlements(host.state)
        self.assertEqual(checked["settlements"]["settlement-a"], record)
        self.assertIsNone(record["current_generation_digest"])

    def test_settlement_records_terminal_and_preserves_uncertainty_current_and_generation(self):
        state = _state(); plan = _plan()
        status, candidate, record = settle_candidate(state, plan, "sha256:" + "9" * 64)
        self.assertEqual(status, "settled")
        self.assertIsNone(candidate["active"])
        self.assertEqual(candidate["generation"], state["generation"])
        self.assertEqual(candidate["current"], state["current"])
        self.assertEqual(candidate["results"], state["results"])
        self.assertEqual(record["original_transaction"], state["active"])
        self.assertEqual(record["original_result"], state["results"]["activation-a"])
        self.assertEqual(record["terminal_receipt"]["code"], "abandoned_with_effects")
        self.assertEqual(validate_settlements(candidate), candidate)

    def test_exact_replay_returns_record_and_conflicts_do_not_change_state(self):
        state = _state(); plan = _plan(); approval = "sha256:" + "9" * 64
        _, settled, record = settle_candidate(state, plan, approval)
        status, replay, retained = settle_candidate(settled, plan, approval)
        self.assertEqual(status, "replay"); self.assertEqual(replay, settled)
        self.assertEqual(retained, record)
        changed = _plan(request_id="settlement-b")
        status, unchanged, retained = settle_candidate(settled, changed, approval)
        self.assertEqual(status, "conflict")
        self.assertEqual(unchanged, settled); self.assertIsNone(retained)
        status, unchanged, retained = settle_candidate(settled, plan, "sha256:" + "8" * 64)
        self.assertEqual(status, "conflict"); self.assertEqual(unchanged, settled)

    def test_missing_uncertainty_or_recovery_provisional_refuses(self):
        state = _state(); plan = _plan(); approval = "sha256:" + "9" * 64
        for change in (
            {"active": None},
            {"active": {**state["active"], "effect_entered": False}},
            {"active": {**state["active"], "phase": "runtime_pending"}},
            {"recovery_provisional": {"request_id": "other"}},
        ):
            candidate = {**state, **change}
            before = copy.deepcopy(candidate)
            try:
                status, retained_state, retained = settle_candidate(candidate, plan, approval)
            except SettlementRepositoryError as exc:
                self.assertEqual(exc.code, "persistence_uncertain")
            else:
                self.assertEqual(status, "conflict")
                self.assertEqual(retained_state, candidate)
                self.assertIsNone(retained)
            self.assertEqual(candidate, before)

    def test_generation_and_evidence_changes_refuse_without_mutation(self):
        plan = _plan(); approval = "sha256:" + "9" * 64
        for mutate in (
            lambda s: s.update(generation=2),
            lambda s: s["active"].update(transaction_digest="sha256:" + "0" * 64),
            lambda s: s["active"]["recovery_context"].update(
                target={**TARGET.as_mapping(), "daemon_identity": "daemon-b"}),
            lambda s: s["results"]["activation-a"]["proof_pin"].update(
                host_acceptance_receipt="host-acceptance/" + "3" * 64),
        ):
            state = _state(); before = copy.deepcopy(state); mutate(state)
            changed = copy.deepcopy(state)
            try:
                status, retained_state, retained = settle_candidate(state, plan, approval)
            except SettlementRepositoryError as exc:
                self.assertEqual(exc.code, "persistence_uncertain")
            else:
                self.assertEqual(status, "conflict")
                self.assertEqual(retained_state, state)
                self.assertIsNone(retained)
            self.assertEqual(state, changed)
            self.assertNotEqual(state, before)

    def test_corrupt_settlement_record_is_rejected(self):
        state = _state(); plan = _plan()
        _, state, _ = settle_candidate(state, plan, "sha256:" + "9" * 64)
        state["settlements"]["settlement-a"]["terminal_receipt"]["code"] = "committed"
        with self.assertRaises(SettlementRepositoryError): validate_settlements(state)


if __name__ == "__main__":
    unittest.main()
