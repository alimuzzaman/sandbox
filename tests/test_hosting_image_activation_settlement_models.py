import base64
from sandbox.hosting.images.activation.models import activation_digest
import unittest

from sandbox.hosting.images.activation.settlement_models import (
    SettlementApproval, SettlementDataAssessment, SettlementObservation,
    SettlementPlan,
)
from sandbox.hosting.images.staging_models import StagingTarget


TARGET = StagingTarget("machine-a", "remote/project/production", "daemon-a")
TX = "sha256:" + "a" * 64
REQUEST = "activation-request-a"
GENERATION = 0
DIGEST = "sha256:" + "b" * 64
CONTAINER = "1" * 64
PRESERVED = "sha256:" + "c" * 64
REVISION = "d" * 40


def ssh_signature() -> str:
    armored = b"-----BEGIN SSH SIGNATURE-----\nsynthetic\n-----END SSH SIGNATURE-----\n"
    return base64.b64encode(armored).decode("ascii")


def observation(**changes):
    values = dict(
        target=TARGET, transaction_digest=TX, generation=GENERATION,
        runtime_epoch="daemon-a", container_identities=(CONTAINER,),
        preserved_identities=(PRESERVED,), inventory_digest=DIGEST,
        process_identities=(), quiescent=True,
    )
    values.update(changes)
    return SettlementObservation.create(**values)


def assessment(**changes):
    values = dict(
        target=TARGET, transaction_digest=TX, application_revision=REVISION,
        backup_receipt_digests=(DIGEST,),
        compatibility="forward_initialization_reviewed",
    )
    values.update(changes)
    return SettlementDataAssessment.create(**values)


def plan(**changes):
    values = dict(
        request_id="settlement-request-a", active_request_id=REQUEST,
        active_request_digest=DIGEST, transaction_digest=TX, target=TARGET,
        generation=GENERATION, current_generation_digest=None,
        observation=observation(), data_assessment=assessment(),
        reason="retained_effect_unknown",
    )
    values.update(changes)
    return SettlementPlan.create(**values)


