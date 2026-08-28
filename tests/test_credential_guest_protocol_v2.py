import base64
import dataclasses
import hashlib
import ipaddress
import json
import struct
import unittest

from sandbox.isolation.credential_guest_protocol_v2 import (
    AuthorizedEffectContextV2,
    AuthorizedEgressDecisionV2,
    EffectExecutionResultV2,
    EffectExecutionV2,
    GUEST_PROTOCOL_REGISTRY,
    GuestProtocolV2Error,
    GuestRequestV2,
    GuestResultV2,
    GuestTransportObservationV2,
    build_egress_projection_v2,
    build_guest_transport_projection_v2,
    decode_guest_request_v2,
    decode_guest_result_v2,
    authorize_egress_decision_v2,
    encode_guest_request_v2,
    encode_guest_result_v2,
    guest_protocol_registry_digest_v2,
    guest_request_digest_v2,
    verify_guest_transport_v2,
    canonical_egress_projection_v2,
)
from sandbox.isolation.models import (
    EgressGrant,
    EgressGrantSet,
    ManagedIsolationPolicy,
)


MACHINE = "sb-0123456789ab"
DIGESTS = tuple(hashlib.sha256(f"guest-v2-{index}".encode()).hexdigest()
                for index in range(8))


def policy():
    return ManagedIsolationPolicy(
        1, MACHINE, {"base": 200000, "count": 65536},
        {"path": "/images/demo.img"}, (), (),
        {"egress": "deny", "veth": "ve-sb01234567",
         "host_address": "10.203.0.1/30", "guest_address": "10.203.0.2/30",
         "default_route": False},
        {"no_new_privileges": True}, frozenset(),
        {"memory_max": 1024, "pids_max": 64}, (),
    )


def grants(target):
    values = (
        EgressGrant("dns-api", MACHINE, "hostname_https", ("api.example.com",),
                    (443,), "2999-01-01T00:00:00Z"),
        EgressGrant("ip-api-a", MACHINE, "public_cidr_tcp", ("93.184.216.0/24",),
                    (443,), "2999-01-01T00:00:00Z"),
        EgressGrant("ip-api-b", MACHINE, "public_cidr_tcp", ("8.8.8.8/32",),
                    (443,), "2999-01-01T00:00:00Z"),
    )
    return EgressGrantSet(MACHINE, target.digest, values)


def request():
    return GuestRequestV2(
        machine_id=MACHINE, binding_id="binding-01234567", binding_version=1,
        scheme="https", host="api.example.com", port=443, method="POST",
        path="/v1/items", headers=(("accept", "application/json"),),
        body=b'{"ok":true}', content_type="application/json", deadline_ms=30_000,
        correlation_id="guest-correlation-1",
    )


def egress_decision():
    target = policy()
    return authorize_egress_decision_v2(
        build_egress_projection_v2(target, grants(target)),
        host="api.example.com", sni_hostname="api.example.com", port=443,
        resolved_addresses=("93.184.216.34", "8.8.8.8"),
        now="2026-08-28T00:00:00Z",
    )


def authorized_context():
    selected = request()
    decision = egress_decision()
    return AuthorizedEffectContextV2(
        request=selected, egress_decision=decision,
        egress_digest=decision.projection_digest, machine_id=MACHINE,
        broker_epoch="1" * 32, controller_epoch="2" * 32,
        operation_id="operation-012345",
        request_digest=guest_request_digest_v2(selected),
        binding_id=selected.binding_id, binding_version=selected.binding_version,
        decision_id="decision-0123456", authorization_digest=DIGESTS[0],
        auth_form="authorization_bearer", lease_id="lease-0123456789",
        lease_sequence=1, descriptor_size=32,
        request_deadline_unix_ms=1_800_000_030_000,
        binding_expires_at_unix_ms=1_800_000_040_000,
        authorization_expires_at_unix_ms=1_800_000_005_000,
        lease_expires_at_unix_ms=1_800_000_004_000,
        activation_expires_at_unix_ms=1_800_000_020_000,
    )


