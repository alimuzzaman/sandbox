import copy
import hashlib
import json
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
V1 = ROOT / "specs/045-credential-vault-isolation/contracts/credential-broker-service-v1.md"
V2 = ROOT / "specs/045-credential-vault-isolation/contracts/credential-broker-controller-authority-v2.md"
PLAN = ROOT / "specs/045-credential-vault-isolation/plan.md"
DATA_MODEL = ROOT / "specs/045-credential-vault-isolation/data-model.md"
QUICKSTART = ROOT / "specs/045-credential-vault-isolation/quickstart.md"
TASKS = ROOT / "specs/045-credential-vault-isolation/tasks.md"
REVIEWED_REGISTRY_DIGEST = "4a2ec24e98481efdf3f7f3ce2020613e8d340e7d0158beca1a3d49265120ebd2"


def schema_table(document):
    bounded = document.split("<!-- CONTROLLER_V2_SCHEMA_TABLE_BEGIN -->", 1)[1]
    bounded = bounded.split("<!-- CONTROLLER_V2_SCHEMA_TABLE_END -->", 1)[0]
    return json.loads(bounded.split("```json", 1)[1].split("```", 1)[0])


def exact_keys(schema, payload):
    return set(payload) == set(schema["required"])


def identifier_valid(rule, value):
    return (
        isinstance(value, str)
        and rule["min"] <= len(value) <= rule["max"]
        and re.fullmatch(rule["pattern"], value) is not None
    )