class SettlementModelTests(unittest.TestCase):
    def test_recomputed_boolean_schema_is_rejected(self):
        approval = SettlementApproval.create(
            authority_id="authority-a", authority_revision="revision-a",
            plan_digest=plan().plan_digest, issued_at=100, expires_at=200,
            signature=ssh_signature())
        for value, kind, digest_field in (
            (observation(), "observation", "observation_digest"),
            (assessment(), "data-assessment", "assessment_digest"),
            (plan(), "plan", "plan_digest"),
            (approval, "approval", "approval_digest"),
        ):
            raw = value.as_mapping()
            raw["schema_version"] = True
            body = {key: item for key, item in raw.items() if key != digest_field}
            raw[digest_field] = activation_digest(
                "sandbox.hosting.images.settlement-" + kind + ".v1", body)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                type(value).from_mapping(raw)

    def test_observation_epoch_must_match_target_daemon(self):
        with self.assertRaises(ValueError):
            observation(runtime_epoch="another-daemon")

    def test_round_trips_all_closed_values(self):
        for value in (observation(), assessment(), plan()):
            cls = type(value)
            self.assertEqual(cls.from_mapping(value.as_mapping()), value)
        approval = SettlementApproval.create(
            authority_id="settlement-authority/controller-a",
            authority_revision="settlement-v1", plan_digest=plan().plan_digest,
            issued_at=100, expires_at=200, signature=ssh_signature())
        self.assertEqual(SettlementApproval.from_mapping(approval.as_mapping()), approval)

    def test_unknown_fields_and_wrong_schema_are_rejected(self):
        raw = observation().as_mapping(); raw["extra"] = 1
        with self.assertRaises(ValueError): SettlementObservation.from_mapping(raw)
        raw = assessment().as_mapping(); raw["schema_version"] = 2
        with self.assertRaises(ValueError): SettlementDataAssessment.from_mapping(raw)
        raw = plan().as_mapping(); raw["extra"] = 1
        with self.assertRaises(ValueError): SettlementPlan.from_mapping(raw)
        raw = SettlementApproval.create(
            authority_id="authority-a", authority_revision="revision-a",
            plan_digest=plan().plan_digest, issued_at=100, expires_at=200,
            signature=ssh_signature()).as_mapping()
        raw["signature"] = "x"
        with self.assertRaises(ValueError): SettlementApproval.from_mapping(raw)

    def test_digest_tampering_is_rejected(self):
        raw = observation().as_mapping(); raw["observation_digest"] = DIGEST
        with self.assertRaises(ValueError): SettlementObservation.from_mapping(raw)
        raw = assessment().as_mapping(); raw["assessment_digest"] = DIGEST
        with self.assertRaises(ValueError): SettlementDataAssessment.from_mapping(raw)
        raw = plan().as_mapping(); raw["plan_digest"] = DIGEST
        with self.assertRaises(ValueError): SettlementPlan.from_mapping(raw)
        approval = SettlementApproval.create(
            authority_id="authority-a", authority_revision="revision-a",
            plan_digest=plan().plan_digest, issued_at=100, expires_at=200,
            signature=ssh_signature()).as_mapping()
        approval["approval_digest"] = DIGEST
        with self.assertRaises(ValueError): SettlementApproval.from_mapping(approval)

    def test_observation_rejects_nonquiescent_processes_and_bad_identities(self):
        with self.assertRaises(ValueError): observation(quiescent=False)
        with self.assertRaises(ValueError): observation(process_identities=("pid",))
        with self.assertRaises(ValueError): observation(container_identities=("bad",))
        with self.assertRaises(ValueError): observation(
            container_identities=(CONTAINER, CONTAINER))
        with self.assertRaises(ValueError): observation(
            container_identities=("2" * 64, CONTAINER))

    def test_assessment_requires_nonempty_backup_and_revision(self):
        with self.assertRaises(ValueError): assessment(backup_receipt_digests=())
        with self.assertRaises(ValueError): assessment(application_revision="A" * 40)
        with self.assertRaises(ValueError): assessment(
            backup_receipt_digests=(DIGEST, DIGEST))
        with self.assertRaises(ValueError): assessment(
            compatibility="forward_initialization_unreviewed")

    def test_plan_cross_binds_target_transaction_generation_and_assessment(self):
        other = StagingTarget("machine-b", "remote/project/production", "daemon-b")
        with self.assertRaises(ValueError): plan(observation=observation(target=other))
        with self.assertRaises(ValueError): plan(data_assessment=assessment(transaction_digest=DIGEST))
        with self.assertRaises(ValueError): plan(observation=observation(generation=1))
        with self.assertRaises(ValueError): plan(data_assessment=assessment(target=other))
        with self.assertRaises(ValueError): plan(reason="operator_override")

    def test_bounds_and_bool_fields_are_rejected(self):
        with self.assertRaises(ValueError): observation(generation=True)
        with self.assertRaises(ValueError): observation(
            preserved_identities=tuple("sha256:" + format(i, "064x") for i in range(513)))
        with self.assertRaises(ValueError): plan(generation=True)
        with self.assertRaises(ValueError): SettlementApproval.create(
            authority_id="authority-a", authority_revision="revision-a",
            plan_digest=plan().plan_digest, issued_at=True, expires_at=200,
            signature=ssh_signature())
        with self.assertRaises(ValueError): SettlementApproval.create(
            authority_id="authority-a", authority_revision="revision-a",
            plan_digest=plan().plan_digest, issued_at=100, expires_at=3701,
            signature=ssh_signature())

    def test_signature_payload_excludes_signature_and_digest_and_checks_ssh_armor(self):
        approval = SettlementApproval.create(
            authority_id="authority-a", authority_revision="revision-a",
            plan_digest=plan().plan_digest, issued_at=100, expires_at=200,
            signature=ssh_signature())
        self.assertNotIn(approval.signature.encode(), approval.signature_payload())
        self.assertNotIn(approval.approval_digest.encode(), approval.signature_payload())
        for signature in ("bad", base64.b64encode(b"not ssh").decode(),
                          "x" * 8193):
            with self.assertRaises(ValueError): SettlementApproval.create(
                authority_id="authority-a", authority_revision="revision-a",
                plan_digest=plan().plan_digest, issued_at=100, expires_at=200,
                signature=signature)


if __name__ == "__main__":
    unittest.main()
