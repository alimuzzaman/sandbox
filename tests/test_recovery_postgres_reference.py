import copy
import unittest

from sandbox.recovery import postgres_helper as helper


def schema_records():
    return {
        "constraints": [
            {"table": "orders", "name": "orders_check", "definition": "CHECK (amount > 0)", "validated": True},
            {"table": "orders", "name": "orders_customer_fkey", "definition": "FOREIGN KEY (customer_id) REFERENCES customers(id)", "validated": True},
        ],
        "columns": [
            {"table": "customers", "column": "id", "type": "integer", "nullable": "NO"},
            {"table": "orders", "column": "id", "type": "integer", "nullable": "NO"},
            {"table": "orders", "column": "amount", "type": "numeric", "nullable": "NO"},
        ],
    }


def observation(*, version=1, structure=None, **changes):
    value = {
        "major": 16,
        "database_identity": "db-identity",
        "table_counts": [{"name": "orders", "count": 3}],
        "migration_checksum": "migration-checksum",
        "schema_digest": "sha256:" + "a" * 64,
        "constraints_valid": True,
    }
    if version == 2:
        value["schema_fingerprint_version"] = 2
        value["schema_structure_digest"] = structure or helper.schema_structure_digest(schema_records())
    value.update(changes)
    return value


def source(*, profile="lenzora-dev", **changes):
    value = {
        "client_image_id": "sha256:" + "c" * 64,
        "database": "app",
        "role": "postgres",
        "profile": profile,
    }
    value.update(changes)
    return value


def evidence(*, version=2, **changes):
    value = observation(version=version)
    value["dump_digest"] = "sha256:" + "d" * 64
    value.update(changes)
    return value


def proof_for(source_value, evidence_value, actual, *, method="raw-capture-equality",
              archive_digest=None, native_request_id=None, database=None, role=None,
              reference=None, target_schema_digest=None, verified_at=200):
    archive_digest = archive_digest or "sha256:" + "e" * 64
    native_request_id = native_request_id or "f" * 64
    database = database or source_value["database"]
    role = role or source_value["role"]
    return {
        "native_request_id": native_request_id,
        "source_digest": helper._schema_digest(source_value),
        "archive_digest": archive_digest,
        "dump_digest": evidence_value["dump_digest"],
        "image_id": source_value["client_image_id"],
        "database": database,
        "role": role,
        "captured_schema_digest": evidence_value["schema_digest"],
        "schema_version": 1,
        "method": method,
        "observed_schema_digest": actual["schema_digest"],
        "verified_at": verified_at,
        "target_schema_digest": target_schema_digest,
        "reference": reference,
    }


def reference_for(source_value, evidence_value, *, native_request_id=None,
                  archive_digest=None, database=None, role=None,
                  structure_digest=None, reference_schema_digest=None,
                  structure_origin="capture-v2", completed_at=100):
    archive_digest = archive_digest or "sha256:" + "e" * 64
    native_request_id = native_request_id or "f" * 64
    database = database or source_value["database"]
    role = role or source_value["role"]
    return {
        "native_request_id": native_request_id,
        "source_digest": helper._schema_digest(source_value),
        "archive_digest": archive_digest,
        "dump_digest": evidence_value["dump_digest"],
        "image_id": source_value["client_image_id"],
        "database": database,
        "role": role,
        "captured_schema_digest": evidence_value["schema_digest"],
        "schema_version": 1,
        "method": "archived-schema-reference-v1",
        "structure_digest": structure_digest or evidence_value["schema_structure_digest"],
        "structure_origin": structure_origin,
        "reference_schema_digest": reference_schema_digest or "sha256:" + "a" * 64,
        "completed_at": completed_at,
    }


