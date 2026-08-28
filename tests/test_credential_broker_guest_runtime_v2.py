import dataclasses
import socket
import threading
import unittest
from unittest import mock

from sandbox.isolation.credential_controller_lifecycle_v2 import DerivedServiceConfigV2
from sandbox.isolation.credential_guest_protocol_v2 import (
    GuestProtocolV2Error,
    GuestResultV2,
    GuestTransportObservationV2,
    build_egress_projection_v2,
    encode_guest_request_v2,
)
from sandbox.isolation.credential_upstream import VerifiedHttpsUpstream
from tests import test_credential_controller_authority_v2 as authority
from tests import test_credential_controller_lifecycle_v2 as lifecycle
from tests import test_credential_guest_protocol_v2 as guest_protocol


broker = authority.broker


def plan():
    return DerivedServiceConfigV2.derive(lifecycle.document("broker"))


class GuestSocket:
    def __init__(self, packet=b""):
        self.packet = bytearray(packet)
        self.closed = 0
        self.timeout = None
        self.sent = []

    def settimeout(self, value): self.timeout = value
    def recv(self, size, flags=0):
        if flags:
            raise BlockingIOError
        value = bytes(self.packet[:size])
        del self.packet[:size]
        return value
    def sendall(self, value): self.sent.append(value)
    def close(self): self.closed += 1


class BlockingGuestSocket(GuestSocket):
    def __init__(self):
        super().__init__()
        self.released = threading.Event()

    def recv(self, size, flags=0):
        del size, flags
        self.released.wait(2)
        return b""

    def close(self):
        self.closed += 1
        self.released.set()


class BadCloseGuestSocket(GuestSocket):
    def __init__(self, *, failures):
        super().__init__()
        self.failures = failures
        self.close_attempts = 0

    def close(self):
        self.close_attempts += 1
        if self.close_attempts <= self.failures:
            raise RuntimeError("hostile close detail must stay bounded")
        self.closed += 1


class ListenerSocket:
    def __init__(self, accepted=None, *, readback=b"veth-sb0\0"):
        self.accepted = accepted
        self.readback = readback
        self.closed = 0
        self.bound = None
        self.backlog = None
        self.option = None

    def setsockopt(self, level, kind, value): self.option = (level, kind, value)
    def getsockopt(self, level, kind, size):
        del level, kind, size
        return self.readback
    def bind(self, value): self.bound = value
    def listen(self, value): self.backlog = value
    def accept(self): return self.accepted, ("10.73.0.2", 47000)
    def close(self): self.closed += 1


class QueuedListenerSocket(ListenerSocket):
    def __init__(self, accepted):
        super().__init__(None)
        self.accepted_queue = list(accepted)
        self.accept_calls = 0

    def accept(self):
        self.accept_calls += 1
        return self.accepted_queue.pop(0), ("10.73.0.2", 47000)


class DnsContext:
    def __init__(self, values, *, available=True, stuck=False, unkillable=False):
        self.values = tuple(values)
        self.available = available
        self.stuck = stuck
        self.unkillable = unkillable
        self.processes = []

    class Receiver:
        def __init__(self, owner): self.owner, self.closed = owner, 0
        def poll(self, _timeout): return self.owner.available
        def recv(self): return (True, self.owner.values)
        def close(self): self.closed += 1

    class Sender:
        def close(self): pass

    class ProcessReceipt:
        def __init__(self, owner):
            self.owner, self.started, self.terminated = owner, False, 0
        def start(self): self.started = True
        def is_alive(self):
            return self.owner.stuck and (self.owner.unkillable or self.terminated == 0)
        def terminate(self): self.terminated += 1
        def join(self, _timeout=None): pass

    def Pipe(self, duplex=False):
        self.duplex = duplex
        return self.Receiver(self), self.Sender()

    def Process(self, **_kwargs):
        selected = self.ProcessReceipt(self)
        self.processes.append(selected)
        return selected


