import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from tests.hosting_image_fixtures import FakeBroker, FakeWorker, stage_request, staging_policy


class TestImageStagingService(unittest.TestCase):
    def test_unproved_prepare_cleanup_is_fenced_and_never_relaunched_on_replay(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_service import ImageStagingService
        from sandbox.transports.remote_hosting_images import RemoteImageStageError
        class Worker:
            def __init__(self): self.calls = 0
            def prepare(self, _request, _policy):
                self.calls += 1
                raise RemoteImageStageError("helper_failed",
                    process={"unit_inactive": False, "cgroup_empty_or_removed": False},
                    cleanup={"complete": False})
        with tempfile.TemporaryDirectory() as directory:
            policy = staging_policy(); request = stage_request(policy=policy); worker = Worker()
            service = ImageStagingService(
                repository=StageRepository(Path(directory)), broker=FakeBroker(), worker=worker)
            result = service.stage(request, policy)
            replay = service.stage(request, policy)
            self.assertEqual((result.result_class, result.code), ("uncertain", "cleanup_unproven"))
            self.assertEqual(replay.as_mapping(), result.as_mapping())
            self.assertEqual(worker.calls, 1)

    def test_proven_not_launched_prepare_failure_is_terminal_and_replayed(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_service import ImageStagingService
        from sandbox.transports.remote_hosting_images import RemoteImageStageError
        class Worker:
            def __init__(self): self.calls = 0
            def prepare(self, _request, _policy):
                self.calls += 1
                raise RemoteImageStageError("helper_failed",
                    process={"unit_inactive": True, "cgroup_empty_or_removed": True,
                             "not_launched": True}, cleanup={"complete": True})
        with tempfile.TemporaryDirectory() as directory:
            policy = staging_policy(); request = stage_request(policy=policy); worker = Worker()
            service = ImageStagingService(
                repository=StageRepository(Path(directory)), broker=FakeBroker(), worker=worker)
            result = service.stage(request, policy); replay = service.stage(request, policy)
            self.assertEqual((result.result_class, result.code), ("failed", "helper_failed"))
            self.assertEqual(replay.as_mapping(), result.as_mapping())
            self.assertEqual(worker.calls, 1)

    def test_real_description_drift_transport_fences_v1_without_touching_incumbent(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_service import ImageStagingService
        from sandbox.hosting.images.staging_worker import StageWorker
        from tests.hosting_image_fixtures import description_drift_transport
        with tempfile.TemporaryDirectory() as directory:
            policy = staging_policy(); request = stage_request(policy=policy)
            transport, sender, commands = description_drift_transport(
                policy.helper.as_mapping(), 1)
            service = ImageStagingService(repository=StageRepository(Path(directory)),
                broker=FakeBroker(), worker=StageWorker(transport))
            result = service.stage(request, policy); replay = service.stage(request, policy)
            self.assertEqual((result.result_class, result.code), ("uncertain", "cleanup_unproven"))
            self.assertEqual(replay.as_mapping(), result.as_mapping())
            self.assertEqual(sender.prepares, 1)
            self.assertFalse(any(" kill " in item or " stop " in item for item in commands))

    def test_exact_success_is_canonical_replay_and_has_zero_activation_capability(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_service import ImageStagingService
        with tempfile.TemporaryDirectory() as directory:
            policy = staging_policy(); request = stage_request(policy=policy)
            broker = FakeBroker(); worker = FakeWorker()
            service = ImageStagingService(repository=StageRepository(Path(directory)), broker=broker, worker=worker)
            result = service.stage(request, policy); replay = service.stage(request, policy)
            self.assertTrue(result.ok)
            self.assertEqual(result.as_mapping(), replay.as_mapping())
            rendered = repr(result.as_mapping()).lower()
            for forbidden in ("credential", "compose", "activation", "rollback", "prune"):
                self.assertNotIn(forbidden, rendered)
            self.assertEqual(len(worker.calls), 1)

    def test_policy_refusal_precedes_broker_and_helper(self):
        from sandbox.hosting.images.staging_models import StagingPolicy, staging_digest
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_service import ImageStagingService
        with tempfile.TemporaryDirectory() as directory:
            policy = staging_policy(); request = stage_request(policy=policy)
            broker = FakeBroker(); worker = FakeWorker()
            service = ImageStagingService(repository=StageRepository(Path(directory)), broker=broker, worker=worker)
            raw = policy.as_mapping(); raw["plan_digest"] = "sha256:" + "f" * 64
            identity = dict(raw); identity.pop("policy_digest")
            raw["policy_digest"] = staging_digest(
                "sandbox.hosting.images.staging-policy.v1", identity)
            result = service.stage(request, StagingPolicy.from_mapping(raw))
            self.assertFalse(result.ok); self.assertEqual((broker.calls, worker.calls), ([], []))

    def test_mutated_proof_fails_downstream_validation(self):
        from sandbox.hosting.images import validate_staged_image_proof
        from sandbox.hosting.images.staging_models import StagingContractError
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_service import ImageStagingService
        with tempfile.TemporaryDirectory() as directory:
            policy = staging_policy(); request = stage_request(policy=policy)
            result = ImageStagingService(repository=StageRepository(Path(directory)),
                broker=FakeBroker(), worker=FakeWorker()).stage(request, policy)
            changed = result.proof.as_mapping(); changed["target"]["daemon_identity"] = "daemon-b"
            with self.assertRaises(StagingContractError): validate_staged_image_proof(changed)

    def test_full_canonical_proof_preserves_every_authorized_identity_and_reorders_safely(self):
        from sandbox.hosting.images import validate_staged_image_proof
        from sandbox.hosting.images.staging_models import StagedImageProof
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_service import ImageStagingService
        from tests.hosting_image_fixtures import reverse_objects
        with tempfile.TemporaryDirectory() as directory:
            policy = staging_policy(); request = stage_request(policy=policy)
            result = ImageStagingService(repository=StageRepository(Path(directory)),
                broker=FakeBroker(), worker=FakeWorker()).stage(request, policy)
            raw = result.proof.as_mapping()
            self.assertEqual(set(raw), StagedImageProof.FIELDS)
            self.assertEqual(raw["request"], {"request_id": request.request_id,
                                              "request_digest": request.request_digest})
            self.assertEqual(raw["plan_digest"], request.plan.plan_digest)
            self.assertEqual(raw["staging_policy_digest"], policy.policy_digest)
            self.assertEqual(raw["target"], policy.target.as_mapping())
            expected_helper = policy.helper.as_mapping(); expected_helper.pop("entry")
            self.assertEqual(raw["helper"], expected_helper)
            self.assertEqual(raw["delivery_identity_projection"],
                             request.plan.delivery_identity_projection.as_mapping())
            self.assertEqual(raw["observed_identity"]["target"], policy.target.as_mapping())
            self.assertEqual(raw["observed_identity"]["local_image_id"],
                             raw["observed_identity"]["config_digest"])
            self.assertEqual(validate_staged_image_proof(reverse_objects(raw)).as_mapping(), raw)
            self.assertEqual(validate_staged_image_proof(raw).staging_generation,
                             result.generation)

    def test_proof_unknown_missing_stale_and_every_identity_mutation_refuses(self):
        from sandbox.hosting.images import validate_staged_image_proof
        from sandbox.hosting.images.staging_models import StagingContractError
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_service import ImageStagingService
        with tempfile.TemporaryDirectory() as directory:
            policy = staging_policy(); request = stage_request(policy=policy)
            result = ImageStagingService(repository=StageRepository(Path(directory)),
                broker=FakeBroker(), worker=FakeWorker()).stage(request, policy)
            original = result.proof.as_mapping()
            mutations = (
                (("schema_version",), 2), (("request", "request_id"), "other-request"),
                (("request", "request_digest"), "sha256:" + "0" * 64),
                (("plan_digest",), "sha256:" + "0" * 64),
                (("staging_policy_digest",), "sha256:" + "0" * 64),
                (("target", "machine_identity"), "machine-b"),
                (("target", "target_identity"), "target-b"),
                (("target", "daemon_identity"), "daemon-b"),
                (("helper", "artifact_digest"), "sha256:" + "0" * 64),
                (("helper", "runtime_revision"), "b" * 40),
                (("helper", "capability_revision"), "different-capability"),
                (("delivery_identity_projection", "target_scope", "remote"), "other-remote"),
                (("delivery_identity_projection", "target_scope", "project"), "other-project"),
                (("delivery_identity_projection", "target_scope", "environment"), "staging"),
                (("delivery_identity_projection", "registry"), "registry.example"),
                (("delivery_identity_projection", "repository"), "other/repository"),
                (("delivery_identity_projection", "manifest_digest"), "sha256:" + "0" * 64),
                (("delivery_identity_projection", "config_digest"), "sha256:" + "0" * 64),
                (("delivery_identity_projection", "manifest_media_type"), "other/media"),
                (("delivery_identity_projection", "repository_qualified_digest"),
                    "ghcr.io/other@sha256:" + "1" * 64),
                (("delivery_identity_projection", "intended_visibility"), "public"),
                (("delivery_identity_projection", "platform", "architecture"), "arm64"),
                (("delivery_identity_projection", "topology", "persistent_services"), ["web"]),
                (("observed_identity", "target_epoch_start"), "machine-b"),
                (("observed_identity", "target_epoch_end"), "machine-b"),
                (("observed_identity", "daemon_epoch_start"), "daemon-b"),
                (("observed_identity", "daemon_epoch_end"), "daemon-b"),
                (("observed_identity", "target", "machine_identity"), "machine-b"),
                (("observed_identity", "target", "target_identity"), "target-b"),
                (("observed_identity", "target", "daemon_identity"), "daemon-b"),
                (("observed_identity", "repository"), "other/repository"),
                (("observed_identity", "repo_digest"), "ghcr.io/other@sha256:" + "1" * 64),
                (("observed_identity", "config_digest"), "sha256:" + "0" * 64),
                (("observed_identity", "local_image_id"), "sha256:" + "3" * 64),
                (("observed_identity", "platform", "architecture"), "arm64"),
                (("observed_identity", "topology_digest"), "sha256:" + "0" * 64),
                (("observed_identity", "observed_topology", "one_shot_services"), []),
                (("registry_access_observation", "anonymous_exact_manifest"), "succeeded"),
                (("registry_access_observation", "authenticated_exact_manifest"), "denied"),
                (("registry_access_observation", "observation_digest"), "sha256:" + "0" * 64),
                (("observation_id",), "sha256:" + "0" * 64),
                (("staging_generation",), result.generation + 1),
                (("proof_digest",), "sha256:" + "0" * 64),
            )
            for path, value in mutations:
                with self.subTest(path=path):
                    changed = deepcopy(original); cursor = changed
                    for key in path[:-1]: cursor = cursor[key]
                    cursor[path[-1]] = value
                    with self.assertRaises(StagingContractError):
                        validate_staged_image_proof(changed)
            unknown_nested = deepcopy(original); unknown_nested["observed_identity"]["unknown"] = True
            for changed in ({**deepcopy(original), "unknown": True}, unknown_nested):
                with self.assertRaises(StagingContractError):
                    validate_staged_image_proof(changed)
            for missing in tuple(original):
                with self.subTest(missing=missing), self.assertRaises(StagingContractError):
                    validate_staged_image_proof({key: value for key, value in
                        deepcopy(original).items() if key != missing})

    def test_proof_replay_privacy_and_expired_authority_remain_separate(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_service import ImageStagingService
        with tempfile.TemporaryDirectory() as directory:
            repository = StageRepository(Path(directory)); policy = staging_policy()
            request = stage_request(policy=policy); broker = FakeBroker(); worker = FakeWorker()
            service = ImageStagingService(repository=repository, broker=broker, worker=worker)
            first = service.stage(request, policy); replay = service.stage(request, policy)
            self.assertEqual(first.as_mapping(), replay.as_mapping())
            rendered = repr(first.as_mapping()).lower()
            for forbidden in ("credential", "github_token", "ghcr_token", "docker_password",
                              "synthetic-stage-canary", "docker_config", "argv", "stdout",
                              "stderr", "/run/sandbox-image-stage"):
                self.assertNotIn(forbidden, rendered)
            self.assertEqual(len(worker.calls), 1)

    def test_real_repository_compaction_expires_old_proof_but_retained_proof_validates_and_replays(self):
        from sandbox.hosting.images import validate_staged_image_proof
        from sandbox.hosting.images.staging_models import MAX_PROOFS
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_service import ImageStagingService
        with tempfile.TemporaryDirectory() as directory:
            repository = StageRepository(Path(directory)); policy = staging_policy(); results = []
            for index in range(MAX_PROOFS + 1):
                request = stage_request(request_id=f"proof-{index}", generation=index,
                                        policy=policy)
                service = ImageStagingService(repository=repository,
                    broker=FakeBroker(), worker=FakeWorker())
                results.append((request, service.stage(request, policy)))
            expired_request, _expired_result = results[0]
            retained_request, retained_result = results[-1]
            self.assertEqual(repository.lookup(policy.target.target_identity,
                                               expired_request.request_id).code,
                             "proof_expired")
            self.assertEqual(validate_staged_image_proof(
                retained_result.proof.as_mapping()).as_mapping(),
                retained_result.proof.as_mapping())
            replay = ImageStagingService(repository=repository,
                broker=FakeBroker(), worker=FakeWorker()).stage(retained_request, policy)
            self.assertEqual(replay.as_mapping(), retained_result.as_mapping())

    def test_zero_forbidden_import_and_callable_reachability(self):
        import ast
        import sandbox.hosting.images as images
        from sandbox.hosting.images.staging_service import ImageStagingService
        from sandbox.hosting.images.staging_worker import StageWorker
        from sandbox.transports.remote_hosting_images import RegisteredRemoteImageTransport
        root = Path(__file__).parent.parent
        production = tuple(root / path for path in (
            "sandbox/hosting/images/staging_models.py",
            "sandbox/hosting/images/staging_policy.py",
            "sandbox/hosting/images/staging_repository.py",
            "sandbox/hosting/images/staging_service.py",
            "sandbox/hosting/images/staging_worker.py",
            "sandbox/transports/remote_hosting_images.py"))
        imports = set()
        for path in production:
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import): imports.update(alias.name for alias in node.names)
                if isinstance(node, ast.ImportFrom): imports.add(node.module or "")
        forbidden_modules = ("compose", "init", "runtime", "edge", "adoption",
                             "rollback", "prune", "sandbox.commands.hosting")
        for forbidden in forbidden_modules:
            self.assertFalse(any(name == forbidden or name.endswith("." + forbidden)
                                 for name in imports), forbidden)
        forbidden_callables = ("activate", "apply", "adopt", "rollback", "prune",
                               "deploy", "initialize", "continue_edge", "start_runtime")
        for owner in (images, ImageStagingService, StageWorker, RegisteredRemoteImageTransport):
            for name in forbidden_callables:
                self.assertFalse(hasattr(owner, name), (owner, name))
        for exported in images.__all__:
            self.assertFalse(any(word in exported.lower() for word in
                ("compose", "runtime", "edge", "adopt", "rollback", "prune", "activate")))

    def test_real_flows_never_reach_poisoned_runtime_capabilities(self):
        from sandbox.hosting.images.staging_models import StagingPolicy, staging_digest
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_service import ImageStagingService
        from sandbox.hosting.images.staging_worker import StageWorker
        from sandbox.transports.remote_hosting_images import RemoteStageResponse
        from tests.hosting_image_fixtures import local_observation

        poison_calls = []

        class PoisonedCapabilities:
            def _poison(self, name):
                def reached(*_args, **_kwargs):
                    poison_calls.append(name)
                    raise AssertionError(f"forbidden capability reached: {name}")
                return reached

            def __init__(self):
                for name in ("compose", "initialize", "start_runtime", "continue_edge",
                             "adopt", "rollback", "prune", "activate"):
                    setattr(self, name, self._poison(name))

        class Lease:
            def __init__(self):
                self.used = False

            def consume(self, consumer):
                if self.used:
                    raise AssertionError("lease reused")
                self.used = True
                return consumer(b"synthetic-credential")

            def invalidate(self):
                self.used = True

        class Broker(PoisonedCapabilities):
            def __init__(self):
                super().__init__(); self.prepares = 0

            def prepare_for_stage(self, **_kwargs):
                self.prepares += 1
                return Lease()

        class Channel:
            def __init__(self, frame, policy):
                self.frame = frame; self.policy = policy

            def deliver(self, _credential):
                observation = local_observation(self.policy)
                observation_payload = {"observation_id": observation.observation_id,
                                       **observation.body_mapping()}
                observation_payload["target"] = observation.target
                process = {
                    "unit_name": self.frame["unit_name"],
                    "cgroup": ("/user.slice/user-1000.slice/user@1000.service/app.slice/"
                               f"{self.frame['unit_name']}"),
                    "delegated": False, "escape_allowed": False,
                    "unit_inactive": True, "cgroup_empty_or_removed": True,
                }
                return RemoteStageResponse(True, "staged", {
                    "observation": observation_payload,
                    "process": process, "cleanup": {"complete": True},
                })

            def cancel(self):
                return {"unit_inactive": True, "cgroup_empty_or_removed": True,
                        "cleanup_complete": True}

        class Transport(PoisonedCapabilities):
            def __init__(self, policy):
                super().__init__(); self.policy = policy; self.prepares = 0

            def prepare(self, _remote, frame, *, timeout_seconds):
                self.prepares += 1
                self.assert_timeout = timeout_seconds
                return Channel(frame, self.policy)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); hosts = root / "hosts.json"
            legacy = b'{"feature047":true,"feature048":true,"authorizes_stage":false}'
            hosts.write_bytes(legacy)
            policy = staging_policy(); broker = Broker(); transport = Transport(policy)
            worker = StageWorker(transport)

            success_request = stage_request(request_id="poison-success", policy=policy)
            success_service = ImageStagingService(
                repository=StageRepository(root / "success"), broker=broker, worker=worker)
            self.assertTrue(success_service.stage(success_request, policy).ok)

            raw = policy.as_mapping(); raw["plan_digest"] = "sha256:" + "f" * 64
            identity = dict(raw); identity.pop("policy_digest")
            raw["policy_digest"] = staging_digest(
                "sandbox.hosting.images.staging-policy.v1", identity)
            refused = success_service.stage(
                stage_request(request_id="poison-refused", policy=policy),
                StagingPolicy.from_mapping(raw))
            self.assertEqual((refused.result_class, broker.prepares, transport.prepares),
                             ("refused", 1, 1))

            reconcile_request = stage_request(request_id="poison-reconcile", policy=policy)
            reconcile_repository = StageRepository(root / "reconcile")
            self.assertEqual(reconcile_repository.accept(reconcile_request)[0], "accepted")
            reconcile_service = ImageStagingService(
                repository=reconcile_repository, broker=broker, worker=worker)
            self.assertEqual(reconcile_service.status(reconcile_request).result_class,
                             "in_progress")
            reconciled = reconcile_service.reconcile(reconcile_request, policy,
                lambda _request, _record: {
                    "unit_inactive": True, "cgroup_empty_or_removed": True,
                    "cleanup_complete": True, "exact_effect": False,
                })
            self.assertTrue(reconciled.ok)
            self.assertEqual((broker.prepares, transport.prepares), (2, 2))
            self.assertEqual(poison_calls, [])
            self.assertEqual(hosts.read_bytes(), legacy)

    def test_legacy_047_048_evidence_is_rejected_and_hosts_state_is_unchanged(self):
        from sandbox.hosting.images import validate_staged_image_proof
        from sandbox.hosting.images.staging_models import StageRequest, StagingContractError
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_service import ImageStagingService
        legacy_proof = {"schema_version": 0, "host_receipt": "legacy-047",
                        "hosts_generation": 12, "compose_config_digest": "old"}
        with self.assertRaises(StagingContractError):
            validate_staged_image_proof(legacy_proof)
        policy = staging_policy()
        with self.assertRaises(StagingContractError) as raised:
            StageRequest.create(request_id="legacy", expected_generation=0,
                plan={"schema_version": 0, "recovery_receipt": "legacy-048"},
                staging_policy_digest=policy.policy_digest, target=policy.target,
                confirmed=True)
        self.assertEqual(raised.exception.code, "plan_invalid")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); hosts = root / "hosts.json"
            sentinel = b'{"feature048":"unchanged","authorizes_stage":false}'
            hosts.write_bytes(sentinel)
            request = stage_request(policy=policy)
            result = ImageStagingService(repository=StageRepository(root / "stage"),
                broker=FakeBroker(), worker=FakeWorker()).stage(request, policy)
            self.assertTrue(result.ok); self.assertEqual(hosts.read_bytes(), sentinel)


if __name__ == "__main__": unittest.main()
