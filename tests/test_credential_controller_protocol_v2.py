import dataclasses
import hashlib
import json
import pathlib
import struct
import unittest

from sandbox.isolation.credential_controller_protocol_v2 import (
    AuthorizationIdentity,
    AuthorizationRegistry,
    DirectionalSequence,
    LeaseSequence,
    MAX_AUTHORIZATION_TOMBSTONES,
    MAX_SEQUENCE,
    PROTOCOL,
    ProtocolV2Error,
    REVIEWED_REGISTRY,
    REVIEWED_REGISTRY_DIGEST,
    TemporalObservation,
    authorization_digest,
    canonical_json,
    decode_controller_frame,
    decode_lease_ack,
    decode_lease_frame,
    digest_document,
    encode_controller_frame,
    encode_lease_ack,
    encode_lease_frame,
    registry_digest,
    validate_digest,
)


NOW = 1_800_000_000_000
MACHINE = "machine-01234567"
BROKER_EPOCH = "0123456789abcdef0123456789abcdef"
CONTROLLER_EPOCH = "fedcba9876543210fedcba9876543210"
DIGEST = "01" * 32
DIGEST_2 = "02" * 32
OPERATION = "operation-012345"
BINDING = "binding-01234567"
DECISION = "decision-0123456"
EVIDENCE = "evidence-0123456"
LEASE = "lease-0123456789"
AUDIT = "audit-0123456789"
PHASE = "audit-abcdefghij"
COMMIT = "commit-012345678"


def authorization_values():
    return {
        "protocol": PROTOCOL, "machine_id": MACHINE,
        "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
        "operation_id": OPERATION, "request_digest": DIGEST,
        "binding_id": BINDING, "binding_version": 1,
        "auth_form": "authorization_bearer", "policy_digest": DIGEST,
        "egress_digest": DIGEST, "broker_digest": DIGEST,
        "proof_digest": DIGEST, "effective_isolation_digest": DIGEST,
        "evidence_id": EVIDENCE, "binding_expires_at_unix_ms": NOW + 20_000,
        "authorization_expires_at_unix_ms": NOW + 4_000,
        "decision_id": DECISION,
    }


