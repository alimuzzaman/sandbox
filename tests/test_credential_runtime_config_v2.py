import hashlib
import io
import json
import os
import stat
from types import SimpleNamespace
import unittest
from unittest import mock

from sandbox.isolation.credential_controller_lifecycle_v2 import DerivedServiceConfigV2
from tests import test_credential_controller_authority_v2 as authority_fixtures
from tests import test_credential_controller_lifecycle_v2 as lifecycle_fixtures


broker = authority_fixtures.broker
MACHINE = authority_fixtures.MACHINE


class Kernel:
    def __init__(self, payload, **changes):
        self.payload = payload
        self.offset = 0
        self.closed = 0
        self.opened = []
        self.observed = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o640, st_uid=0, st_gid=2001,
            st_size=len(payload),
        )
        for name, value in changes.items():
            setattr(self.observed, name, value)

    def open(self, path, flags):
        self.opened.append((path, flags))
        return 17
    def fstat(self, descriptor):
        self.assert_descriptor(descriptor)
        return self.observed
    def read(self, descriptor, size):
        self.assert_descriptor(descriptor)
        value = self.payload[self.offset:self.offset + size]
        self.offset += len(value)
        return value
    def close(self, descriptor):
        self.assert_descriptor(descriptor)
        self.closed += 1
    @staticmethod
    def assert_descriptor(value):
        if value != 17:
            raise OSError("bad descriptor")


def plan():
    return DerivedServiceConfigV2.derive(lifecycle_fixtures.document("broker"))


def plans():
    return (DerivedServiceConfigV2.derive(lifecycle_fixtures.document("controller")),
            DerivedServiceConfigV2.derive(lifecycle_fixtures.document("broker")))