class PostgresReferenceSchemaTests(unittest.TestCase):
    def test_structure_digest_is_table_order_independent_but_keeps_column_order(self):
        original = schema_records()
        reordered = copy.deepcopy(original)
        reordered["constraints"].reverse()
        reordered["columns"] = [reordered["columns"][1], reordered["columns"][2], reordered["columns"][0]]
        self.assertEqual(helper.schema_structure_digest(original),
                         helper.schema_structure_digest(reordered))

        changed = copy.deepcopy(original)
        changed["columns"][1], changed["columns"][2] = changed["columns"][2], changed["columns"][1]
        self.assertNotEqual(helper.schema_structure_digest(original),
                            helper.schema_structure_digest(changed))

    def test_constraint_definition_is_ignored_only_by_structure_fingerprint(self):
        original = schema_records()
        changed = copy.deepcopy(original)
        changed["constraints"][0]["definition"] = "CHECK ((amount)::numeric > (0)::numeric)"
        self.assertEqual(helper.schema_structure_digest(original),
                         helper.schema_structure_digest(changed))
        self.assertNotEqual(helper._schema_digest(original), helper._schema_digest(changed))

    def test_each_structural_field_changes_the_structure_fingerprint(self):
        for kind, field, value in (
            ("constraints", "table", "payments"),
            ("constraints", "name", "orders_other_check"),
            ("constraints", "validated", False),
            ("columns", "table", "payments"),
            ("columns", "column", "total"),
            ("columns", "type", "text"),
            ("columns", "nullable", "YES"),
        ):
            with self.subTest(kind=kind, field=field):
                changed = copy.deepcopy(schema_records())
                changed[kind][0][field] = value
                self.assertNotEqual(helper.schema_structure_digest(schema_records()),
                                    helper.schema_structure_digest(changed))

    def test_v1_matches_fixed_observation_fields_without_structure_field(self):
        result = helper.observation_matches(observation(), observation())
        self.assertEqual(set(result), {
            "constraints_valid", "database_identity", "major", "migration_checksum",
            "schema_digest", "table_counts",
        })
        self.assertTrue(all(result.values()))

    def test_v2_requires_and_compares_structural_fingerprint(self):
        value = observation(version=2)
        result = helper.observation_matches(value, copy.deepcopy(value))
        self.assertIn("schema_structure_digest", result)
        self.assertTrue(all(result.values()))

        changed = copy.deepcopy(value)
        changed["schema_structure_digest"] = "sha256:" + "b" * 64
        self.assertFalse(helper.observation_matches(value, changed)["schema_structure_digest"])
        changed = copy.deepcopy(value)
        changed["schema_digest"] = "sha256:" + "b" * 64
        self.assertFalse(helper.observation_matches(value, changed)["schema_digest"])

    def test_renamed_database_omits_only_database_identity(self):
        value = observation()
        renamed = observation(database_identity="different-db")
        result = helper.observation_matches(value, renamed, renamed_database=True)
        self.assertNotIn("database_identity", result)
        self.assertTrue(all(result.values()))

    def test_unknown_and_malformed_fingerprint_versions_fail_closed(self):
        cases = [
            {**observation(), "schema_fingerprint_version": 3},
            {**observation(), "schema_fingerprint_version": 1, "schema_structure_digest": "sha256:" + "a" * 64},
            {**observation(), "schema_fingerprint_version": 2},
            {**observation(version=2), "schema_structure_digest": "bad"},
            {**observation(version=2), "schema_fingerprint_version": 1},
            {key: value for key, value in observation().items() if key != "schema_digest"},
        ]
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "schema_evidence_invalid"):
                    helper.observation_matches(value, observation())

        with self.assertRaisesRegex(ValueError, "schema_evidence_invalid"):
            helper.observation_matches(observation(version=2), observation())

    def test_raw_capture_proof_accepts_only_complete_observation_equality(self):
        source_value = source()
        evidence_value = evidence()
        actual = observation(version=2)
        proof = proof_for(source_value, evidence_value, actual)

        self.assertIs(helper.validate_schema_proof(
            proof, source_value, evidence_value, actual, "sha256:" + "e" * 64,
            "f" * 64, "app", "postgres"), proof)

        changed = copy.deepcopy(actual)
        changed["table_counts"] = [{"name": "orders", "count": 4}]
        with self.assertRaisesRegex(ValueError, "restore_verification_failed"):
            helper.validate_schema_proof(
                proof_for(source_value, evidence_value, changed), source_value,
                evidence_value, changed, "sha256:" + "e" * 64, "f" * 64,
                "app", "postgres")

        changed = copy.deepcopy(proof)
        changed["reference"] = {"unexpected": True}
        with self.assertRaisesRegex(ValueError, "restore_verification_failed"):
            helper.validate_schema_proof(
                changed, source_value, evidence_value, actual,
                "sha256:" + "e" * 64, "f" * 64, "app", "postgres")

    def test_archived_reference_proof_accepts_raw_schema_text_change_with_same_structure(self):
        source_value = source()
        evidence_value = evidence()
        actual = observation(version=2, schema_digest="sha256:" + "b" * 64)
        reference = reference_for(
            source_value, evidence_value,
            reference_schema_digest="sha256:" + "c" * 64)
        proof = proof_for(
            source_value, evidence_value, actual,
            method="archived-schema-reference-v1", reference=reference,
            target_schema_digest=reference["reference_schema_digest"])

        self.assertIs(helper.validate_schema_proof(
            proof, source_value, evidence_value, actual, "sha256:" + "e" * 64,
            "f" * 64, "app", "postgres"), proof)

        changed = copy.deepcopy(actual)
        changed["schema_structure_digest"] = "sha256:" + "b" * 64
        with self.assertRaisesRegex(ValueError, "restore_verification_failed"):
            helper.validate_schema_proof(
                proof_for(
                    source_value, evidence_value, changed,
                    method="archived-schema-reference-v1", reference=reference,
                    target_schema_digest=reference["reference_schema_digest"]),
                source_value, evidence_value, changed, "sha256:" + "e" * 64,
                "f" * 64, "app", "postgres")

        changed = copy.deepcopy(actual)
        changed["table_counts"] = [{"name": "orders", "count": 4}]
        changed_proof = proof_for(
            source_value, evidence_value, changed,
            method="archived-schema-reference-v1", reference=reference,
            target_schema_digest=reference["reference_schema_digest"])
        with self.assertRaisesRegex(ValueError, "restore_verification_failed"):
            helper.validate_schema_proof(
                changed_proof, source_value, evidence_value, changed,
                "sha256:" + "e" * 64, "f" * 64, "app", "postgres")

    def test_archived_reference_proof_allows_only_bound_legacy_dev_source(self):
        source_value = source()
        evidence_value = evidence(version=1)
        actual = observation(version=2)
        reference = reference_for(
            source_value, evidence_value,
            structure_digest=actual["schema_structure_digest"],
            structure_origin="legacy-live-capture-match")
        proof = proof_for(
            source_value, evidence_value, actual,
            method="archived-schema-reference-v1", reference=reference,
            target_schema_digest=reference["reference_schema_digest"])

        self.assertIs(helper.validate_schema_proof(
            proof, source_value, evidence_value, actual, "sha256:" + "e" * 64,
            "f" * 64, "app", "postgres"), proof)

        for changed_source in (
            source(profile="lenzora-prod-legacy"),
            source(credential_reference="registered-secret"),
        ):
            changed_reference = reference_for(
                changed_source, evidence_value,
                structure_digest=actual["schema_structure_digest"],
                structure_origin="legacy-live-capture-match")
            changed_proof = proof_for(
                changed_source, evidence_value, actual,
                method="archived-schema-reference-v1", reference=changed_reference,
                target_schema_digest=changed_reference["reference_schema_digest"])
            with self.subTest(source=changed_source):
                with self.assertRaisesRegex(ValueError, "schema_evidence_invalid"):
                    helper.validate_schema_proof(
                        changed_proof, changed_source, evidence_value, actual,
                        "sha256:" + "e" * 64, "f" * 64, "app", "postgres")

    def test_proof_rejects_unknown_mode_and_changed_binding_before_acceptance(self):
        source_value = source()
        evidence_value = evidence()
        actual = observation(version=2)
        proof = proof_for(source_value, evidence_value, actual)

        cases = []
        unknown_mode = copy.deepcopy(proof)
        unknown_mode["method"] = "other"
        cases.append(unknown_mode)
        for field, value in (
            ("archive_digest", "sha256:" + "a" * 64),
            ("dump_digest", "sha256:" + "b" * 64),
            ("captured_schema_digest", "sha256:" + "c" * 64),
            ("native_request_id", "0" * 64),
            ("observed_schema_digest", "sha256:" + "b" * 64),
        ):
            changed = copy.deepcopy(proof)
            changed[field] = value
            cases.append(changed)

        for changed in cases:
            with self.subTest(field=changed):
                with self.assertRaisesRegex(ValueError, "schema_evidence_invalid"):
                    helper.validate_schema_proof(
                        changed, source_value, evidence_value, actual,
                        "sha256:" + "e" * 64, "f" * 64, "app", "postgres")

    def test_raw_proof_ignores_only_database_identity_for_renamed_target(self):
        source_value = source()
        evidence_value = evidence()
        actual = observation(version=2, database_identity="renamed-db")
        proof = proof_for(
            source_value, evidence_value, actual, database="restored_app")

        self.assertIs(helper.validate_schema_proof(
            proof, source_value, evidence_value, actual, "sha256:" + "e" * 64,
            "f" * 64, "restored_app", "postgres"), proof)


if __name__ == "__main__":
    unittest.main()