def message(message_type):
    common = {
        "protocol": PROTOCOL, "type": message_type, "machine_id": MACHINE,
        "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
        "sequence": 2,
    }
    digest_fields = {
        "policy_digest": DIGEST, "egress_digest": DIGEST,
        "broker_digest": DIGEST, "proof_digest": DIGEST,
        "effective_isolation_digest": DIGEST, "evidence_id": EVIDENCE,
    }
    if message_type == "HELLO_V2":
        result = {**common, **digest_fields, "sequence": 1, "broker_pid": 123,
                  "broker_start_ticks": 456, "broker_executable_digest": DIGEST,
                  "broker_unit_digest": DIGEST, "broker_config_digest": DIGEST}
        del result["controller_epoch"]
        return result, "broker_to_controller", {}
    if message_type == "HELLO_ACK_V2":
        return ({**common, "sequence": 1, "reply_to": 1, "accepted": True,
                 "controller_pid": 234, "controller_start_ticks": 567,
                 "controller_executable_digest": DIGEST,
                 "controller_unit_digest": DIGEST,
                 "controller_config_digest": DIGEST, "handshake_digest": DIGEST},
                "controller_to_broker", {})
    if message_type == "ACTIVATE_V2":
        result = {**common, **digest_fields, "activation_digest": DIGEST,
                  "activation_expires_at_unix_ms": NOW + 20_000}
        document = {field: result["sequence" if field == "request_sequence" else field]
                    for field in REVIEWED_REGISTRY["digest_documents"]["activation_digest"]}
        result["activation_digest"] = digest_document("activation_digest", document)
        return result, "controller_to_broker", {}
    if message_type == "ACTIVATE_ACK_V2":
        result = {**common, "reply_to": 2, "activation_digest": DIGEST,
                  "admission_state": "open", "activate_decision": "activated",
                  "active_operation_count": 0, "acknowledged_at_unix_ms": NOW + 10,
                  "activation_expires_at_unix_ms": NOW + 20_000,
                  "reason_code": "activated"}
        context = {"request_receipt_unix_ms": NOW,
                   "activation_expires_at_unix_ms": NOW + 20_000}
        return result, "broker_to_controller", context
    if message_type == "QUIESCE_V2":
        result = {**common, "reason_code": "operator_stop",
                  "drain_deadline_unix_ms": NOW + 4_000,
                  "quiesce_digest": DIGEST}
        document = {field: result["sequence" if field == "request_sequence" else field]
                    for field in REVIEWED_REGISTRY["digest_documents"]["quiesce_digest"]}
        result["quiesce_digest"] = digest_document("quiesce_digest", document)
        return result, "controller_to_broker", {}
    if message_type == "QUIESCE_ACK_V2":
        result = {**common, "reply_to": 2, "quiesce_digest": DIGEST,
                  "admission_state": "closed", "drain_status": "drained",
                  "active_operation_count": 0, "acknowledged_at_unix_ms": NOW + 20,
                  "drain_deadline_unix_ms": NOW + 4_000, "reason_code": "drained"}
        return result, "broker_to_controller", {
            "request_receipt_unix_ms": NOW, "drain_deadline_unix_ms": NOW + 4_000}
    if message_type == "CLAIM_NEXT_V2":
        return ({**common, "wait_deadline_unix_ms": NOW + 900},
                "controller_to_broker", {})
    if message_type == "CLAIMED_V2_CLAIMED":
        result = {**common, "type": "CLAIMED_V2", "reply_to": 2,
                  "claim_state": "claimed", "operation_id": OPERATION,
                  "request_digest": DIGEST, "binding_id": BINDING,
                  "binding_version": 1, "scheme": "https", "host": "api.example.com",
                  "port": 443, "method": "POST", "path": "/v1/items",
                  "content_type": "application/json", "header_bytes": 12,
                  "body_bytes": 34, "request_deadline_unix_ms": NOW + 20_000,
                  "correlation_id": "corr-1"}
        return result, "broker_to_controller", {
            "original_guest_request_receipt_unix_ms": NOW - 1_000}
    if message_type == "CLAIMED_V2_NO_PENDING":
        return ({**common, "type": "CLAIMED_V2", "reply_to": 2,
                 "claim_state": "no_pending", "retry_after_ms": 50},
                "broker_to_controller", {})
    if message_type == "AUTHORIZE_V2":
        values = authorization_values()
        result = {**common, **{key: value for key, value in values.items()
                              if key not in {"protocol", "machine_id", "broker_epoch", "controller_epoch"}}}
        result["authorization_digest"] = authorization_digest(values)
        return result, "controller_to_broker", {
            "activation_expires_at_unix_ms": NOW + 10_000,
            "request_deadline_unix_ms": NOW + 8_000}
    if message_type == "AUTHORIZED_V2":
        return ({**common, "reply_to": 2, "operation_id": OPERATION,
                 "request_digest": DIGEST, "binding_id": BINDING,
                 "binding_version": 1, "decision_id": DECISION,
                 "authorization_digest": DIGEST,
                 "authorization_expires_at_unix_ms": NOW + 4_000},
                "broker_to_controller", {
                    "authorization_expires_at_unix_ms": NOW + 4_000})
    if message_type == "REFUSE_V2":
        return ({**common, "operation_id": OPERATION, "request_digest": DIGEST,
                 "binding_id": BINDING, "binding_version": 1,
                 "decision_id": DECISION, "reason_code": "binding_missing"},
                "controller_to_broker", {})
    if message_type == "AUDIT_PRE_V2":
        result = {**common, "operation_id": OPERATION, "binding_id": BINDING,
                  "binding_version": 1, "decision_id": DECISION,
                  "audit_root_id": AUDIT, "phase_id": PHASE,
                  "audit_fingerprint": DIGEST, "event_code": "credential_effect_pre"}
        document = {field: result[field] for field in REVIEWED_REGISTRY["digest_documents"]["audit_pre_fingerprint"]}
        result["audit_fingerprint"] = digest_document("audit_pre_fingerprint", document)
        return result, "broker_to_controller", {}
    if message_type == "AUDIT_POST_V2":
        result = {**common, "operation_id": OPERATION, "binding_id": BINDING,
                  "binding_version": 1, "decision_id": DECISION,
                  "audit_root_id": AUDIT, "phase_id": PHASE,
                  "audit_fingerprint": DIGEST, "pre_commit_id": COMMIT,
                  "outcome_class": "completed", "effect_certainty": "completed",
                  "reason_code": "upstream_completed"}
        document = {field: result[field] for field in REVIEWED_REGISTRY["digest_documents"]["audit_post_fingerprint"]}
        result["audit_fingerprint"] = digest_document("audit_post_fingerprint", document)
        return result, "broker_to_controller", {}
    if message_type == "AUDIT_ACK_V2":
        return ({**common, "reply_to": 2, "audit_root_id": AUDIT,
                 "phase": "pre", "phase_id": PHASE,
                 "audit_fingerprint": DIGEST, "commit_id": COMMIT,
                 "disposition": "committed"}, "controller_to_broker", {})
    raise AssertionError(message_type)


def lease_values():
    return {
        "machine_id": MACHINE, "broker_epoch": BROKER_EPOCH,
        "controller_epoch": CONTROLLER_EPOCH, "operation_id": OPERATION,
        "request_digest": DIGEST, "binding_id": BINDING, "binding_version": 1,
        "auth_form": "authorization_bearer", "policy_digest": DIGEST,
        "egress_digest": DIGEST, "broker_digest": DIGEST,
        "proof_digest": DIGEST, "effective_isolation_digest": DIGEST,
        "evidence_id": EVIDENCE, "decision_id": DECISION,
        "authorization_digest": DIGEST_2,
        "authorization_expires_at_unix_ms": NOW + 4_000,
        "lease_id": LEASE, "lease_sequence": 1,
        "lease_expires_at_unix_ms": NOW + 3_000, "descriptor_size": 64,
    }


def lease_caps():
    return {"authorization_expires_at_unix_ms": NOW + 4_000,
            "binding_expires_at_unix_ms": NOW + 20_000,
            "activation_expires_at_unix_ms": NOW + 10_000,
            "request_deadline_unix_ms": NOW + 8_000}


def ack_values():
    return {"type": "LEASE_ACK_V2", "machine_id": MACHINE,
            "broker_epoch": BROKER_EPOCH, "controller_epoch": CONTROLLER_EPOCH,
            "lease_id": LEASE, "lease_sequence": 1,
            "authorization_digest": DIGEST_2, "audit_root_id": AUDIT,
            "post_phase_id": PHASE, "post_commit_id": COMMIT,
            "outcome_class": "completed", "effect_certainty": "completed",
            "reason_code": "upstream_completed"}