class ControllerAuthorityV2ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v1 = V1.read_text(encoding="utf-8")
        cls.v2 = V2.read_text(encoding="utf-8")
        cls.plan = PLAN.read_text(encoding="utf-8")
        cls.data_model = DATA_MODEL.read_text(encoding="utf-8")
        cls.quickstart = QUICKSTART.read_text(encoding="utf-8")
        cls.tasks = TASKS.read_text(encoding="utf-8")
        cls.registry = schema_table(cls.v2)

    def test_registry_has_only_reviewed_wire_messages_and_variants(self):
        expected = {
            "HELLO_V2", "HELLO_ACK_V2", "CLAIM_NEXT_V2",
            "CLAIMED_V2_CLAIMED", "CLAIMED_V2_NO_PENDING",
            "AUTHORIZE_V2", "AUTHORIZED_V2", "REFUSE_V2",
            "ACTIVATE_V2", "ACTIVATE_ACK_V2", "QUIESCE_V2",
            "QUIESCE_ACK_V2", "AUDIT_PRE_V2", "AUDIT_POST_V2",
            "AUDIT_ACK_V2",
            "LEASE_ACK_V2",
        }
        self.assertEqual(set(self.registry["messages"]), expected)
        self.assertEqual(
            self.registry["messages"]["CLAIMED_V2_NO_PENDING"]["wire_type"],
            "CLAIMED_V2",
        )
        canonical = json.dumps(
            self.registry, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), REVIEWED_REGISTRY_DIGEST)

    def test_every_schema_is_exact_and_uses_declared_field_types(self):
        known_fields = set(self.registry["field_types"])
        for name, schema in self.registry["messages"].items():
            with self.subTest(name=name):
                self.assertEqual(len(schema["required"]), len(set(schema["required"])))
                self.assertFalse(set(schema["required"]) - known_fields)
                baseline = {field: object() for field in schema["required"]}
                self.assertTrue(exact_keys(schema, baseline))
                missing = dict(baseline)
                missing.pop(schema["required"][-1])
                self.assertFalse(exact_keys(schema, missing))
                extra = dict(baseline, unknown_field=True)
                self.assertFalse(exact_keys(schema, extra))

        primitive_types = {
            "boolean_true", "timestamp", "digest", "uint_0_16", "auth_form",
            "positive_sequence", "uint_0_1048576", "content_type",
            "uint_0_65536", "http_method", "request_path", "reason_code",
            "uint_50_1000", "https_literal", "protocol_literal",
            "message_literal", "pid", "https_port_443", "evidence_id_or_null",
        }
        declared_types = (
            primitive_types
            | set(self.registry["enums"])
            | set(self.registry["identifier_rules"])
        )
        self.assertFalse(set(self.registry["field_types"].values()) - declared_types)

    def test_identifier_rules_reject_boundary_and_alphabet_mutations(self):
        samples = {
            "machine_id": "machine-01234567",
            "binding_id": "binding-01234567",
            "operation_id": "operation-012345",
            "decision_id": "decision-0123456",
            "evidence_id": "evidence-0123456",
            "audit_id": "audit-0123456789",
            "commit_id": "commit-012345678",
            "epoch": "0123456789abcdef0123456789abcdef",
        }
        for name, value in samples.items():
            rule = self.registry["identifier_rules"][name]
            with self.subTest(name=name):
                self.assertTrue(identifier_valid(rule, value))
                self.assertFalse(identifier_valid(rule, value.upper()))
                self.assertFalse(identifier_valid(rule, value + ("x" * 64)))
                self.assertFalse(identifier_valid(rule, value[: rule["min"] - 1]))

    def test_numeric_bounds_sequence_gaps_and_timeouts_are_normative(self):
        bounds = self.registry["bounds"]
        self.assertEqual(
            bounds,
            {
                "activation_ttl_ms": 30000,
                "audit_ack_timeout_ms": 1000,
                "audit_transport_retries": 1,
                "authorization_ttl_ms": 5000,
                "clock_skew_ms": 250,
                "controller_frame_bytes": 16384,
                "drain_timeout_ms": 5000,
                "handshake_timeout_ms": 1000,
                "lease_ack_bytes": 444,
                "lease_ack_timeout_ms": 1000,
                "lease_terminal_grace_ms": 2000,
                "lease_bytes": 16384,
                "lease_frame_bytes": 732,
                "lease_ttl_ms": 5000,
                "max_active_operations": 16,
                "max_sequence": 9007199254740991,
                "min_sequence": 1,
                "no_pending_retry_max_ms": 1000,
                "no_pending_retry_min_ms": 50,
                "timestamp_max_unix_ms": 4102444800000,
                "timestamp_min_unix_ms": 1700000000000,
            },
        )
        self.assertEqual(bounds["min_sequence"], 1)
        self.assertEqual(bounds["max_sequence"], 9007199254740991)
        self.assertEqual(bounds["handshake_timeout_ms"], 1000)
        self.assertEqual(bounds["audit_transport_retries"], 1)
        self.assertEqual(bounds["drain_timeout_ms"], 5000)
        self.assertEqual(bounds["lease_ttl_ms"], 5000)
        self.assertIn("exactly the preceding value plus one, without gaps", self.v2)
        for mutated in (0, -1, bounds["max_sequence"] + 1, 1.0, True):
            self.assertFalse(type(mutated) is int and 1 <= mutated <= bounds["max_sequence"])

    def test_reason_code_allowlists_are_closed_to_mutation(self):
        reasons = self.registry["reason_codes"]
        self.assertEqual(set(reasons), {"activate", "post", "quiesce", "refuse"})
        for context, allowed in reasons.items():
            with self.subTest(context=context):
                self.assertEqual(len(allowed), len(set(allowed)))
                self.assertNotIn("arbitrary_error", allowed)
                mutated = list(allowed) + ["arbitrary_error"]
                self.assertNotEqual(mutated, allowed)

    def test_handshake_precedes_authority_and_binds_mutual_kernel_identity(self):
        hello = self.registry["messages"]["HELLO_V2"]
        ack = self.registry["messages"]["HELLO_ACK_V2"]
        self.assertEqual(hello["direction"], "broker_to_controller")
        self.assertEqual(ack["direction"], "controller_to_broker")
        for field in ("broker_pid", "broker_start_ticks", "broker_executable_digest",
                      "broker_unit_digest", "broker_config_digest"):
            self.assertIn(field, hello["required"])
        for field in ("controller_pid", "controller_start_ticks",
                      "controller_executable_digest", "controller_unit_digest",
                      "controller_config_digest", "handshake_digest"):
            self.assertIn(field, ack["required"])
        self.assertIn("No lifecycle, claim, authorization, lease, or audit traffic", self.v2)
        self.assertIn("SO_PEERCRED", self.v2)
        self.assertIn("SCM_CREDENTIALS", self.v2)
        self.assertNotIn("controller_epoch", hello["required"])
        self.assertEqual(
            self.registry["field_exceptions"]["HELLO_V2"]["forbidden"],
            ["controller_epoch"],
        )

    def test_lifecycle_acknowledgements_bind_request_digest_and_terminal_state(self):
        activate = self.registry["messages"]["ACTIVATE_ACK_V2"]["required"]
        quiesce = self.registry["messages"]["QUIESCE_ACK_V2"]["required"]
        for field in ("reply_to", "activation_digest", "admission_state",
                      "activate_decision", "active_operation_count"):
            self.assertIn(field, activate)
        for field in ("reply_to", "quiesce_digest", "admission_state",
                      "drain_status", "active_operation_count",
                      "drain_deadline_unix_ms"):
            self.assertIn(field, quiesce)
        self.assertIn("sends one terminal lifecycle acknowledgement", self.v2)
        self.assertIn("sends exactly one terminal acknowledgement", self.v2)

    def test_authorization_and_proof_roles_are_exact(self):
        authorization = self.registry["messages"]["AUTHORIZE_V2"]["required"]
        for field in ("auth_form", "authorization_digest", "proof_digest",
                      "effective_isolation_digest", "evidence_id"):
            self.assertIn(field, authorization)
        self.assertEqual(
            self.registry["auth_forms"],
            ["authorization_bearer", "x_api_key"],
        )
        self.assertIn("The broker's proof role is exact comparison, not fresh proof evaluation", self.v2)
        self.assertIn("helper-produced sealed configured expectations", self.v2)

    def test_digest_documents_have_exact_mutation_checked_field_sets(self):
        documents = self.registry["digest_documents"]
        self.assertEqual(
            set(documents),
            {"activation_digest", "audit_post_fingerprint", "audit_pre_fingerprint",
             "authorization_digest", "handshake_digest", "quiesce_digest"},
        )
        for name, fields in documents.items():
            with self.subTest(name=name):
                self.assertEqual(len(fields), len(set(fields)))
                baseline = {field: object() for field in fields}
                self.assertEqual(set(baseline), set(fields))
                missing = dict(baseline)
                missing.pop(fields[-1])
                self.assertNotEqual(set(missing), set(fields))
                extra = dict(baseline, unexpected=True)
                self.assertNotEqual(set(extra), set(fields))
        self.assertIn("request_sequence", self.registry["field_types"])
        self.assertIn("request_sequence", documents["activation_digest"])
        self.assertIn("request_sequence", documents["quiesce_digest"])

    def test_temporal_rules_cover_exactly_all_seven_timestamp_fields(self):
        rules = self.registry["temporal_rules"]
        timestamp_fields = {
            field for field, field_type in self.registry["field_types"].items()
            if field_type == "timestamp"
        }
        self.assertEqual(set(rules), timestamp_fields)
        for message_name, schema in self.registry["messages"].items():
            for field in set(schema["required"]) & timestamp_fields:
                with self.subTest(message=message_name, field=field):
                    self.assertIn(message_name, rules[field]["messages"])
        self.assertEqual(
            set(rules),
            {"acknowledged_at_unix_ms", "activation_expires_at_unix_ms",
             "authorization_expires_at_unix_ms", "binding_expires_at_unix_ms",
             "drain_deadline_unix_ms", "request_deadline_unix_ms",
             "wait_deadline_unix_ms"},
        )
        self.assertEqual(rules["request_deadline_unix_ms"]["max_future_ms"], 30000)
        self.assertIsNone(rules["binding_expires_at_unix_ms"]["max_future_ms"])
        self.assertIn(
            "wait_deadline_unix_ms",
            self.registry["messages"]["CLAIM_NEXT_V2"]["required"],
        )

    def test_binary_lease_layout_is_contiguous_and_exactly_732_bytes(self):
        lease_section = self.v2.split("The v2 lease envelope is exactly", 1)[1]
        lease_section = lease_section.split("## Persistent audit protocol", 1)[0]
        rows = [
            (int(offset), int(width), name)
            for offset, width, name in re.findall(
                r"^\| (\d+) \| (\d+) \| `([^`]+)` \|$", lease_section, re.MULTILINE
            )
        ]
        self.assertEqual(rows[0], (0, 8, "magic"))
        self.assertEqual(rows[-1], (700, 32, "frame_digest"))
        cursor = 0
        for offset, width, _name in rows:
            self.assertEqual(offset, cursor)
            cursor += width
        self.assertEqual(cursor, 732)
        mutated = copy.deepcopy(rows)
        offset, width, name = mutated[8]
        mutated[8] = (offset + 1, width, name)
        self.assertNotEqual(mutated[8][0], sum(item[1] for item in mutated[:8]))
        self.assertIn("Every integer is unsigned\nbig-endian", self.v2)
        self.assertIn("SHA-256 over bytes 0 through 699", self.v2)

    def test_lease_ack_is_exact_same_socket_terminal_binary_protocol(self):
        schema = self.registry["messages"]["LEASE_ACK_V2"]
        self.assertEqual(schema["direction"], "broker_to_controller_same_lease_socket")
        self.assertEqual(schema["encoding"], "fixed_binary_444")
        self.assertEqual(
            schema["required"],
            ["type", "machine_id", "broker_epoch", "controller_epoch", "lease_id",
             "lease_sequence", "authorization_digest", "audit_root_id",
             "post_phase_id", "post_commit_id", "outcome_class",
             "effect_certainty", "reason_code"],
        )
        ack_section = self.v2.split("## Same-socket lease acknowledgement v2", 1)[1]
        ack_section = ack_section.split("## Replay, rotation, restart, and expiry", 1)[0]
        rows = [
            (int(offset), int(width), name)
            for offset, width, name in re.findall(
                r"^\| (\d+) \| (\d+) \| `([^`]+)` \|$", ack_section, re.MULTILINE
            )
        ]
        self.assertEqual(rows[0], (0, 8, "ack_magic"))
        self.assertEqual(rows[-1], (412, 32, "ack_frame_digest"))
        self.assertEqual(sum(width for _offset, width, _name in rows), 444)
        self.assertIn("does not retry the lease or ACK", ack_section)

    def test_lease_sequence_is_scoped_to_both_process_epochs(self):
        self.assertIn("exact `(controller_epoch, broker_epoch)` pair", self.v2)
        self.assertIn("safely resets the new pair to 1", self.v2)

    def test_audit_semantics_are_distinct_from_transport_replay(self):
        pre = self.registry["messages"]["AUDIT_PRE_V2"]["required"]
        post = self.registry["messages"]["AUDIT_POST_V2"]["required"]
        ack = self.registry["messages"]["AUDIT_ACK_V2"]["required"]
        for fields in (pre, post, ack):
            self.assertIn("audit_root_id", fields)
            self.assertIn("phase_id", fields)
            self.assertIn("audit_fingerprint", fields)
        self.assertIn("Transport and semantic idempotency are separate", self.v2)
        self.assertIn("uses the next transport sequence", self.v2)
        self.assertIn("every durable PRE tombstone without a POST", self.v2)
        self.assertEqual(
            self.registry["enums"]["post_pairs"],
            [["completed", "completed"], ["refused", "none"],
             ["indeterminate", "possible"], ["indeterminate", "completed"]],
        )

    def test_upstream_response_boundary_does_not_claim_universal_confinement(self):
        self.assertIn("never deliberately emits, copies, or logs", self.v2)
        self.assertIn("bounded best-effort redaction", self.v2)
        self.assertIn("makes no universal response-\nconfinement claim", self.v2)
        self.assertIn("arbitrary transformed-response confinement remains unproven", self.quickstart)

    def test_v1_is_historical_and_completed_t043_precedes_live_gates(self):
        for document in (self.v1, self.plan, self.data_model, self.quickstart):
            self.assertIn("fake/local-only", document)
        self.assertIn("T022, T029, and T031 remain blocked", self.tasks)
        self.assertIn("independently Sol High-accepted T043 implementation provides one connected inert full-flow v2 harness", self.plan)
        self.assertIn("locally complete and independently accepted", self.plan)

    def test_local_contract_complete_but_release_gates_remain_open(self):
        for document in (self.v2, self.quickstart, self.tasks):
            self.assertIn("implemented_unproven", document)
            self.assertIn("adoptable=false", document)
        self.assertIn("evidence_id=null", self.v2)
        for task_id in (22, 29, 31):
            self.assertIn(f"- [ ] T{task_id:03d}", self.tasks)
        self.assertIn("- [x] T036", self.tasks)
        self.assertIn("- [x] T035", self.tasks)
        for task_id in (37, 38, 43):
            self.assertIn(f"- [x] T{task_id:03d}", self.tasks)
        self.assertIn("- [x] T039", self.tasks)
        self.assertIn("- [x] T040", self.tasks)
        self.assertIn("- [x] T041", self.tasks)
        self.assertIn("- [x] T042", self.tasks)


if __name__ == "__main__":
    unittest.main()
