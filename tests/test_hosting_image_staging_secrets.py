import unittest
import tempfile
from pathlib import Path

from tests.hosting_image_fixtures import staging_policy


class FakeLease:
    def __init__(self, value): self.value = value; self.calls = 0
    def consume(self, callback): self.calls += 1; return callback(self.value)


class FakeResolver:
    def __init__(self, lease, error=None): self.lease = lease; self.error = error; self.calls = []
    def issue_revision_bound(self, binding, **kwargs):
        self.calls.append((binding, kwargs))
        if self.error: raise self.error
        return self.lease


class TestImageStagingSecrets(unittest.TestCase):
    def binding(self):
        from datetime import datetime, timedelta, timezone
        from sandbox.isolation.credential_binding import CredentialBinding
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        return CredentialBinding("binding-a", "instance-a", "personal/GHCR_TOKEN",
            "1" * 64, "2" * 64, "3" * 64,
            "https", "ghcr.io", 443, "GET", "/v2/acme/widget/manifests/" + "1" * 64,
            "bearer", future, "machine-a", version=3, state="ready")

    def test_fixed_adapter_uses_one_broker_lease_consume_callback(self):
        from sandbox.secrets.service import GHCRStagingCredentialAdapter
        policy = staging_policy(); lease = FakeLease(b"unique-synthetic-canary")
        adapter = GHCRStagingCredentialAdapter(FakeResolver(lease), self.binding(),
            recipient=policy.broker_recipient,
            credential_reference_revision=policy.credential_reference_revision,
            revision_key=b"k" * 32)
        observed = adapter.consume_for_stage(recipient=policy.broker_recipient,
            binding_id=policy.broker_binding_id, binding_version=policy.broker_binding_version,
            consumer=lambda value: {"length": len(value)})
        self.assertEqual(observed, {"length": 23}); self.assertEqual(lease.calls, 1)

    def test_recipient_substitution_refuses_before_lease_issue(self):
        from sandbox.secrets.models import SecretBrokerError
        from sandbox.secrets.service import GHCRStagingCredentialAdapter
        policy = staging_policy(); resolver = FakeResolver(FakeLease(b"canary"))
        adapter = GHCRStagingCredentialAdapter(resolver, self.binding(),
            recipient=policy.broker_recipient,
            credential_reference_revision=policy.credential_reference_revision,
            revision_key=b"k" * 32)
        with self.assertRaises(SecretBrokerError):
            adapter.consume_for_stage(recipient="ghcr-repository-read:other/repo@sha256:" + "1" * 64,
                binding_id="binding-a", binding_version=3, consumer=lambda _value: {})
        self.assertEqual(resolver.calls, [])

    def test_source_revision_drift_refuses_before_lease_issue(self):
        from sandbox.secrets.models import SecretBrokerError
        from sandbox.secrets.service import GHCRStagingCredentialAdapter
        policy = staging_policy(); resolver = FakeResolver(FakeLease(b"canary"),
            SecretBrokerError("revision_conflict", "changed"))
        adapter = GHCRStagingCredentialAdapter(resolver, self.binding(),
            recipient=policy.broker_recipient,
            credential_reference_revision=policy.credential_reference_revision,
            revision_key=b"k" * 32)
        with self.assertRaises(SecretBrokerError):
            adapter.consume_for_stage(recipient=policy.broker_recipient,
                binding_id=policy.broker_binding_id, binding_version=policy.broker_binding_version,
                consumer=lambda _value: {})
        self.assertEqual(len(resolver.calls), 1)

    def test_generic_secret_runner_is_not_a_staging_dependency(self):
        from pathlib import Path
        root = Path(__file__).parent.parent
        sources = "".join((root / path).read_text() for path in (
            "sandbox/hosting/images/staging_service.py", "sandbox/hosting/images/staging_worker.py",
            "sandbox/transports/remote_hosting_images.py"))
        self.assertNotIn("SecretService.run", sources); self.assertNotIn("run_many", sources)
        self.assertNotIn("sandbox.secrets.runner", sources)

    def test_revision_bound_snapshot_is_wiped_on_success_callback_failure_and_refusal(self):
        from datetime import datetime, timedelta, timezone
        from sandbox.isolation.credential_resolver import BrokerLease, SecretReference
        canary = b"STAGING_REVISION_SNAPSHOT_CANARY"
        class Resolver:
            def __init__(self): self.reads = 0
            def _read_reference(self, _reference):
                self.reads += 1; raise AssertionError("snapshot lease reopened source")
        callbacks = (lambda value: {"length": len(value)},
                     lambda _value: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
                     lambda value: value)
        for index, callback in enumerate(callbacks):
            with self.subTest(index=index):
                resolver = Resolver()
                lease = BrokerLease(resolver, SecretReference("personal", "GHCR_TOKEN", "personal"),
                    binding_id=f"binding-{index}", binding_version=1,
                    deadline=datetime.now(timezone.utc) + timedelta(minutes=1),
                    lease_id=f"lease-{index}", material=canary, snapshot_bound=True)
                try: lease.consume(callback)
                except Exception: pass
                self.assertIsNone(lease._material); self.assertEqual(resolver.reads, 0)
                self.assertNotIn(canary, repr(lease).encode())

    def test_canary_terminal_path_matrix_never_leaks_to_public_surfaces(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_service import ImageStagingService
        from sandbox.hosting.images.staging_worker import StageWorkerError
        from tests.hosting_image_fixtures import local_observation, stage_request, staging_policy
        canary = b"UNIQUE_STAGE_CANARY_NOT_A_SECRET"
        scenarios = {
            "success": None, "failure": ("pull_failed", True, True),
            "cancel": ("cancelled", True, True), "signal": ("helper_failed", True, True),
            "timeout": ("helper_failed", False, False),
            "cleanup": ("cleanup_unproven", True, False),
            "crash": ("helper_failed", False, False),
        }
        for index, (name, failure) in enumerate(scenarios.items()):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                policy = staging_policy(); request = stage_request(
                    request_id=f"stage-{name}", policy=policy)
                class Lease:
                    def __init__(self): self.material = bytearray(canary)
                    def consume(self, consumer):
                        detached = self.material; self.material = None
                        try: return consumer(bytes(detached))
                        finally: detached[:] = b"\x00" * len(detached)
                    def invalidate(self):
                        if self.material is not None:
                            self.material[:] = b"\x00" * len(self.material); self.material = None
                class Broker:
                    argv = (); environment = {}; logs = (); state = {}
                    def __init__(self): self.lease = Lease()
                    def prepare_for_stage(self, **_binding): return self.lease
                class Prepared:
                    frame = {"unit_name": "sandbox-image-stage-fake.service"}
                    argv = (); environment = {}; logs = ()
                    def deliver(self, credential):
                        self.received_length = len(credential)
                        if credential != canary: raise AssertionError("wrong canary")
                        if failure:
                            code, terminated, cleaned = failure
                            raise StageWorkerError(code,
                                process={"unit_inactive": terminated,
                                    "cgroup_empty_or_removed": terminated},
                                cleanup={"complete": cleaned})
                        return local_observation(policy), {
                            "unit_name": self.frame["unit_name"], "cgroup": "/exact",
                            "delegated": False, "escape_allowed": False,
                            "unit_inactive": True, "cgroup_empty_or_removed": True}, {"complete": True}
                    def cancel(self):
                        safe = failure is not None and failure[1] and failure[2]
                        return {"unit_inactive": safe, "cgroup_empty_or_removed": safe,
                                "cleanup_complete": safe}
                class Worker:
                    def __init__(self): self.prepared = Prepared()
                    def prepare(self, _request, _policy): return self.prepared
                broker = Broker(); worker = Worker(); root = Path(directory)
                result = ImageStagingService(repository=StageRepository(root),
                    broker=broker, worker=worker).stage(request, policy)
                public = repr({"result": result.as_mapping(), "frame": worker.prepared.frame,
                    "argv": worker.prepared.argv, "environment": worker.prepared.environment,
                    "logs": worker.prepared.logs, "broker": {
                        "argv": broker.argv, "environment": broker.environment,
                        "logs": broker.logs, "state": broker.state}}).encode()
                ledger = b"".join(path.read_bytes() for path in root.rglob("*.json"))
                self.assertNotIn(canary, public + ledger)
                self.assertIsNone(broker.lease.material)

    def test_real_helper_frame_workspace_and_cleanup_lifecycle_matrix(self):
        import hashlib
        import io
        import json
        import os
        import shutil
        import subprocess
        from datetime import datetime, timedelta, timezone
        from sandbox.hosting.images import staging_helper
        from sandbox.hosting.images.staging_worker import StageWorker
        from sandbox.isolation.credential_resolver import BrokerLease, SecretReference
        from tests.hosting_image_fixtures import stage_request

        canary = b"REAL_HELPER_CREDENTIAL_CANARY"
        framed = len(canary).to_bytes(4, "big") + canary
        self.assertEqual(staging_helper._read_frame(io.BytesIO(framed), 64 * 1024), canary)
        policy = staging_policy(); request = stage_request(policy=policy)
        class CaptureTransport:
            def prepare(self, _remote, frame, **_kwargs): self.frame = frame; return object()
        capture = CaptureTransport(); StageWorker(capture).prepare(request, policy)
        plan = capture.frame
        helper_path = Path(staging_helper.__file__)
        plan["helper"]["artifact_digest"] = "sha256:" + hashlib.sha256(
            helper_path.read_bytes()).hexdigest()

        branches = ("success", "login_failure", "pull_failure", "signal",
                    "timeout", "crash", "credential_cleanup_failure", "final_cleanup_failure")
        for branch in branches:
            with self.subTest(branch=branch), tempfile.TemporaryDirectory() as directory:
                base = Path(directory); run_parent = base / "run"; run_parent.mkdir(mode=0o700)
                run_root = run_parent / "sandbox-image-stage"
                mountinfo = f"1 0 0:1 / {run_parent} rw - tmpfs tmpfs rw\n"
                verified = staging_helper._verify_workspace_parent(
                    run_root, mountinfo_text=mountinfo, required_uid=os.geteuid())
                captured = {"argv": [], "environment": [], "input_lengths": [], "logs": []}
                def runner(argv, *, environment, input_data=None, timeout=300):
                    captured["argv"].append(tuple(argv)); captured["environment"].append(dict(environment))
                    if input_data is not None:
                        self.assertEqual(input_data.rstrip(b"\n"), canary)
                        captured["input_lengths"].append(len(input_data))
                    command = tuple(argv)
                    if branch == "signal": raise ValueError("cancelled")
                    if branch == "timeout": raise subprocess.TimeoutExpired(command, timeout)
                    if branch == "crash": raise RuntimeError("synthetic crash")
                    if command[:2] == ("docker", "login"):
                        config = Path(environment["DOCKER_CONFIG"]); config.mkdir(parents=True)
                        (config / "config.json").write_bytes(canary)
                        return subprocess.CompletedProcess(command,
                            1 if branch == "login_failure" else 0, stdout=b"", stderr=b"")
                    if command[:2] == ("docker", "pull"):
                        return subprocess.CompletedProcess(command,
                            1 if branch == "pull_failure" else 0, stdout=b"", stderr=b"")
                    if command[:2] == ("docker", "info"):
                        return subprocess.CompletedProcess(command, 0, stdout=b"daemon-a\n", stderr=b"")
                    topology = plan["topology"]
                    # Docker 29 may expose the exact pulled manifest as the
                    # local image ID; config_digest remains receipt-bound.
                    inspect = {"Id": plan["repository_qualified_digest"],
                        "RepoDigests": [plan["repository_qualified_digest"]],
                        "Os": "linux", "Architecture": "amd64", "Config": {"Labels": {
                            staging_helper.TOPOLOGY_LABEL: json.dumps(topology,
                                sort_keys=True, separators=(",", ":"))}}}
                    return subprocess.CompletedProcess(command, 0,
                        stdout=json.dumps(inspect).encode(), stderr=b"")
                def remover(path, ignore_errors=False):
                    target = Path(path)
                    if branch == "credential_cleanup_failure" and target.name == "docker":
                        raise OSError("synthetic credential cleanup failure")
                    if branch == "final_cleanup_failure" and target.name.startswith("operation-"):
                        raise OSError("synthetic final cleanup failure")
                    shutil.rmtree(target, ignore_errors=ignore_errors)
                class NoReadResolver:
                    def _read_reference(self, _reference):
                        raise AssertionError("revision-bound helper lease reopened source")
                lease = BrokerLease(NoReadResolver(),
                    SecretReference("personal", "GHCR_TOKEN", "personal"),
                    binding_id="helper-lifecycle", binding_version=1,
                    deadline=datetime.now(timezone.utc) + timedelta(minutes=1),
                    lease_id="helper-lifecycle", material=canary, snapshot_bound=True)
                response = lease.consume(lambda credential: staging_helper.execute(
                    plan, credential, run_root=verified, runner=runner,
                    anonymous_probe=lambda *_args: True,
                    cgroup_identity=lambda _unit: (
                        "/user.slice/user-1000.slice/user@1000.service/app.slice/"
                        + plan["unit_name"]),
                    machine_epoch_reader=lambda: "raw-machine-a",
                    projected_identity_reader=lambda: "machine-a", remover=remover))
                self.assertIsNone(lease._material)
                captured_bytes = repr({"argv": captured["argv"],
                    "environment": captured["environment"], "logs": captured["logs"],
                    "frame": plan, "result": response}).encode()
                self.assertNotIn(canary, captured_bytes)
                self.assertNotIn(b"raw-machine-a", captured_bytes)
                if branch == "success":
                    self.assertTrue(response["ok"])
                    observation = response["payload"]["observation"]
                    self.assertEqual(observation["target_epoch_start"], "machine-a")
                    self.assertEqual(observation["target_epoch_end"], "machine-a")
                leftovers = tuple(verified.glob("operation-*"))
                if branch == "final_cleanup_failure":
                    self.assertEqual(response["code"], "cleanup_unproven")
                    for leftover in leftovers: shutil.rmtree(leftover)
                else:
                    self.assertEqual(leftovers, ())


if __name__ == "__main__": unittest.main()