class RegistryAndJsonTests(unittest.TestCase):
    def test_frozen_registry_has_reviewed_digest_without_markdown_parse(self):
        self.assertEqual(registry_digest(), REVIEWED_REGISTRY_DIGEST)
        self.assertEqual(len(REVIEWED_REGISTRY["messages"]), 16)
        with self.assertRaises(TypeError):
            REVIEWED_REGISTRY["bounds"]["lease_frame_bytes"] = 1

    def test_all_json_variants_round_trip_and_are_canonical(self):
        variants = [name for name in REVIEWED_REGISTRY["messages"]
                    if name != "LEASE_ACK_V2"]
        for variant in variants:
            value, direction, context = message(variant)
            with self.subTest(variant=variant):
                encoded = encode_controller_frame(value, direction=direction,
                                                  now_ms=NOW,
                                                  temporal_context=context)
                self.assertEqual(encoded, json.dumps(value, sort_keys=True,
                    separators=(",", ":")).encode())
                self.assertEqual(decode_controller_frame(encoded, direction=direction,
                    now_ms=NOW, temporal_context=context), value)

    def test_hello_golden_vector_and_controller_epoch_exception(self):
        value, direction, context = message("HELLO_V2")
        encoded = encode_controller_frame(value, direction=direction, now_ms=NOW,
                                          temporal_context=context)
        self.assertEqual(hashlib.sha256(encoded).hexdigest(),
                         "97bfd7070d7592cabfe80c3f92412e9b6ff08da91fc67930c10f9e7d18046044")
        value["controller_epoch"] = CONTROLLER_EPOCH
        with self.assertRaisesRegex(ProtocolV2Error, "message_keys_invalid"):
            encode_controller_frame(value, direction=direction, now_ms=NOW)

    def test_duplicate_float_bool_noncanonical_utf8_oversize_and_shape_refused(self):
        value, direction, _ = message("HELLO_V2")
        canonical = canonical_json(value)
        cases = [
            canonical.replace(b'{', b'{"type":"HELLO_V2",', 1),
            canonical.replace(b'"sequence":1', b'"sequence":1.0'),
            canonical.replace(b'"sequence":1', b'"sequence":true'),
            b" " + canonical,
            b"\xff",
            b"[]",
            b"{" + (b'"x":"y",' * 9000) + b'"z":1}',
        ]
        for packet in cases:
            with self.subTest(packet=packet[:20]):
                with self.assertRaises(ProtocolV2Error):
                    decode_controller_frame(packet, direction=direction, now_ms=NOW)

    def test_v1_unknown_mixed_and_binary_json_refused(self):
        value, direction, _ = message("HELLO_V2")
        for wire_type, protocol in (("HELLO_V1", PROTOCOL),
                                    ("HELLO_V3", PROTOCOL),
                                    ("UNKNOWN", PROTOCOL),
                                    ("HELLO_V2", "credential-broker-controller-v1"),
                                    ("LEASE_ACK_V2", PROTOCOL)):
            mutated = dict(value, type=wire_type, protocol=protocol)
            with self.subTest(wire_type=wire_type, protocol=protocol):
                with self.assertRaises(ProtocolV2Error):
                    decode_controller_frame(canonical_json(mutated),
                                            direction=direction, now_ms=NOW)

    def test_exact_keys_directions_types_bounds_and_enums_refused(self):
        value, direction, context = message("CLAIMED_V2_CLAIMED")
        mutations = []
        missing = dict(value); missing.pop("path"); mutations.append(missing)
        mutations.append(dict(value, unknown=True))
        mutations.append(dict(value, binding_version=True))
        mutations.append(dict(value, port=443.0))
        mutations.append(dict(value, auth_form="bearer"))
        mutations.append(dict(value, path="/a/../b"))
        mutations.append(dict(value, content_type="Application/JSON"))
        mutations.append(dict(value, host="api.example.com."))
        for mutated in mutations:
            with self.subTest(keys=set(mutated) - set(value)):
                with self.assertRaises(ProtocolV2Error):
                    encode_controller_frame(mutated, direction=direction,
                                            now_ms=NOW, temporal_context=context)
        with self.assertRaisesRegex(ProtocolV2Error, "direction_invalid"):
            encode_controller_frame(value, direction="controller_to_broker",
                                    now_ms=NOW, temporal_context=context)

    def test_temporal_boundaries_use_injected_time(self):
        value, direction, context = message("AUTHORIZE_V2")
        for expiry in (NOW, NOW + 5_001):
            mutated = dict(value, authorization_expires_at_unix_ms=expiry)
            with self.subTest(expiry=expiry):
                with self.assertRaisesRegex(ProtocolV2Error, "temporal_invalid"):
                    encode_controller_frame(mutated, direction=direction,
                                            now_ms=NOW, temporal_context=context)
        at_bound = dict(value, authorization_expires_at_unix_ms=NOW + 5_000)
        document = {field: at_bound[field]
                    for field in REVIEWED_REGISTRY["digest_documents"]["authorization_digest"]}
        at_bound["authorization_digest"] = authorization_digest(document)
        encode_controller_frame(at_bound, direction=direction, now_ms=NOW,
                                temporal_context=context)
        with self.assertRaisesRegex(ProtocolV2Error, "clock_invalid"):
            encode_controller_frame(value, direction=direction, now_ms=True,
                                    temporal_context=context)

    def test_authorize_requires_and_obeys_every_mandatory_deadline_cap(self):
        value, direction, context = message("AUTHORIZE_V2")
        for missing in ("activation_expires_at_unix_ms", "request_deadline_unix_ms"):
            incomplete = dict(context); incomplete.pop(missing)
            with self.subTest(missing=missing):
                with self.assertRaisesRegex(ProtocolV2Error, "temporal_context_invalid"):
                    encode_controller_frame(value, direction=direction, now_ms=NOW,
                                            temporal_context=incomplete)
        for cap in ("activation_expires_at_unix_ms", "request_deadline_unix_ms"):
            bounded = dict(context, **{cap: value["authorization_expires_at_unix_ms"]})
            encode_controller_frame(value, direction=direction, now_ms=NOW,
                                    temporal_context=bounded)
            refused = dict(context, **{cap: value["authorization_expires_at_unix_ms"] - 1})
            with self.subTest(cap=cap):
                with self.assertRaisesRegex(ProtocolV2Error, "temporal_invalid"):
                    encode_controller_frame(value, direction=direction, now_ms=NOW,
                                            temporal_context=refused)

    def test_monotonic_observer_accepts_exact_skew_and_refuses_rollback_uncertainty(self):
        observer = TemporalObservation()
        self.assertEqual(observer.observe(NOW, uncertainty_ms=250), NOW + 250)
        self.assertEqual(observer.observe(NOW, uncertainty_ms=0), NOW + 250)
        self.assertEqual(observer.observe(NOW + 100, uncertainty_ms=100), NOW + 250)
        self.assertEqual(observer.observe(NOW + 250, uncertainty_ms=0), NOW + 250)
        with self.assertRaisesRegex(ProtocolV2Error, "clock_rollback"):
            observer.observe(NOW - 1)
        with self.assertRaisesRegex(ProtocolV2Error, "clock_closed"):
            observer.observe(NOW)
        with self.assertRaisesRegex(ProtocolV2Error, "clock_uncertain"):
            TemporalObservation().observe(NOW, uncertainty_ms=251)
        value, direction, context = message("AUTHORIZE_V2")
        wire_observer = TemporalObservation()
        encode_controller_frame(value, direction=direction, now_ms=NOW,
                                temporal_context=context, observation=wire_observer)
        with self.assertRaisesRegex(ProtocolV2Error, "clock_rollback"):
            encode_controller_frame(value, direction=direction, now_ms=NOW - 251,
                                    temporal_context=context, observation=wire_observer)

    def test_digest_documents_are_exact_and_have_golden_vector(self):
        values = authorization_values()
        self.assertEqual(authorization_digest(values),
                         "321623887536a81bc1aa70958827db41f083e2beef77075ca8d833207dcd2cb4")
        for mutated in ({**values, "extra": 1},
                        {key: item for key, item in values.items() if key != "decision_id"}):
            with self.assertRaisesRegex(ProtocolV2Error, "digest_document_invalid"):
                authorization_digest(mutated)
        activation = {key: ({"type": "ACTIVATE_V2", "request_sequence": 2}.get(key,
                      message("ACTIVATE_V2")[0].get(key)))
                      for key in REVIEWED_REGISTRY["digest_documents"]["activation_digest"]}
        self.assertEqual(len(digest_document("activation_digest", activation)), 64)
        validate_digest("authorization_digest", values, authorization_digest(values))
        with self.assertRaisesRegex(ProtocolV2Error, "digest_mismatch"):
            validate_digest("authorization_digest", values, DIGEST)

    def test_self_contained_wire_digest_mutations_are_refused(self):
        for variant in ("ACTIVATE_V2", "QUIESCE_V2", "AUTHORIZE_V2",
                        "AUDIT_PRE_V2", "AUDIT_POST_V2"):
            value, direction, context = message(variant)
            field = "audit_fingerprint" if variant.startswith("AUDIT_") else {
                "ACTIVATE_V2": "activation_digest", "QUIESCE_V2": "quiesce_digest",
                "AUTHORIZE_V2": "authorization_digest"}[variant]
            mutated = dict(value, **{field: DIGEST_2})
            with self.subTest(variant=variant):
                with self.assertRaisesRegex(ProtocolV2Error, "digest_mismatch"):
                    encode_controller_frame(mutated, direction=direction, now_ms=NOW,
                                            temporal_context=context)

    def test_error_code_and_repr_are_bounded_and_secret_free(self):
        error = ProtocolV2Error("bad-code contains secret material")
        self.assertEqual(error.as_dict(), {"code": "protocol_refused"})
        self.assertNotIn("secret material", repr(error))

    def test_exported_digest_and_temporal_arguments_are_validated_before_lookup(self):
        values = authorization_values()
        expected = authorization_digest(values)
        invalid_names = (None, True, 1, [], {}, "", "x" * 1000, "unknown")
        for name in invalid_names:
            with self.subTest(name_type=type(name).__name__):
                with self.assertRaises(ProtocolV2Error):
                    digest_document(name, values)
                with self.assertRaises(ProtocolV2Error):
                    validate_digest(name, values, expected)
        malformed = (None, True, 1, 1.5, "", [], ["x"])
        for bad_values in malformed:
            with self.subTest(values_type=type(bad_values).__name__):
                with self.assertRaises(ProtocolV2Error):
                    authorization_digest(bad_values)
        for bad_expected in (None, True, 1, [], {}, "", "g" * 64):
            with self.subTest(expected_type=type(bad_expected).__name__):
                with self.assertRaises(ProtocolV2Error):
                    validate_digest("authorization_digest", values, bad_expected)

        hello, direction, _ = message("HELLO_V2")
        for context in (True, 1, 1.5, "", [], ["x"]):
            with self.subTest(context_type=type(context).__name__):
                with self.assertRaises(ProtocolV2Error):
                    encode_controller_frame(hello, direction=direction, now_ms=NOW,
                                            temporal_context=context)
                with self.assertRaises(ProtocolV2Error):
                    decode_controller_frame(canonical_json(hello),
                                            direction=direction, now_ms=NOW,
                                            temporal_context=context)