def dns_authority(values, **changes):
    return broker.AuthorizedDnsResolverV2(
        context=DnsContext(values, **changes), monotonic=lambda: 0.0)


def observation(**changes):
    value = GuestTransportObservationV2(
        machine_id="sb-0123456789ab", family="AF_INET",
        socket_type="SOCK_STREAM", interface="veth-sb0",
        bind_to_device_readback="veth-sb0", subnet="10.73.0.0/30",
        local_address="10.73.0.1", local_port=18443,
        peer_address="10.73.0.2", forwarded=False, loopback=False,
        route_interface="veth-sb0", route_source="10.73.0.2",
        network_namespace_isolated=True, default_egress_denied=True,
        default_route_absent=True,
    )
    return dataclasses.replace(value, **changes)


class TestLinuxGuestV2Listener(unittest.TestCase):
    def listener(self, accepted, *, observed=None, readback=b"veth-sb0\0"):
        raw = ListenerSocket(accepted, readback=readback)
        selected = broker.LinuxGuestV2Listener(
            plan(), topology_observer=lambda *_args: observed or observation(),
            socket_factory=lambda *_args: raw, clock=lambda: 1_800_000_000_000,
            so_bindtodevice=77,
        )
        return selected, raw

    def test_exact_sealed_bind_topology_then_one_sbg2_and_sbr2(self):
        connection = GuestSocket(encode_guest_request_v2(guest_protocol.request()))
        selected, raw = self.listener(connection)
        self.assertEqual(selected.start(platform="linux", effective_uid=993)["code"],
                         "guest_listener_started")
        self.assertEqual(raw.bound, ("10.73.0.1", 18443))
        self.assertEqual(raw.backlog, 16)
        self.assertEqual(raw.option[2], b"veth-sb0\0")
        selected.set_admission(True)
        owned = selected.accept_once()
        result = GuestResultV2.failure(
            state="refused", code="revoked", retryable=False,
            correlation_id=owned.request.correlation_id)
        owned.deliver(result)
        self.assertEqual(connection.sent[0][:5], b"SBR2\x02")
        self.assertEqual(connection.closed, 1)
        selected.release(owned.identity)
        self.assertEqual(connection.closed, 1)

    def test_closed_until_activation_and_topology_before_bytes(self):
        connection = GuestSocket(encode_guest_request_v2(guest_protocol.request()))
        selected, _raw = self.listener(connection)
        selected.start(platform="linux", effective_uid=993)
        with self.assertRaisesRegex(Exception, "admission_closed"):
            selected.accept_once()
        selected.set_admission(True)
        selected._topology_observer = lambda *_args: observation(
            default_egress_denied=False)
        with self.assertRaisesRegex(Exception, "guest_transport_denied"):
            selected.accept_once()
        self.assertEqual(len(connection.packet), len(
            encode_guest_request_v2(guest_protocol.request())))
        self.assertEqual(connection.closed, 1)

    def test_v1_trailing_timeout_and_bind_readback_fail_closed(self):
        for packet in (b"SBGR\x01\0\0\0\0", b"SBG2\x02\0\0\0\x01xextra"):
            with self.subTest(packet=packet[:4]):
                connection = GuestSocket(packet)
                selected, _raw = self.listener(connection)
                selected.start(platform="linux", effective_uid=993)
                selected.set_admission(True)
                with self.assertRaises(Exception):
                    selected.accept_once()
                self.assertEqual(connection.closed, 1)
        selected, raw = self.listener(GuestSocket(), readback=b"other\0")
        with self.assertRaisesRegex(Exception, "guest_listener_start_refused"):
            selected.start(platform="linux", effective_uid=993)
        self.assertEqual(raw.closed, 1)

    def test_platform_uid_and_cleanup_are_closed_and_idempotent(self):
        selected, raw = self.listener(GuestSocket())
        for platform, uid in (("darwin", 993), ("linux", 0), ("linux", 992)):
            with self.subTest(platform=platform, uid=uid), self.assertRaises(Exception):
                selected.start(platform=platform, effective_uid=uid)
        selected.start(platform="linux", effective_uid=993)
        self.assertTrue(selected.close()["ok"])
        self.assertTrue(selected.close()["ok"])
        self.assertEqual(raw.closed, 1)


