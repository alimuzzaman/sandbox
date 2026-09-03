import json
import io
import os
import subprocess
import sys
import unittest

from tests.subprocess_support import run_test_process, synthetic_environment
from tests.hosting_image_fixtures import stage_request


class TestImageStagingProcess(unittest.TestCase):
    def test_unit_identity_is_request_bound_not_pid_bound(self):
        from sandbox.hosting.images.staging_worker import unit_name
        request = stage_request(); first = unit_name(request.request_id, request.request_digest)
        self.assertRegex(first, r"^sandbox-image-stage-[0-9a-f]{32}\.service$")
        self.assertEqual(first, unit_name(request.request_id, request.request_digest))
        self.assertNotIn("pid", first)

    def test_helper_cgroup_identity_requires_exact_effective_user_slice(self):
        from unittest.mock import patch
        from sandbox.hosting.images import staging_helper
        unit = "sandbox-image-stage-" + "a" * 32 + ".service"
        uid = 1000
        exact = f"/user.slice/user-{uid}.slice/user@{uid}.service/app.slice/{unit}"
        with patch.object(staging_helper.Path, "is_file", return_value=True), \
             patch.object(staging_helper.Path, "read_text", return_value=f"0::{exact}\n"), \
             patch.object(staging_helper.os, "geteuid", return_value=uid):
            self.assertEqual(staging_helper._cgroup_identity(unit), exact)
        for wrong in (f"/system.slice/{unit}", exact + "/child", exact.replace(unit, "other.service")):
            with self.subTest(wrong=wrong), \
                 patch.object(staging_helper.Path, "is_file", return_value=True), \
                 patch.object(staging_helper.Path, "read_text", return_value=f"0::{wrong}\n"), \
                 patch.object(staging_helper.os, "geteuid", return_value=uid), \
                 self.assertRaisesRegex(ValueError, "process_unproven"):
                staging_helper._cgroup_identity(unit)

    def test_helper_rejects_unframed_input_with_closed_synthetic_environment(self):
        result = run_test_process(
            (sys.executable, "-m", "sandbox.hosting.images.staging_helper",
             "sandbox-image-stage-helper-v1"),
            input=b"unsafe", capture_output=True, env=synthetic_environment(), timeout=10)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"]); self.assertEqual(payload["code"], "protocol_invalid")

    def test_transport_requires_inactive_and_empty_cgroup_not_process_group(self):
        from pathlib import Path
        source = (Path(__file__).parent.parent / "sandbox/transports/remote_hosting_images.py").read_text()
        self.assertIn("cgroup.events", source); self.assertIn("populated 0", source)
        self.assertIn("ActiveState", source); self.assertNotIn("getpgid", source)

    def test_cancel_requires_exact_unit_inactive_and_exact_cgroup_empty(self):
        from sandbox.transports.remote_hosting_images import _PreparedRemoteStage

        class Process:
            def __init__(self): self.killed = False
            def poll(self): return None
            def kill(self): self.killed = True

        for active, populated, expected in (
                ("inactive", 0, True), ("active", 0, False), ("inactive", 1, False)):
            calls = []
            def observe(_remote, command, timeout):
                calls.append(command)
                if "Description --value" in command:
                    return subprocess.CompletedProcess(
                        (), 0, stdout="sandbox-image-stage-attempt-test\n")
                if "Description --property=ControlGroup" in command:
                    return subprocess.CompletedProcess((), 0, stdout=(
                        "Description=sandbox-image-stage-attempt-test\n"
                        "ControlGroup=/user.slice/user-1000.slice/user@1000.service/"
                        "app.slice/exact.service\n"))
                if "systemctl --user stop" in command:
                    return subprocess.CompletedProcess((), 0, stdout="")
                if "ActiveState --property=Description" in command:
                    return subprocess.CompletedProcess((), 0, stdout=(
                        f"ActiveState={active}\n"
                        "Description=sandbox-image-stage-attempt-test\n"))
                return subprocess.CompletedProcess((), 0 if populated == 0 else 1, stdout="")
            prepared = _PreparedRemoteStage(
                Process(), {}, "exact.service", "sandbox-image-stage-attempt-test",
                "/user.slice/user-1000.slice/user@1000.service/app.slice/exact.service",
                observe, 1)
            evidence = prepared.cancel()
            self.assertEqual(evidence["unit_inactive"] and evidence["cgroup_empty_or_removed"],
                             expected)
            self.assertTrue(any("cgroup.events" in command for command in calls))

    def test_timeout_signal_and_cleanup_uncertainty_never_become_safe_terminal(self):
        from sandbox.hosting.images.staging_service import ImageStagingService
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_worker import StageWorkerError
        from tests.hosting_image_fixtures import FakeBroker, staging_policy
        import tempfile
        from pathlib import Path

        class Prepared:
            frame = {"unit_name": "sandbox-image-stage-timeout.service"}
            def deliver(self, _credential):
                raise StageWorkerError("pull_failed", process={"unit_inactive": False,
                    "cgroup_empty_or_removed": False}, cleanup={"complete": False})
            def cancel(self):
                return {"unit_inactive": True, "cgroup_empty_or_removed": False,
                        "cleanup_complete": False}
        class Worker:
            def prepare(self, _request, _policy): return Prepared()
        with tempfile.TemporaryDirectory() as directory:
            policy = staging_policy(); request = stage_request(policy=policy)
            result = ImageStagingService(repository=StageRepository(Path(directory)),
                broker=FakeBroker(), worker=Worker()).stage(request, policy)
            self.assertEqual((result.result_class, result.code),
                             ("uncertain", "cleanup_unproven"))
            second = stage_request(request_id="stage-request-after-timeout", generation=1,
                                   policy=policy)
            self.assertEqual(StageRepository(Path(directory)).accept(second)[2].code,
                             "target_busy")

    def test_process_contract_contains_no_pid_or_process_group_adoption(self):
        from pathlib import Path
        root = Path(__file__).parent.parent
        source = "".join((root / name).read_text() for name in (
            "sandbox/hosting/images/staging_helper.py",
            "sandbox/transports/remote_hosting_images.py"))
        for forbidden in ("start_new_session=False", "os.killpg", "getpgid", "pidfile"):
            self.assertNotIn(forbidden, source)

    def test_worker_rejects_delegation_escape_wrong_cgroup_and_populated_descendants(self):
        from sandbox.hosting.images.staging_worker import StageWorker, StageWorkerError
        from sandbox.transports.remote_hosting_images import RemoteStageResponse
        from tests.hosting_image_fixtures import local_observation, staging_policy
        policy = staging_policy(); request = stage_request(policy=policy)
        observation = local_observation(policy)
        observation_raw = {"observation_id": observation.observation_id,
                           **observation.body_mapping()}
        class Channel:
            def __init__(self, process): self.process = process
            def deliver(self, _credential):
                return RemoteStageResponse(True, "staged", {"observation": observation_raw,
                    "process": self.process, "cleanup": {"complete": True}})
            def cancel(self): return {}
        class Transport:
            def __init__(self, process): self.process = process
            def prepare(self, *_args, **_kwargs): return Channel(self.process)
        unit = "sandbox-image-stage-" + "a" * 32 + ".service"
        base = {"unit_name": unit,
                "cgroup": "/user.slice/user-1000.slice/user@1000.service/app.slice/" + unit,
                "delegated": False, "escape_allowed": False,
                "unit_inactive": True, "cgroup_empty_or_removed": True}
        for mutation in ({"delegated": True}, {"escape_allowed": True},
                         {"cgroup": "/user.slice/user-1000.slice/user@1000.service/app.slice/other.service"},
                         {"cgroup_empty_or_removed": False}):
            process = {**base, **mutation}
            prepared = StageWorker(Transport(process)).prepare(request, policy)
            prepared.frame["unit_name"] = unit
            with self.subTest(mutation=mutation), self.assertRaises(StageWorkerError):
                prepared.deliver(b"synthetic")

    def test_signal_timeout_cancel_crash_cleanup_and_fence_matrix(self):
        from pathlib import Path
        import tempfile
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_service import ImageStagingService
        from sandbox.hosting.images.staging_worker import StageWorkerError
        from tests.hosting_image_fixtures import FakeBroker, staging_policy
        scenarios = {
            "signal": ("helper_failed", True, True, "failed"),
            "cancel": ("cancelled", True, True, "failed"),
            "timeout": ("helper_failed", False, False, "uncertain"),
            "double_fork_populated": ("process_unproven", False, True, "uncertain"),
            "cleanup_failure": ("cleanup_unproven", True, False, "uncertain"),
            "crash": ("helper_failed", False, False, "uncertain"),
        }
        for index, (name, values) in enumerate(scenarios.items()):
            code, terminated, cleaned, expected = values
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                policy = staging_policy(); request = stage_request(
                    request_id=f"process-{name}", policy=policy)
                class Prepared:
                    frame = {"unit_name": "sandbox-image-stage-matrix.service"}
                    def deliver(self, _credential):
                        raise StageWorkerError(code,
                            process={"unit_inactive": terminated,
                                     "cgroup_empty_or_removed": terminated},
                            cleanup={"complete": cleaned})
                    def cancel(self):
                        return {"unit_inactive": terminated,
                                "cgroup_empty_or_removed": terminated,
                                "cleanup_complete": cleaned}
                class Worker:
                    def prepare(self, *_args): return Prepared()
                repository = StageRepository(Path(directory))
                result = ImageStagingService(repository=repository,
                    broker=FakeBroker(), worker=Worker()).stage(request, policy)
                self.assertEqual(result.result_class, expected)
                status = repository.record_status(request.target.target_identity,
                                                  request.request_id)
                self.assertEqual(status["cleanup"]["complete"], cleaned)
                if expected == "uncertain":
                    other = stage_request(request_id=f"after-{name}", generation=1,
                                          policy=policy)
                    self.assertEqual(repository.accept(other)[2].code, "target_busy")

    def test_real_transport_ready_timeout_kills_and_stops_exact_unit(self):
        from sandbox.hosting.images.staging_worker import StageWorker
        from sandbox.transports.remote_hosting_images import (
            RegisteredRemoteImageTransport, RemoteImageStageError,
        )
        from tests.hosting_image_fixtures import staging_policy
        policy = staging_policy(); request = stage_request(policy=policy); commands = []
        class Process:
            stdin = io.BytesIO(); stdout = io.BytesIO(); stderr = io.BytesIO()
            def read_ready(self, _timeout): return b""
            def kill(self): self.killed = True
        process = Process()
        class Sender:
            def prepare(self, _remote, argv, **_kwargs): process.argv = argv; return process
        cleanup_observations = ["owned", "terminal"]
        def observe(_remote, command, timeout):
            commands.append(command)
            if command == "id -u":
                return subprocess.CompletedProcess((), 0, stdout=str(os.geteuid()) + "\n")
            if command.startswith("sha256sum"):
                return subprocess.CompletedProcess((), 0, stdout="9" * 64 + "  helper\n")
            if "manifest.json" in command:
                return subprocess.CompletedProcess((), 0, stdout=json.dumps({
                    "schema_version": 1, **policy.helper.as_mapping()}))
            if "--property=LoadState" in command:
                description = next(item.split("=", 1)[1] for item in process.argv
                                   if item.startswith("--description="))
                unit = next(item.split("=", 1)[1] for item in process.argv
                            if item.startswith("--unit="))
                phase = cleanup_observations.pop(0)
                if phase == "owned":
                    return subprocess.CompletedProcess((), 0, stdout=(
                        f"LoadState=loaded\nActiveState=active\nDescription={description}\n"
                        f"ControlGroup=/user.slice/user-{os.geteuid()}.slice/"
                        f"user@{os.geteuid()}.service/app.slice/{unit}\n"))
                return subprocess.CompletedProcess((), 0, stdout=(
                    f"LoadState=not-found\nActiveState=inactive\nDescription={unit}\n"
                    "ControlGroup=\n"))
            if "cgroup.events" in command:
                return subprocess.CompletedProcess((), 0, stdout="")
            return subprocess.CompletedProcess((), 0, stdout="")
        transport = RegisteredRemoteImageTransport(remote_lookup=lambda _name: {"provisioned": True},
            ssh_private_frame=Sender(), unit_observer=observe,
            resolve_home=lambda _remote: "/root/sandbox")
        with self.assertRaises(RemoteImageStageError):
            StageWorker(transport).prepare(request, policy)
        self.assertTrue(any("systemctl --user kill --kill-whom=all" in command for command in commands))
        self.assertTrue(any(command.startswith("systemctl --user stop") for command in commands))

    def test_same_request_launch_collision_never_kills_incumbent_unit(self):
        from sandbox.hosting.images.staging_worker import StageWorker
        from sandbox.transports.remote_hosting_images import (
            RegisteredRemoteImageTransport, RemoteImageStageError,
        )
        from tests.hosting_image_fixtures import staging_policy
        policy = staging_policy(); request = stage_request(policy=policy); commands = []
        class Process:
            stdin = io.BytesIO(); stdout = io.BytesIO(); stderr = io.BytesIO()
            def read_ready(self, _timeout): return b""
            def kill(self): self.killed = True
        class Sender:
            def prepare(self, _remote, argv, **_kwargs): return Process()
        def observe(_remote, command, timeout):
            commands.append(command)
            if command == "id -u":
                return subprocess.CompletedProcess((), 0, stdout=str(os.geteuid()) + "\n")
            if command.startswith("sha256sum"):
                return subprocess.CompletedProcess((), 0, stdout="9" * 64 + "  helper\n")
            if "manifest.json" in command:
                return subprocess.CompletedProcess((), 0, stdout=json.dumps({
                    "schema_version": 1, **policy.helper.as_mapping()}))
            if "--property=LoadState" in command:
                unit = command.rsplit(" ", 1)[-1]
                return subprocess.CompletedProcess(
                    (), 0, stdout=("LoadState=loaded\nActiveState=active\n"
                        "Description=sandbox-image-stage-attempt-incumbent\n"
                        f"ControlGroup=/user.slice/user-{os.geteuid()}.slice/"
                        f"user@{os.geteuid()}.service/app.slice/{unit}\n"))
            return subprocess.CompletedProcess((), 0, stdout="")
        transport = RegisteredRemoteImageTransport(
            remote_lookup=lambda _name: {"provisioned": True}, ssh_private_frame=Sender(),
            unit_observer=observe, resolve_home=lambda _remote: "/root/sandbox")
        with self.assertRaises(RemoteImageStageError):
            StageWorker(transport).prepare(request, policy)
        self.assertFalse(any(" kill " in command or " stop " in command for command in commands))

    def test_launch_property_drift_refuses_before_credential_delivery(self):
        from sandbox.hosting.images.staging_worker import StageWorker
        from sandbox.transports.remote_hosting_images import (
            RegisteredRemoteImageTransport, RemoteImageStageError,
        )
        from tests.hosting_image_fixtures import staging_policy
        policy = staging_policy(); request = stage_request(policy=policy)
        properties = ("ActiveState", "ControlGroup", "KillMode", "Delegate",
                      "NoNewPrivileges", "RestrictSUIDSGID", "ProtectControlGroups")
        for changed in properties:
            with self.subTest(changed=changed):
                class Process:
                    stdin = io.BytesIO(); stdout = io.BytesIO(); stderr = io.BytesIO()
                    def read_ready(self, _timeout): return b"READY\n"
                    def kill(self): self.killed = True
                process = Process()
                class Sender:
                    def prepare(self, _remote, argv, **_kwargs):
                        process.argv = argv; return process
                def observe(_remote, command, timeout):
                    if command == "id -u":
                        return subprocess.CompletedProcess((), 0, stdout="1000\n")
                    if command.startswith("sha256sum"):
                        return subprocess.CompletedProcess((), 0, stdout="9" * 64 + " helper\n")
                    if "manifest.json" in command:
                        return subprocess.CompletedProcess((), 0, stdout=json.dumps({
                            "schema_version": 1, **policy.helper.as_mapping()}))
                    description = next((item.split("=", 1)[1] for item in process.argv
                                        if item.startswith("--description=")), "")
                    if "Description --value" in command:
                        return subprocess.CompletedProcess((), 0, stdout=description + "\n")
                    if "property=ActiveState --property=Description" in command:
                        unit = next(item.split("=", 1)[1] for item in process.argv
                                    if item.startswith("--unit="))
                        values = {"ActiveState": "active", "Description": description,
                                  "ControlGroup": ("/user.slice/user-1000.slice/"
                                                   "user@1000.service/app.slice/" + unit),
                                  "KillMode": "control-group", "Delegate": "no",
                                  "NoNewPrivileges": "yes", "RestrictSUIDSGID": "yes",
                                  "ProtectControlGroups": "yes"}
                        values[changed] = "drift"
                        return subprocess.CompletedProcess((), 0, stdout="".join(
                            f"{key}={value}\n" for key, value in values.items()))
                    return subprocess.CompletedProcess((), 0, stdout="")
                transport = RegisteredRemoteImageTransport(
                    remote_lookup=lambda _name: {"provisioned": True},
                    ssh_private_frame=Sender(), unit_observer=observe,
                    resolve_home=lambda _remote: "/home/alim/sandbox")
                with self.assertRaises(RemoteImageStageError):
                    StageWorker(transport).prepare(request, policy)
                self.assertFalse(hasattr(process, "credential_delivered"))

    def test_real_transport_populated_descendant_then_empty_cancel_and_exact_cleanup(self):
        from sandbox.hosting.images.staging_worker import StageWorker
        from sandbox.transports.remote_hosting_images import RegisteredRemoteImageTransport
        from tests.hosting_image_fixtures import local_observation, staging_policy
        policy = staging_policy(); request = stage_request(policy=policy); commands = []
        observation = local_observation(policy)
        observation_raw = {"observation_id": observation.observation_id,
                           **observation.body_mapping()}
        class Process:
            def __init__(self):
                self.stdin = io.BytesIO(); self.stdout = io.BytesIO(); self.stderr = io.BytesIO()
                self.returncode = 0; self.alive = True; self.frame = None
            def read_ready(self, _timeout): return b"READY\n"
            def communicate(self, timeout):
                unit = next(item.split("=", 1)[1] for item in self.argv if item.startswith("--unit="))
                payload = {"schema_version": 1, "ok": True, "code": "staged", "payload": {
                    "observation": observation_raw, "process": {"unit_name": unit,
                        "cgroup": (f"/user.slice/user-{os.geteuid()}.slice/"
                                   f"user@{os.geteuid()}.service/app.slice/{unit}"),
                        "delegated": False, "escape_allowed": False},
                    "cleanup": {"complete": True}}}
                self.alive = False; return json.dumps(payload).encode(), b""
            def poll(self): return None if self.alive else 0
            def kill(self): self.alive = False
        process = Process()
        class Sender:
            def prepare(self, _remote, argv, **_kwargs): process.argv = argv; return process
        populated = [1, 0]
        def observe(_remote, command, timeout):
            commands.append(command)
            if command == "id -u":
                return subprocess.CompletedProcess((), 0, stdout=str(os.geteuid()) + "\n")
            if command.startswith("sha256sum"):
                return subprocess.CompletedProcess((), 0, stdout="9" * 64 + "  helper\n")
            if "manifest.json" in command:
                return subprocess.CompletedProcess((), 0, stdout=json.dumps({
                    "schema_version": 1, **policy.helper.as_mapping()}))
            if command.startswith(
                    "systemctl --user show --property=ActiveState --property=Description") \
                    and "--property=ProtectControlGroups" in command:
                unit = next(item.split("=", 1)[1] for item in process.argv
                            if item.startswith("--unit="))
                description = next(item.split("=", 1)[1] for item in process.argv
                                   if item.startswith("--description="))
                cgroup = (f"/user.slice/user-{os.geteuid()}.slice/"
                          f"user@{os.geteuid()}.service/app.slice/{unit}")
                return subprocess.CompletedProcess((), 0, stdout=(
                    f"ActiveState=active\nDescription={description}\nControlGroup={cgroup}\n"
                    "KillMode=control-group\nDelegate=no\nNoNewPrivileges=yes\n"
                    "RestrictSUIDSGID=yes\nProtectControlGroups=yes\n"))
            if "Description --value" in command:
                description = next(item.split("=", 1)[1] for item in process.argv
                                   if item.startswith("--description="))
                return subprocess.CompletedProcess((), 0, stdout=description + "\n")
            if (command.startswith("systemctl --user show --property=LoadState ")
                    and "--property=Description" in command
                    and "--property=ProtectControlGroups" not in command):
                unit = next(item.split("=", 1)[1] for item in process.argv if item.startswith("--unit="))
                description = next(item.split("=", 1)[1] for item in process.argv
                                   if item.startswith("--description="))
                return subprocess.CompletedProcess((), 0,
                    stdout=(f"LoadState=loaded\nActiveState=inactive\nDescription={description}\n"
                            f"ControlGroup=/user.slice/user-{os.geteuid()}.slice/"
                            f"user@{os.geteuid()}.service/app.slice/{unit}\n"))
            if "Description --property=ControlGroup" in command:
                description = next(item.split("=", 1)[1] for item in process.argv
                                   if item.startswith("--description="))
                return subprocess.CompletedProcess((), 0,
                    stdout=(f"Description={description}\n"
                            f"ControlGroup=/user.slice/user-{os.geteuid()}.slice/"
                            f"user@{os.geteuid()}.service/app.slice/"
                            + next(item.split("=", 1)[1]
                                for item in process.argv if item.startswith("--unit=")) + "\n"))
            if "ActiveState --property=Description" in command:
                description = next(item.split("=", 1)[1] for item in process.argv
                                   if item.startswith("--description="))
                return subprocess.CompletedProcess((), 0, stdout=(
                    f"ActiveState=inactive\nDescription={description}\n"))
            if "cgroup.events" in command:
                value = populated.pop(0)
                return subprocess.CompletedProcess((), 0 if value == 0 else 1,
                    stdout=f"populated {value}\n")
            return subprocess.CompletedProcess((), 0, stdout="")
        transport = RegisteredRemoteImageTransport(remote_lookup=lambda _name: {"provisioned": True},
            ssh_private_frame=Sender(), unit_observer=observe,
            resolve_home=lambda _remote: "/root/sandbox")
        prepared = StageWorker(transport).prepare(request, policy)
        self.assertEqual(process.argv[:2], ("systemd-run", "--user"))
        with self.assertRaises(Exception): prepared.deliver(b"synthetic")
        evidence = prepared.cancel()
        self.assertTrue(evidence["unit_inactive"])
        self.assertTrue(evidence["cgroup_empty_or_removed"])
        self.assertEqual(populated, [])
        self.assertTrue(any("systemctl --user kill --kill-whom=all" in command for command in commands))

    def test_success_accepts_exact_unloaded_user_unit_and_skips_reset_failed(self):
        from sandbox.transports.remote_hosting_images import _PreparedRemoteStage
        unit = "sandbox-image-stage-" + "a" * 32 + ".service"
        cgroup = "/user.slice/user-1001.slice/user@1001.service/app.slice/" + unit
        output = json.dumps({"schema_version": 1, "ok": True, "code": "staged",
            "payload": {"process": {"unit_name": unit, "cgroup": cgroup}}}).encode()
        class Process:
            stdin = io.BytesIO(); returncode = 0
            def communicate(self, timeout): return output, b""
        commands = []
        def observe(_remote, command, timeout):
            commands.append(command)
            if "--property=LoadState" in command:
                return subprocess.CompletedProcess((), 0, stdout=(
                    f"LoadState=not-found\nActiveState=inactive\nDescription={unit}\nControlGroup=\n"))
            if "cgroup.events" in command:
                return subprocess.CompletedProcess((), 0, stdout="")
            return subprocess.CompletedProcess((), 1, stdout="")
        prepared = _PreparedRemoteStage(Process(), {}, unit, "attempt", cgroup, observe, 30)
        response = prepared.deliver(b"credential")
        self.assertTrue(response.payload["process"]["unit_inactive"])
        self.assertFalse(any("reset-failed" in command for command in commands))


if __name__ == "__main__": unittest.main()