class BinaryCodecTests(unittest.TestCase):
    def test_lease_exact_layout_round_trip_and_golden_vector(self):
        value = lease_values()
        packet = encode_lease_frame(value, now_ms=NOW, deadline_caps=lease_caps())
        self.assertEqual(len(packet), 732)
        self.assertEqual(packet[:16], b"SBCLV2\0\0\x00\x02\x02\xdc\x00\x00\x02\xdc")
        self.assertEqual(packet[280], 1)
        self.assertEqual(packet[281:288], bytes(7))
        self.assertEqual(packet[700:], hashlib.sha256(packet[:700]).digest())
        self.assertEqual(hashlib.sha256(packet).hexdigest(),
                         "c1d90fb0394098505164eee6533d6b4a7e6b39a632a73f46d341308575fdd24a")
        self.assertEqual(decode_lease_frame(packet, now_ms=NOW,
                                           deadline_caps=lease_caps()), value)

    def test_lease_rejects_every_region_mutation_and_lengths(self):
        packet = encode_lease_frame(lease_values(), now_ms=NOW,
                                    deadline_caps=lease_caps())
        for offset in (0, 8, 10, 12, 16, 80, 96, 112, 176, 208, 272, 280,
                       281, 288, 320, 352, 384, 416, 448, 512, 576, 608, 616,
                       680, 688, 696, 700, 731):
            mutated = bytearray(packet); mutated[offset] ^= 1
            with self.subTest(offset=offset):
                with self.assertRaises(ProtocolV2Error):
                    decode_lease_frame(bytes(mutated), now_ms=NOW,
                                       deadline_caps=lease_caps())
        for mutated in (packet[:-1], packet + b"\0", b""):
            with self.assertRaisesRegex(ProtocolV2Error, "lease_size_invalid"):
                decode_lease_frame(mutated, now_ms=NOW, deadline_caps=lease_caps())

    def test_lease_rejects_tags_padding_numeric_and_deadline_boundaries(self):
        base = lease_values()
        mutations = [dict(base, auth_form="bearer"), dict(base, binding_version=True),
                     dict(base, lease_sequence=0), dict(base, descriptor_size=0),
                     dict(base, descriptor_size=16385),
                     dict(base, lease_expires_at_unix_ms=NOW),
                     dict(base, lease_expires_at_unix_ms=NOW + 5_001)]
        for value in mutations:
            with self.subTest(value=value):
                with self.assertRaises(ProtocolV2Error):
                    encode_lease_frame(value, now_ms=NOW, deadline_caps=lease_caps())

    def test_ack_exact_layout_round_trip_and_golden_vector(self):
        value = ack_values()
        packet = encode_lease_ack(value)
        self.assertEqual(len(packet), 444)
        self.assertEqual(packet[:12], b"SBACK2\0\0\x00\x02\x01\xbc")
        self.assertEqual(packet[404:407], bytes((1, 2, 1)))
        self.assertEqual(packet[407:412], bytes(5))
        self.assertEqual(packet[412:], hashlib.sha256(packet[:412]).digest())
        self.assertEqual(hashlib.sha256(packet).hexdigest(),
                         "ba0f4e936bb60dab41a4c244dab7e281bc97ecd9d8266413a76794234c52ea72")
        self.assertEqual(decode_lease_ack(packet), value)

    def test_ack_rejects_mutations_lengths_tags_padding_and_post_pairs(self):
        packet = encode_lease_ack(ack_values())
        for offset in (0, 8, 10, 12, 76, 92, 108, 172, 180, 212, 276,
                       340, 404, 405, 406, 407, 412, 443):
            mutated = bytearray(packet); mutated[offset] ^= 1
            with self.subTest(offset=offset):
                with self.assertRaises(ProtocolV2Error):
                    decode_lease_ack(bytes(mutated))
        for mutated in (packet[:-1], packet + b"\0"):
            with self.assertRaisesRegex(ProtocolV2Error, "lease_ack_size_invalid"):
                decode_lease_ack(mutated)
        invalid = dict(ack_values(), outcome_class="completed", effect_certainty="none")
        with self.assertRaisesRegex(ProtocolV2Error, "lease_ack_invalid"):
            encode_lease_ack(invalid)

    def test_property_style_malformed_inputs_never_escape_protocol_error(self):
        malformed = (None, True, False, -1, 0, 1, 1.5, "", "x" * 20000,
                     [], ["x"], {}, {"x": []})
        hello, direction, _ = message("HELLO_V2")
        for item in malformed:
            with self.subTest(kind=type(item).__name__):
                calls = (
                    lambda item=item: encode_controller_frame(
                        dict(hello, machine_id=item), direction=direction, now_ms=NOW),
                    lambda item=item: encode_lease_frame(
                        dict(lease_values(), auth_form=item), now_ms=NOW,
                        deadline_caps=lease_caps()),
                    lambda item=item: encode_lease_ack(
                        dict(ack_values(), outcome_class=item)),
                )
                for call in calls:
                    with self.assertRaises(ProtocolV2Error):
                        call()
                if not isinstance(item, dict):
                    with self.assertRaises(ProtocolV2Error):
                        canonical_json(item)
        for bad_caps in malformed:
            with self.subTest(caps=type(bad_caps).__name__):
                with self.assertRaises(ProtocolV2Error):
                    encode_lease_frame(lease_values(), now_ms=NOW,
                                       deadline_caps=bad_caps)
        for packet in malformed:
            with self.subTest(packet_type=type(packet).__name__):
                for decoder in (
                        lambda packet=packet: decode_controller_frame(
                            packet, direction="broker_to_controller", now_ms=NOW),
                        lambda packet=packet: decode_lease_frame(
                            packet, now_ms=NOW, deadline_caps=lease_caps()),
                        lambda packet=packet: decode_lease_ack(packet)):
                    with self.assertRaises(ProtocolV2Error):
                        decoder()

    def test_every_json_lease_and_ack_field_rejects_nested_value_with_protocol_error(self):
        for variant, schema in REVIEWED_REGISTRY["messages"].items():
            if variant == "LEASE_ACK_V2":
                continue
            value, direction, context = message(variant)
            for field in schema["required"]:
                with self.subTest(variant=variant, field=field):
                    with self.assertRaises(ProtocolV2Error):
                        encode_controller_frame(dict(value, **{field: []}),
                                                direction=direction, now_ms=NOW,
                                                temporal_context=context)
        for field in lease_values():
            with self.subTest(binary="lease", field=field):
                with self.assertRaises(ProtocolV2Error):
                    encode_lease_frame(dict(lease_values(), **{field: []}),
                                       now_ms=NOW, deadline_caps=lease_caps())
        for field in ack_values():
            with self.subTest(binary="ack", field=field):
                with self.assertRaises(ProtocolV2Error):
                    encode_lease_ack(dict(ack_values(), **{field: []}))


