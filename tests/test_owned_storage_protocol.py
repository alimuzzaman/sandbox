"""Unit tests for owned storage protocol envelopes and codecs."""

import json
import unittest

from sandbox.owned_storage.protocol import (
    MAX_CONTROL_FRAME_BYTES,
    PROTOCOL_VERSION,
    StorageProtocolError,
    compute_request_digest,
    decode_request,
    encode_failure_response,
    encode_request,
    encode_success_response,
)


class TestOwnedStorageProtocol(unittest.TestCase):
    def setUp(self):
        self.valid_request = {
            "protocol": PROTOCOL_VERSION,
            "operation": "publish",
            "request_id": "req_123",
            "request_digest": "sha256:abc",
            "remote_identity": "rem_remote",
            "project_identity": "proj_demo",
            "authorization": {
                "authorization_id": "auth_123",
                "controller_epoch": "epoch_1",
                "sequence": 42,
                "caller_identity_digest": "sha256:caller",
                "application_policy_digest": "sha256:policy",
                "policy_generation": 7,
                "promotion_id": "prom_123",
                "authority_binding_id": "bind_123",
                "binding_generation": 3,
                "expires_at": "2026-09-04T12:00:00Z",
            },
            "qualification": None,
            "deadline_unix_ms": 1788177600000,
            "input": {
                "relationship_id": "rel_123",
                "workspace_id": "ws_123",
                "generation_id": "gen_123",
                "manifest_digest": "sha256:manifest",
                "archive_manifest_digest": "sha256:archive",
                "file_count": 12,
                "byte_count": 12345,
                "stream_bytes": 8192,
            },
        }

    def test_protocol_constants(self):
        self.assertEqual(PROTOCOL_VERSION, "owned-storage-authority-v1")
        self.assertEqual(MAX_CONTROL_FRAME_BYTES, 65536)

    def test_encode_and_decode_valid_request(self):
        encoded = encode_request(self.valid_request)
        self.assertIsInstance(encoded, bytes)
        self.assertTrue(len(encoded) <= MAX_CONTROL_FRAME_BYTES)

        decoded = decode_request(encoded)
        self.assertEqual(decoded["protocol"], PROTOCOL_VERSION)
        self.assertEqual(decoded["operation"], "publish")
        self.assertEqual(decoded["request_id"], "req_123")
        self.assertEqual(decoded["input"]["file_count"], 12)

    def test_reject_oversized_control_frame(self):
        oversized = b"a" * (MAX_CONTROL_FRAME_BYTES + 1)
        with self.assertRaises(StorageProtocolError) as ctx:
            decode_request(oversized)
        self.assertIn("oversized", str(ctx.exception).lower())

    def test_reject_unknown_field_in_request(self):
        req = dict(self.valid_request)
        req["unexpected_field"] = "bad"
        raw = json.dumps(req).encode("utf-8")
        with self.assertRaises(StorageProtocolError) as ctx:
            decode_request(raw)
        self.assertIn("unknown field", str(ctx.exception).lower())

    def test_reject_float_value(self):
        req = dict(self.valid_request)
        req["input"] = dict(req["input"])
        req["input"]["byte_count"] = 12.5
        raw = json.dumps(req).encode("utf-8")
        with self.assertRaises(StorageProtocolError) as ctx:
            decode_request(raw)
        self.assertIn("float", str(ctx.exception).lower())

    def test_compute_request_digest_is_canonical_and_excludes_transport(self):
        digest1 = compute_request_digest(self.valid_request)
        self.assertTrue(digest1.startswith("sha256:"))

        # Modifying transport-only cursor/deadline doesn't alter canonical request digest
        req2 = dict(self.valid_request)
        req2["deadline_unix_ms"] = 9999999999999
        digest2 = compute_request_digest(req2)
        self.assertEqual(digest1, digest2)

        # Modifying input DOES alter digest
        req3 = dict(self.valid_request)
        req3["input"] = dict(req3["input"])
        req3["input"]["file_count"] = 13
        digest3 = compute_request_digest(req3)
        self.assertNotEqual(digest1, digest3)

    def test_encode_success_response(self):
        resp = encode_success_response(
            operation="publish",
            operation_id="op_123",
            request_id="req_123",
            status="accepted",
            obj={
                "id": "obj_123",
                "kind": "sync_generation",
                "lifecycle": "accepted",
                "evidence_digest": "sha256:evidence",
                "known_bytes": 12345,
            },
            replay=False,
            complete=True,
            reason_code=None,
            observed_at="2026-09-04T00:00:00Z",
        )
        data = json.loads(resp.decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["protocol"], PROTOCOL_VERSION)
        self.assertEqual(data["operation"], "publish")
        self.assertEqual(data["status"], "accepted")
        self.assertIsNone(data["reason_code"])

    def test_encode_failure_response(self):
        resp = encode_failure_response(
            operation="cleanup",
            operation_id="op_123",
            request_id="req_123",
            status="refused",
            code="object_not_previewed",
            message="Object is not present in preview candidates",
            retryable=False,
            object_id="obj_123",
            complete=True,
        )
        data = json.loads(resp.decode("utf-8"))
        self.assertFalse(data["ok"])
        self.assertEqual(data["protocol"], PROTOCOL_VERSION)
        self.assertEqual(data["status"], "refused")
        self.assertEqual(data["code"], "object_not_previewed")
        self.assertFalse(data["retryable"])


if __name__ == "__main__":
    unittest.main()
