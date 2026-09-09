import unittest

from sandbox.hosting.images.activation.settlement_diagnostics import (
    SettlementDiagnosticRefusal,
    settlement_diagnostic,
    settlement_refusal,
)


CID = "0123456789abcdef" * 4


class SettlementDiagnosticTests(unittest.TestCase):
    @staticmethod
    def _value(*, reason="target_identity_changed", subject="target",
               sample="identity_before", container_id=None):
        value = {
            "schema_version": 1,
            "reason": reason,
            "subject": subject,
            "sample": sample,
        }
        if container_id is not None:
            value["container_id"] = container_id
        return value

    def test_representative_valid_combinations_are_accepted(self):
        cases = (
            (
                "evidence_changed",
                self._value(
                    reason="target_identity_changed",
                    subject="target",
                    sample="identity_after",
                ),
            ),
            (
                "evidence_changed",
                self._value(
                    reason="daemon_identity_changed",
                    subject="daemon",
                    sample="second",
                ),
            ),
            (
                "evidence_changed",
                self._value(
                    reason="container_binding_changed",
                    subject="owned_container",
                    sample="comparison",
                    container_id=CID,
                ),
            ),
            (
                "not_quiescent",
                self._value(
                    reason="helper_activity_present",
                    subject="helper_activity",
                    sample="first",
                ),
            ),
            (
                "process_owner_unavailable",
                self._value(
                    reason="container_process_owner_unavailable",
                    subject="owned_container",
                    sample="second",
                    container_id=CID,
                ),
            ),
        )

        for code, value in cases:
            with self.subTest(code=code, value=value):
                result = settlement_diagnostic(value, code)
                self.assertEqual(result, value)

    def test_returned_mapping_is_a_fresh_copy(self):
        value = self._value()

        result = settlement_diagnostic(value, "evidence_changed")

        self.assertIsNot(result, value)
        result["sample"] = "identity_after"
        self.assertEqual(value["sample"], "identity_before")
        value["sample"] = "identity_after"
        self.assertEqual(result["sample"], "identity_after")

    def test_missing_unknown_and_private_fields_are_rejected(self):
        value = self._value()

        for field in tuple(value):
            with self.subTest(missing=field):
                candidate = dict(value)
                del candidate[field]
                self.assertIsNone(settlement_diagnostic(candidate, "evidence_changed"))

        for field, field_value in (
            ("private_path", "/private/secret"),
            ("command", "docker inspect --format '{{json .}}'"),
            ("credential", "synthetic-secret"),
            ("__class__", "forged"),
        ):
            with self.subTest(extra=field):
                candidate = dict(value)
                candidate[field] = field_value
                self.assertIsNone(settlement_diagnostic(candidate, "evidence_changed"))

    def test_type_confusion_is_rejected_for_container_and_fields(self):
        for bad_value in (None, [], (), "diagnostic", 1):
            with self.subTest(value_type=type(bad_value).__name__):
                self.assertIsNone(settlement_diagnostic(bad_value, "evidence_changed"))

        for bad_code in (None, True, 1, 1.0, [], {"code": "evidence_changed"}):
            with self.subTest(code_type=type(bad_code).__name__):
                self.assertIsNone(settlement_diagnostic(self._value(), bad_code))

        for field in ("reason", "subject", "sample"):
            for bad_value in (None, True, 1, 1.0, b"identity_before", [], {}):
                with self.subTest(field=field, value_type=type(bad_value).__name__):
                    candidate = self._value()
                    candidate[field] = bad_value
                    self.assertIsNone(settlement_diagnostic(candidate, "evidence_changed"))

        candidate = self._value()
        candidate["schema_version"] = True
        self.assertIsNone(settlement_diagnostic(candidate, "evidence_changed"))
        candidate["schema_version"] = 1.0
        self.assertIsNone(settlement_diagnostic(candidate, "evidence_changed"))

    def test_code_reason_subject_and_sample_relations_are_closed(self):
        cases = (
            (
                "wrong code for reason",
                "not_quiescent",
                self._value(),
            ),
            (
                "wrong reason for code",
                "evidence_changed",
                self._value(reason="helper_activity_present", subject="helper_activity",
                            sample="first"),
            ),
            (
                "wrong subject",
                "evidence_changed",
                self._value(reason="target_identity_changed", subject="daemon", sample="first"),
            ),
            (
                "wrong sample",
                "evidence_changed",
                self._value(sample="first"),
            ),
            (
                "unknown code",
                "unknown_code",
                self._value(),
            ),
            (
                "unknown reason",
                "evidence_changed",
                self._value(reason="unknown_reason"),
            ),
            (
                "unknown subject",
                "evidence_changed",
                self._value(subject="unknown_subject"),
            ),
            (
                "unknown sample",
                "evidence_changed",
                self._value(sample="unknown_sample"),
            ),
        )

        for name, code, value in cases:
            with self.subTest(case=name):
                self.assertIsNone(settlement_diagnostic(value, code))

    def test_container_id_is_required_only_for_owned_container(self):
        owned = self._value(
            reason="container_binding_changed",
            subject="owned_container",
            sample="comparison",
        )
        self.assertIsNone(settlement_diagnostic(owned, "evidence_changed"))
        owned["container_id"] = CID
        self.assertIsNotNone(settlement_diagnostic(owned, "evidence_changed"))

        unowned = self._value(
            reason="target_identity_changed",
            subject="target",
            sample="identity_before",
        )
        self.assertIsNotNone(settlement_diagnostic(unowned, "evidence_changed"))
        unowned["container_id"] = CID
        self.assertIsNone(settlement_diagnostic(unowned, "evidence_changed"))
        unowned["container_id"] = None
        self.assertIsNone(settlement_diagnostic(unowned, "evidence_changed"))

    def test_owned_container_id_requires_exact_lowercase_64_hex(self):
        value = self._value(
            reason="container_binding_changed",
            subject="owned_container",
            sample="comparison",
        )

        for container_id in (
            CID,
            "a" * 64,
            "f" * 64,
        ):
            with self.subTest(container_id=container_id):
                candidate = dict(value)
                candidate["container_id"] = container_id
                self.assertIsNotNone(settlement_diagnostic(candidate, "evidence_changed"))

        for container_id in (
            "a" * 63,
            "a" * 65,
            "A" * 64,
            "g" * 64,
            "0" * 63 + "G",
            64,
            True,
            None,
            b"a" * 64,
        ):
            with self.subTest(container_id=container_id):
                candidate = dict(value)
                candidate["container_id"] = container_id
                self.assertIsNone(settlement_diagnostic(candidate, "evidence_changed"))

    def test_refusal_retains_code_but_discards_invalid_detail(self):
        valid = settlement_refusal(
            "evidence_changed",
            "target_identity_changed",
            "target",
            "identity_before",
        )
        self.assertIsInstance(valid, SettlementDiagnosticRefusal)
        self.assertEqual(str(valid), "evidence_changed")
        self.assertEqual(valid.diagnostic, self._value())

        invalid = settlement_refusal(
            "evidence_changed",
            "target_identity_changed",
            "target",
            "private_sample",
        )
        self.assertIsInstance(invalid, SettlementDiagnosticRefusal)
        self.assertEqual(str(invalid), "evidence_changed")
        self.assertIsNone(invalid.diagnostic)


if __name__ == "__main__":
    unittest.main()
