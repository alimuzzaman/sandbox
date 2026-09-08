import base64
import hashlib
import subprocess
import tempfile
from pathlib import Path
import unittest

from tests.subprocess_support import run_test_process

from sandbox.hosting.images.activation.settlement_models import (
    SettlementApproval, SettlementDataAssessment, SettlementObservation,
    SettlementPlan,
)
from sandbox.hosting.images.staging_models import StagingTarget


TARGET = StagingTarget("machine-a", "remote/project/production", "daemon-a")
TX = "sha256:" + "a" * 64
REQUEST = "activation-request-a"
DIGEST = "sha256:" + "b" * 64
CONTAINER = "1" * 64
PRESERVED = "sha256:" + "c" * 64
REVISION = "d" * 40
AUTHORITY = "settlement-authority/controller-a"
AUTHORITY_REVISION = "settlement-v1"
NAMESPACE = "sandbox-feature-051-settlement"


def _observation() -> SettlementObservation:
    return SettlementObservation.create(
        target=TARGET, transaction_digest=TX, generation=0,
        runtime_epoch="daemon-a", container_identities=(CONTAINER,),
        preserved_identities=(PRESERVED,), inventory_digest=DIGEST,
        process_identities=(), quiescent=True)


def _assessment() -> SettlementDataAssessment:
    return SettlementDataAssessment.create(
        target=TARGET, transaction_digest=TX, application_revision=REVISION,
        backup_receipt_digests=(DIGEST,), compatibility="forward_initialization_reviewed")


def _plan(*, request_id: str = "settlement-request-a") -> SettlementPlan:
    return SettlementPlan.create(
        request_id=request_id, active_request_id=REQUEST,
        active_request_digest=DIGEST, transaction_digest=TX, target=TARGET,
        generation=0, current_generation_digest=None,
        observation=_observation(), data_assessment=_assessment())


def _placeholder_signature() -> str:
    return base64.b64encode(
        b"-----BEGIN SSH SIGNATURE-----\nsynthetic\n-----END SSH SIGNATURE-----\n"
    ).decode("ascii")


class SettlementPolicyTests(unittest.TestCase):
    def _key(self, root: Path, name: str) -> tuple[Path, str]:
        path = root / name
        result = run_test_process(
            ("/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)),
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0)
        public = path.with_suffix(".pub").read_text(encoding="ascii").strip()
        return path, " ".join(public.split()[:2])

    def _signed_approval(self, root: Path, key: Path, plan: SettlementPlan,
                         *, namespace: str = NAMESPACE, issued_at: int = 100,
                         expires_at: int = 200) -> SettlementApproval:
        draft = SettlementApproval.create(
            authority_id=AUTHORITY, authority_revision=AUTHORITY_REVISION,
            plan_digest=plan.plan_digest, issued_at=issued_at,
            expires_at=expires_at, signature=_placeholder_signature())
        payload = root / ("approval-" + str(len(tuple(root.glob("approval-*.json")))) + ".json")
        payload.write_bytes(draft.signature_payload())
        result = run_test_process(
            ("/usr/bin/ssh-keygen", "-Y", "sign", "-f", str(key),
             "-n", namespace, str(payload)), check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0)
        signature = base64.b64encode(
            payload.with_name(payload.name + ".sig").read_bytes()).decode("ascii")
        return SettlementApproval.create(
            authority_id=AUTHORITY, authority_revision=AUTHORITY_REVISION,
            plan_digest=plan.plan_digest, issued_at=issued_at,
            expires_at=expires_at, signature=signature)

    def test_real_signed_approval_binds_namespace_key_plan_revision_and_time(self):
        from sandbox.hosting.images.activation.settlement_policy import (
            SettlementApprovalVerifier, verify_settlement_approval,
        )

        plan = _plan()
        with tempfile.TemporaryDirectory(prefix="sandbox-settlement-test-") as directory:
            root = Path(directory)
            key, public = self._key(root, "authority")
            approval = self._signed_approval(root, key, plan)
            verifier = SettlementApprovalVerifier(
                public, AUTHORITY, AUTHORITY_REVISION,
                "sha256:" + hashlib.sha256(public.encode("ascii")).hexdigest())
            self.assertTrue(verifier.verify(approval, plan, now=100))
            self.assertTrue(verify_settlement_approval(approval, plan, verifier, now=199))
            self.assertFalse(verifier.verify(approval, plan, now=99))
            self.assertFalse(verifier.verify(approval, plan, now=200))
            self.assertFalse(verifier.verify(approval, _plan(request_id="other"), now=100))
            wrong_revision = SettlementApprovalVerifier(
                public, AUTHORITY, "settlement-v2",
                "sha256:" + hashlib.sha256(public.encode("ascii")).hexdigest())
            self.assertFalse(wrong_revision.verify(approval, plan, now=100))

    def test_wrong_namespace_and_key_are_refused(self):
        from sandbox.hosting.images.activation.settlement_policy import SettlementApprovalVerifier

        plan = _plan()
        with tempfile.TemporaryDirectory(prefix="sandbox-settlement-test-") as directory:
            root = Path(directory)
            key, public = self._key(root, "authority")
            other_key, other_public = self._key(root, "other")
            wrong_namespace = self._signed_approval(root, key, plan, namespace="other-namespace")
            verifier = SettlementApprovalVerifier(
                public, AUTHORITY, AUTHORITY_REVISION,
                "sha256:" + hashlib.sha256(public.encode("ascii")).hexdigest())
            self.assertFalse(verifier.verify(wrong_namespace, plan, now=100))
            signed = self._signed_approval(root, key, plan)
            other_verifier = SettlementApprovalVerifier(
                other_public, AUTHORITY, AUTHORITY_REVISION,
                "sha256:" + hashlib.sha256(other_public.encode("ascii")).hexdigest())
            self.assertFalse(other_verifier.verify(signed, plan, now=100))

    def test_installed_digest_and_principal_are_strict(self):
        from sandbox.hosting.images.activation.settlement_policy import SettlementApprovalVerifier

        with tempfile.TemporaryDirectory(prefix="sandbox-settlement-test-") as directory:
            _key, public = self._key(Path(directory), "authority")
            digest = "sha256:" + hashlib.sha256(public.encode("ascii")).hexdigest()
            with self.assertRaises(ValueError):
                SettlementApprovalVerifier(public + "\n", AUTHORITY, AUTHORITY_REVISION, digest)
            with self.assertRaises(ValueError):
                SettlementApprovalVerifier(public, AUTHORITY + "\nforged", AUTHORITY_REVISION, digest)
            with self.assertRaises(ValueError):
                SettlementApprovalVerifier(public, AUTHORITY, AUTHORITY_REVISION,
                                          "sha256:" + "0" * 64)

    def test_arbitrary_verifier_cannot_cross_machine_authority_boundary(self):
        from sandbox.hosting.images.activation.settlement_policy import verify_settlement_approval

        class ForgedVerifier:
            def verify(self, *_args, **_kwargs):
                return True

        self.assertFalse(verify_settlement_approval(object(), object(), ForgedVerifier()))


if __name__ == "__main__":
    unittest.main()