class TestCredentialRuntimeConfigV2(unittest.TestCase):
    def load(self, selected=None, **kwargs):
        selected = selected or plan()
        kernel = kwargs.pop("kernel", Kernel(selected.canonical_bytes))
        value = broker.load_runtime_config_v2(
            broker.runtime_config_path_v2(MACHINE, "broker"), machine_id=MACHINE,
            component="broker", expected_group_gid=2001,
            kernel=kernel, **kwargs,
        )
        return value, kernel

    def test_exact_canonical_owned_config_and_nofollow(self):
        value, kernel = self.load()
        self.assertEqual(value, plan())
        self.assertEqual(kernel.closed, 1)
        self.assertTrue(kernel.opened[0][1] & getattr(os, "O_NOFOLLOW", 0))
        controller = plans()[0]
        controller_kernel = Kernel(controller.canonical_bytes)
        loaded = broker.load_runtime_config_v2(
            broker.runtime_config_path_v2(MACHINE, "controller"),
            machine_id=MACHINE, component="controller",
            expected_group_gid=2001, kernel=controller_kernel)
        self.assertEqual(loaded, controller)
        self.assertTrue(controller_kernel.opened[0][1]
                        & getattr(os, "O_NOFOLLOW", 0))

    def test_fixed_role_path_machine_and_gid_are_authority_gates(self):
        selected = plan()
        for changes in (
            {"path": "/tmp/forged"}, {"machine_id": "sb-ffffffffffff"},
            {"component": "controller"}, {"expected_group_gid": 2002},
        ):
            values = dict(
                path=broker.runtime_config_path_v2(MACHINE, "broker"),
                machine_id=MACHINE, component="broker", expected_group_gid=2001,
                kernel=Kernel(selected.canonical_bytes),
            )
            values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                broker.load_runtime_config_v2(**values)
            if "expected_group_gid" in changes:
                self.assertEqual(len(values["kernel"].opened), 1)
                self.assertEqual(values["kernel"].closed, 1)
            else:
                self.assertEqual(values["kernel"].opened, [])

    def test_metadata_mutations_close_once(self):
        selected = plan()
        cases = (
            {"st_mode": stat.S_IFLNK | 0o640}, {"st_uid": 1}, {"st_gid": 1},
            {"st_mode": stat.S_IFREG | 0o644}, {"st_size": 0},
            {"st_size": broker.MAX_CONFIG_BYTES + 1},
        )
        for changes in cases:
            kernel = Kernel(selected.canonical_bytes, **changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.load(selected, kernel=kernel)
            self.assertEqual(kernel.closed, 1)

    def test_short_oversize_digest_noncanonical_extra_component_machine_gid_refuse(self):
        selected = plan()
        documents = []
        for name, value in (("component", "controller"),
                            ("machine_id", "sb-ffffffffffff"),
                            ("service_gid", 2002)):
            changed = json.loads(selected.canonical_bytes)
            changed[name] = value
            documents.append(json.dumps(changed, sort_keys=True,
                                        separators=(",", ":")).encode("ascii"))
        changed = json.loads(selected.canonical_bytes)
        changed["extra"] = True
        documents.append(json.dumps(changed, sort_keys=True,
                                    separators=(",", ":")).encode("ascii"))
        documents.extend((selected.canonical_bytes + b"\n", b"{}",
                          b"x" * (broker.MAX_CONFIG_BYTES + 1)))
        for payload in documents:
            kernel = Kernel(payload)
            with self.subTest(size=len(payload)), self.assertRaises(ValueError):
                broker.load_runtime_config_v2(
                    broker.runtime_config_path_v2(MACHINE, "broker"),
                    machine_id=MACHINE, component="broker",
                    expected_group_gid=2001, kernel=kernel,
                )
            self.assertEqual(kernel.closed, 1)

    def test_read_and_close_errors_do_not_succeed(self):
        selected = plan()
        class ReadError(Kernel):
            def read(self, *_args): raise OSError("private")
        with self.assertRaises(OSError):
            self.load(selected, kernel=ReadError(selected.canonical_bytes))
        class CloseError(Kernel):
            def close(self, descriptor):
                super().close(descriptor)
                raise OSError("private")
        with self.assertRaises(OSError):
            self.load(selected, kernel=CloseError(selected.canonical_bytes))

    def test_reciprocal_cgroup_processes_are_pinned_start_observe_start(self):
        controller, broker_plan = plans()
        events = []
        def pid_reader(selected):
            events.append((selected.component, "pid"))
            return 101 if selected.component == "controller" else 202
        def start_reader(pid):
            events.append((pid, "start")); return pid + 1000
        def details(pid, selected):
            events.append((pid, "details"))
            return {
                "uid": selected.document["service_uid"],
                "gid": selected.document["service_gid"],
                "executable_digest": selected.document["executable_digest"],
                "unit_digest": selected.document["unit_digest"],
                "config_digest": selected.document["own_config_digest"],
            }
        identities = broker.pin_reciprocal_process_identities_v2(
            controller, broker_plan, cgroup_pid_reader=pid_reader,
            start_reader=start_reader, detail_reader=details)
        self.assertEqual((identities[0].pid, identities[1].pid), (101, 202))
        self.assertEqual(events, [
            ("controller", "pid"), (101, "start"), (101, "details"),
            (101, "start"), ("broker", "pid"), (202, "start"),
            (202, "details"), (202, "start")])
        observer = broker.pinned_process_identity_observer_v2(identities[0])
        self.assertIs(observer(101, 992, 2001), identities[0])
        with self.assertRaisesRegex(Exception, "peer_identity_mismatch"):
            observer(102, 992, 2001)

    def test_reciprocal_plan_and_start_race_refuse_before_or_during_observation(self):
        controller, broker_plan = plans()
        changed = json.loads(controller.canonical_bytes)
        changed["peer_config_digest"] = "0" * 64
        crossed = DerivedServiceConfigV2.derive(changed)
        calls = []
        with self.assertRaisesRegex(Exception, "lifecycle_plan_invalid"):
            broker.pin_reciprocal_process_identities_v2(
                crossed, broker_plan,
                cgroup_pid_reader=lambda selected: calls.append(selected) or 101,
                start_reader=lambda _pid: 1001,
                detail_reader=lambda _pid, _plan: {})
        self.assertEqual(calls, [])
        starts = iter((1001, 1002))
        with self.assertRaisesRegex(Exception, "peer_identity_mismatch"):
            broker.pin_process_identity_v2(
                controller, cgroup_pid_reader=lambda _plan: 101,
                start_reader=lambda _pid: next(starts),
                detail_reader=lambda _pid, selected: {
                    "uid": selected.document["service_uid"],
                    "gid": selected.document["service_gid"],
                    "executable_digest": selected.document["executable_digest"],
                    "unit_digest": selected.document["unit_digest"],
                    "config_digest": selected.document["own_config_digest"]})

    def test_exact_cgroup_procs_requires_one_canonical_pid(self):
        selected = plan()
        expected = ("/sys/fs/cgroup/system.slice/"
                    f"{selected.document['unit_identity']}/cgroup.procs")
        for payload, accepted in (("321\n", True), ("", False),
                                  ("321\n322\n", False), (" 321\n", False)):
            opened = []
            def fake_open(path, *args, **kwargs):
                opened.append((path, args, kwargs)); return io.StringIO(payload)
            with self.subTest(payload=payload), mock.patch("builtins.open", fake_open):
                if accepted:
                    self.assertEqual(broker._linux_cgroup_pid_v2(selected), 321)
                else:
                    with self.assertRaisesRegex(Exception, "peer_identity_unavailable"):
                        broker._linux_cgroup_pid_v2(selected)
            self.assertEqual(opened[0][0], expected)

    def test_standalone_authority_loads_and_validates_both_before_process_reads(self):
        controller, broker_plan = plans()
        events = []
        def loader(path, **kwargs):
            events.append(("load", kwargs["component"], path))
            return controller if kwargs["component"] == "controller" else broker_plan
        def pinner(first, second):
            events.append(("pin", first.component, second.component))
            def identity(selected, pid):
                return broker.ProcessIdentityV2(
                    uid=selected.document["service_uid"],
                    gid=selected.document["service_gid"], pid=pid,
                    start_ticks=pid + 1000,
                    executable_digest=selected.document["executable_digest"],
                    unit_digest=selected.document["unit_digest"],
                    config_digest=selected.document["own_config_digest"])
            return identity(first, 101), identity(second, 202)
        prepared = broker.prepare_standalone_authority_v2(
            MACHINE, service_gid=2001, plan_loader=loader,
            identity_pinner=pinner)
        self.assertEqual([item[0] for item in events], ["load", "load", "pin"])
        self.assertEqual(prepared["config"].controller.pid, 101)
        self.assertEqual(prepared["config"].broker.pid, 202)
        self.assertEqual(prepared["controller_observer"](101, 992, 2001).pid, 101)

        changed = json.loads(controller.canonical_bytes)
        changed["peer_config_digest"] = "0" * 64
        crossed = DerivedServiceConfigV2.derive(changed)
        reads = []
        with self.assertRaisesRegex(Exception, "runtime_config_invalid"):
            broker.prepare_standalone_authority_v2(
                MACHINE, service_gid=2001,
                plan_loader=lambda _path, **kwargs: (
                    crossed if kwargs["component"] == "controller" else broker_plan),
                identity_pinner=lambda *_plans: reads.append(1))
        self.assertEqual(reads, [])

    def test_private_composition_constructs_closed_graph_and_cleans_in_reverse(self):
        controller_plan, broker_plan = plans()
        def identity(selected, pid):
            return broker.ProcessIdentityV2(
                uid=selected.document["service_uid"],
                gid=selected.document["service_gid"], pid=pid,
                start_ticks=pid + 1000,
                executable_digest=selected.document["executable_digest"],
                unit_digest=selected.document["unit_digest"],
                config_digest=selected.document["own_config_digest"])
        prepared = broker.prepare_standalone_authority_v2(
            MACHINE, service_gid=2001,
            plan_loader=lambda _path, **kwargs: (
                controller_plan if kwargs["component"] == "controller"
                else broker_plan),
            identity_pinner=lambda first, second: (
                identity(first, 101), identity(second, os.getpid())))
        events = []
        class Controller:
            def __init__(self, config, **_kwargs):
                self.config, self.admission_open, self.session = config, False, None
            def start(self, **kwargs):
                events.append("controller_start")
                self.session = None
                return {"ok": True}
            def close(self): events.append("controller_close"); return {"ok": True}
        class Guest:
            fail = False
            def __init__(self, plan, **_kwargs):
                self.plan, self.admission_open = plan, False
            def start(self, **_kwargs):
                events.append("guest_start")
                if self.fail: raise OSError("bind refused")
                return {"ok": True}
            def close(self): events.append("guest_close"); return {"ok": True}
        class Dns:
            def close(self): events.append("dns_close"); return {"ok": True}
        class Loop:
            def __init__(self, *_args, **_kwargs): events.append("loop_construct")
            def run_forever(self): events.append("run"); return {"ok": False}
            def close(self): events.append("loop_close"); return {"ok": True}
        patches = (
            mock.patch.object(broker.sys, "platform", "linux"),
            mock.patch.object(broker, "_running_as_root", return_value=False),
            mock.patch.object(broker.os, "geteuid", return_value=993),
            mock.patch.object(broker.os, "getegid", return_value=2001),
            mock.patch.object(broker, "LinuxControllerV2Listener", Controller),
            mock.patch.object(broker, "LinuxGuestV2Listener", Guest),
            mock.patch.object(broker, "AuthorizedDnsResolverV2", return_value=Dns()),
            mock.patch.object(broker, "CredentialBrokerServiceLoopV2", Loop),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patches[5], patches[6], patches[7], \
                mock.patch.object(broker.socket, "SO_PEERCRED", 1, create=True), \
                mock.patch.object(broker.socket, "SO_PASSCRED", 2, create=True), \
                mock.patch.object(broker.socket, "SCM_CREDENTIALS", 3, create=True), \
                mock.patch.object(broker.socket, "SCM_RIGHTS", 4, create=True), \
                mock.patch.object(broker.socket, "SO_BINDTODEVICE", 5, create=True), \
                mock.patch.object(broker.socket, "SOCK_SEQPACKET", 6, create=True):
            broker.compose_standalone_service_v2(prepared)
            first = list(events)
            events.clear(); Guest.fail = True
            with self.assertRaisesRegex(Exception, "live_transport_unproven"):
                broker.compose_standalone_service_v2(prepared)
            failure = list(events)
            Guest.fail = False
        self.assertEqual(first[:4], ["controller_start", "guest_start",
                                     "loop_construct", "run"])
        self.assertEqual(first[-4:], ["loop_close", "guest_close",
                                      "controller_close", "dns_close"])
        self.assertEqual(failure, ["controller_start", "guest_start",
                                   "guest_close", "controller_close", "dns_close"])

    def test_private_composition_refuses_platform_or_root_before_construction(self):
        with self.assertRaisesRegex(Exception, "service_composition_invalid"):
            broker.compose_standalone_service_v2({})

    def test_cli_requires_exact_v2_protocol_machine_and_no_path_or_digest_authority(self):
        selected = plan()
        good = ["--protocol", "credential-broker-controller-v2",
                "--machine-id", MACHINE]
        with mock.patch.object(broker, "_running_as_root", return_value=False), \
                mock.patch.object(broker.sys, "platform", "linux"), \
                mock.patch.object(broker.os, "geteuid", return_value=993), \
                mock.patch.object(broker.os, "getegid", return_value=2001), \
                mock.patch.object(broker, "prepare_standalone_authority_v2",
                                  return_value={"broker_plan": selected}) as prepare, \
                mock.patch.object(broker, "compose_standalone_service_v2",
                                  return_value={"ok": False}) as compose, \
                mock.patch("builtins.print") as output:
            self.assertEqual(broker.main(good), 4)
        prepare.assert_called_once_with(MACHINE, service_gid=2001)
        compose.assert_called_once_with({"broker_plan": selected})
        rendered = output.call_args.args[0]
        self.assertLessEqual(len(rendered.encode()), broker.MAX_SAFE_DOCUMENT_BYTES)
        self.assertNotIn("source", rendered.lower())
        for protocol in ("credential-broker-controller-v1",
                         "credential-broker-controller-v3"):
            bad = list(good); bad[1] = protocol
            with mock.patch.object(broker, "prepare_standalone_authority_v2") as denied, \
                    mock.patch("builtins.print"):
                self.assertEqual(broker.main(bad), 4)
            denied.assert_not_called()
        for extra in (("--config", "/tmp/forged"),
                      ("--config-digest", "0" * 64)):
            with mock.patch.object(broker, "prepare_standalone_authority_v2") as denied, \
                    mock.patch("builtins.print"):
                self.assertEqual(broker.main(good + list(extra)), 4)
            denied.assert_not_called()


if __name__ == "__main__":
    unittest.main()
