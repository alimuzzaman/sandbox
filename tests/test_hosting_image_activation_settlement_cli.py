import copy
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.hosting.images.activation.repository import ActivationRepository
from sandbox.hosting.images.activation.settlement_cli import run_settlement
from sandbox.hosting.images.activation.settlement_models import SettlementApproval
from tests.test_hosting_image_activation_settlement_models import ssh_signature
from tests.test_hosting_image_activation_settlement_repository import _plan, _state
from tests.test_hosting_image_activation_settlement_service import Observer, Store
from tests.test_hosting_image_activation_v2 import FakeHostStatePort, FakeStageRepositoryPort, FakeTargetMutationPort


class SettlementCliTests(unittest.TestCase):
    def test_plan_apply_and_replay_use_exact_selectors_and_separate_confirmation(self):
        plan = _plan()
        approval = SettlementApproval.create(authority_id="operator", authority_revision="1",
            plan_digest=plan.plan_digest, issued_at=50, expires_at=200, signature=ssh_signature())
        host = FakeHostStatePort(); host.state = _state()
        stage = FakeStageRepositoryPort()
        repository = ActivationRepository(host_state_port=host,
            stage_repository=stage, target_mutation_port=FakeTargetMutationPort())
        observer = Observer(plan.observation); store = Store(approval)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assessment = root / "assessment.json"; saved_plan = root / "plan.json"
            assessment.write_text(json.dumps(plan.data_assessment.as_mapping()))
            saved_plan.write_text(json.dumps(plan.as_mapping()))
            args = SimpleNamespace(settlement_phase="plan", request_id=plan.request_id,
                expected_generation=0, activation_transaction=plan.transaction_digest,
                settlement_data_assessment=str(assessment), settlement_plan=str(saved_plan),
                settlement_approval=approval.approval_digest, confirm=False)
            def run():
                return run_settlement(args, target=plan.target.target_identity,
                    repository=repository, approval_store=store, observer=observer, clock=lambda: 100)
            before = copy.deepcopy(host.state)
            self.assertEqual(run()["plan"], plan.as_mapping())
            self.assertEqual(host.state, before)
            self.assertEqual(observer.calls, 1)
            args.settlement_phase = "apply"
            self.assertEqual(run()["code"], "authority_missing")
            self.assertEqual(observer.calls, 1)
            args.confirm = True; args.request_id = "wrong"
            self.assertEqual(run()["code"], "request_conflict")
            self.assertEqual(host.state, before)
            args.request_id = plan.request_id
            self.assertEqual(run()["code"], "abandoned_with_effects")
            self.assertEqual(observer.calls, 3)
            self.assertEqual(run()["code"], "abandoned_with_effects")
            self.assertEqual(observer.calls, 3)
            self.assertIsNone(host.state["active"])
            self.assertEqual(host.state["results"], before["results"])

    def test_cli_completed_successor_replay_needs_no_current_authority_or_runtime(self):
        from sandbox.commands.hosting import _cmd_host_image
        from tests.test_hosting_image_activation_settlement_forward import successor_fixture, signed_request
        _state_value, unsigned, grant, approval = successor_fixture()
        request = signed_request(unsigned, approval)
        result = {"schema_version": 2, "ok": True, "result_class": "success", "code": "committed"}
        class Repository:
            def lookup_terminal_v2(self, target, *, request_id, request_digest):
                self_test.assertEqual((target, request_id, request_digest),
                    (approval.target.target_identity, request.request_id, request.request_digest))
                return result
        self_test = self
        bundle = {"schema_version": 2, "compose_snapshot": request.compose_snapshot.as_mapping(),
            "rollback_grant": grant.as_mapping(), "rollback_grant_public_key": "ssh-ed25519 AAAA",
            "stage_ledger": {"authority": "feature-050-stage-ledger-v2", "revision": 1}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "plan.json").write_text(json.dumps(request.plan_set.as_mapping()))
            (root / "proof.json").write_text(json.dumps(request.proof_set))
            args = SimpleNamespace(image_action="activate", project_dir=str(root), environment="production",
                remote="synthetic", request_id=request.request_id, expected_generation=0, confirm=True,
                verified_plan=str(root / "plan.json"), staged_proof=str(root / "proof.json"),
                admission_deadline="2999-01-01T00:00:00Z", settlement_forward_approval=approval.approval_digest,
                settlement_predecessor=approval.predecessor_digest)
            out = StringIO()
            with patch("sandbox.commands.hosting._host_image_machine_bundle", return_value=bundle), \
                 patch("sandbox.hosting.images.activation.repository.ActivationRepository", return_value=Repository()), \
                 patch("sandbox.hosting.images.activation.settlement_store.SettlementApprovalStore.read_forward_claim",
                       return_value=approval) as read_claim, \
                 patch("sandbox.hosting.images.activation.settlement_store.SettlementApprovalStore.read_forward",
                       side_effect=AssertionError("replay checked current authorization")), \
                 patch("sandbox.commands.hosting._authenticated_machine_identity", side_effect=AssertionError("runtime opened")), \
                 patch("sandbox.commands.hosting.personal_secrets.hosting_binding_key", side_effect=AssertionError("broker opened")), \
                 patch("sandbox.commands.hosting.time.time", return_value=approval.expires_at + 100), redirect_stdout(out):
                _cmd_host_image({}, args)
            self.assertEqual(json.loads(out.getvalue()), result)
            read_claim.assert_called_once_with(approval.target.as_mapping(), approval.approval_digest)

    def test_edge_preflight_checks_provider_and_defers_origin_but_receipt_requires_origin(self):
        from sandbox.commands.hosting import _HostImageEdgeAdapter
        from tests.test_hosting_image_activation_settlement_repository import settle_candidate
        _, state, _ = settle_candidate(_state(), _plan(), "sha256:" + "9" * 64)
        adapter = _HostImageEdgeAdapter({"routes": [], "healthcheck": {"path": "/health"}},
            activation_repository=SimpleNamespace(snapshot=lambda _target: state), target_identity="target-a")
        with patch("sandbox.commands.hosting._guarded_host_apply_plan", return_value={
                "records": [{"hostname": "example.test"}],
                "cloudflare": {"configured": True, "records": [{"exists": True}]}}), \
             patch("sandbox.commands.hosting._verify_edge", side_effect=RuntimeError("origin unavailable")) as verify:
            adapter.preflight({}, "synthetic")
            verify.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, "origin unavailable"):
                adapter._v2_receipt()
            verify.assert_called_once()
        with patch("sandbox.commands.hosting._guarded_host_apply_plan", return_value={
                "records": [], "cloudflare": {"configured": True, "error": "synthetic provider failure"}}):
            with self.assertRaisesRegex(ValueError, "edge_incomplete"):
                adapter.preflight({}, "synthetic")
