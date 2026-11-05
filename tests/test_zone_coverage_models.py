from __future__ import annotations

import unittest

from sandbox.hosting.zone_coverage import (
    COVERAGE_SCHEMA_UNSUPPORTED,
    CoverageError,
)
from sandbox.hosting.zone_coverage.models import (
    CURRENT_SCHEMA_VERSION,
    CoverageState,
    DependentState,
    can_transition,
    eligible,
    normalize_zone,
    validate_document,
)


class TestZoneCoverageModels(unittest.TestCase):
    def test_states_defined(self):
        self.assertEqual(CoverageState.ABSENT.value, "absent")
        self.assertEqual(CoverageState.RETIRED.value, "retired")
        self.assertEqual(CoverageState.ENABLING.value, "enabling")
        self.assertEqual(CoverageState.FALLBACK_ONLY.value, "fallback_only")
        self.assertEqual(CoverageState.ACTIVE.value, "active")
        self.assertEqual(CoverageState.DISABLING.value, "disabling")
        self.assertEqual(CoverageState.DISABLED.value, "disabled")
        self.assertEqual(CoverageState.INCOMPLETE.value, "incomplete")

    def test_allowed_transitions(self):
        # absent, retired -> enabling
        self.assertTrue(can_transition("absent", "enabling", kind="enable"))
        self.assertTrue(can_transition("retired", "enabling", kind="enable"))
        # enabling -> fallback_only (stop-before-publish)
        self.assertTrue(can_transition("enabling", "fallback_only"))
        # enabling -> active (done)
        self.assertTrue(can_transition("enabling", "active"))
        # enabling -> incomplete (failure)
        self.assertTrue(can_transition("enabling", "incomplete"))
        # enabling -> enabling (resume)
        self.assertTrue(can_transition("enabling", "enabling"))
        # disabling -> disabling (resume)
        self.assertTrue(can_transition("disabling", "disabling"))
        # incomplete(enable) -> enabling (retry)
        self.assertTrue(can_transition("incomplete", "enabling", interrupted_kind="enable", kind="enable"))
        # incomplete(enable) -> fallback_only (rollback ok)
        self.assertTrue(can_transition("incomplete", "fallback_only", interrupted_kind="enable"))
        # incomplete(disable) -> disabling (retry)
        self.assertTrue(can_transition("incomplete", "disabling", interrupted_kind="disable", kind="disable"))
        # incomplete(disable) cannot retry to enable
        self.assertFalse(can_transition("incomplete", "enabling", interrupted_kind="disable", kind="enable"))
        # fallback_only -> enabling
        self.assertTrue(can_transition("fallback_only", "enabling", kind="enable"))
        # active -> disabling
        self.assertTrue(can_transition("active", "disabling", kind="disable"))
        # disabling -> disabled
        self.assertTrue(can_transition("disabling", "disabled"))
        # disabling -> incomplete
        self.assertTrue(can_transition("disabling", "incomplete"))
        # disabled -> enabling
        self.assertTrue(can_transition("disabled", "enabling", kind="enable"))
        # active -> active (repair)
        self.assertTrue(can_transition("active", "active", kind="repair"))
        # active -> incomplete (repair failure)
        self.assertTrue(can_transition("active", "incomplete", kind="repair"))
        # disabled, fallback_only -> retired
        self.assertTrue(can_transition("disabled", "retired", kind="remove_fallback"))
        self.assertTrue(can_transition("fallback_only", "retired", kind="remove_fallback"))
        # invalid transitions
        self.assertFalse(can_transition("active", "retired"))
        self.assertFalse(can_transition("absent", "active"))
        self.assertFalse(can_transition("disabled", "active"))

    def test_validate_document_valid(self):
        doc = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "zone": "asb.bd",
            "zone_id": "z123",
            "state": "active",
            "generation": 1,
            "operation": None,
            "interrupted_kind": None,
            "shared_records": [
                {"id": "r1", "type": "A", "content": "1.2.3.4", "proxied": True}
            ],
            "fallback": {
                "file": "/etc/nginx/conf.d/sandbox-coverage-asb.bd.conf",
                "digest": "abc",
                "marker": "12345678",
                "certificate": {
                    "path": "/etc/sandbox-edge/certs/coverage-asb.bd.pem",
                    "not_after": "2027-01-01T00:00:00Z",
                    "id": "cert1",
                },
            },
            "exceptions": [{"hostname": "api.asb.bd", "file": "f1", "owner": "sandbox"}],
            "dependents": {
                "proj/stg/prod": {
                    "hostname": "site.asb.bd",
                    "state": "bound",
                    "operation_id": "op1",
                    "generation": 1,
                    "updated_at": 1700000000,
                }
            },
            "dependents_revision": 1,
            "history": [{"at": 1700000000, "kind": "enable", "result": "ok", "operation_id": "op1"}],
        }
        validated = validate_document(doc)
        self.assertEqual(validated["zone"], "asb.bd")
        self.assertEqual(validated["state"], "active")

    def test_validate_document_higher_schema_refusal(self):
        doc = {
            "schema_version": 2,
            "zone": "asb.bd",
            "state": "active",
        }
        with self.assertRaises(CoverageError) as ctx:
            validate_document(doc)
        self.assertEqual(ctx.exception.code, COVERAGE_SCHEMA_UNSUPPORTED)

    def test_validate_document_invalid_structure(self):
        for bad in (
            "not a dict",
            {"schema_version": 1, "zone": "INVALID ZONE!"},
            {"schema_version": 1, "zone": "asb.bd", "state": "unknown_state"},
            {"schema_version": 1, "zone": "asb.bd", "state": "active", "generation": -1},
            {"schema_version": 1, "zone": "asb.bd", "state": "active", "dependents_revision": "not_an_int"},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_document(bad)  # type: ignore

    def test_eligible_name(self):
        records = [
            {"name": "other.asb.bd", "type": "A"},
            {"name": "exact.asb.bd", "type": "A"},
            {"name": "_acme-challenge.sub.asb.bd", "type": "TXT"},
        ]
        # First-level eligible name
        self.assertTrue(eligible("site.asb.bd", "asb.bd", records))
        self.assertTrue(eligible("site.asb.bd.", "asb.bd.", records))
        self.assertTrue(eligible("SITE.ASB.BD", "asb.bd", records))

        # Apex is not eligible (0 labels above zone)
        self.assertFalse(eligible("asb.bd", "asb.bd", records))

        # Multi-level is not eligible (>1 label above zone)
        self.assertFalse(eligible("sub.site.asb.bd", "asb.bd", records))

        # Wildcard label is not eligible
        self.assertFalse(eligible("*.asb.bd", "asb.bd", records))

        # Leading underscore label is not eligible
        self.assertFalse(eligible("_acme.asb.bd", "asb.bd", records))

        # Other zone is not eligible
        self.assertFalse(eligible("site.example.com", "asb.bd", records))

        # Exact node already exists in records
        self.assertFalse(eligible("exact.asb.bd", "asb.bd", records))

        # Subordinate node exists in records (RFC 4592 empty non-terminal)
        self.assertFalse(eligible("sub.asb.bd", "asb.bd", records))

        # Node containing name as substring but not subordinate is eligible
        self.assertTrue(eligible("site.asb.bd", "asb.bd", [{"name": "site.asb.bd.example.org", "type": "A"}]))


if __name__ == "__main__":
    unittest.main()
