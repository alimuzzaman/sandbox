import unittest
from unittest import mock

from sandbox.isolation.credential_controller_protocol_v2 import (
    AuthorizationIdentity,
    AuthorizationRegistry,
    ProtocolV2Error,
    REVIEWED_REGISTRY,
    digest_document,
)
from sandbox.isolation.credential_controller_service_v2 import ControllerServiceV2Error
from tests.test_credential_controller_service_v2 import (
    BROKER_EPOCH, CONFIG, CONTROLLER, CONTROLLER_EPOCH, DIGESTS, MACHINE, NOW,
    SCM_CREDENTIALS, SCM_RIGHTS, SO_PEERCRED, FakeConnection, FakeListener, ack,
    broker, frame, observer_for, self_observer_for,
)


OWNER = "broker-session-0123456789"


def authenticated_session():
    connection = FakeConnection(CONTROLLER, [frame(ack(), "controller_to_broker")])
    terminal = []
    session = broker.BrokerControllerV2Connection(
        connection, CONFIG, BROKER_EPOCH, OWNER, on_terminal=terminal.append,
    )
    session.handshake(
        observer=observer_for(CONTROLLER), now_ms=NOW,
        monotonic=iter((1.0, 1.1)).__next__, so_peercred=SO_PEERCRED,
        scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
        closer=lambda _fd: None,
    )
    return session, connection, terminal


def activation(sequence=2, controller_epoch=CONTROLLER_EPOCH):
    result = {
        "protocol": broker.CONTROLLER_PROTOCOL_V2, "type": "ACTIVATE_V2",
        "machine_id": MACHINE, "broker_epoch": BROKER_EPOCH,
        "controller_epoch": controller_epoch, "sequence": sequence,
        **CONFIG.configured_digests(),
        "activation_digest": DIGESTS[8],
        "activation_expires_at_unix_ms": NOW + 20_000,
    }
    document = {
        name: result["sequence" if name == "request_sequence" else name]
        for name in REVIEWED_REGISTRY["digest_documents"]["activation_digest"]
    }
    result["activation_digest"] = digest_document("activation_digest", document)
    return result


def authorization(index):
    return AuthorizationIdentity(
        owner=OWNER, machine_id=MACHINE, broker_epoch=BROKER_EPOCH,
        controller_epoch=CONTROLLER_EPOCH,
        operation_id=f"operation-{index:06d}", request_digest=DIGESTS[0],
        binding_id=f"binding-{index:08d}", binding_version=1,
        decision_id=f"decision-{index:07d}", authorization_digest=DIGESTS[1],
        expires_at_unix_ms=NOW + 4_000,
        binding_expires_at_unix_ms=NOW + 20_000,
        activation_expires_at_unix_ms=NOW + 10_000,
        request_deadline_unix_ms=NOW + 8_000,
    )


