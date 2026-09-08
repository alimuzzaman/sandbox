import base64
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.subprocess_support import run_test_process

from sandbox.hosting.images.activation.settlement_forward import ForwardSettlementApproval
from sandbox.hosting.images.activation.settlement_models import (
    SettlementApproval,
    SettlementDataAssessment,
    SettlementObservation,
    SettlementPlan,
)
from sandbox.hosting.images.activation.settlement_service import SettlementError
from sandbox.hosting.images.activation.settlement_store import SettlementApprovalStore
from sandbox.hosting.images.staging_models import StagingTarget


TARGET = StagingTarget("machine-a", "target-a", "daemon-a")
TX = "sha256:" + "a" * 64
REQUEST_DIGEST = "sha256:" + "b" * 64
PROOF_DIGEST = "sha256:" + "c" * 64
REVISION = "d" * 40
AUTHORITY = "settlement-authority/controller-a"
AUTHORITY_REVISION = "settlement-v1"
SETTLEMENT_NAMESPACE = "sandbox-feature-051-settlement"
FORWARD_NAMESPACE = "sandbox-feature-051-settlement-forward"


def _placeholder_signature() -> str:
    return base64.b64encode(
        b"-----BEGIN SSH SIGNATURE-----\nsynthetic\n"
        b"-----END SSH SIGNATURE-----\n"
    ).decode("ascii")


def _observation() -> SettlementObservation:
    return SettlementObservation.create(
        target=TARGET,
        transaction_digest=TX,
        generation=0,
        runtime_epoch=TARGET.daemon_identity,
        container_identities=("1" * 64,),
        preserved_identities=(PROOF_DIGEST,),
        inventory_digest=REQUEST_DIGEST,
    )


def _assessment() -> SettlementDataAssessment:
    return SettlementDataAssessment.create(
        target=TARGET,
        transaction_digest=TX,
        application_revision=REVISION,
        backup_receipt_digests=(REQUEST_DIGEST,),
    )


def _plan(request_id: str = "settlement-request-a") -> SettlementPlan:
    return SettlementPlan.create(
        request_id=request_id,
        active_request_id="activation-a",
        active_request_digest=REQUEST_DIGEST,
        transaction_digest=TX,
        target=TARGET,
        generation=0,
        current_generation_digest=None,
        observation=_observation(),
        data_assessment=_assessment(),
    )


