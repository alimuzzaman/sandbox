import json
import io
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
                if "ControlGroup --value" in command:
                    return subprocess.CompletedProcess((), 0, stdout="/system.slice/exact.service\n")
                if "systemctl stop" in command:
                    return subprocess.CompletedProcess((), 0, stdout="")
                if "ActiveState --value" in command:
                    return subprocess.CompletedProcess((), 0, stdout=active + "\n")
                return subprocess.CompletedProcess((), 0 if populated == 0 else 1, stdout="")
            prepared = _PreparedRemoteStage(Process(), {}, "exact.service", observe, 1)
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
        base = {"unit_name": unit, "cgroup": "/system.slice/" + unit,
                "delegated": False, "escape_allowed": False,
                "unit_inactive": True, "cgroup_empty_or_removed": True}
        for mutation in ({"delegated": True}, {"escape_allowed": True},
                         {"cgroup": "/system.slice/other.service"},
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
        class Sender:
            def prepare(self, *_args, **_kwargs): return Process()
        def observe(_remote, command, timeout):
            commands.append(command)
            if command.startswith("sha256sum"):
                return subprocess.CompletedProcess((), 0, stdout="9" * 64 + "  helper\n")
            if "manifest.json" in command:
                return subprocess.CompletedProcess((), 0, stdout=json.dumps({
                    "schema_version": 1, **policy.helper.as_mapping()}))
            return subprocess.CompletedProcess((), 0, stdout="")
        transport = RegisteredRemoteImageTransport(remote_lookup=lambda _name: {"provisioned": True},
            ssh_private_frame=Sender(), unit_observer=observe,
            resolve_home=lambda _remote: "/root/sandbox")
        with self.assertRaises(RemoteImageStageError):
            StageWorker(transport).prepare(request, policy)
        self.assertTrue(any("systemctl kill --kill-whom=all" in command for command in commands))
        self.assertTrue(any(command.startswith("systemctl stop") for command in commands))

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
                        "cgroup": "/system.slice/" + unit, "delegated": False,
                        "escape_allowed": False}, "cleanup": {"complete": True}}}
                self.alive = False; return json.dumps(payload).encode(), b""
            def poll(self): return None if self.alive else 0
            def kill(self): self.alive = False
        process = Process()
        class Sender:
            def prepare(self, _remote, argv, **_kwargs): process.argv = argv; return process
        populated = [1, 0]
        def observe(_remote, command, timeout):
            commands.append(command)
            if command.startswith("sha256sum"):
                return subprocess.CompletedProcess((), 0, stdout="9" * 64 + "  helper\n")
            if "manifest.json" in command:
                return subprocess.CompletedProcess((), 0, stdout=json.dumps({
                    "schema_version": 1, **policy.helper.as_mapping()}))
            if "ActiveState --property=ControlGroup" in command:
                unit = next(item.split("=", 1)[1] for item in process.argv if item.startswith("--unit="))
                return subprocess.CompletedProcess((), 0,
                    stdout="inactive\n/system.slice/" + unit + "\n")
            if "ControlGroup --value" in command:
                return subprocess.CompletedProcess((), 0,
                    stdout="/system.slice/" + next(item.split("=", 1)[1]
                        for item in process.argv if item.startswith("--unit=")) + "\n")
            if "ActiveState --value" in command:
                return subprocess.CompletedProcess((), 0, stdout="inactive\n")
            if "cgroup.events" in command:
                value = populated.pop(0)
                return subprocess.CompletedProcess((), 0 if value == 0 else 1,
                    stdout=f"populated {value}\n")
            return subprocess.CompletedProcess((), 0, stdout="")
        transport = RegisteredRemoteImageTransport(remote_lookup=lambda _name: {"provisioned": True},
            ssh_private_frame=Sender(), unit_observer=observe,
            resolve_home=lambda _remote: "/root/sandbox")
        prepared = StageWorker(transport).prepare(request, policy)
        with self.assertRaises(Exception): prepared.deliver(b"synthetic")
        evidence = prepared.cancel()
        self.assertTrue(evidence["unit_inactive"])
        self.assertTrue(evidence["cgroup_empty_or_removed"])
        self.assertEqual(populated, [])
        self.assertTrue(any("systemctl kill --kill-whom=all" in command for command in commands))


if __name__ == "__main__": unittest.main()