class Executor(EffectExecutionV2):
    def __init__(self):
        super().__init__()
        self.calls = []

    def execute_authorized(self, context, descriptor):
        self.calls.append((context, descriptor))
        return EffectExecutionResultV2(
            guest_result=GuestResultV2.success(
                200, (("content-type", "application/json"),), b"{}",
                context.request.correlation_id,
            ),
            effect_phase="effect_entered",
            outcome_class="completed", effect_certainty="completed",
            reason_code="upstream_completed",
        )


class RaisingExecutor(EffectExecutionV2):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def execute_authorized(self, _context, _descriptor):
        raise self.error


class InvalidExecutor(EffectExecutionV2):
    def execute_authorized(self, _context, _descriptor):
        return object()


class CorrelationExecutor(EffectExecutionV2):
    def execute_authorized(self, _context, _descriptor):
        return EffectExecutionResultV2(
            GuestResultV2.success(200, (), b"", "different-correlation"),
            "effect_entered", "completed", "completed", "upstream_completed")


class PreEffectExecutor(EffectExecutionV2):
    def execute_authorized(self, context, _descriptor):
        return EffectExecutionResultV2(
            GuestResultV2.failure(
                state="refused", code="egress_denied", retryable=False,
                correlation_id=context.request.correlation_id),
            "pre_effect", "refused", "none", "egress_denied")


