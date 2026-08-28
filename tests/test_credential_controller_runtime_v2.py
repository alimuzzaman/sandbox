from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest import mock

from sandbox.isolation.credential_controller_protocol_v2 import (
    LEASE_ACK_BYTES, LEASE_FRAME_BYTES,
)
from sandbox.isolation.credential_controller_runtime_v2 import (
    ControllerRoleRuntimeV2,
    fixed_controller_connector_v2,
    parse_proc_net_unix_v2,
    wait_for_exact_controller_listener_v2,
)
from sandbox.isolation.credential_controller_service_v2 import (
    ControllerServiceV2Error,
    abstract_controller_address,
)
from sandbox.isolation.credential_guest_protocol_v2 import GUEST_PROTOCOL_REGISTRY
from sandbox.isolation.credential_service_runtime_v2 import (
    load_runtime_config_v2,
    pin_process_identity_v2,
    runtime_config_path_v2,
)
from tests.test_credential_controller_lifecycle_v2 import (
    TestCredentialControllerLifecycleV2,
)


def controller_script():
    path = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
            / "native-credential-controller.py")
    spec = importlib.util.spec_from_file_location("native_credential_controller", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepared():
    fixture = TestCredentialControllerLifecycleV2(
        methodName="test_exact_fixed_verbs_and_controller_first_start_broker_first_stop")
    lifecycle, _executor = fixture.plans()
    return MappingProxyType({
        "controller_plan": lifecycle.controller,
        "broker_plan": lifecycle.broker,
        "controller_identity": lifecycle.session.config.controller,
        "controller_observer": lambda _pid, _uid, _gid: lifecycle.session.config.controller,
        "broker_identity_pinner": lambda _plan: lifecycle.session.config.broker,
    })


class ConfigKernel:
    def __init__(self, payload, *, mode=stat.S_IFREG | 0o640, uid=0, gid=2001):
        self.payload = payload
        self.offset = 0
        self.closed = []
        self.observed_flags = None
        self.info = SimpleNamespace(st_mode=mode, st_uid=uid, st_gid=gid,
                                    st_size=len(payload))

    def open(self, _path, flags):
        self.observed_flags = flags
        return 11

    def fstat(self, _descriptor): return self.info
    def read(self, _descriptor, size):
        value = self.payload[self.offset:self.offset + size]
        self.offset += len(value)
        return value
    def close(self, descriptor): self.closed.append(descriptor)


class TestCredentialServiceRuntimeV2(unittest.TestCase):
    def test_fixed_nofollow_loader_accepts_only_exact_canonical_role(self):
        plan = prepared()["controller_plan"]
        kernel = ConfigKernel(plan.canonical_bytes)
        loaded = load_runtime_config_v2(
            runtime_config_path_v2(plan.machine_id, "controller"),
            machine_id=plan.machine_id, component="controller",
            expected_group_gid=plan.service_gid, kernel=kernel)
        self.assertEqual(loaded.config_digest, plan.config_digest)
        self.assertTrue(kernel.observed_flags & getattr(os, "O_NOFOLLOW", 0))
        self.assertEqual(kernel.closed, [11])
        for change in (
            {"mode": stat.S_IFLNK | 0o640}, {"uid": 1}, {"gid": 1},
        ):
            bad = ConfigKernel(plan.canonical_bytes, **change)
            with self.subTest(change=change), self.assertRaisesRegex(
                    ControllerServiceV2Error, "runtime_config_invalid"):
                load_runtime_config_v2(
                    runtime_config_path_v2(plan.machine_id, "controller"),
                    machine_id=plan.machine_id, component="controller",
                    expected_group_gid=plan.service_gid, kernel=bad)

    def test_process_pin_rechecks_start_ticks_and_exact_identity(self):
        plan = prepared()["controller_plan"]
        details = {
            "uid": plan.document["service_uid"], "gid": plan.document["service_gid"],
            "executable_digest": plan.document["executable_digest"],
            "unit_digest": plan.document["unit_digest"],
            "config_digest": plan.document["own_config_digest"],
        }
        ticks = iter((10, 11))
        with self.assertRaisesRegex(ControllerServiceV2Error, "peer_identity_mismatch"):
            pin_process_identity_v2(
                plan, cgroup_pid_reader=lambda _plan: 44,
                start_reader=lambda _pid: next(ticks),
                detail_reader=lambda _pid, _plan: details)

    def test_proc_parser_requires_one_exact_listening_seqpacket(self):
        address = abstract_controller_address("sb-0123456789ab", "d" * 64)
        name = "@" + address[1:].decode("ascii")
        header = "Num RefCount Protocol Flags Type St Inode Path\n"
        row = f"000: 2 0 00010000 0005 01 12345 {name}\n"
        self.assertTrue(parse_proc_net_unix_v2(header + row, address))
        self.assertFalse(parse_proc_net_unix_v2(header + row + row, address))
        self.assertFalse(parse_proc_net_unix_v2(
            header + row.replace("0005", "0001"), address))
        self.assertFalse(parse_proc_net_unix_v2(
            header + row.replace("00010000", "00000000"), address))
        with self.assertRaisesRegex(ControllerServiceV2Error, "listener_unavailable"):
            parse_proc_net_unix_v2("bad", address)
        with self.assertRaisesRegex(ControllerServiceV2Error, "listener_unavailable"):
            parse_proc_net_unix_v2("\ud800", address)

    def test_listener_wait_is_bounded_and_does_not_connect(self):
        address = abstract_controller_address("sb-0123456789ab", "d" * 64)
        name = "@" + address[1:].decode("ascii")
        header = "Num RefCount Protocol Flags Type St Inode Path\n"
        row = f"000: 2 0 00010000 0005 01 12345 {name}\n"
        reads = iter((header, header, header + row))
        waits = []
        clock = iter((1.0, 1.1, 1.2)).__next__
        self.assertTrue(wait_for_exact_controller_listener_v2(
            "sb-0123456789ab", "d" * 64, reader=lambda: next(reads),
            monotonic=clock, deadline=2.0, wait=waits.append))
        self.assertEqual(len(waits), 2)
        huge = 10 ** 10000
        class HostileInt(int):
            def __float__(self): raise RuntimeError("raw secret")
        class HostileFloat(float):
            def __float__(self): raise RuntimeError("raw secret")
        hostile = (HostileInt(1), HostileFloat(1.0))
        for deadline in (float("nan"), float("inf"), float("-inf"), huge, -huge,
                         *hostile):
            with self.subTest(deadline=deadline), self.assertRaisesRegex(
                    ControllerServiceV2Error, "listener_unavailable"):
                wait_for_exact_controller_listener_v2(
                    "sb-0123456789ab", "d" * 64, reader=lambda: header,
                    monotonic=lambda: 1.0, deadline=deadline, wait=lambda _value: None)
        for clock in (float("nan"), float("inf"), float("-inf"), huge, -huge,
                      *hostile):
            with self.subTest(clock=clock), self.assertRaisesRegex(
                    ControllerServiceV2Error, "listener_unavailable"):
                wait_for_exact_controller_listener_v2(
                    "sb-0123456789ab", "d" * 64, reader=lambda: header,
                    monotonic=lambda: clock, deadline=2.0, wait=lambda _value: None)

    def test_fixed_connector_sets_and_verifies_passcred_and_closes_failures(self):
        import sandbox.isolation.credential_controller_runtime_v2 as runtime_module

        class Connection:
            def __init__(self, observed=1): self.observed = observed; self.closed = 0; self.set = []
            def setsockopt(self, *args): self.set.append(args)
            def getsockopt(self, *_args): return self.observed
            def close(self): self.closed += 1

        accepted = Connection()
        refused = Connection(0)
        boolean = Connection(True)
        with mock.patch.object(runtime_module.socket, "SO_PASSCRED", 16, create=True):
            self.assertIs(fixed_controller_connector_v2(
                runtime_module.socket.AF_UNIX, runtime_module.socket.SOCK_SEQPACKET, 0,
                socket_factory=lambda *_args: accepted), accepted)
            self.assertEqual(accepted.set, [(runtime_module.socket.SOL_SOCKET, 16, 1)])
            with self.assertRaisesRegex(ControllerServiceV2Error, "connection_refused"):
                fixed_controller_connector_v2(
                    runtime_module.socket.AF_UNIX,
                    runtime_module.socket.SOCK_SEQPACKET, 0,
                    socket_factory=lambda *_args: refused)
            with self.assertRaisesRegex(ControllerServiceV2Error, "connection_refused"):
                fixed_controller_connector_v2(
                    runtime_module.socket.AF_UNIX,
                    runtime_module.socket.SOCK_SEQPACKET, 0,
                    socket_factory=lambda *_args: boolean)
        self.assertEqual(refused.closed, 1)
        self.assertEqual(boolean.closed, 1)

    def test_default_listener_deadline_rejects_nonfinite_initial_clock(self):
        selected = prepared()
        huge = 10 ** 10000
        class HostileInt(int):
            def __float__(self): raise RuntimeError("raw secret")
        class HostileFloat(float):
            def __float__(self): raise RuntimeError("raw secret")
        for clock in (float("nan"), float("inf"), float("-inf"), huge, -huge,
                      HostileInt(1), HostileFloat(1.0)):
            runtime = ControllerRoleRuntimeV2(selected, provider=None)
            with self.subTest(clock=clock), self.assertRaisesRegex(
                    ControllerServiceV2Error, "listener_unavailable"):
                runtime.start_closed(
                    platform="linux",
                    effective_uid=selected["controller_identity"].uid,
                    effective_gid=selected["controller_identity"].gid,
                    listener_reader=lambda: "", connector=lambda *_args: None,
                    now_ms=1700000000000, listener_monotonic=lambda: clock,
                    so_peercred=1, scm_credentials=2, scm_rights=3)

    def test_wire_and_guest_tags_remain_v2_fixed(self):
        self.assertEqual((LEASE_FRAME_BYTES, LEASE_ACK_BYTES), (732, 444))
        self.assertEqual(GUEST_PROTOCOL_REGISTRY["envelopes"]["request_magic"], "SBG2")
        self.assertEqual(GUEST_PROTOCOL_REGISTRY["envelopes"]["result_magic"], "SBR2")


class TestControllerRoleRuntimeV2(unittest.TestCase):
    def test_closed_start_observes_then_connects_once_and_never_activates(self):
        events = []

        class Session: authenticated = True
        class Service:
            def __init__(self, *_args, **_kwargs): self.session = None
            def start(self, **_kwargs): events.append("controller_start"); return {"ok": True}
            def connect(self, **_kwargs):
                events.append("connect")
                self.session = Session()
                return {"ok": True}
            def stop(self):
                events.append("stop")
                return {"ok": True, "code": "controller_stopped",
                        "admission_open": False}

        selected = prepared()
        original_broker_pinner = selected["broker_identity_pinner"]
        selected = MappingProxyType({
            **dict(selected),
            "broker_identity_pinner": lambda plan: (
                events.append("pin_broker"), original_broker_pinner(plan))[1],
        })
        address = abstract_controller_address(
            selected["controller_plan"].machine_id,
            selected["broker_plan"].document["broker_digest"])
        table = ("Num RefCount Protocol Flags Type St Inode Path\n"
                 f"000: 2 0 00010000 0005 01 12345 @"
                 f"{address[1:].decode('ascii')}\n")
        import sandbox.isolation.credential_controller_runtime_v2 as runtime_module
        with mock.patch.object(runtime_module, "PersistentControllerService", Service), \
                mock.patch.object(runtime_module, "ControllerLifecycleAuthorityV2",
                                  return_value=SimpleNamespace()), \
                mock.patch.object(runtime_module.os, "getpid", return_value=41):
            runtime = ControllerRoleRuntimeV2(selected, provider=None)
            original = runtime_module.wait_for_exact_controller_listener_v2
            with mock.patch.object(
                    runtime_module, "wait_for_exact_controller_listener_v2",
                    side_effect=lambda *args, **kwargs: (
                        events.append("observe"), original(
                            *args, **kwargs))[1]):
                result = runtime.start_closed(
                    platform="linux",
                    effective_uid=selected["controller_identity"].uid,
                    effective_gid=selected["controller_identity"].gid,
                    listener_reader=lambda: table, connector=lambda *_args: None,
                    now_ms=1700000000000, so_peercred=1,
                    scm_credentials=2, scm_rights=3)
        self.assertEqual(events, ["observe", "pin_broker", "controller_start", "connect"])
        self.assertFalse(result["admission_open"])
        self.assertFalse(result["authorities_ready"])
        self.assertIsNone(runtime.operation_authority)
        self.assertIsNone(runtime.audit_authority)

    def test_observation_connect_race_is_terminal_and_not_retried(self):
        calls = []

        class Service:
            session = None
            def __init__(self, *_args, **_kwargs): pass
            def start(self, **_kwargs): return {"ok": True}
            def connect(self, **_kwargs): calls.append("connect"); raise OSError("raw")
            def stop(self):
                calls.append("stop")
                return {"ok": True, "code": "controller_stopped",
                        "admission_open": False}

        selected = prepared()
        import sandbox.isolation.credential_controller_runtime_v2 as runtime_module
        with mock.patch.object(runtime_module, "PersistentControllerService", Service), \
                mock.patch.object(runtime_module.os, "getpid", return_value=41), \
                mock.patch.object(runtime_module, "wait_for_exact_controller_listener_v2",
                                  return_value=True):
            runtime = ControllerRoleRuntimeV2(selected, provider=None)
            with self.assertRaisesRegex(ControllerServiceV2Error, "start_refused"):
                runtime.start_closed(
                    platform="linux",
                    effective_uid=selected["controller_identity"].uid,
                    effective_gid=selected["controller_identity"].gid,
                    listener_reader=lambda: "", connector=lambda *_args: None,
                    now_ms=1700000000000, so_peercred=1,
                    scm_credentials=2, scm_rights=3)
        self.assertEqual(calls, ["connect", "stop"])

    def test_real_t040_handshake_accepts_exact_default_session_owner(self):
        from tests import test_credential_controller_service_v2 as t040
        import sandbox.isolation.credential_controller_runtime_v2 as runtime_module

        class Plan:
            def __init__(self, component, identity):
                self.machine_id = t040.MACHINE
                self.canonical_bytes = component.encode("ascii")
                self.document = {
                    "service_uid": identity.uid, "service_gid": identity.gid,
                    "policy_digest": t040.CONFIG.policy_digest,
                    "egress_digest": t040.CONFIG.egress_digest,
                    "broker_digest": t040.CONFIG.broker_digest,
                    "proof_digest": t040.CONFIG.proof_digest,
                    "effective_isolation_digest": t040.CONFIG.effective_isolation_digest,
                    "evidence_id": t040.CONFIG.evidence_id,
                }

        selected = MappingProxyType({
            "controller_plan": Plan("controller", t040.CONTROLLER),
            "broker_plan": Plan("broker", t040.BROKER),
            "controller_identity": t040.CONTROLLER,
            "controller_observer": t040.observer_for(t040.CONTROLLER),
            "broker_identity_pinner": lambda _plan: t040.BROKER,
        })
        class CredentialConnection(t040.FakeConnection):
            def __init__(self, *args): super().__init__(*args); self.passcred = 0
            def setsockopt(self, _level, option, value):
                if option != 16: raise OSError
                self.passcred = value
            def getsockopt(self, level, option, *args):
                if option == 16 and not args: return self.passcred
                return super().getsockopt(level, option, *args)

        connection = CredentialConnection(
            t040.BROKER, [t040.frame(t040.hello(), "broker_to_controller")])
        with mock.patch.object(runtime_module.os, "getpid", return_value=t040.CONTROLLER.pid), \
                mock.patch.object(runtime_module.socket, "SO_PASSCRED", 16, create=True), \
                mock.patch.object(runtime_module, "wait_for_exact_controller_listener_v2",
                                  return_value=True):
            runtime = ControllerRoleRuntimeV2(
                selected, provider=None,
                epoch_factory=lambda: t040.CONTROLLER_EPOCH)
            result = runtime.start_closed(
                platform="linux", effective_uid=t040.CONTROLLER.uid,
                effective_gid=t040.CONTROLLER.gid, listener_reader=lambda: "",
                connector=lambda *args: fixed_controller_connector_v2(
                    *args, socket_factory=lambda *_socket_args: connection),
                now_ms=t040.NOW,
                monotonic=iter((1.0, 1.1, 1.2)).__next__,
                so_peercred=t040.SO_PEERCRED,
                scm_credentials=t040.SCM_CREDENTIALS,
                scm_rights=t040.SCM_RIGHTS, listener_deadline=2.0)
        self.assertTrue(result["ok"])
        self.assertRegex(runtime.service.session.owner,
                         r"^controller-session-[0-9a-f]{16}$")
        self.assertTrue(runtime.service.session.authenticated)
        self.assertEqual(connection.passcred, 1)

    def test_cleanup_failure_is_sticky_exact_once_and_first_error_wins(self):
        selected = prepared()
        calls = []

        class Service:
            session = None
            def __init__(self, *_args, **_kwargs): pass
            def start(self, **_kwargs): raise ControllerServiceV2Error("controller_start_refused")
            def stop(self): calls.append("stop"); raise RuntimeError("raw secret")

        import sandbox.isolation.credential_controller_runtime_v2 as runtime_module
        with mock.patch.object(runtime_module, "PersistentControllerService", Service), \
                mock.patch.object(runtime_module.os, "getpid", return_value=41), \
                mock.patch.object(runtime_module, "wait_for_exact_controller_listener_v2",
                                  return_value=True):
            runtime = ControllerRoleRuntimeV2(selected, provider=None)
            with self.assertRaisesRegex(ControllerServiceV2Error, "controller_start_refused"):
                runtime.start_closed(
                    platform="linux", effective_uid=selected["controller_identity"].uid,
                    effective_gid=selected["controller_identity"].gid,
                    listener_reader=lambda: "", connector=lambda *_args: None,
                    now_ms=1700000000000, so_peercred=1,
                    scm_credentials=2, scm_rights=3)
            self.assertEqual(runtime.stop()["code"], "controller_start_refused")
        self.assertEqual(calls, ["stop"])

    def test_malformed_cleanup_result_is_bounded_and_not_repeated(self):
        calls = []

        class Service:
            def stop(self): calls.append(True); return {"ok": True}

        runtime = ControllerRoleRuntimeV2(prepared(), provider=None)
        runtime.service = Service()
        expected = {"ok": False, "code": "controller_cleanup_failed",
                    "admission_open": False}
        self.assertEqual(runtime.stop(), expected)
        self.assertEqual(runtime.stop(), expected)
        self.assertEqual(calls, [True])


class TestControllerExecutableV2(unittest.TestCase):
    def test_exact_cli_environment_and_closed_result(self):
        module = controller_script()

        class Connection:
            def recv(self, *_args): return b""

        class Runtime:
            def start_closed(self, **kwargs):
                self.connector = kwargs["connector"]
                return {"ok": True}
            def run_closed(self, **kwargs):
                state = kwargs["poll"](Connection())
                return {"ok": True, "code": f"controller_{state}"}
            def stop(self):
                return {"ok": True, "code": "controller_stopped",
                        "admission_open": False}

        prepare_calls = []
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = module.main(
                ["--protocol", module.CONTROLLER_PROTOCOL_V2,
                 "--machine-id", "sb-0123456789ab"],
                environ=module.FIXED_ENVIRONMENT, platform="linux",
                geteuid=lambda: 992, getegid=lambda: 2001,
                prepare=lambda machine_id, **kwargs: (
                    prepare_calls.append((machine_id, kwargs)), Runtime())[1],
                select_read=lambda readers, *_args: (readers, (), ()))
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(stream.getvalue()), {
            "ok": False, "code": "controller_eof", "admission_open": False})
        self.assertEqual(len(prepare_calls), 1)

    def test_signal_terminates_persistent_loop_and_cleanup_runs_once(self):
        module = controller_script()
        installed = []
        stops = []

        def install(_signum, handler):
            if callable(handler) and getattr(handler, "__name__", "") == "request_stop":
                installed.append(handler)
            return None

        class Runtime:
            def start_closed(self, **_kwargs): return {"ok": True}
            def run_closed(self, *, poll):
                installed[0](15, None)
                return {"ok": True, "code": f"controller_{poll(object())}"}
            def stop(self):
                stops.append(True)
                return {"ok": True, "code": "controller_stopped",
                        "admission_open": False}

        stream = io.StringIO()
        with redirect_stdout(stream):
            module.main(
                ["--protocol", module.CONTROLLER_PROTOCOL_V2,
                 "--machine-id", "sb-0123456789ab"],
                environ=module.FIXED_ENVIRONMENT, platform="linux",
                geteuid=lambda: 992, getegid=lambda: 2001,
                prepare=lambda *_args, **_kwargs: Runtime(), install_signal=install,
                select_read=lambda *_args: self.fail("select after signal"))
        self.assertEqual(json.loads(stream.getvalue())["code"], "controller_signal")
        self.assertEqual(stops, [True])

    def test_root_platform_extra_argv_and_environment_refuse_before_prepare(self):
        module = controller_script()
        cases = (
            ({"platform": "darwin", "geteuid": lambda: 992,
              "argv": ["--protocol", module.CONTROLLER_PROTOCOL_V2,
                       "--machine-id", "sb-0123456789ab"]},
             "controller_platform_refused"),
            ({"platform": "linux", "geteuid": lambda: 0,
              "argv": ["--protocol", module.CONTROLLER_PROTOCOL_V2,
                       "--machine-id", "sb-0123456789ab"]},
             "controller_identity_refused"),
            ({"platform": "linux", "geteuid": lambda: 992,
              "argv": ["--protocol", module.CONTROLLER_PROTOCOL_V2,
                       "--machine-id", "sb-0123456789ab", "extra"]},
             "controller_refused"),
        )
        for values, expected in cases:
            calls = []
            stream = io.StringIO()
            with self.subTest(expected=expected), redirect_stdout(stream):
                module.main(
                    values["argv"], environ=module.FIXED_ENVIRONMENT,
                    platform=values["platform"], geteuid=values["geteuid"],
                    getegid=lambda: 2001,
                    prepare=lambda *_args, **_kwargs: calls.append(True))
            self.assertEqual(json.loads(stream.getvalue())["code"], expected)
            self.assertEqual(calls, [])
        stream = io.StringIO()
        with redirect_stdout(stream):
            module.main(
                ["--protocol", module.CONTROLLER_PROTOCOL_V2,
                 "--machine-id", "sb-0123456789ab"],
                environ={**module.FIXED_ENVIRONMENT, "TOKEN": "forbidden"},
                platform="linux", geteuid=lambda: 992, getegid=lambda: 2001,
                prepare=lambda *_args, **_kwargs: self.fail("prepare called"))
        self.assertEqual(json.loads(stream.getvalue())["code"], "controller_refused")


if __name__ == "__main__":
    unittest.main()