class Transport:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.closed = 0

    def request(self, *args):
        self.calls.append((args[0], args[1], dict(args[2]), bytes(args[3]), args[4]))
        return self.result

    def close(self): self.closed += 1


class TestPinnedHTTPSCredentialEffectV2(unittest.TestCase):
    @staticmethod
    def authority():
        return broker.ExactNftDestinationSetAuthorityV2(
            lambda decision: broker.ExactNftDestinationSetReceiptV2(
                decision.projection_digest, decision.nft_destination_set,
                decision.nft_destination_set[0]))

    def upstream(self, transport, calls):
        def connector(address, host, port, timeout, context):
            calls.append((address, host, port, timeout, context))
            return transport
        return VerifiedHttpsUpstream(
            resolver=lambda _host: (_ for _ in ()).throw(
                AssertionError("effect re-resolved DNS")),
            connector=connector, clock=lambda: 0.0,
        )

    def test_exact_authorized_ip_original_sni_host_and_material_wipe_boundary(self):
        transport = Transport({
            "status": 200,
            "headers": {"Content-Type": "application/json", "Connection": "close"},
            "body": b"{}",
        })
        calls = []
        selected = broker.PinnedHTTPSCredentialEffectV2(
            self.upstream(transport, calls),
            destination_authority=self.authority(),
            descriptor_reader=lambda fd, size, offset: b"x" * size,
        )
        context = guest_protocol.authorized_context()
        result = selected.execute(context, 71)
        self.assertTrue(result.guest_result.ok)
        self.assertEqual(result.guest_result.headers, ())
        self.assertEqual(result.guest_result.body, b"")
        self.assertEqual(calls[0][0:3],
                         ("8.8.8.8", "api.example.com", 443))
        method, path, headers, body, _timeout = transport.calls[0]
        self.assertEqual((method, path, body),
                         ("POST", "/v1/items", b'{"ok":true}'))
        self.assertEqual(headers["host"], "api.example.com")
        self.assertEqual(headers["authorization"], "Bearer " + "x" * 32)
        self.assertEqual(transport.closed, 1)

    def test_exact_body_etag_and_allowlisted_reflection_never_cross_sbr2(self):
        credential = b"reflection-secret-material-1234"
        self.assertEqual(len(credential), 31)
        reflected = credential.decode("ascii")
        transport = Transport({
            "status": 200,
            "headers": {"ETag": reflected, "Content-Language": reflected,
                        "Cache-Control": "private," + reflected},
            "body": credential,
        })
        selected = broker.PinnedHTTPSCredentialEffectV2(
            self.upstream(transport, []),
            destination_authority=self.authority(),
            descriptor_reader=lambda _fd, size, _offset: credential[:size],
            wall_clock_ms=lambda: 1_800_000_000_000)
        context = dataclasses.replace(
            guest_protocol.authorized_context(), descriptor_size=len(credential))
        result = selected.execute(context, 71)
        packet = broker.encode_guest_result_v2(result.guest_result)
        self.assertNotIn(credential, packet)
        self.assertEqual(result.guest_result.headers, ())
        self.assertEqual(result.guest_result.body, b"")

    def test_non_2xx_is_completed_uncertainty_and_never_redirected_or_retried(self):
        for status in (302, 401, 500):
            with self.subTest(status=status):
                transport, calls = Transport({
                    "status": status, "headers": {}, "body": b"denied"}), []
                selected = broker.PinnedHTTPSCredentialEffectV2(
                    self.upstream(transport, calls),
                    destination_authority=self.authority(),
                    descriptor_reader=lambda _fd, size, _offset: b"x" * size)
                result = selected.execute(guest_protocol.authorized_context(), 71)
                self.assertEqual((result.outcome_class, result.effect_certainty,
                                  result.reason_code),
                                 ("indeterminate", "completed", "upstream_refused"))
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(transport.calls), 1)

    def test_post_entry_exception_is_indeterminate_and_not_replayable(self):
        calls = []
        selected = broker.PinnedHTTPSCredentialEffectV2(
            self.upstream(Transport({}), calls),
            destination_authority=self.authority(),
            descriptor_reader=lambda *_args: (_ for _ in ()).throw(OSError()))
        context = guest_protocol.authorized_context()
        with self.assertRaisesRegex(GuestProtocolV2Error, "effect_indeterminate"):
            selected.execute(context, 71)
        with self.assertRaisesRegex(GuestProtocolV2Error, "effect_replayed"):
            selected.execute(context, 71)

    def test_absolute_deadline_caps_effect_without_connect(self):
        calls = []
        context = guest_protocol.authorized_context()
        selected = broker.PinnedHTTPSCredentialEffectV2(
            self.upstream(Transport({}), calls),
            destination_authority=self.authority(),
            descriptor_reader=lambda _fd, size, _offset: b"x" * size,
            wall_clock_ms=lambda: context.lease_expires_at_unix_ms)
        result = selected.execute(context, 71)
        self.assertEqual((result.outcome_class, result.effect_certainty,
                          result.reason_code),
                         ("indeterminate", "possible", "deadline_exceeded"))
        self.assertEqual(calls, [])

    def test_resolve_once_authorizes_complete_set_and_rebinding_cannot_change_effect(self):
        target = guest_protocol.policy()
        projection = build_egress_projection_v2(target, guest_protocol.grants(target))
        projection = {**projection, "grants": [
            {**item, "destinations": list(item["destinations"]),
             "ports": list(item["ports"])} for item in projection["grants"]]}
        document = lifecycle.document(
            "broker", policy_digest=target.digest,
            egress_digest=projection["egress_digest"],
            egress_projection=projection,
            guest_transport_projection={
                **lifecycle.guest_transport_projection(target.digest),
                "machine_id": target.machine_id,
            },
        )
        document["own_config_digest"] = lifecycle.process_config_digest_v2(document)
        selected_plan = DerivedServiceConfigV2.derive(document)
        calls = []
        upstream = VerifiedHttpsUpstream(
            resolver=lambda host: calls.append(host) or
                ("93.184.216.34", "8.8.8.8"),
            connector=lambda *_args: None,
        )
        decision = broker.resolve_authorized_guest_egress_v2(
            selected_plan, guest_protocol.request(), upstream,
            dns_authority(("93.184.216.34", "8.8.8.8")),
            now="2026-08-28T00:00:00Z",
            deadline_unix_ms=1_800_000_030_000,
            wall_clock_ms=lambda: 1_800_000_000_000)
        self.assertEqual(calls, [])
        self.assertEqual(decision.resolved_addresses, ("8.8.8.8", "93.184.216.34"))
        upstream.resolver = lambda _host: ("1.1.1.1",)
        self.assertEqual(decision.resolved_addresses, ("8.8.8.8", "93.184.216.34"))
        denied = VerifiedHttpsUpstream(
            resolver=lambda _host: ("93.184.216.34", "1.1.1.1"),
            connector=lambda *_args: None)
        with self.assertRaisesRegex(Exception, "egress_denied"):
            broker.resolve_authorized_guest_egress_v2(
                selected_plan, guest_protocol.request(), denied,
                dns_authority(("93.184.216.34", "1.1.1.1")),
                now="2026-08-28T00:00:00Z",
                deadline_unix_ms=1_800_000_030_000,
                wall_clock_ms=lambda: 1_800_000_000_000)

    def test_dns_collection_is_capped_and_elapsed_deadline_is_fail_closed(self):
        target = guest_protocol.policy()
        projection = build_egress_projection_v2(target, guest_protocol.grants(target))
        projection = {**projection, "grants": [
            {**item, "destinations": list(item["destinations"]),
             "ports": list(item["ports"])} for item in projection["grants"]]}
        document = lifecycle.document(
            "broker", policy_digest=target.digest,
            egress_digest=projection["egress_digest"],
            egress_projection=projection,
            guest_transport_projection={
                **lifecycle.guest_transport_projection(target.digest),
                "machine_id": target.machine_id,
            })
        document["own_config_digest"] = lifecycle.process_config_digest_v2(document)
        selected_plan = DerivedServiceConfigV2.derive(document)
        excessive = VerifiedHttpsUpstream(
            resolver=lambda _host: (), connector=lambda *_args: None,
            clock=lambda: 0.0)
        with self.assertRaisesRegex(Exception, "egress_denied"):
            broker.resolve_authorized_guest_egress_v2(
                selected_plan, guest_protocol.request(), excessive,
                dns_authority(tuple("8.8.8.%d" % index for index in range(17))),
                now="2026-08-28T00:00:00Z",
                deadline_unix_ms=1_800_000_030_000,
                wall_clock_ms=lambda: 1_800_000_000_000)

        elapsed_values = iter((0.0, 5.0))
        elapsed = VerifiedHttpsUpstream(
            resolver=lambda _host: (), connector=lambda *_args: None,
            total_seconds=5.0, clock=lambda: next(elapsed_values))
        with self.assertRaisesRegex(Exception, "egress_denied"):
            broker.resolve_authorized_guest_egress_v2(
                selected_plan, guest_protocol.request(), elapsed,
                dns_authority(("8.8.8.8",)),
                now="2026-08-28T00:00:00Z",
                deadline_unix_ms=1_800_000_030_000,
                wall_clock_ms=lambda: 1_800_000_000_000)

        calls = []
        expired = VerifiedHttpsUpstream(
            resolver=lambda _host: calls.append(1) or (),
            connector=lambda *_args: None, clock=lambda: 0.0)
        with self.assertRaisesRegex(Exception, "egress_denied"):
            broker.resolve_authorized_guest_egress_v2(
                selected_plan, guest_protocol.request(), expired,
                dns_authority(("8.8.8.8",)),
                now="2026-08-28T00:00:00Z",
                deadline_unix_ms=1_800_000_000_000,
                wall_clock_ms=lambda: 1_800_000_000_000)
        self.assertEqual(calls, [])

    def test_dns_worker_caps_answers_and_stuck_cleanup_retains_authority(self):
        sent = []
        class Sender:
            def send(self, value): sent.append(value)
            def close(self): pass
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (f"8.8.8.{index}", 443))
            for index in range(10_000)]
        with mock.patch.object(
                broker.socket, "getaddrinfo", return_value=answers):
            broker._authorized_dns_worker_v2("api.example.com", Sender())
        self.assertTrue(sent[0][0])
        self.assertEqual(len(sent[0][1]), 17)

        context = DnsContext((), available=False, stuck=True, unkillable=True)
        authority = broker.AuthorizedDnsResolverV2(
            context=context, monotonic=lambda: 0.0)
        with self.assertRaisesRegex(Exception, "dns_cleanup_incomplete"):
            authority.resolve_until("api.example.com", 1.0)
        self.assertEqual(len(authority._active), 1)
        self.assertEqual(authority.close()["code"], "dns_cleanup_incomplete")

    def test_dns_deadline_boundary_refuses_before_process_start(self):
        context = DnsContext(("8.8.8.8",))
        authority = broker.AuthorizedDnsResolverV2(
            context=context, monotonic=lambda: 1.0)
        with self.assertRaisesRegex(Exception, "egress_denied"):
            authority.resolve_until("api.example.com", 1.0)
        self.assertEqual(context.processes, [])