class TestCredentialGuestProtocolV2(unittest.TestCase):
    def test_exact_request_and_result_round_trip_and_vectors(self):
        selected = request()
        packet = encode_guest_request_v2(selected)
        self.assertEqual(packet[:4], b"SBG2")
        self.assertEqual(packet[4], 2)
        self.assertEqual(decode_guest_request_v2(packet), selected)
        self.assertEqual(guest_request_digest_v2(selected),
                         "9532346a704c35b7514024bd4398e95f7f6015b3305e3279a184b9e4e35475cc")
        self.assertEqual(guest_protocol_registry_digest_v2(),
                         "3f8a869b73c1a36b7a4889b66856f60168bcf054ac4478312d406d6725d4f645")
        state_codes = GUEST_PROTOCOL_REGISTRY["result"]["state_codes"]
        self.assertEqual(set(state_codes), {"refused", "indeterminate"})
        self.assertEqual(
            set(state_codes["refused"]) & set(state_codes["indeterminate"]),
            {"deadline_exceeded", "upstream_refused"})
        self.assertIn("upstream_refused", state_codes["refused"])
        self.assertIn("upstream_refused", state_codes["indeterminate"])

        success = GuestResultV2.success(
            200, (("content-type", "application/json"),), b"{}",
            selected.correlation_id,
        )
        result_packet = encode_guest_result_v2(success)
        self.assertEqual(result_packet[:5], b"SBR2\x02")
        self.assertEqual(decode_guest_result_v2(result_packet), success)
        refusal = GuestResultV2.failure(
            state="refused", code="request_invalid", retryable=False,
            correlation_id=selected.correlation_id,
        )
        self.assertEqual(decode_guest_result_v2(encode_guest_result_v2(refusal)), refusal)

    def test_unknown_v1_noncanonical_duplicate_trailing_and_extra_refuse(self):
        packet = encode_guest_request_v2(request())
        mutations = (b"SBGR" + packet[4:], packet[:4] + b"\x01" + packet[5:],
                     packet + b"x")
        for mutation in mutations:
            with self.subTest(mutation=mutation[:9]), self.assertRaises(GuestProtocolV2Error):
                decode_guest_request_v2(mutation)

        payload = packet[9:]
        duplicate = payload[:-1] + b',"machine_id":"sb-0123456789ab"}'
        duplicate_packet = struct.pack("!4sBI", b"SBG2", 2, len(duplicate)) + duplicate
        with self.assertRaises(GuestProtocolV2Error):
            decode_guest_request_v2(duplicate_packet)
        value = json.loads(payload)
        value["operation_id"] = "operation-012345"
        extra = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        with self.assertRaises(GuestProtocolV2Error):
            decode_guest_request_v2(struct.pack("!4sBI", b"SBG2", 2, len(extra)) + extra)
        value.pop("operation_id")
        value["binding_version"] = 10 ** 100
        huge = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        with self.assertRaises(GuestProtocolV2Error):
            decode_guest_request_v2(struct.pack("!4sBI", b"SBG2", 2, len(huge)) + huge)

    def test_request_and_result_bounds_headers_base64_and_redirect_refuse(self):
        forbidden = ("authorization", "proxy-authorization", "x-api-key",
                     "connection", "transfer-encoding", "content-type")
        for name in forbidden:
            with self.subTest(name=name), self.assertRaises(GuestProtocolV2Error):
                dataclasses.replace(request(), headers=((name, "x"),))
        with self.assertRaises(GuestProtocolV2Error):
            dataclasses.replace(request(), body=b"x" * (1024 * 1024 + 1))
        with self.assertRaises(GuestProtocolV2Error):
            GuestResultV2.success(302, (), b"", "guest-correlation-1")
        with self.assertRaises(GuestProtocolV2Error):
            GuestResultV2.success(200, (), b"x" * (4 * 1024 * 1024 + 1),
                                  "guest-correlation-1")
        packet = bytearray(encode_guest_request_v2(request()))
        marker = base64.b64encode(request().body)
        start = bytes(packet).index(marker)
        packet[start] = ord("!")
        with self.assertRaises(GuestProtocolV2Error):
            decode_guest_request_v2(bytes(packet))
        for changes in ({"host": []}, {"method": []}, {"port": True}):
            with self.subTest(changes=changes), self.assertRaises(GuestProtocolV2Error):
                dataclasses.replace(request(), **changes)
        with self.assertRaises(GuestProtocolV2Error):
            GuestResultV2.failure(
                state="refused", code=[], retryable=False,
                correlation_id="guest-correlation-1")

    def test_fixed_private_veth_projection_and_exact_kernel_observation(self):
        projection = build_guest_transport_projection_v2(policy())
        self.assertEqual(projection.port, 18443)
        self.assertEqual(projection.subnet, "10.203.0.0/30")
        observation = GuestTransportObservationV2(
            machine_id=MACHINE, family="AF_INET", socket_type="SOCK_STREAM",
            interface="ve-sb01234567", bind_to_device_readback="ve-sb01234567",
            subnet="10.203.0.0/30", local_address="10.203.0.1",
            local_port=18443, peer_address="10.203.0.2", forwarded=False,
            loopback=False, route_interface="ve-sb01234567",
            route_source="10.203.0.2", network_namespace_isolated=True,
            default_egress_denied=True, default_route_absent=True,
        )
        self.assertTrue(verify_guest_transport_v2(projection, observation))
        for name, value in (("forwarded", True), ("loopback", True),
                            ("route_interface", "lo"),
                            ("bind_to_device_readback", "other"),
                            ("peer_address", "10.203.0.1")):
            with self.subTest(name=name):
                self.assertFalse(verify_guest_transport_v2(
                    projection, dataclasses.replace(observation, **{name: value})))
        invalid_networks = (
            {"egress": "allow"}, {"default_route": True},
            {"host_address": 167772161},
            {"host_address": "10.203.0.1"},
            {"host_address": "10.203.000.001/30"},
            {"host_address": "127.0.0.1/30", "guest_address": "127.0.0.2/30"},
            {"host_address": "169.254.1.1/30", "guest_address": "169.254.1.2/30"},
            {"host_address": "192.0.2.1/30", "guest_address": "192.0.2.2/30"},
            {"host_address": "0.0.0.1/30", "guest_address": "0.0.0.2/30"},
            {"host_address": "224.0.0.1/30", "guest_address": "224.0.0.2/30"},
        )
        for mutation in invalid_networks:
            network = dict(policy().network)
            network.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(GuestProtocolV2Error):
                build_guest_transport_projection_v2(
                    dataclasses.replace(policy(), network=network, digest=""))

    def test_reciprocal_egress_projection_requires_full_dns_ip_intersection(self):
        target = policy()
        projection = build_egress_projection_v2(target, grants(target))
        self.assertEqual(projection["egress_digest"], grants(target).digest)
        with self.assertRaises(TypeError):
            projection["grants"] = ()
        decision = authorize_egress_decision_v2(
            projection, host="api.example.com", port=443,
            sni_hostname="api.example.com",
            resolved_addresses=("93.184.216.34", "8.8.8.8"),
            now="2026-08-28T00:00:00Z",
        )
        self.assertIsInstance(decision, AuthorizedEgressDecisionV2)
        self.assertEqual(decision.resolved_addresses, ("8.8.8.8", "93.184.216.34"))
        self.assertEqual(decision.nft_destination_set, decision.resolved_addresses)
        self.assertEqual(decision.projection_digest, projection["egress_digest"])
        for host, addresses in (("other.example.com", ("93.184.216.34",)),
                                ("api.example.com", ("93.184.216.34", "1.1.1.1")),
                                ("api.example.com", ("93.184.216.34", "93.184.216.34")),
                                ("api.example.com", ()),
                                ("api.example.com", ("127.0.0.1",))):
            with self.subTest(host=host, addresses=addresses):
                with self.assertRaisesRegex(GuestProtocolV2Error, "egress_denied"):
                    authorize_egress_decision_v2(
                        projection, host=host, port=443,
                        sni_hostname=host, resolved_addresses=addresses,
                        now="2026-08-28T00:00:00Z")
        for host, sni in (("api.example.com", "other.example.com"),
                          ("93.184.216.34", "93.184.216.34")):
            with self.subTest(host=host, sni=sni), self.assertRaises(GuestProtocolV2Error):
                authorize_egress_decision_v2(
                    projection, host=host, sni_hostname=sni, port=443,
                    resolved_addresses=("93.184.216.34",),
                    now="2026-08-28T00:00:00Z")
        changed = json.loads(json.dumps({
            key: ([dict(item) for item in value] if key == "grants" else value)
            for key, value in projection.items()
        }))
        changed["grants"][0]["destinations"] = ["other.example.com"]
        with self.assertRaisesRegex(GuestProtocolV2Error, "egress_projection_invalid"):
            canonical_egress_projection_v2(changed)
        empty = EgressGrantSet(MACHINE, target.digest)
        closed = build_egress_projection_v2(target, empty)
        forged = dict(closed)
        forged["egress_digest"] = DIGESTS[7]
        with self.assertRaisesRegex(GuestProtocolV2Error, "egress_projection_invalid"):
            canonical_egress_projection_v2(forged)

    def test_authorized_effect_context_is_exact_and_execution_is_one_shot(self):
        context = authorized_context()
        with self.assertRaisesRegex(GuestProtocolV2Error, "effect_context_invalid"):
            dataclasses.replace(context, egress_digest=DIGESTS[6])
        for value in (True, 0, 9_007_199_254_740_992, 10 ** 100):
            with self.subTest(lease_sequence=value), self.assertRaisesRegex(
                    GuestProtocolV2Error, "effect_context_invalid"):
                dataclasses.replace(context, lease_sequence=value)
        self.assertEqual(
            dataclasses.replace(
                context, lease_sequence=9_007_199_254_740_991).lease_sequence,
            9_007_199_254_740_991)
        executor = Executor()
        result = executor.execute(context, 71)
        self.assertEqual(result.effect_certainty, "completed")
        self.assertEqual(len(executor.calls), 1)
        with self.assertRaisesRegex(GuestProtocolV2Error, "effect_replayed"):
            executor.execute(context, 71)
        context = authorized_context()
        executor = PreEffectExecutor()
        with self.assertRaisesRegex(GuestProtocolV2Error, "effect_indeterminate"):
            executor.execute(context, 71)
        with self.assertRaisesRegex(GuestProtocolV2Error, "effect_replayed"):
            executor.execute(context, 71)
        context = authorized_context()
        executor = CorrelationExecutor()
        with self.assertRaisesRegex(GuestProtocolV2Error, "effect_indeterminate"):
            executor.execute(context, 71)

    def test_effect_post_combinations_are_phase_exact_and_reason_bound(self):
        correlation = request().correlation_id
        allowed = (
            ("pre_effect", "refused", "none", "upstream_refused"),
            ("pre_effect", "refused", "none", "deadline_exceeded"),
            ("pre_effect", "refused", "none", "revoked"),
            ("pre_effect", "refused", "none", "lease_invalid"),
            ("effect_entered", "completed", "completed", "upstream_completed"),
            ("effect_entered", "indeterminate", "possible", "guest_disconnected"),
            ("effect_entered", "indeterminate", "possible", "deadline_exceeded"),
            ("effect_entered", "indeterminate", "possible", "audit_unavailable"),
            ("effect_entered", "indeterminate", "possible", "internal_indeterminate"),
            ("effect_entered", "indeterminate", "completed", "audit_unavailable"),
            ("effect_entered", "indeterminate", "completed", "internal_indeterminate"),
        )
        for phase, outcome, certainty, reason in allowed:
            if outcome == "completed":
                guest = GuestResultV2.success(200, (), b"", correlation)
            else:
                guest = GuestResultV2.failure(
                    state=outcome, code=reason, retryable=False,
                    correlation_id=correlation)
            with self.subTest(item=(phase, outcome, certainty, reason)):
                selected = EffectExecutionResultV2(
                    guest, phase, outcome, certainty, reason)
                self.assertEqual(selected.reason_code, reason)
                mutations = (
                    ("effect_entered" if phase == "pre_effect" else "pre_effect",
                     outcome, certainty, reason),
                    (phase, outcome, "none" if outcome == "indeterminate" else "possible",
                     reason),
                    (phase, outcome, certainty,
                     "internal_indeterminate" if reason != "internal_indeterminate"
                     else "upstream_completed"),
                )
                for mutation in mutations:
                    with self.subTest(mutation=mutation), self.assertRaisesRegex(
                            GuestProtocolV2Error, "effect_result_invalid"):
                        EffectExecutionResultV2(guest, *mutation)
        refused_deadline = GuestResultV2.failure(
            state="refused", code="deadline_exceeded", retryable=False,
            correlation_id=correlation)
        with self.assertRaisesRegex(GuestProtocolV2Error, "effect_result_invalid"):
            EffectExecutionResultV2(
                refused_deadline, "effect_entered", "refused", "none",
                "deadline_exceeded")
        upstream = GuestResultV2.failure(
            state="refused", code="upstream_refused", retryable=False,
            correlation_id=correlation)
        with self.assertRaisesRegex(GuestProtocolV2Error, "effect_result_invalid"):
            EffectExecutionResultV2(
                upstream, "pre_effect", "refused", "none", "revoked")

    def test_every_post_entry_executor_failure_is_indeterminate_and_tombstoned(self):
        for error in (GuestProtocolV2Error("request_invalid"), RuntimeError("hostile")):
            context = authorized_context()
            executor = RaisingExecutor(error)
            with self.subTest(error=type(error).__name__), self.assertRaisesRegex(
                    GuestProtocolV2Error, "effect_indeterminate"):
                executor.execute(context, 71)
            with self.assertRaisesRegex(GuestProtocolV2Error, "effect_replayed"):
                executor.execute(context, 71)
        context = authorized_context()
        executor = InvalidExecutor()
        with self.assertRaisesRegex(GuestProtocolV2Error, "effect_indeterminate"):
            executor.execute(context, 71)
        with self.assertRaisesRegex(GuestProtocolV2Error, "effect_replayed"):
            executor.execute(context, 71)


if __name__ == "__main__":
    unittest.main()