class TestCredentialBrokerConnectionV2(unittest.TestCase):
    def test_broker_cleanup_failure_is_stored_across_disconnect_and_close(self):
        class CloseFailure(FakeConnection):
            def close(self):
                self.closed += 1
                raise RuntimeError("hostile close detail")

        connection = CloseFailure(CONTROLLER, [])
        listener = FakeListener((connection,))
        endpoint = broker.LinuxControllerV2Listener(
            CONFIG, epoch_factory=lambda: BROKER_EPOCH,
            owner_factory=lambda: OWNER, socket_factory=lambda *_args: listener,
        )
        with mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
            endpoint.start(
                platform="linux", enabled=True, effective_uid=CONFIG.broker.uid,
                self_observer=self_observer_for(),
            )
        with self.assertRaises(ControllerServiceV2Error) as raised:
            endpoint.accept_once(
                observer=observer_for(CONTROLLER), now_ms=NOW,
                monotonic=lambda: (_ for _ in ()).throw(RuntimeError("clock")),
                so_peercred=SO_PEERCRED, scm_credentials=SCM_CREDENTIALS,
                scm_rights=SCM_RIGHTS, closer=lambda _fd: None,
            )
        self.assertEqual(str(raised.exception), "controller_socket_cleanup_failed")
        expected = {"ok": False, "code": "controller_socket_cleanup_failed",
                    "admission_open": False}
        self.assertEqual(endpoint.disconnect(), expected)
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "controller_socket_cleanup_failed"):
            endpoint.accept_once(
                observer=observer_for(CONTROLLER), now_ms=NOW,
                monotonic=lambda: 2.0, so_peercred=SO_PEERCRED,
                scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
                closer=lambda _fd: None,
            )
        self.assertEqual(endpoint.close(), expected)
        self.assertEqual(endpoint.close(), expected)
        self.assertEqual(connection.closed, 1)
        self.assertEqual(listener.closed, 1)

    def test_listener_close_preserves_registry_disconnect_failure_through_cleanup(self):
        class MismatchRegistry(AuthorizationRegistry):
            disconnects = 0
            quiesces = 0

            def disconnect(self, **_kwargs):
                type(self).disconnects += 1
                raise ProtocolV2Error("authorization_registry_identity_mismatch")

            def quiesce(self):
                type(self).quiesces += 1
                return super().quiesce()

        connection = FakeConnection(CONTROLLER, [frame(ack(), "controller_to_broker")])
        listener = FakeListener((connection,))
        endpoint = broker.LinuxControllerV2Listener(
            CONFIG, epoch_factory=lambda: BROKER_EPOCH,
            owner_factory=lambda: OWNER, socket_factory=lambda *_args: listener,
        )
        with mock.patch.object(broker.socket, "SO_PASSCRED", 16, create=True):
            endpoint.start(
                platform="linux", enabled=True, effective_uid=CONFIG.broker.uid,
                self_observer=self_observer_for(),
            )
        endpoint.accept_once(
            observer=observer_for(CONTROLLER), now_ms=NOW,
            monotonic=iter((1.0, 1.1)).__next__, so_peercred=SO_PEERCRED,
            scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
            closer=lambda _fd: None,
            registry_factory=lambda **kwargs: MismatchRegistry(**kwargs),
        )
        first = endpoint.close()
        second = endpoint.close()
        self.assertEqual(first, {"ok": False, "code": "registry_disconnect_refused",
                                 "admission_open": False})
        self.assertEqual(second, first)
        self.assertEqual(MismatchRegistry.disconnects, 1)
        self.assertEqual(MismatchRegistry.quiesces, 1)
        self.assertEqual(connection.closed, 1)
        self.assertEqual(listener.closed, 1)

    def test_connection_pins_one_clock_observer_and_rollback_closes(self):
        session, connection, terminal = authenticated_session()
        pinned = session._observation
        connection.packets.extend((
            frame(activation(2), "controller_to_broker"),
            frame(activation(3), "controller_to_broker"),
        ))
        session.receive_frame(
            observer=observer_for(CONTROLLER), now_ms=NOW + 100,
            so_peercred=SO_PEERCRED, scm_credentials=SCM_CREDENTIALS,
            scm_rights=SCM_RIGHTS, closer=lambda _fd: None,
        )
        self.assertIs(session._observation, pinned)
        with self.assertRaisesRegex(ControllerServiceV2Error,
                                    "controller_frame_refused"):
            session.receive_frame(
                observer=observer_for(CONTROLLER), now_ms=NOW - 1_000,
                so_peercred=SO_PEERCRED, scm_credentials=SCM_CREDENTIALS,
                scm_rights=SCM_RIGHTS, closer=lambda _fd: None,
            )
        self.assertIs(session._observation, pinned)
        self.assertFalse(session.authenticated)
        self.assertEqual(len(terminal), 1)

    def test_disconnect_uses_exact_registry_tuple_once_then_quiesces(self):
        class TrackingRegistry(AuthorizationRegistry):
            calls = []
            quiesces = 0
            mismatch = False

            def disconnect(self, **kwargs):
                type(self).calls.append(dict(kwargs))
                if type(self).mismatch:
                    raise ProtocolV2Error("authorization_registry_identity_mismatch")
                return super().disconnect(**kwargs)

            def quiesce(self):
                type(self).quiesces += 1
                return super().quiesce()

        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch):
                TrackingRegistry.calls = []
                TrackingRegistry.quiesces = 0
                TrackingRegistry.mismatch = mismatch
                connection = FakeConnection(
                    CONTROLLER, [frame(ack(), "controller_to_broker")],
                )
                session = broker.BrokerControllerV2Connection(
                    connection, CONFIG, BROKER_EPOCH, OWNER,
                    registry_factory=lambda **kwargs: TrackingRegistry(**kwargs),
                )
                session.handshake(
                    observer=observer_for(CONTROLLER), now_ms=NOW,
                    monotonic=iter((1.0, 1.1)).__next__, so_peercred=SO_PEERCRED,
                    scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
                    closer=lambda _fd: None,
                )
                first_close = session.close("controller_eof")
                second_close = session.close("again")
                self.assertEqual(TrackingRegistry.calls, [{
                    "machine_id": MACHINE, "broker_epoch": BROKER_EPOCH,
                    "controller_epoch": CONTROLLER_EPOCH, "owner": OWNER,
                }])
                self.assertEqual(TrackingRegistry.quiesces, 1)
                self.assertFalse(session.authenticated)
                self.assertFalse(session.admission_open)
                expected_code = ("registry_disconnect_refused" if mismatch
                                 else "broker_controller_closed")
                self.assertEqual(first_close["code"], expected_code)
                self.assertEqual(second_close["code"], expected_code)

    def test_identity_bounds_and_hostile_terminal_callback_stay_bounded(self):
        with self.assertRaisesRegex(ControllerServiceV2Error, "process_identity_invalid"):
            type(CONTROLLER)(
                uid=2**31, gid=CONTROLLER.gid, pid=CONTROLLER.pid,
                start_ticks=CONTROLLER.start_ticks,
                executable_digest=CONTROLLER.executable_digest,
                unit_digest=CONTROLLER.unit_digest,
                config_digest=CONTROLLER.config_digest,
            )
        connection = FakeConnection(CONTROLLER, [frame(ack(), "controller_to_broker")])
        session = broker.BrokerControllerV2Connection(
            connection, CONFIG, BROKER_EPOCH, OWNER,
            on_terminal=lambda _reason: (_ for _ in ()).throw(RuntimeError("untrusted")),
        )
        session.handshake(
            observer=observer_for(CONTROLLER), now_ms=NOW,
            monotonic=iter((1.0, 1.1)).__next__, so_peercred=SO_PEERCRED,
            scm_credentials=SCM_CREDENTIALS, scm_rights=SCM_RIGHTS,
            closer=lambda _fd: None,
        )
        self.assertEqual(session.close()["code"], "terminal_callback_failed")

    def test_post_handshake_sequences_start_at_two_and_are_independent(self):
        session, connection, terminal = authenticated_session()
        connection.packets.append(frame(activation(2), "controller_to_broker"))
        received = session.receive_frame(
            observer=observer_for(CONTROLLER), now_ms=NOW,
            so_peercred=SO_PEERCRED, scm_credentials=SCM_CREDENTIALS,
            scm_rights=SCM_RIGHTS, closer=lambda _fd: None,
        )
        self.assertEqual(received["sequence"], 2)
        # Broker-to-controller remains independently ready for sequence 2.
        session.sequences.accept("broker_to_controller", 2)
        self.assertFalse(session.sequences.closed)
        self.assertEqual(terminal, [])

    def test_duplicate_skipped_and_cross_epoch_frames_terminalize_once(self):
        mutations = (
            activation(3),
            activation(2, controller_epoch="03" * 16),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                session, connection, terminal = authenticated_session()
                connection.packets.append(frame(mutation, "controller_to_broker"))
                original = session.registry
                with self.assertRaises(ControllerServiceV2Error):
                    session.receive_frame(
                        observer=observer_for(CONTROLLER), now_ms=NOW,
                        so_peercred=SO_PEERCRED, scm_credentials=SCM_CREDENTIALS,
                        scm_rights=SCM_RIGHTS, closer=lambda _fd: None,
                    )
                session.close()
                self.assertIs(session.registry, original)
                self.assertFalse(session.admission_open)
                self.assertEqual(len(terminal), 1)
                self.assertEqual(connection.closed, 1)

    def test_capacity_closure_never_reconstructs_registry(self):
        session, _connection, _terminal = authenticated_session()
        original = session.registry
        for index in range(16):
            session.registry.insert(authorization(index), now_ms=NOW)
        with self.assertRaisesRegex(ProtocolV2Error, "authorization_capacity"):
            session.registry.insert(authorization(16), now_ms=NOW)
        self.assertIs(session.registry, original)
        self.assertTrue(session.authenticated)
        self.assertFalse(session.admission_open)

    def test_disconnect_quiesces_registry_and_closes_each_surface_once(self):
        session, connection, terminal = authenticated_session()
        session.registry.insert(authorization(0), now_ms=NOW)
        self.assertEqual(session.close("controller_eof")["admission_open"], False)
        session.close("again")
        self.assertEqual(len(session.registry), 0)
        self.assertEqual(connection.closed, 1)
        self.assertEqual(terminal, ["controller_eof"])


if __name__ == "__main__":
    unittest.main()