class FakeOperations:
    def __init__(self):
        self.requests = []
        self.expired = []
        self.results = {}
        self.terminal = []

    def submit_typed_v2(self, request, *, connection_identity, now_ms):
        self.requests.append((request, connection_identity, now_ms))
        self.results[connection_identity] = {
            "ok": True, "state": "credential_pending", "code": "credential_pending",
            "correlation_id": request.correlation_id,
        }
        return {"ok": True, "code": "credential_pending",
                "correlation_id": request.correlation_id,
                "operation_id": "operation-012345"}

    def expire(self, now_ms): self.expired.append(now_ms)
    def state(self, _operation_id): return "pending"
    def terminalize_known(self, operation_id, code):
        self.terminal.append((operation_id, code)); return True
    def authorization_deadlines(self, _operation_id):
        return 1_800_000_000_000, 1
    def effect_context(self, _operation_id, *, now_ms):
        del now_ms
        return {"request": self.requests[0][0]}
    def connection_identity(self, _operation_id):
        return self.requests[0][1]


class FakeSession:
    def __init__(self):
        self.admission_open = True
        self.operations = FakeOperations()
        self.closed = 0
        self.cancel = None

    def guest_result_v2(self, identity, *, consume=False):
        value = self.operations.results[identity]
        if consume: self.operations.results.pop(identity, None)
        return value

    def close(self, _reason):
        self.closed += 1
        if self.cancel is not None:
            self.cancel.set()
        return {"ok": True, "code": "broker_controller_closed",
                "admission_open": False}