class SettlementStoreTests(unittest.TestCase):
    def _key(self, root: Path, name: str) -> tuple[Path, str]:
        path = root / name
        result = run_test_process(
            ("/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        public = path.with_suffix(".pub").read_text(encoding="ascii").strip()
        return path, " ".join(public.split()[:2])

    def _signed(self, root: Path, key: Path, value, *, namespace: str):
        draft = type(value).create(
            **{
                key_name: getattr(value, key_name)
                for key_name in value.__dataclass_fields__
                if key_name not in {"schema_version", "signature", "approval_digest"}
            },
            signature=_placeholder_signature(),
        )
        payload = root / ("payload-" + str(len(tuple(root.glob("payload-*.json")))) + ".json")
        payload.write_bytes(draft.signature_payload())
        result = run_test_process(
            ("/usr/bin/ssh-keygen", "-Y", "sign", "-f", str(key), "-n", namespace, str(payload)),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        signature = base64.b64encode(
            payload.with_name(payload.name + ".sig").read_bytes()
        ).decode("ascii")
        fields = {
            key_name: getattr(value, key_name)
            for key_name in value.__dataclass_fields__
            if key_name not in {"schema_version", "signature", "approval_digest"}
        }
        return type(value).create(**fields, signature=signature)

    def _settlement_approval(self, root: Path, key: Path, *, namespace=SETTLEMENT_NAMESPACE,
                             plan=None, issued_at=100, expires_at=200):
        plan = plan or _plan()
        draft = SettlementApproval.create(
            authority_id=AUTHORITY,
            authority_revision=AUTHORITY_REVISION,
            plan_digest=plan.plan_digest,
            issued_at=issued_at,
            expires_at=expires_at,
            signature=_placeholder_signature(),
        )
        return self._signed(root, key, draft, namespace=namespace)

    def _forward_approval(self, root: Path, key: Path, *, namespace=FORWARD_NAMESPACE):
        draft = ForwardSettlementApproval.create(
            authority_id=AUTHORITY,
            authority_revision=AUTHORITY_REVISION,
            operation="activate",
            target=TARGET,
            request_id="forward-request-a",
            expected_generation=0,
            predecessor_digest=TX,
            transaction_digest=TX,
            application_revision=REVISION,
            plan_set_digest=REQUEST_DIGEST,
            proof_set_digest=PROOF_DIGEST,
            compose_snapshot_digest=REQUEST_DIGEST,
            policy_digest=PROOF_DIGEST,
            rollback_grant_digest=TX,
            data_assessment=_assessment(),
            issued_at=100,
            expires_at=200,
            signature=_placeholder_signature(),
        )
        return self._signed(root, key, draft, namespace=namespace)

    def test_signed_install_read_and_exact_replay_are_bound_to_plan(self):
        with tempfile.TemporaryDirectory(
                prefix=".sandbox-settlement-store-", dir=Path(__file__).resolve().parents[1]) as directory:
            root = Path(directory)
            key, public = self._key(root, "authority")
            plan = _plan()
            approval = self._settlement_approval(root, key, plan=plan)
            store = SettlementApprovalStore(root / "installed")

            installed = store.install_settlement(plan, approval, public, now=100)
            replayed = store.install_settlement(plan, approval, public, now=100)
            self.assertEqual(installed["code"], "installed")
            self.assertEqual(replayed["code"], "replayed")
            self.assertEqual(replayed["installation_digest"], installed["installation_digest"])
            self.assertEqual(store.read_settlement(plan, approval.approval_digest, now=199), approval)

            changed_plan = _plan(request_id="settlement-request-b")
            with self.assertRaisesRegex(SettlementError, "authority_mismatch"):
                store.read_settlement(changed_plan, approval.approval_digest, now=100)

    def test_stale_approval_and_missing_or_unsafe_files_fail_closed(self):
        with tempfile.TemporaryDirectory(
                prefix=".sandbox-settlement-store-", dir=Path(__file__).resolve().parents[1]) as directory:
            root = Path(directory)
            key, public = self._key(root, "authority")
            plan = _plan()
            approval = self._settlement_approval(root, key, plan=plan)
            store = SettlementApprovalStore(root / "installed")
            store.install_settlement(plan, approval, public, now=100)
            path = store._path("settlement", plan.target.as_mapping(), approval.approval_digest)

            with self.assertRaisesRegex(SettlementError, "authority_mismatch"):
                store.read_settlement(plan, approval.approval_digest, now=200)

            os.chmod(path, 0o644)
            with self.assertRaisesRegex(Exception, "path_unsafe") as unsafe:
                store.read_settlement(plan, approval.approval_digest, now=100)
            self.assertEqual(getattr(unsafe.exception, "code", None), "path_unsafe")

            os.chmod(path, 0o600)
            other_plan = _plan(request_id="settlement-request-b")
            other_approval = self._settlement_approval(root, key, plan=other_plan)
            store.install_settlement(other_plan, other_approval, public, now=100)
            other_path = store._path(
                "settlement", other_plan.target.as_mapping(), other_approval.approval_digest)
            os.replace(other_path, path)
            with self.assertRaisesRegex(SettlementError, "authority_mismatch"):
                store.read_settlement(plan, approval.approval_digest, now=100)

            path.unlink()
            with self.assertRaisesRegex(SettlementError, "authority_missing"):
                store.read_settlement(plan, approval.approval_digest, now=100)

    def test_forward_and_settlement_namespaces_cannot_cross_install_or_read(self):
        with tempfile.TemporaryDirectory(
                prefix=".sandbox-settlement-store-", dir=Path(__file__).resolve().parents[1]) as directory:
            root = Path(directory)
            key, public = self._key(root, "authority")
            plan = _plan()
            settlement_store = SettlementApprovalStore(root / "installed")

            forward_namespace_on_settlement = self._settlement_approval(
                root, key, namespace=FORWARD_NAMESPACE, plan=plan)
            with self.assertRaisesRegex(SettlementError, "authority_mismatch"):
                settlement_store.install_settlement(
                    plan, forward_namespace_on_settlement, public, now=100)

            settlement_approval = self._settlement_approval(root, key, plan=plan)
            with self.assertRaisesRegex(SettlementError, "authority_mismatch"):
                settlement_store.install_settlement(
                    plan, settlement_approval, public, now=200)

            forward_namespace = self._forward_approval(root, key)
            with self.assertRaisesRegex(SettlementError, "authority_mismatch"):
                settlement_store.install_forward(
                    self._forward_approval(root, key, namespace=SETTLEMENT_NAMESPACE), public, now=100)
            self.assertEqual(
                settlement_store.install_forward(forward_namespace, public, now=100)["code"],
                "installed",
            )
            with self.assertRaisesRegex(SettlementError, "authority_missing"):
                settlement_store.read_settlement(
                    plan, forward_namespace.approval_digest, now=100)
            self.assertEqual(
                settlement_store.read_forward(
                    TARGET.as_mapping(), forward_namespace.approval_digest, now=199),
                forward_namespace,
            )


if __name__ == "__main__":
    unittest.main()
