import base64
import copy
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.hosting_image_fixtures import local_observation, stage_request, staging_policy
from tests.subprocess_support import run_test_process
from tests.test_hosting_image_activation_settlement_repository import (
    REQUEST_DIGEST,
    TARGET,
    TX,
    _plan,
    _state,
)

from sandbox.hosting.images.activation.repository import ActivationRepository
from sandbox.hosting.images.activation.settlement_models import SettlementApproval
from sandbox.hosting.images.activation.settlement_service import SettlementService
from sandbox.hosting.images.activation.settlement_store import SettlementApprovalStore
from sandbox.hosting.images.staging_models import StageResult, StagedImageProof
from sandbox.hosting.images.staging_repository import StageRepository, StageRepositoryError
from sandbox.hosting.recovery.repository import RecoveryRepository


AUTHORITY = "settlement-authority/controller-a"
AUTHORITY_REVISION = "settlement-v1"
NAMESPACE = "sandbox-feature-051-settlement"


def _placeholder_signature() -> str:
    return base64.b64encode(
        b"-----BEGIN SSH SIGNATURE-----\nsynthetic\n"
        b"-----END SSH SIGNATURE-----\n"
    ).decode("ascii")


class RecordingObserver:
    def __init__(self, observation):
        self.observation = observation
        self.calls = 0

    def observe(self, *, transaction, generation):
        self.calls += 1
        return self.observation