class TestCredentialBrokerServiceLoopV2(unittest.TestCase):
    def service(self, connection, *, selected_dns=None):
        selected_plan = plan()
        raw_listener = (QueuedListenerSocket(connection)
                        if type(connection) is list else ListenerSocket(connection))
        guest = broker.LinuxGuestV2Listener(
            selected_plan, topology_observer=lambda *_args: observation(),
            socket_factory=lambda *_args: raw_listener,
            clock=lambda: 1_800_000_000_000, so_bindtodevice=77)
        guest.start(platform="linux", effective_uid=993)
        exact_config = dataclasses.replace(
            authority.CONFIG,
            policy_digest=selected_plan.document["policy_digest"],
            egress_digest=selected_plan.document["egress_digest"],
            broker_digest=selected_plan.document["broker_digest"],
            proof_digest=selected_plan.document["proof_digest"],
            effective_isolation_digest=selected_plan.document[
                "effective_isolation_digest"],
            evidence_id=selected_plan.document["evidence_id"],
            broker=dataclasses.replace(
                authority.CONFIG.broker, uid=selected_plan.document["service_uid"],
                gid=selected_plan.document["service_gid"],
                executable_digest=selected_plan.document["executable_digest"],
                unit_digest=selected_plan.document["unit_digest"],
                config_digest=selected_plan.document["own_config_digest"]),
            controller=dataclasses.replace(
                authority.CONFIG.controller,
                executable_digest=selected_plan.document["peer_executable_digest"],
                unit_digest=selected_plan.document["peer_unit_digest"],
                config_digest=selected_plan.document["peer_config_digest"]),
        )
        controller = broker.LinuxControllerV2Listener(
            exact_config, epoch_factory=lambda: authority.BROKER_EPOCH,
            owner_factory=lambda: "broker-owner-0123456789",
            socket_factory=lambda *_args: None)
        controller.session = FakeSession()
        upstream = VerifiedHttpsUpstream(
            resolver=lambda _host: ("93.184.216.34",),
            connector=lambda *_args: None)
        executor = broker.PinnedHTTPSCredentialEffectV2(
            upstream,
            destination_authority=TestPinnedHTTPSCredentialEffectV2.authority(),
            descriptor_reader=lambda _fd, size, _offset: b"x" * size)
        service = broker.CredentialBrokerServiceLoopV2(
            selected_plan, controller, guest, upstream,
            selected_dns or dns_authority(("93.184.216.34",)), executor,
            clock=lambda: 1_800_000_000_000,
            now_text=lambda: "2026-08-28T00:00:00Z",
            observer=lambda *_args: exact_config.controller,
            descriptor_observer=lambda _fd: {}, descriptor_closer=lambda _fd: None,
            closer=lambda _fd: None,
            audit_id_factory=lambda kind: "audit-" + kind + "0123456789",
            monotonic=lambda: 0.0, so_peercred=1, so_passcred=2,
            scm_credentials=3, scm_rights=4,
            selector_factory=lambda: None,
        )
        return service, controller.session, raw_listener

    def test_dns_cleanup_failure_is_sticky_and_closes_epoch_admission(self):
        connection = GuestSocket(encode_guest_request_v2(guest_protocol.request()))
        context = DnsContext((), available=False, stuck=True, unkillable=True)
        selected_dns = broker.AuthorizedDnsResolverV2(
            context=context, monotonic=lambda: 0.0)
        service, session, _raw = self.service(
            connection, selected_dns=selected_dns)
        session.operations.authorization_deadlines = (
            lambda _operation_id: (1_800_000_030_000, 1))
        service.guest.set_admission(True)
        service.accept_guest_once()

        service._execute_operation("operation-012345")

        self.assertEqual(service.close()["code"], "dns_cleanup_incomplete")
        self.assertEqual(service.close()["code"], "dns_cleanup_incomplete")
        self.assertTrue(service._closed)
        self.assertFalse(service.guest.admission_open)
        self.assertEqual(session.closed, 1)
        self.assertEqual(session.operations.terminal,
                         [("operation-012345", "dns_cleanup_incomplete")])
        self.assertEqual(len(selected_dns._active), 1)
        self.assertEqual(service.guest._reservations, set())
        for _index in range(16):
            with self.assertRaisesRegex(Exception, "service_loop_closed"):
                service.accept_guest_async()
        self.assertEqual(len(selected_dns._active), 1)

    def test_pending_reader_reservations_refuse_seventeenth_without_thread(self):
        connections = [BlockingGuestSocket() for _index in range(17)]
        service, _session, raw_listener = self.service(connections)
        service.guest.set_admission(True)
        for _index in range(16):
            self.assertEqual(service.accept_guest_async()["code"],
                             "guest_read_pending")
        with self.assertRaisesRegex(Exception, "admission_closed"):
            service.accept_guest_async()
        self.assertEqual(raw_listener.accept_calls, 16)
        self.assertEqual(len(service.guest._reservations), 16)
        self.assertEqual(len(service._pending_guest_sockets), 16)
        self.assertEqual(len(service._reader_workers), 16)
        self.assertEqual(service.guest._active, {})

        self.assertTrue(service.close()["ok"])
        self.assertEqual(service._pending_guest_sockets, {})
        self.assertEqual(service._reader_workers, {})
        self.assertEqual(service.guest._active, {})
        self.assertEqual(service.guest._reservations, set())
        self.assertTrue(all(item.closed == 1 for item in connections[:16]))
        self.assertEqual(connections[16].closed, 0)

    def test_invalid_monotonic_retains_bad_close_until_service_cleanup(self):
        connection = BadCloseGuestSocket(failures=2)
        service, session, _raw_listener = self.service(connection)
        service.guest.set_admission(True)
        service._monotonic = lambda: {"hostile": "clock"}

        with self.assertRaisesRegex(Exception, "guest_socket_cleanup_failed"):
            service.accept_guest_async()

        self.assertTrue(service._closed)
        self.assertFalse(service.guest.admission_open)
        self.assertEqual(session.closed, 1)
        self.assertEqual(connection.close_attempts, 2)
        self.assertEqual(len(service._pending_guest_sockets), 1)
        self.assertEqual(len(service.guest._reservations), 1)
        self.assertEqual(service._reader_workers, {})

        result = service.close()
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "guest_socket_cleanup_failed")
        self.assertEqual(connection.close_attempts, 3)
        self.assertEqual(connection.closed, 1)
        self.assertEqual(service._pending_guest_sockets, {})
        self.assertEqual(service.guest._reservations, set())
        self.assertEqual(service._reader_workers, {})
        self.assertEqual(service.close(), result)
        self.assertEqual(connection.close_attempts, 3)

    def test_activation_gated_typed_admission_terminal_delivery_and_cleanup(self):
        connection = GuestSocket(encode_guest_request_v2(guest_protocol.request()))
        service, session, raw_listener = self.service(connection)
        service.guest.set_admission(True)
        admitted = service.accept_guest_once()
        self.assertEqual(admitted["operation_id"], "operation-012345")
        request, identity, received = session.operations.requests[0]
        self.assertIs(type(request), type(guest_protocol.request()))
        self.assertEqual(received, 1_800_000_000_000)
        session.operations.results[identity] = {
            "ok": False, "state": "refused", "code": "revoked",
            "correlation_id": request.correlation_id,
        }
        self.assertEqual(service.synchronize(), 1)
        self.assertEqual(connection.sent[0][:5], b"SBR2\x02")
        self.assertEqual(connection.closed, 1)
        self.assertTrue(service.close()["ok"])
        self.assertEqual(raw_listener.closed, 1)

    def test_tick_tracks_activation_and_expiry_without_reopening_closed_session(self):
        service, session, _raw = self.service(
            GuestSocket(encode_guest_request_v2(guest_protocol.request())))
        self.assertTrue(service.tick(1_800_000_000_000)["admission_open"])
        self.assertEqual(session.operations.expired, [1_800_000_000_000])
        session.admission_open = False
        self.assertFalse(service.tick(1_800_000_000_001)["admission_open"])
        service.close()
        with self.assertRaisesRegex(Exception, "service_loop_closed"):
            service.tick(1_800_000_000_002)

    def test_guest_disconnect_closes_owned_socket_and_terminalizes_operation_once(self):
        connection = GuestSocket(encode_guest_request_v2(guest_protocol.request()))
        service, session, _raw = self.service(connection)
        service.guest.set_admission(True)
        service.accept_guest_once()
        identity = session.operations.requests[0][1]
        service.guest_disconnected(identity)
        service.guest_disconnected(identity)
        self.assertEqual(connection.closed, 1)
        self.assertEqual(session.operations.terminal,
                         [("operation-012345", "guest_disconnected")])

    def test_slow_guest_reader_does_not_block_loop_tick_and_close_joins_it(self):
        connection = BlockingGuestSocket()
        service, _session, _raw = self.service(connection)
        service.guest.set_admission(True)
        service.accept_guest_async()
        self.assertEqual(service.tick(1_800_000_000_001)["code"],
                         "service_loop_ready")
        result = service.close()
        self.assertTrue(result["ok"])
        self.assertEqual(connection.closed, 1)
        self.assertEqual(service._reader_workers, {})

    def test_close_cancels_and_joins_effect_worker_before_releasing_ownership(self):
        service, session, _raw = self.service(
            GuestSocket(encode_guest_request_v2(guest_protocol.request())))
        cancel = threading.Event()
        exited = threading.Event()
        session.cancel = cancel
        worker = threading.Thread(
            target=lambda: (cancel.wait(), exited.set()),
            name="credential-effect-v2-blocked")
        service._workers["operation-012345"] = worker
        worker.start()
        result = service.close()
        self.assertTrue(result["ok"])
        self.assertTrue(exited.is_set())
        self.assertFalse(worker.is_alive())
        self.assertFalse(worker.daemon)
        self.assertEqual(service._workers, {})

    def test_uncooperative_effect_worker_remains_owned_as_cleanup_incomplete(self):
        service, _session, _raw = self.service(
            GuestSocket(encode_guest_request_v2(guest_protocol.request())))
        release = threading.Event()
        worker = threading.Thread(
            target=release.wait, name="credential-effect-v2-uncooperative")
        service._workers["operation-012345"] = worker
        worker.start()
        result = service.close()
        self.assertEqual(result["code"], "cleanup_incomplete")
        self.assertIs(service._workers["operation-012345"], worker)
        release.set()
        worker.join(1)
        service.close()
        self.assertEqual(service._workers, {})

if __name__ == "__main__":
    unittest.main()
