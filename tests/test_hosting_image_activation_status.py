import json
from pathlib import Path
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.hosting.images.activation.repository import empty_activation_state


class ActivationStatusTests(unittest.TestCase):
    def test_settled_status_retains_uncertainty_and_exposes_required_successor(self):
        from sandbox.hosting.images.activation.status import activation_status
        from tests.test_hosting_image_activation_settlement_repository import _state, _plan, settle_candidate
        _, state, record = settle_candidate(_state(), _plan(), "sha256:" + "9" * 64)
        result = activation_status(state)
        self.assertEqual(result["retained_settlement_count"], 1)
        self.assertEqual(result["required_settlement_predecessor"], record["terminal_receipt"]["terminal_digest"])
        self.assertEqual(result["generation"], 0)
        self.assertIsNone(result["current_generation_digest"])
        self.assertEqual(result["retained_result_count"], 1)
        self.assertNotIn("signature", json.dumps(result))

    def test_fixed_failure_codes_survive_without_dependency_error_text(self):
        from sandbox.commands.hosting import _cmd_host_image
        from tests.test_hosting_image_activation_v2 import artifacts
        plan, proof, _snapshot = artifacts()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "plan.json").write_text(json.dumps(plan.as_mapping()))
            (root / "proof.json").write_text(json.dumps(proof.as_mapping()))
            args = SimpleNamespace(image_action="activate", project_dir=str(root),
                environment="production", remote="synthetic", request_id="status-test",
                expected_generation=0, confirm=True,
                verified_plan=str(root / "plan.json"), staged_proof=str(root / "proof.json"),
                admission_deadline="2999-01-01T00:00:00Z")
            for message, expected in (("topology_mismatch", "topology_mismatch"),
                    ("private payload must never escape", "policy_mismatch")):
                out = StringIO()
                with patch("sandbox.commands.hosting._host_image_machine_bundle",
                           side_effect=RuntimeError(message)), \
                        redirect_stdout(out), self.assertRaises(SystemExit):
                    _cmd_host_image({}, args)
                self.assertEqual(json.loads(out.getvalue())["code"], expected)
                self.assertNotIn("private payload", out.getvalue())

    def test_empty_valid_state_is_observed_without_inventing_runtime_evidence(self):
        from sandbox.hosting.images.activation.status import activation_status
        result = activation_status(empty_activation_state())
        self.assertEqual(result, {
            "schema_version": 1, "ok": True, "code": "observed",
            "state_schema_version": 1, "generation": 0, "active": None,
            "current_generation_digest": None, "previous_generation_digest": None,
            "retained_result_count": 0, "retained_recovery_count": 0,
        })

    def test_active_v2_reports_only_recovery_selectors(self):
        from sandbox.hosting.images.activation.status import activation_status
        from sandbox.hosting.images.activation.v2_repository import accept_candidate_v2
        from tests.test_hosting_image_activation_v2 import artifacts, grant_for, request_for, TARGET
        plan, proof, snapshot = artifacts(graph=True)
        grant = grant_for(plan, proof, snapshot=snapshot)
        request = request_for(plan, proof, snapshot, grant)
        pin = {"lease_id": "activation-lease/" + "a" * 48,
               "holder": "activation-owner/activate-v2-a", "phase": "accepted",
               "proof_digest": proof.proof_digest,
               "host_acceptance_receipt": "host-acceptance/" + "b" * 64}
        _, state, _ = accept_candidate_v2(empty_activation_state(), request,
            holder=pin["holder"], proof_pin=pin,
            recovery_context={"target": TARGET, "compose_project": "lenzora",
                "selected_services": list(plan.policy.persistent_services),
                "compose_snapshot": snapshot.as_mapping(),
                "compatibility_grant": grant.as_mapping()},
            prior_generation_digest=grant.prior_generation_digest)
        result = activation_status(state)
        self.assertEqual(result["state_schema_version"], 1)
        self.assertEqual(result["active"]["schema_version"], 2)
        self.assertEqual(result["active"]["transaction_digest"], state["active"]["transaction_digest"])
        self.assertEqual(set(result["active"]), {"schema_version", "request_id",
            "request_digest", "transaction_digest", "operation", "phase", "effect_entered"})
        for forbidden in ("compose_snapshot", "proof_pin", "holder", "compatibility_grant"):
            self.assertNotIn(forbidden, json.dumps(result))

    def test_corrupt_state_is_never_reported_empty(self):
        from sandbox.hosting.images.activation.status import activation_status
        from sandbox.hosting.images.activation.repository import ActivationRepositoryError
        state = empty_activation_state(); state["active"] = {"schema_version": 2}
        with self.assertRaises(ActivationRepositoryError):
            activation_status(state)

    def test_cli_read_requires_no_confirmation_plan_broker_or_stage_repository(self):
        from sandbox.commands.hosting import cmd_host
        args = SimpleNamespace(action="image", image_action="status", project_dir="/synthetic",
            environment="production", remote="synthetic", json=True, all=False, confirm=False)
        out = StringIO()
        with patch("sandbox.commands.hosting.hosting.validate_manifest", return_value={}), \
                patch("sandbox.commands.hosting.hosting.state_key", return_value="target-a"), \
                patch("sandbox.commands.hosting.remote.registered_remote_lock", return_value=nullcontext()), \
                patch("sandbox.commands.hosting.remote.get_remote", return_value={"name": "synthetic"}), \
                patch("sandbox.hosting.images.activation.repository.ActivationRepository.snapshot",
                      return_value=empty_activation_state()), \
                patch("sandbox.commands.hosting.RecoveryRepository") as recovery, \
                patch("sandbox.hosting.images.staging_repository.StageRepository", side_effect=AssertionError("stage opened")), \
                patch("sandbox.commands.hosting._host_image_machine_bundle", side_effect=AssertionError("authority opened")), \
                redirect_stdout(out):
            cmd_host({}, args)
        self.assertTrue(json.loads(out.getvalue())["ok"])
        recovery.return_value.target_mutation_port.assert_called_once_with("image-recover")