class DurableSettlementTests(unittest.TestCase):
    def _key(self, root: Path) -> tuple[Path, str]:
        path = root / "authority"
        result = run_test_process(
            ("/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        public = path.with_suffix(".pub").read_text(encoding="ascii").strip()
        return path, " ".join(public.split()[:2])

    def _approval(self, root: Path, key: Path, plan):
        draft = SettlementApproval.create(
            authority_id=AUTHORITY,
            authority_revision=AUTHORITY_REVISION,
            plan_digest=plan.plan_digest,
            issued_at=50,
            expires_at=200,
            signature=_placeholder_signature(),
        )
        payload = root / "approval-payload"
        payload.write_bytes(draft.signature_payload())
        result = run_test_process(
            ("/usr/bin/ssh-keygen", "-Y", "sign", "-f", str(key), "-n", NAMESPACE, str(payload)),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        signature = base64.b64encode((root / "approval-payload.sig").read_bytes()).decode("ascii")
        return SettlementApproval.create(
            authority_id=AUTHORITY,
            authority_revision=AUTHORITY_REVISION,
            plan_digest=plan.plan_digest,
            issued_at=50,
            expires_at=200,
            signature=signature,
        )

    def _fixture(self, root: Path):
        """Seed both real ledgers through their normal validation and file writers."""
        stage = StageRepository(root / "stage")
        policy = staging_policy()
        stage_request_value = stage_request(policy=policy)
        disposition, generation, _ = stage.accept(stage_request_value)
        self.assertEqual((disposition, generation), ("accepted", 1))
        stage.transition(stage_request_value, "credential_pending")
        stage.transition(stage_request_value, "helper_running", process={
            "unit_name": "sandbox-image-stage-fixture.service",
            "unit_inactive": False,
            "cgroup_empty_or_removed": False,
        })
        stage.transition(stage_request_value, "pulling")
        process = {
            "unit_name": "sandbox-image-stage-fixture.service",
            "cgroup": "/sandbox-image-stage-fixture.service",
            "delegated": False,
            "escape_allowed": False,
            "unit_inactive": True,
            "cgroup_empty_or_removed": True,
        }
        stage.transition(stage_request_value, "cleanup_pending", process=process,
                         cleanup={"complete": True})
        stage.transition(stage_request_value, "observing")
        proof = StagedImageProof.create(
            stage_request_value, policy, local_observation(policy), generation)
        stage.commit(stage_request_value, StageResult(
            1, True, "success", "staged", stage_request_value.request_id,
            generation, proof))
        _stage_generation, stage_revision = stage.target_revision(TARGET.target_identity)

        outer = RecoveryRepository(root / "hosts.json", root / "locks")
        host = outer.activation_host_state_port()
        state = copy.deepcopy(_state())
        lease_id = "activation-lease/" + "a" * 48
        holder = state["active"]["holder"]
        for row in (state["active"], state["results"]["activation-a"]):
            row["proof_pin"]["lease_id"] = lease_id
            row["proof_pin"]["proof_digest"] = proof.proof_digest
            row["proof_pin"]["host_acceptance_receipt"] = host.activation_acceptance_receipt(
                TARGET.target_identity,
                holder=holder,
                request_id="activation-a",
                request_digest=REQUEST_DIGEST,
                proof_digest=proof.proof_digest,
            )
        state["results"]["activation-a"]["proof_digest"] = proof.proof_digest
        outer._write({
            "version": 1,
            "hosts": {
                TARGET.target_identity: {
                    "generation": 0,
                    "image_activation": state,
                    "unknown_outer_field": {"retain": True},
                },
                "unrelated-target": {"legacy": {"retain": True}},
            },
        })

        mutation = outer.target_mutation_port("activate")
        with stage.proof_custody_transaction(
                TARGET.target_identity,
                target_mutation_port=mutation,
                host_state_port=host) as custody:
            lease = custody.prepare(
                lease_id=lease_id,
                holder=holder,
                admission_deadline="2099-01-01T00:00:00Z",
                activation_request_id="activation-a",
                activation_request_digest=REQUEST_DIGEST,
                stage_request_id=stage_request_value.request_id,
                stage_request_digest=stage_request_value.request_digest,
                proof_digest=proof.proof_digest,
                stage_generation=generation,
                ledger_authority="feature-050-stage-ledger-v2",
                ledger_revision=stage_revision,
            )
            evidence = host.lookup_activation_acceptance(
                TARGET.target_identity,
                holder=holder,
                request_id="activation-a",
                request_digest=REQUEST_DIGEST,
                proof_digest=proof.proof_digest,
            )
            self.assertEqual(evidence.state, "accepted")
            custody.promote(lease, evidence)

        plan = _plan()
        key, public = self._key(root)
        approval = self._approval(root, key, plan)
        store = SettlementApprovalStore(root / "approvals")
        self.assertEqual(store.install_settlement(plan, approval, public, now=100)["code"],
                         "installed")
        observer = RecordingObserver(plan.observation)
        repository = ActivationRepository(
            host_state_port=host,
            stage_repository=stage,
            target_mutation_port=mutation,
        )
        service = SettlementService(
            repository=repository,
            observer=observer,
            approval_store=store,
            clock=lambda: 100,
        )
        return {
            "stage": stage,
            "outer": outer,
            "host": host,
            "mutation": mutation,
            "repository": repository,
            "service": service,
            "plan": plan,
            "approval": approval,
            "observer": observer,
            "lease_id": lease_id,
            "proof_digest": proof.proof_digest,
        }

    def test_terminal_settlement_receipt_is_the_only_release_authority_and_preserves_outer_state(self):
        with tempfile.TemporaryDirectory(
                prefix=".sandbox-settlement-durable-", dir=Path(__file__).resolve().parents[1]) as directory:
            fixture = self._fixture(Path(directory))
            stage = fixture["stage"]
            outer = fixture["outer"]
            host = fixture["host"]
            mutation = fixture["mutation"]
            lease_id = fixture["lease_id"]
            before = outer.load()
            before_state = before["hosts"][TARGET.target_identity]["image_activation"]

            with stage.proof_custody_transaction(
                    TARGET.target_identity,
                    target_mutation_port=mutation,
                    host_state_port=host) as custody:
                lease = custody.lookup(lease_id)
                old_uncertain_evidence = host.durable_terminal_authority_evidence(
                    lease, terminal_receipt=TX)
                with self.assertRaisesRegex(StageRepositoryError, "terminal_not_durable"):
                    custody.release(lease, old_uncertain_evidence)
            self.assertIn(lease_id, stage._load_unlocked(TARGET.target_identity)["leases"])

            result = fixture["service"].apply(
                fixture["plan"], approval_digest=fixture["approval"].approval_digest)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["code"], "abandoned_with_effects")
            after = outer.load()
            after_target = after["hosts"][TARGET.target_identity]
            after_state = after_target["image_activation"]
            self.assertEqual(after["hosts"]["unrelated-target"],
                             before["hosts"]["unrelated-target"])
            self.assertEqual(after_target["unknown_outer_field"],
                             before["hosts"][TARGET.target_identity]["unknown_outer_field"])
            self.assertEqual(after_target["generation"], before["hosts"][TARGET.target_identity]["generation"])
            self.assertIsNone(after_state["current"])
            self.assertIsNone(after_state["previous"])
            self.assertEqual(after_state["generation"], before_state["generation"])
            self.assertEqual(after_state["results"], before_state["results"])
            self.assertIsNone(after_state["active"])
            self.assertIn(fixture["plan"].request_id, after_state["settlements"])
            self.assertNotIn(lease_id, stage._load_unlocked(TARGET.target_identity)["leases"])

    def test_lost_commit_ack_keeps_custody_until_durable_replay(self):
        with tempfile.TemporaryDirectory(
                prefix=".sandbox-settlement-durable-", dir=Path(__file__).resolve().parents[1]) as directory:
            fixture = self._fixture(Path(directory))
            outer = fixture["outer"]
            stage = fixture["stage"]
            lease_id = fixture["lease_id"]
            original_write = outer._write

            def lose_commit_ack(state):
                original_write(state)
                raise OSError("synthetic lost commit acknowledgement")

            outer._write = lose_commit_ack
            first = fixture["service"].apply(
                fixture["plan"], approval_digest=fixture["approval"].approval_digest)
            outer._write = original_write

            self.assertEqual(first["code"], "persistence_uncertain")
            self.assertEqual(fixture["observer"].calls, 2)
            self.assertIn(lease_id, stage._load_unlocked(TARGET.target_identity)["leases"])
            self.assertIn(fixture["plan"].request_id,
                          outer.load()["hosts"][TARGET.target_identity]["image_activation"]["settlements"])

            replay = fixture["service"].apply(
                fixture["plan"], approval_digest=fixture["approval"].approval_digest)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(replay["code"], "abandoned_with_effects")
            self.assertEqual(fixture["observer"].calls, 2)
            self.assertNotIn(lease_id, stage._load_unlocked(TARGET.target_identity)["leases"])

    def test_lost_release_ack_retains_terminal_and_only_exact_replay_releases(self):
        with tempfile.TemporaryDirectory(
                prefix=".sandbox-settlement-durable-", dir=Path(__file__).resolve().parents[1]) as directory:
            fixture = self._fixture(Path(directory))
            stage = fixture["stage"]
            lease_id = fixture["lease_id"]
            original_write = stage._write_unlocked

            def lose_release_ack(*args, **kwargs):
                raise OSError("synthetic lost release acknowledgement")

            stage._write_unlocked = lose_release_ack
            first = fixture["service"].apply(
                fixture["plan"], approval_digest=fixture["approval"].approval_digest)
            stage._write_unlocked = original_write

            self.assertEqual(first["code"], "custody_pending")
            self.assertIn(lease_id, stage._load_unlocked(TARGET.target_identity)["leases"])
            self.assertIn(fixture["plan"].request_id,
                          fixture["outer"].load()["hosts"][TARGET.target_identity]["image_activation"]["settlements"])

            replay = fixture["service"].apply(
                fixture["plan"], approval_digest=fixture["approval"].approval_digest)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(replay["code"], "abandoned_with_effects")
            self.assertEqual(fixture["observer"].calls, 2)
            self.assertNotIn(lease_id, stage._load_unlocked(TARGET.target_identity)["leases"])


if __name__ == "__main__":
    unittest.main()