class ReplayAndAuthorizationStateTests(unittest.TestCase):
    def registry(self, **changes):
        values = {"machine_id": MACHINE, "broker_epoch": BROKER_EPOCH,
                  "controller_epoch": CONTROLLER_EPOCH, "owner": "controller-1"}
        values.update(changes)
        return AuthorizationRegistry(**values)

    def pinned(self, **changes):
        values = {"machine_id": MACHINE, "broker_epoch": BROKER_EPOCH,
                  "controller_epoch": CONTROLLER_EPOCH, "owner": "controller-1"}
        values.update(changes)
        return values

    def test_directional_sequence_is_independent_and_terminal_on_failure(self):
        state = DirectionalSequence()
        state.accept("broker_to_controller", 1)
        state.accept("controller_to_broker", 1)
        state.accept("broker_to_controller", 2)
        with self.assertRaisesRegex(ProtocolV2Error, "sequence_invalid"):
            state.accept("controller_to_broker", 3)
        self.assertTrue(state.closed)
        with self.assertRaisesRegex(ProtocolV2Error, "sequence_invalid"):
            state.accept("controller_to_broker", 2)

    def test_sequence_refuses_bool_zero_duplicate_and_exhaustion(self):
        for sequence in (True, 0, -1, MAX_SEQUENCE + 1):
            with self.subTest(sequence=sequence):
                with self.assertRaises(ProtocolV2Error):
                    DirectionalSequence().accept("broker_to_controller", sequence)
        state = DirectionalSequence()
        state._next["broker_to_controller"] = MAX_SEQUENCE
        state.accept("broker_to_controller", MAX_SEQUENCE)
        self.assertTrue(state.closed)

    def test_audit_transport_retry_allows_only_one_lost_sequence(self):
        state = DirectionalSequence()
        state.accept("broker_to_controller", 1)
        state.accept_audit_retry("broker_to_controller", 3)
        state.accept("broker_to_controller", 4)
        with self.assertRaisesRegex(ProtocolV2Error, "sequence_invalid"):
            state.accept_audit_retry("controller_to_broker", 3)
        self.assertTrue(state.closed)

    def test_lease_sequence_is_epoch_pair_scoped(self):
        state = LeaseSequence(CONTROLLER_EPOCH, BROKER_EPOCH)
        state.accept(CONTROLLER_EPOCH, BROKER_EPOCH, 1)
        with self.assertRaisesRegex(ProtocolV2Error, "lease_sequence_invalid"):
            state.accept("0" * 32, BROKER_EPOCH, 2)
        fresh = LeaseSequence("0" * 32, BROKER_EPOCH)
        fresh.accept("0" * 32, BROKER_EPOCH, 1)

    def identity(self, index=0, **changes):
        value = AuthorizationIdentity(
            owner="controller-1", machine_id=MACHINE,
            broker_epoch=BROKER_EPOCH, controller_epoch=CONTROLLER_EPOCH,
            operation_id=f"operation-{index:06d}", request_digest=DIGEST,
            binding_id=BINDING, binding_version=1,
            decision_id=f"decision-{index:07d}", authorization_digest=DIGEST_2,
            expires_at_unix_ms=NOW + 4_000,
            binding_expires_at_unix_ms=NOW + 20_000,
            activation_expires_at_unix_ms=NOW + 10_000,
            request_deadline_unix_ms=NOW + 8_000)
        return dataclasses.replace(value, **changes)

    def test_registry_exact_match_one_use_and_repr(self):
        registry = self.registry()
        item = self.identity()
        registry.insert(item, now_ms=NOW)
        mismatch = dataclasses.replace(item, request_digest=DIGEST_2)
        with self.assertRaisesRegex(ProtocolV2Error, "authorization_mismatch_consumed"):
            registry.match_and_consume(mismatch, now_ms=NOW)
        with self.assertRaisesRegex(ProtocolV2Error, "authorization_mismatch"):
            registry.match_and_consume(item, now_ms=NOW)
        self.assertEqual(registry.tombstone_count, 1)
        exact = self.identity(1)
        registry.insert(exact, now_ms=NOW)
        self.assertEqual(registry.match_and_consume(exact, now_ms=NOW), exact)
        self.assertEqual(repr(registry), "AuthorizationRegistry(active=0, closed=False)")
        self.assertNotIn(DIGEST, repr(item))

    def test_registry_pins_machine_epoch_pair_and_authenticated_owner(self):
        registry = self.registry()
        item = self.identity()
        registry.insert(item, now_ms=NOW)
        mixed = (
            dataclasses.replace(item, machine_id="machine-76543210"),
            dataclasses.replace(item, broker_epoch="1" * 32),
            dataclasses.replace(item, controller_epoch="2" * 32),
            dataclasses.replace(item, owner="controller-2"),
        )
        for foreign in mixed:
            with self.subTest(foreign=repr(foreign)):
                with self.assertRaisesRegex(
                        ProtocolV2Error, "authorization_registry_identity_mismatch"):
                    registry.insert(foreign, now_ms=NOW)
                with self.assertRaisesRegex(
                        ProtocolV2Error, "authorization_registry_identity_mismatch"):
                    registry.match_and_consume(foreign, now_ms=NOW)
                self.assertEqual(len(registry), 1)
                self.assertEqual(registry.tombstone_count, 0)
        for foreign_pin in (
                self.pinned(machine_id="machine-76543210"),
                self.pinned(broker_epoch="1" * 32),
                self.pinned(controller_epoch="2" * 32),
                self.pinned(owner="controller-2")):
            with self.subTest(pin=tuple(foreign_pin.values())):
                with self.assertRaisesRegex(
                        ProtocolV2Error, "authorization_registry_identity_mismatch"):
                    registry.revoke(**foreign_pin, operation_id=OPERATION)
                with self.assertRaisesRegex(
                        ProtocolV2Error, "authorization_registry_identity_mismatch"):
                    registry.disconnect(**foreign_pin)
                self.assertEqual(len(registry), 1)
        self.assertEqual(registry.match_and_consume(item, now_ms=NOW), item)

    def test_registry_constructor_is_bounded_and_has_no_reset_surface(self):
        invalid = (None, True, 1, 1.5, "", [], {}, "x" * 1000)
        for field in ("machine_id", "broker_epoch", "controller_epoch", "owner"):
            for value in invalid:
                with self.subTest(field=field, value_type=type(value).__name__):
                    with self.assertRaisesRegex(
                            ProtocolV2Error, "authorization_registry_identity_invalid"):
                        self.registry(**{field: value})
        registry = self.registry()
        self.assertFalse(hasattr(registry, "reset"))
        self.assertFalse(hasattr(registry, "replace_epoch"))

    def test_registry_capacity_duplicate_expiry_revoke_disconnect_and_quiesce(self):
        registry = self.registry()
        for index in range(16):
            registry.insert(self.identity(index), now_ms=NOW)
        with self.assertRaisesRegex(ProtocolV2Error, "authorization_capacity"):
            registry.insert(self.identity(16), now_ms=NOW)
        self.assertEqual(registry.revoke(**self.pinned(), binding_id=BINDING), 16)
        self.assertEqual(registry.tombstone_count, 16)
        registry = self.registry()
        expired = self.identity(20, expires_at_unix_ms=NOW + 1)
        registry.insert(expired, now_ms=NOW)
        self.assertEqual(registry.expire(now_ms=NOW + 1), 1)
        registry = self.registry(owner="controller-2")
        owned = self.identity(21, owner="controller-2")
        registry.insert(owned, now_ms=NOW)
        self.assertEqual(registry.disconnect(**self.pinned(owner="controller-2")), 1)
        registry.insert(self.identity(22, owner="controller-2"), now_ms=NOW)
        self.assertEqual(registry.quiesce(), 1)
        with self.assertRaisesRegex(ProtocolV2Error, "authorization_closed"):
            registry.insert(self.identity(23, owner="controller-2"), now_ms=NOW)

    def test_registry_refuses_duplicate_invalid_time_and_ambiguous_revoke(self):
        registry = self.registry()
        item = self.identity()
        registry.insert(item, now_ms=NOW)
        with self.assertRaisesRegex(ProtocolV2Error, "authorization_duplicate"):
            registry.insert(item, now_ms=NOW)
        for kwargs in ({}, {"binding_id": BINDING, "operation_id": OPERATION}):
            with self.assertRaisesRegex(ProtocolV2Error, "revoke_scope_invalid"):
                registry.revoke(**self.pinned(), **kwargs)
        with self.assertRaisesRegex(ProtocolV2Error, "clock_invalid"):
            registry.expire(now_ms=True)

    def test_identity_validates_on_construction_and_repr_is_fixed(self):
        item = self.identity()
        self.assertEqual(repr(item), "AuthorizationIdentity(validated=True)")
        for changes in ({"owner": "x" * 1000 + "INJECTED"},
                        {"request_digest": 1}, {"operation_id": []},
                        {"expires_at_unix_ms": True},
                        {"activation_expires_at_unix_ms": NOW + 1}):
            with self.subTest(changes=tuple(changes)):
                with self.assertRaisesRegex(ProtocolV2Error,
                                            "authorization_identity_invalid"):
                    dataclasses.replace(item, **changes)

    def test_registry_clock_caps_tombstones_and_thousand_operation_probe(self):
        registry = self.registry()
        first = self.identity()
        registry.insert(first, now_ms=NOW, clock_uncertainty_ms=250)
        with self.assertRaisesRegex(ProtocolV2Error, "clock_rollback"):
            registry.match_and_consume(first, now_ms=NOW - 251)
        self.assertEqual(len(registry), 0)
        self.assertEqual(registry.tombstone_count, 1)
        with self.assertRaisesRegex(ProtocolV2Error, "authorization_closed"):
            registry.match_and_consume(first, now_ms=NOW)
        skew = self.registry()
        skew.insert(first, now_ms=NOW)
        skew.match_and_consume(first, now_ms=NOW - 250)

        bounded = self.registry()
        admitted = 0
        refused = 0
        for index in range(1000):
            item = self.identity(index)
            try:
                bounded.insert(item, now_ms=NOW)
                bounded.match_and_consume(item, now_ms=NOW)
                admitted += 1
            except ProtocolV2Error as error:
                self.assertIn(error.code, {"authorization_tombstone_capacity"})
                refused += 1
            self.assertLessEqual(bounded.tombstone_count,
                                 MAX_AUTHORIZATION_TOMBSTONES)
        self.assertEqual(admitted, MAX_AUTHORIZATION_TOMBSTONES)
        self.assertEqual(refused, 1000 - MAX_AUTHORIZATION_TOMBSTONES)
        later = self.identity(1001, expires_at_unix_ms=NOW + 8_000,
                              activation_expires_at_unix_ms=NOW + 10_000,
                              request_deadline_unix_ms=NOW + 9_000)
        with self.assertRaisesRegex(ProtocolV2Error,
                                    "authorization_tombstone_capacity"):
            bounded.insert(later, now_ms=NOW + 4_000)
        later_far = self.identity(
            1002, expires_at_unix_ms=NOW + 105_000,
            binding_expires_at_unix_ms=NOW + 120_000,
            activation_expires_at_unix_ms=NOW + 110_000,
            request_deadline_unix_ms=NOW + 108_000)
        with self.assertRaisesRegex(ProtocolV2Error,
                                    "authorization_tombstone_capacity"):
            bounded.insert(later_far, now_ms=NOW + 100_000)
        new_epoch = self.registry(broker_epoch="1" * 32)
        with self.assertRaisesRegex(
                ProtocolV2Error, "authorization_registry_identity_mismatch"):
            new_epoch.insert(later, now_ms=NOW + 4_000)
        self.assertEqual(len(new_epoch), 0)
        new_epoch.insert(dataclasses.replace(later, broker_epoch="1" * 32),
                         now_ms=NOW + 4_000)

        ttl = self.registry()
        exact = self.identity(1002, expires_at_unix_ms=NOW + 5_000)
        ttl.insert(exact, now_ms=NOW)
        too_long = self.identity(1003, expires_at_unix_ms=NOW + 5_001)
        with self.assertRaisesRegex(ProtocolV2Error, "authorization_closed"):
            self.registry().insert(too_long, now_ms=NOW)
        uncertain = self.identity(1004, expires_at_unix_ms=NOW + 250)
        with self.assertRaisesRegex(ProtocolV2Error, "authorization_closed"):
            self.registry().insert(uncertain, now_ms=NOW,
                                   clock_uncertainty_ms=250)

    def test_operation_id_reuse_refused_at_and_far_after_expiry_until_new_epoch(self):
        registry = self.registry()
        item = self.identity()
        registry.insert(item, now_ms=NOW)
        registry.match_and_consume(item, now_ms=NOW)
        for observed in (NOW + 4_000, NOW + 90_000):
            with self.subTest(observed=observed):
                replacement = self.identity(
                    expires_at_unix_ms=observed + 5_000,
                    binding_expires_at_unix_ms=observed + 20_000,
                    activation_expires_at_unix_ms=observed + 10_000,
                    request_deadline_unix_ms=observed + 8_000)
                with self.assertRaisesRegex(ProtocolV2Error,
                                            "authorization_duplicate"):
                    registry.insert(replacement, now_ms=observed)
        fresh_epoch = self.registry(controller_epoch="2" * 32)
        replacement = self.identity(
            expires_at_unix_ms=NOW + 100_000,
            binding_expires_at_unix_ms=NOW + 120_000,
            activation_expires_at_unix_ms=NOW + 105_000,
            request_deadline_unix_ms=NOW + 103_000)
        with self.assertRaisesRegex(
                ProtocolV2Error, "authorization_registry_identity_mismatch"):
            fresh_epoch.insert(replacement, now_ms=NOW + 95_000)
        replacement = dataclasses.replace(replacement, controller_epoch="2" * 32)
        fresh_epoch.insert(replacement,
                           now_ms=NOW + 95_000)

    def test_registry_property_style_malformed_inputs_are_bounded(self):
        item = self.identity()
        malformed = (None, True, 0, 1.5, "", [], {}, "x" * 1000)
        for value in malformed:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ProtocolV2Error):
                    self.registry().insert(value, now_ms=NOW)
                with self.assertRaises(ProtocolV2Error):
                    self.registry().match_and_consume(value, now_ms=NOW)
                with self.assertRaises(ProtocolV2Error):
                    self.registry().disconnect(owner=value)
        registry = self.registry()
        registry.insert(item, now_ms=NOW)
        for now in (None, True, 0, 1.5, "", [], {}):
            with self.subTest(now=type(now).__name__):
                with self.assertRaises(ProtocolV2Error):
                    registry.expire(now_ms=now)

    def test_protocol_module_has_no_io_runtime_or_secret_dependencies(self):
        path = pathlib.Path(__file__).parents[1] / "sandbox/isolation/credential_controller_protocol_v2.py"
        source = path.read_text(encoding="utf-8")
        for forbidden in ("import os", "import socket", "subprocess", "pathlib",
                          "urllib", "requests", "CredentialBindingRepository",
                          "SecretReferenceResolver", "open(", "audit sink"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
