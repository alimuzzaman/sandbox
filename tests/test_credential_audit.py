"""Audit-safe lifecycle records and indeterminate effect handling."""

import unittest


class TestCredentialAudit(unittest.TestCase):
    def test_records_are_bounded_and_secret_free(self):
        from sandbox.isolation.credential_audit import CredentialAuditLog

        records = []
        audit = CredentialAuditLog(sink=records.append, clock=lambda: "2026-01-01T00:00:00Z")
        result = audit.record(
            operation="request", instance_id="sb-0123456789ab",
            binding_id="bind-audit-1", actor="project:fixture", decision="allow",
            reason_code="authorized", state="ready", policy_digest="a" * 64,
            egress_digest="b" * 64, broker_digest="c" * 64,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(records[0]["operation"], "request")
        self.assertNotIn("SB_SYNTHETIC", repr(records))
        with self.assertRaises(Exception):
            audit.record(
                operation="request", instance_id="sb-0123456789ab",
                binding_id="bind-audit-1", actor="project:fixture", decision="allow",
                reason_code="authorized", state="ready", body="SB_SYNTHETIC",
            )

    def test_pre_effect_append_failure_blocks_effect(self):
        from sandbox.isolation.credential_audit import CredentialAuditLog

        calls = []
        audit = CredentialAuditLog(sink=lambda _record: (_ for _ in ()).throw(OSError("disk")))
        result = audit.execute(
            operation="request", instance_id="sb-0123456789ab", binding_id="bind-audit-1",
            actor="project:fixture", effect=lambda: calls.append("effect") or {"ok": True, "mutated": True},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["reason"]["code"], "audit_unavailable")
        self.assertEqual(calls, [])

    def test_execute_rejects_unrecognized_fields_before_effect(self):
        from sandbox.isolation.credential_audit import CredentialAuditLog

        calls = []
        audit = CredentialAuditLog()
        result = audit.execute(
            operation="request", instance_id="sb-0123456789ab", binding_id="bind-audit-1",
            actor="project:fixture", effect=lambda: calls.append("effect"), secret="nope",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"]["code"], "audit_fields_invalid")
        self.assertEqual(calls, [])

    def test_post_effect_append_failure_is_indeterminate_and_never_replayed(self):
        from sandbox.isolation.credential_audit import CredentialAuditLog

        calls = []
        count = [0]

        def sink(_record):
            count[0] += 1
            if count[0] == 2:
                raise OSError("disk")

        audit = CredentialAuditLog(sink=sink)
        result = audit.execute(
            operation="request", instance_id="sb-0123456789ab", binding_id="bind-audit-1",
            actor="project:fixture", effect=lambda: calls.append("effect") or {"ok": True, "mutated": True},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "indeterminate")
        self.assertTrue(result["no_replay"])
        self.assertEqual(calls, ["effect"])
        self.assertEqual(audit.replay(result), {"ok": False, "state": "indeterminate", "no_replay": True})


if __name__ == "__main__":
    unittest.main()
