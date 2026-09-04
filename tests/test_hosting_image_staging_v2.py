from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from sandbox.hosting.images.staging_models import HelperIdentity, StagingTarget, staging_digest
from sandbox.hosting.images.staging_v2 import (
    BatchImageObservation, BatchObservation, StageRequestSet, StagedImageProofSet,
    StagingPolicySet,
)
from tests.hosting_image_fixtures import FakeBroker, stage_request, staging_policy
from tests.test_hosting_image_plan_set import FakeVerifier, make_bundle, policy_mapping


def plan_set():
    from sandbox.hosting.images.plan_set import verify_release_bundle
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name); digest = make_bundle(root)
    plan = verify_release_bundle(policy_mapping(digest), root, FakeVerifier())
    temp.cleanup()
    return plan


def policy_set(plan=None):
    plan = plan or plan_set()
    target = StagingTarget("machine-a", "target-a", "daemon-a")
    helper = HelperIdentity("sha256:" + "9" * 64, "sandbox-image-stage-helper-v2",
                            "a" * 40, "systemd-cgroup-v2-batch-stage-v2")
    body = {"schema_version": 2, "plan_set_digest": plan.plan_set_digest,
            "target": target.as_mapping(), "helper": helper.as_mapping(),
            "broker_recipient": f"ghcr-plan-set-read:{plan.plan_set_digest}",
            "broker_binding_id": "binding-a", "broker_binding_version": 3,
            "credential_reference_revision": "credential-revision-a",
            "operation": "ghcr.plan-set.read",
            "capability_revision": "systemd-cgroup-v2-batch-stage-v2"}
    return StagingPolicySet(2, staging_digest(
        "sandbox.hosting.images.staging-policy-set.v2", body), plan.plan_set_digest,
        target, helper, body["broker_recipient"], "binding-a", 3,
        "credential-revision-a", "ghcr.plan-set.read",
        "systemd-cgroup-v2-batch-stage-v2")


def request_set(plan=None, policy=None, *, request_id="stage-set-a", generation=0):
    plan = plan or plan_set(); policy = policy or policy_set(plan)
    return StageRequestSet.create(request_id=request_id, expected_generation=generation,
        plan_set=plan, staging_policy_digest=policy.policy_digest,
        target=policy.target, confirmed=True)


def observation(plan, policy):
    images = tuple(BatchImageObservation(item.name, item.repository, item.image_ref,
        item.config_digest, item.platform, item.config_digest, "denied", "succeeded")
        for item in plan.receipt.images)
    body = {"target_epoch_start": "machine-a", "target_epoch_end": "machine-a",
            "daemon_epoch_start": "daemon-a", "daemon_epoch_end": "daemon-a",
            "target": policy.target.as_mapping(),
            "images": [item.as_mapping() for item in images]}
    return BatchObservation("machine-a", "machine-a", "daemon-a", "daemon-a",
        policy.target, images, staging_digest(
            "sandbox.hosting.images.batch-observation.v2", body))


class FakePrepared:
    def __init__(self, plan, policy, *, unsafe_cleanup=False):
        self.frame = {"unit_name": "sandbox-image-stage-fake.service"}
        self.plan = plan; self.policy = policy; self.unsafe_cleanup = unsafe_cleanup
        self.deliveries = 0

    def deliver(self, credential):
        self.deliveries += 1
        if self.unsafe_cleanup:
            from sandbox.hosting.images.staging_worker import StageWorkerError
            raise StageWorkerError("pull_failed",
                process={"unit_inactive": False, "cgroup_empty_or_removed": False},
                cleanup={"complete": False})
        return observation(self.plan, self.policy), {
            "unit_name": self.frame["unit_name"],
            "cgroup": ("/user.slice/user-1000.slice/user@1000.service/app.slice/"
                       + self.frame["unit_name"]),
            "delegated": False, "escape_allowed": False,
            "unit_inactive": True, "cgroup_empty_or_removed": True}, {"complete": True}

    def cancel(self):
        return {"unit_inactive": not self.unsafe_cleanup,
                "cgroup_empty_or_removed": not self.unsafe_cleanup,
                "cleanup_complete": not self.unsafe_cleanup}


class FakeBatchWorker:
    def __init__(self, *, unsafe_cleanup=False):
        self.prepares = 0; self.unsafe_cleanup = unsafe_cleanup; self.prepared = None

    def prepare(self, request, policy):
        self.prepares += 1
        self.prepared = FakePrepared(request.plan_set, policy,
                                     unsafe_cleanup=self.unsafe_cleanup)
        return self.prepared


class SafeFailurePrepared(FakePrepared):
    """Models a negative helper frame whose cleanup proof is complete."""

    def deliver(self, credential):
        from sandbox.hosting.images.staging_worker import StageWorkerError
        raise StageWorkerError("pull_failed",
            process={"unit_inactive": True, "cgroup_empty_or_removed": True},
            cleanup={"complete": True},
            pull_failure={"image": "worker", "class": "denied"})

    def cancel(self):
        # A transient systemd unit may already be unloaded by this point.
        return {"unit_inactive": False, "cgroup_empty_or_removed": False,
                "cleanup_complete": False}


class SafeFailureWorker(FakeBatchWorker):
    def prepare(self, request, policy):
        self.prepares += 1
        self.prepared = SafeFailurePrepared(request.plan_set, policy)
        return self.prepared


class TestV2BatchStaging(unittest.TestCase):
    def test_v2_pull_failure_classes_are_closed_and_redacted(self):
        from sandbox.hosting.images.staging_helper import _classify_pull_failure
        cases = {
            "denied": b"unauthorized: token synthetic-secret rejected",
            "not_found": b"manifest unknown: repository path missing",
            "network": b"dial tcp: network is unreachable",
            "timeout": b"context deadline exceeded",
            "no_space": b"write layer: no space left on device",
            "daemon": b"unexpected daemon response synthetic-secret",
        }
        for expected, stderr in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(_classify_pull_failure(b"", stderr), expected)

    def test_v2_helper_emits_only_failed_image_and_normalized_class(self):
        import shutil
        import subprocess
        from sandbox.hosting.images import staging_helper
        from sandbox.hosting.images.staging_worker import StageWorkerV2
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        class Capture:
            def prepare(self, _remote, frame, *, timeout_seconds):
                self.frame = frame; return object()
        transport = Capture(); StageWorkerV2(transport).prepare(request, policy)
        frame = transport.frame
        frame["helper"]["artifact_digest"] = "sha256:" + hashlib.sha256(
            Path(staging_helper.__file__).read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            def runner(argv, *, environment, input_data=None, timeout=300):
                command = tuple(argv)
                if command[:2] == ("docker", "login"):
                    config = Path(environment["DOCKER_CONFIG"]); config.mkdir(parents=True)
                    return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
                if command[:2] == ("docker", "info"):
                    return subprocess.CompletedProcess(command, 0, stdout=b"daemon-a\n", stderr=b"")
                return subprocess.CompletedProcess(command, 1,
                    stdout=b"repository-url synthetic-secret", stderr=b"manifest unknown")
            result = staging_helper.execute_v2(frame, b"canary", run_root=Path(temp),
                runner=runner, anonymous_probe=lambda *_: True,
                cgroup_identity=lambda unit: "/app.slice/" + unit,
                machine_epoch_reader=lambda: "machine-a", remover=shutil.rmtree)
        self.assertEqual(result["code"], "pull_failed")
        self.assertEqual(result["payload"]["pull_failure"],
                         {"image": "queue", "class": "not_found"})
        self.assertEqual(set(result["payload"]), {"process", "cleanup", "pull_failure"})
        self.assertNotIn("synthetic-secret", json.dumps(result))

    def test_v2_timeout_propagates_through_helper_worker_ledger_and_status(self):
        import shutil
        import subprocess
        from sandbox.hosting.images import staging_helper
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        from sandbox.hosting.images.staging_worker import StageWorkerV2
        from sandbox.transports.remote_hosting_images import RemoteStageResponse
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)

        class Channel:
            def __init__(self, frame, root): self.frame = frame; self.root = root
            def deliver(self, _credential):
                def runner(argv, *, environment, input_data=None, timeout=300):
                    command = tuple(argv)
                    if command[:2] == ("docker", "login"):
                        Path(environment["DOCKER_CONFIG"]).mkdir(parents=True)
                        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
                    if command[:2] == ("docker", "info"):
                        return subprocess.CompletedProcess(command, 0, stdout=b"daemon-a\n", stderr=b"")
                    raise subprocess.TimeoutExpired(command, timeout)
                response = staging_helper.execute_v2(self.frame, b"canary",
                    run_root=self.root, runner=runner, anonymous_probe=lambda *_: True,
                    cgroup_identity=lambda unit: "/app.slice/" + unit,
                    machine_epoch_reader=lambda: "machine-a", remover=shutil.rmtree)
                response["payload"]["process"].update(
                    unit_inactive=True, cgroup_empty_or_removed=True)
                return RemoteStageResponse(response["ok"], response["code"],
                                           response["payload"], 2)
            def cancel(self):
                return {"unit_inactive": False, "cgroup_empty_or_removed": False,
                        "cleanup_complete": False}
        class Transport:
            def __init__(self, root): self.root = root
            def prepare(self, _remote, frame, *, timeout_seconds):
                frame["helper"]["artifact_digest"] = "sha256:" + hashlib.sha256(
                    Path(staging_helper.__file__).read_bytes()).hexdigest()
                return Channel(frame, self.root)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repository = StageRepository(root / "ledger")
            service = ImagePlanSetStagingService(repository=repository, broker=FakeBroker(),
                worker=StageWorkerV2(Transport(root / "run")))
            (root / "run").mkdir()
            result = service.stage(request, policy)
            status = ImagePlanSetStagingService(
                repository=repository, broker=None, worker=None).status(request)
        self.assertEqual((result.code, result.pull_failure.as_mapping()),
                         ("pull_failed", {"image": "queue", "class": "timeout"}))
        self.assertEqual(status.as_mapping(), result.as_mapping())

    def test_v2_pull_failure_result_round_trips_through_ledger_and_status(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        with tempfile.TemporaryDirectory() as temp:
            repository = StageRepository(Path(temp))
            service = ImagePlanSetStagingService(
                repository=repository, broker=FakeBroker(), worker=SafeFailureWorker())
            result = service.stage(request, policy)
            status = ImagePlanSetStagingService(
                repository=repository, broker=None, worker=None).status(request)
        expected = {"image": "worker", "class": "denied"}
        self.assertEqual(result.as_mapping()["pull_failure"], expected)
        self.assertEqual(status.as_mapping(), result.as_mapping())
        self.assertNotIn("synthetic-secret", json.dumps(status.as_mapping()))

    def test_legacy_v2_pull_failure_without_diagnostic_remains_readable(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        with tempfile.TemporaryDirectory() as temp:
            repository = StageRepository(Path(temp))
            ImagePlanSetStagingService(repository=repository, broker=FakeBroker(),
                worker=SafeFailureWorker()).stage(request, policy)
            with repository.target_lock(policy.target.target_identity):
                state = repository._load_unlocked(policy.target.target_identity)
                state["records"][request.request_id]["result"].pop("pull_failure")
                repository._write_unlocked(policy.target.target_identity, state)
            status = ImagePlanSetStagingService(
                repository=repository, broker=None, worker=None).status(request)
        self.assertEqual((status.code, status.pull_failure), ("pull_failed", None))
        self.assertNotIn("pull_failure", status.as_mapping())

    def test_v2_worker_accepts_only_closed_pull_failure_diagnostic(self):
        from sandbox.hosting.images.staging_worker import StageWorkerError, StageWorkerV2
        from sandbox.transports.remote_hosting_images import RemoteStageResponse
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)

        class Channel:
            def __init__(self, payload): self.payload = payload
            def deliver(self, _credential):
                return RemoteStageResponse(False, "pull_failed", self.payload, 2)
        class Transport:
            def __init__(self, failure=None, extra_payload=None):
                self.failure = failure
                self.extra_payload = extra_payload
            def prepare(self, _remote, frame, *, timeout_seconds):
                process = {"unit_name": frame["unit_name"],
                    "cgroup": "/app.slice/" + frame["unit_name"],
                    "delegated": False, "escape_allowed": False,
                    "unit_inactive": True, "cgroup_empty_or_removed": True}
                payload = {"process": process, "cleanup": {"complete": True}}
                if self.failure is not None: payload["pull_failure"] = self.failure
                if self.extra_payload is not None: payload["detail"] = self.extra_payload
                return Channel(payload)
        with self.assertRaises(StageWorkerError) as caught:
            StageWorkerV2(Transport({"image": "queue", "class": "network"})).prepare(
                request, policy).deliver(b"canary")
        self.assertEqual((caught.exception.code, caught.exception.pull_failure),
                         ("pull_failed", {"image": "queue", "class": "network"}))
        malformed = (
            (None, None),
            ({"image": "api", "class": "network"}, None),
            ({"image": "queue", "class": "other"}, None),
            ({"image": 1, "class": "network"}, None),
            ({"image": "queue", "class": ["network"]}, None),
            ({"image": "queue", "class": "network"}, "forbidden"),
        )
        for failure, extra_payload in malformed:
            with self.subTest(failure=failure, extra_payload=extra_payload), \
                    self.assertRaises(StageWorkerError) as caught_malformed:
                StageWorkerV2(Transport(failure, extra_payload)).prepare(
                    request, policy).deliver(b"canary")
            self.assertEqual((caught_malformed.exception.code,
                              caught_malformed.exception.pull_failure),
                             ("observation_invalid", None))

    def test_malformed_ledger_pull_failure_and_v1_or_wrong_code_use_are_rejected(self):
        from sandbox.hosting.images.staging_repository import StageRepositoryError, _result_from
        base = {"schema_version": 2, "ok": False, "result_class": "failed",
                "code": "pull_failed", "request_id": "stage-set-a", "generation": 1}
        malformed = (
            {**base, "pull_failure": None},
            {**base, "pull_failure": {"image": "api", "class": "network"}},
            {**base, "pull_failure": {"image": "queue", "class": "other"}},
            {**base, "pull_failure": {"image": "queue", "class": "network", "raw": "x"}},
            {**base, "schema_version": 1,
             "pull_failure": {"image": "queue", "class": "network"}},
            {**base, "code": "helper_failed",
             "pull_failure": {"image": "queue", "class": "network"}},
        )
        for raw in malformed:
            with self.subTest(raw=raw), self.assertRaises(StageRepositoryError):
                _result_from(raw, raw["request_id"])

    def test_v1_pull_failure_result_envelope_is_unchanged(self):
        from sandbox.hosting.images.staging_models import StageResult
        from sandbox.hosting.images.staging_repository import _result_from
        result = StageResult(1, False, "failed", "pull_failed", "stage-v1", 1)
        expected = {"schema_version": 1, "ok": False, "result_class": "failed",
                    "code": "pull_failed", "request_id": "stage-v1", "generation": 1}
        self.assertEqual(result.as_mapping(), expected)
        self.assertEqual(_result_from(expected, "stage-v1").as_mapping(), expected)

    def test_batch_observation_accepts_docker29_manifest_image_id(self):
        plan = plan_set(); item = plan.receipt.images[0]
        observed = BatchImageObservation(
            item.name, item.repository, item.image_ref, item.config_digest,
            item.platform, item.manifest_digest, "denied", "succeeded")
        self.assertEqual(observed.local_image_id, item.manifest_digest)

    def test_v2_remote_delivery_failure_is_not_misreported_as_broker_failure(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        from sandbox.isolation.credential_resolver import BrokerLease, SecretReference
        from sandbox.transports.remote_hosting_images import RemoteImageStageError

        class Broker:
            def prepare_for_stage(self, **_kwargs):
                return BrokerLease(
                    object(), SecretReference("personal", "GHCR_TOKEN", "personal"),
                    binding_id="binding", binding_version=1,
                    deadline=datetime.now(timezone.utc) + timedelta(minutes=1),
                    lease_id="lease", material=b"synthetic-stage-canary",
                    snapshot_bound=True,
                )

        class Prepared(FakePrepared):
            def deliver(self, _credential):
                raise RemoteImageStageError(
                    "helper_failed",
                    process={"unit_inactive": True, "cgroup_empty_or_removed": True},
                    cleanup={"complete": True},
                )

            def cancel(self):
                return {"unit_inactive": False, "cgroup_empty_or_removed": False,
                        "cleanup_complete": False}

        class Worker(FakeBatchWorker):
            def prepare(self, request, policy):
                self.prepares += 1
                self.prepared = Prepared(request.plan_set, policy)
                return self.prepared

        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        with tempfile.TemporaryDirectory() as directory:
            result = ImagePlanSetStagingService(repository=StageRepository(Path(directory)),
                broker=Broker(), worker=Worker()).stage(request, policy)
        self.assertEqual((result.result_class, result.code), ("failed", "helper_failed"))

    @staticmethod
    def _uncertain(repository, request, *, effect_entered=False):
        from sandbox.hosting.images.staging_v2 import StageResultSet
        repository.accept(request)
        process = {"unit_inactive": False, "cgroup_empty_or_removed": False}
        if effect_entered:
            repository.transition(request, "credential_pending")
            repository.transition(request, "helper_running", process={
                "unit_name": "sandbox-image-stage-old.service",
                "unit_inactive": False, "cgroup_empty_or_removed": False})
            repository.transition(request, "pulling")
        repository.transition(request, "uncertain", process=process,
                              cleanup={"complete": False})
        return repository.commit(request, StageResultSet(
            2, False, "uncertain", "cleanup_unproven", request.request_id, 1))

    @staticmethod
    def _safe_close_evidence(request, record):
        from sandbox.hosting.images.staging_worker import unit_name
        unit = unit_name(request.request_id, request.request_digest)
        return {"schema_version": 1, "request_id": request.request_id,
                "request_digest": request.request_digest,
                "generation": record["generation"],
                "ledger_revision": record["ledger_revision"],
                "unit_name": unit, "load_state": "not-found",
                "active_state": "inactive", "sub_state": "dead",
                "description": unit, "main_pid": "0", "control_group": "",
                "exact_effect": False, "unit_inactive": True,
                "cgroup_empty_or_removed": True, "cleanup_complete": True}

    def test_v2_prepare_description_drift_is_fenced_and_replayed_without_launch(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        from sandbox.transports.remote_hosting_images import RemoteImageStageError
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        class Worker:
            def __init__(self): self.prepares = 0
            def prepare(self, _request, _policy):
                self.prepares += 1
                raise RemoteImageStageError("helper_failed",
                    process={"unit_inactive": False, "cgroup_empty_or_removed": False,
                             "bootstrap_phase": "inode", "bootstrap_code": "inode_os"},
                    cleanup={"complete": False})
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(); service = ImagePlanSetStagingService(
                repository=StageRepository(Path(directory)), broker=FakeBroker(), worker=worker)
            result = service.stage(request, policy); replay = service.stage(request, policy)
            self.assertEqual((result.result_class, result.code), ("uncertain", "cleanup_unproven"))
            self.assertEqual(replay.as_mapping(), result.as_mapping())
            self.assertEqual(worker.prepares, 1)
            status = service.repository.record_status(
                request.target.target_identity, request.request_id)
            self.assertEqual((status["process"]["bootstrap_phase"],
                              status["process"]["bootstrap_code"]),
                             ("inode", "inode_os"))

    def test_v2_reconcile_safe_closes_old_precredential_uncertainty_without_replay(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        class Poison:
            def __getattr__(self, name):
                raise AssertionError(f"effect boundary opened: {name}")
        with tempfile.TemporaryDirectory() as directory:
            repository = StageRepository(Path(directory)); old = self._uncertain(repository, request)
            service = ImagePlanSetStagingService(
                repository=repository, broker=Poison(), worker=Poison())
            calls = []
            result = service.reconcile_uncertain_failure(request, policy,
                lambda supplied, record: calls.append((supplied, record))
                    or self._safe_close_evidence(supplied, record),
                lambda *_args: self.fail("wrong observer"))
            replay = service.reconcile_uncertain_failure(request, policy,
                lambda *_args: self.fail("terminal replay must not observe the host"),
                lambda *_args: self.fail("terminal replay must not observe the host"))
            self.assertEqual((old.result_class, result.result_class, result.code),
                             ("uncertain", "failed", "precredential_bootstrap_failed"))
            self.assertEqual(replay.as_mapping(), result.as_mapping())
            self.assertEqual(len(calls), 1)
            status = repository.record_status(request.target.target_identity, request.request_id)
            self.assertEqual((status["effect_entered"], status["process"], status["cleanup"]),
                (False, {"unit_inactive": True, "cgroup_empty_or_removed": True},
                 {"complete": True}))
            next_request = request_set(plan, policy, request_id="stage-set-next", generation=1)
            self.assertEqual(repository.accept(next_request)[0], "accepted")

    def test_v2_reconcile_refuses_every_identity_effect_and_observation_drift(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        mutations = (
            lambda value: value.update(request_id="other"),
            lambda value: value.update(request_digest="sha256:" + "f" * 64),
            lambda value: value.update(generation=2),
            lambda value: value.update(ledger_revision=999),
            lambda value: value.update(unit_name="sandbox-image-stage-" + "f" * 32 + ".service"),
            lambda value: value.update(load_state="loaded"),
            lambda value: value.update(active_state="active"),
            lambda value: value.update(main_pid="123"),
            lambda value: value.update(control_group="foreign"),
            lambda value: value.update(exact_effect=True),
            lambda value: value.update(cgroup_empty_or_removed=False),
            lambda value: value.pop("cleanup_complete"),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                repository = StageRepository(Path(directory)); old = self._uncertain(repository, request)
                service = ImagePlanSetStagingService(
                    repository=repository, broker=None, worker=None)
                def observer(supplied, record):
                    evidence = self._safe_close_evidence(supplied, record); mutate(evidence)
                    return evidence
                result = service.reconcile_precredential_failure(request, policy, observer)
                self.assertEqual(result.as_mapping(), old.as_mapping())
                other = request_set(plan, policy, request_id=f"other-{index}", generation=1)
                self.assertEqual(repository.accept(other)[2].code, "target_busy")

        with tempfile.TemporaryDirectory() as directory:
            repository = StageRepository(Path(directory)); old = self._uncertain(
                repository, request, effect_entered=True)
            observed = []
            result = ImagePlanSetStagingService(
                repository=repository, broker=None, worker=None,
            ).reconcile_precredential_failure(
                request, policy, lambda *_args: observed.append(True))
            self.assertEqual(result.as_mapping(), old.as_mapping())
            self.assertEqual(observed, [])

    def test_v2_reconcile_commit_and_owner_release_are_one_durable_write(self):
        from unittest.mock import patch
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        with tempfile.TemporaryDirectory() as directory:
            repository = StageRepository(Path(directory)); old = self._uncertain(repository, request)
            service = ImagePlanSetStagingService(repository=repository, broker=None, worker=None)
            original_write = repository._write_unlocked; observed_states = []
            def capture(target, state):
                observed_states.append(json.loads(json.dumps(state)))
                return original_write(target, state)
            with patch.object(repository, "_write_unlocked", side_effect=OSError("interrupted")):
                result = service.reconcile_precredential_failure(request, policy,
                    self._safe_close_evidence)
            self.assertEqual(result.as_mapping(), old.as_mapping())
            self.assertEqual(repository.lookup_for_request(request).as_mapping(), old.as_mapping())
            with patch.object(repository, "_write_unlocked", side_effect=capture):
                closed = service.reconcile_precredential_failure(request, policy,
                    self._safe_close_evidence)
            self.assertEqual(closed.code, "precredential_bootstrap_failed")
            self.assertEqual(len(observed_states), 1)
            state = observed_states[0]
            self.assertIsNone(state["active_owner"])
            self.assertEqual(state["records"][request.request_id]["result"]["code"],
                             "precredential_bootstrap_failed")

    def test_v2_posteffect_reconcile_closes_cleanup_uncertainty_and_allows_retry(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        class Poison:
            def __getattr__(self, name):
                raise AssertionError(f"effect boundary opened: {name}")
        with tempfile.TemporaryDirectory() as directory:
            repository = StageRepository(Path(directory)); old = self._uncertain(
                repository, request, effect_entered=True)
            service = ImagePlanSetStagingService(
                repository=repository, broker=Poison(), worker=Poison())
            calls = []
            evidence = {"unit_inactive": True, "cgroup_empty_or_removed": True,
                        "workspace_absent": True}
            observer = lambda supplied, record: calls.append((supplied, record)) or evidence
            with patch.object(repository, "_write_unlocked",
                              side_effect=OSError("interrupted")):
                interrupted = service.reconcile_uncertain_failure(
                    request, policy, lambda *_args: self.fail("wrong observer"), observer)
            self.assertEqual(interrupted.as_mapping(), old.as_mapping())
            self.assertEqual(repository.lookup_for_request(request).as_mapping(), old.as_mapping())
            observed_states = []; original_write = repository._write_unlocked
            def capture(target, state):
                observed_states.append(json.loads(json.dumps(state)))
                return original_write(target, state)
            with patch.object(repository, "_write_unlocked", side_effect=capture):
                result = service.reconcile_uncertain_failure(
                    request, policy, lambda *_args: self.fail("wrong observer"), observer)
            replay = service.reconcile_uncertain_failure(
                request, policy, lambda *_args: self.fail("replay must not observe"),
                lambda *_args: self.fail("replay must not observe"))
            self.assertEqual((old.result_class, result.result_class, result.code),
                             ("uncertain", "failed", "cleanup_reconciled"))
            self.assertIsNone(result.proof)
            self.assertEqual(replay.as_mapping(), result.as_mapping())
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(observed_states), 1)
            self.assertIsNone(observed_states[0]["active_owner"])
            self.assertNotIn(request.request_id, observed_states[0]["proofs"])
            self.assertEqual(observed_states[0]["records"][request.request_id]
                             ["result"]["code"], "cleanup_reconciled")
            status = repository.record_status(request.target.target_identity, request.request_id)
            self.assertEqual((status["effect_entered"], status["process"], status["cleanup"]),
                (True, {"unit_inactive": True, "cgroup_empty_or_removed": True},
                 {"complete": True}))
            next_request = request_set(plan, policy, request_id="stage-set-retry", generation=1)
            self.assertEqual(repository.accept(next_request)[0], "accepted")

    def test_v2_posteffect_reconcile_fails_closed_on_partial_or_mismatched_evidence(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        evidence_mutations = (
            lambda value: value.update(unit_inactive=False),
            lambda value: value.update(cgroup_empty_or_removed=False),
            lambda value: value.update(workspace_absent=False),
            lambda value: value.pop("workspace_absent"),
            lambda value: value.update(extra=True),
        )
        for index, mutate in enumerate(evidence_mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                repository = StageRepository(Path(directory)); old = self._uncertain(
                    repository, request, effect_entered=True)
                service = ImagePlanSetStagingService(
                    repository=repository, broker=None, worker=None)
                evidence = {"unit_inactive": True, "cgroup_empty_or_removed": True,
                            "workspace_absent": True}
                mutate(evidence)
                result = service.reconcile_posteffect_cleanup(
                    request, policy, lambda *_args: evidence)
                self.assertEqual(result.as_mapping(), old.as_mapping())
                other = request_set(plan, policy, request_id=f"blocked-{index}", generation=1)
                self.assertEqual(repository.accept(other)[2].code, "target_busy")

        with tempfile.TemporaryDirectory() as directory:
            repository = StageRepository(Path(directory)); old = self._uncertain(
                repository, request, effect_entered=True)
            drifted = request_set(plan, policy, request_id=request.request_id)
            object.__setattr__(drifted, "request_digest", "sha256:" + "f" * 64)
            observed = []
            result = ImagePlanSetStagingService(
                repository=repository, broker=None, worker=None,
            ).reconcile_posteffect_cleanup(
                drifted, policy, lambda *_args: observed.append(True))
            self.assertEqual((result.result_class, result.code),
                             ("refused", "request_conflict"))
            self.assertEqual(observed, [])
            drifted_policy = policy_set(plan)
            object.__setattr__(drifted_policy, "policy_digest", "sha256:" + "e" * 64)
            result = ImagePlanSetStagingService(
                repository=repository, broker=None, worker=None,
            ).reconcile_posteffect_cleanup(
                request, drifted_policy, lambda *_args: observed.append(True))
            self.assertEqual((result.result_class, result.code),
                             ("refused", "policy_mismatch"))
            self.assertEqual(observed, [])

    def test_real_description_drift_transport_fences_v2_without_touching_incumbent(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        from sandbox.hosting.images.staging_worker import StageWorkerV2
        from tests.hosting_image_fixtures import description_drift_transport
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        with tempfile.TemporaryDirectory() as directory:
            transport, sender, commands = description_drift_transport(
                policy.helper.as_mapping(), 2)
            service = ImagePlanSetStagingService(repository=StageRepository(Path(directory)),
                broker=FakeBroker(), worker=StageWorkerV2(transport))
            result = service.stage(request, policy); replay = service.stage(request, policy)
            self.assertEqual((result.result_class, result.code), ("uncertain", "cleanup_unproven"))
            self.assertEqual(replay.as_mapping(), result.as_mapping())
            self.assertEqual(sender.prepares, 1)
            self.assertFalse(any(" kill " in item or " stop " in item for item in commands))

    def test_v2_cli_refusal_preserves_response_schema(self):
        from sandbox.commands.hosting import _cmd_host_stage

        plan = plan_set()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            plan_path = root / "plan-set.json"
            plan_path.write_text(json.dumps(plan.as_mapping()))
            args = SimpleNamespace(
                project_dir=str(project), environment="production",
                remote="scaleway-sandbox", request_id="stage/v2-refusal",
                expected_generation=0, verified_plan=str(plan_path),
                stage_status=False, confirm=True,
            )
            output = StringIO()
            with patch("sandbox.core._paths.RUNTIME_DIR", root / "runtime"), \
                    redirect_stdout(output), self.assertRaises(SystemExit):
                _cmd_host_stage(args)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual((payload["result_class"], payload["code"]),
                         ("refused", "policy_mismatch"))

    def test_v2_cli_reconcile_is_confirmed_closed_and_opens_no_secret_or_worker(self):
        from sandbox.commands.hosting import _cmd_host_stage
        from sandbox.hosting.images.staging_v2 import StageResultSet
        plan = plan_set(); policy = policy_set(plan); scope = plan.policy.target_scope
        expected_request = request_set(plan, policy)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root / "project"; project.mkdir()
            runtime = root / "runtime"; plan_path = root / "plan-set.json"
            plan_path.write_text(json.dumps(plan.as_mapping()))
            scope_id = hashlib.sha256(
                f"{scope.remote}\0{scope.project}\0{scope.environment}".encode()).hexdigest()
            policy_path = (runtime / "hosting" / "image-staging" / "policies"
                           / f"{scope_id}-{policy.plan_set_digest.removeprefix('sha256:')}.json")
            policy_path.parent.mkdir(parents=True)
            policy_path.write_text(json.dumps({"policy": policy.as_mapping(),
                "binding": "must-not-open", "secret_sources": "must-not-open"}))
            args = SimpleNamespace(project_dir=str(project), environment=scope.environment,
                remote=scope.remote, request_id="stage-set-a", expected_generation=0,
                verified_plan=str(plan_path), stage_status=False, reconcile=True, confirm=True)
            observed = []
            class Service:
                def __init__(self, *, repository, broker, worker):
                    self.repository = repository
                    self.assertions = (broker, worker)
                def reconcile_uncertain_failure(self, request, supplied_policy,
                                                observer, posteffect_observer):
                    self_outer.assertEqual(self.assertions, (None, None))
                    self_outer.assertEqual(supplied_policy.as_mapping(), policy.as_mapping())
                    self_outer.assertTrue(callable(posteffect_observer))
                    observed.append(observer(request, {"ledger_revision": 7, "generation": 1}))
                    return StageResultSet(2, False, "failed",
                        "precredential_bootstrap_failed", request.request_id, 1)
            self_outer = self
            closed = {"unit_name": "exact", "load_state": "not-found",
                "active_state": "inactive", "sub_state": "dead", "description": "exact",
                "main_pid": "0", "control_group": "", "exact_effect": False,
                "unit_inactive": True, "cgroup_empty_or_removed": True,
                "cleanup_complete": True}
            output = StringIO()
            with patch("sandbox.core._paths.RUNTIME_DIR", runtime), \
                 patch("sandbox.hosting.images.staging_v2_service.ImagePlanSetStagingService",
                       Service), \
                 patch("sandbox.transports.remote_hosting_images.RegisteredRemoteImageTransport.observe_precredential_absence",
                       return_value=closed) as observer, \
                 patch("sandbox.secrets.sources.SourceRegistry",
                       side_effect=AssertionError("secret source opened")), \
                 patch("sandbox.secrets.service.GHCRStagingCredentialAdapter",
                       side_effect=AssertionError("broker opened")), \
                 patch("sandbox.hosting.images.staging_worker.StageWorkerV2",
                       side_effect=AssertionError("worker opened")), \
                 redirect_stdout(output):
                _cmd_host_stage(args)
            payload = json.loads(output.getvalue())
            self.assertEqual((payload["result_class"], payload["code"]),
                             ("failed", "precredential_bootstrap_failed"))
            self.assertEqual(observed, [{"schema_version": 1,
                "request_id": "stage-set-a", "request_digest": expected_request.request_digest,
                "generation": 1, "ledger_revision": 7, **closed}])
            observer.assert_called_once()

    def test_v2_cli_reconcile_projects_only_posteffect_cleanup_booleans(self):
        from sandbox.commands.hosting import _cmd_host_stage
        from sandbox.hosting.images.staging_worker import unit_name
        from sandbox.hosting.images.staging_v2 import StageResultSet
        plan = plan_set(); policy = policy_set(plan); scope = plan.policy.target_scope
        expected_request = request_set(plan, policy)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root / "project"; project.mkdir()
            runtime = root / "runtime"; plan_path = root / "plan-set.json"
            plan_path.write_text(json.dumps(plan.as_mapping()))
            scope_id = hashlib.sha256(
                f"{scope.remote}\0{scope.project}\0{scope.environment}".encode()).hexdigest()
            policy_path = (runtime / "hosting" / "image-staging" / "policies"
                           / f"{scope_id}-{policy.plan_set_digest.removeprefix('sha256:')}.json")
            policy_path.parent.mkdir(parents=True)
            policy_path.write_text(json.dumps({"policy": policy.as_mapping(),
                "binding": "must-not-open", "secret_sources": "must-not-open"}))
            args = SimpleNamespace(project_dir=str(project), environment=scope.environment,
                remote=scope.remote, request_id="stage-set-a", expected_generation=0,
                verified_plan=str(plan_path), stage_status=False, reconcile=True, confirm=True)
            projected = []
            class Service:
                def __init__(self, *, repository, broker, worker):
                    self.repository = repository
                    self_outer.assertEqual((broker, worker), (None, None))
                def reconcile_uncertain_failure(self, request, supplied_policy,
                                                precredential_observer,
                                                posteffect_observer):
                    self_outer.assertEqual(supplied_policy.as_mapping(), policy.as_mapping())
                    projected.append(posteffect_observer(
                        request, {"ledger_revision": 7, "generation": 1}))
                    return StageResultSet(2, False, "failed", "cleanup_reconciled",
                                          request.request_id, 1)
            self_outer = self
            closed = {"unit_inactive": True, "cgroup_empty_or_removed": True,
                      "workspace_absent": True}
            output = StringIO()
            with patch("sandbox.core._paths.RUNTIME_DIR", runtime), \
                 patch("sandbox.hosting.images.staging_v2_service.ImagePlanSetStagingService",
                       Service), \
                 patch("sandbox.transports.remote_hosting_images.RegisteredRemoteImageTransport.observe_posteffect_cleanup",
                       return_value=closed) as observer, \
                 patch("sandbox.transports.remote_hosting_images.RegisteredRemoteImageTransport.observe_precredential_absence",
                       side_effect=AssertionError("wrong observer")), \
                 patch("sandbox.secrets.sources.SourceRegistry",
                       side_effect=AssertionError("secret source opened")), \
                 patch("sandbox.secrets.service.GHCRStagingCredentialAdapter",
                       side_effect=AssertionError("broker opened")), \
                 patch("sandbox.hosting.images.staging_worker.StageWorkerV2",
                       side_effect=AssertionError("worker opened")), \
                 redirect_stdout(output):
                _cmd_host_stage(args)
            payload = json.loads(output.getvalue())
            self.assertEqual((payload["result_class"], payload["code"]),
                             ("failed", "cleanup_reconciled"))
            self.assertEqual(projected, [closed])
            expected_unit = unit_name(
                expected_request.request_id, expected_request.request_digest)
            observer.assert_called_once_with(scope.remote, expected_unit)

    def test_one_lease_one_helper_persists_one_exhaustive_proof_and_replays(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        with tempfile.TemporaryDirectory() as temp:
            broker = FakeBroker(); worker = FakeBatchWorker()
            service = ImagePlanSetStagingService(
                repository=StageRepository(Path(temp)), broker=broker, worker=worker)
            first = service.stage(request, policy); replay = service.stage(request, policy)
            self.assertTrue(first.ok)
            self.assertEqual(first.as_mapping(), replay.as_mapping())
            self.assertEqual((len(broker.calls), worker.prepares,
                              worker.prepared.deliveries), (1, 1, 1))
            self.assertEqual([row["name"] for row in first.proof.as_mapping()
                              ["observation"]["images"]], ["queue", "web", "worker"])
            self.assertEqual(StagedImageProofSet.from_mapping(
                first.proof.as_mapping()).as_mapping(), first.proof.as_mapping())
            self.assertNotIn("credential", repr(first.as_mapping()).lower())

    def test_policy_drift_refuses_before_broker_or_helper(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        raw = policy.as_mapping(); raw["plan_set_digest"] = "sha256:" + "f" * 64
        identity = dict(raw); identity.pop("policy_digest")
        raw["broker_recipient"] = f'ghcr-plan-set-read:{raw["plan_set_digest"]}'
        identity = dict(raw); identity.pop("policy_digest")
        raw["policy_digest"] = staging_digest(
            "sandbox.hosting.images.staging-policy-set.v2", identity)
        with tempfile.TemporaryDirectory() as temp:
            broker = FakeBroker(); worker = FakeBatchWorker()
            result = ImagePlanSetStagingService(repository=StageRepository(Path(temp)),
                broker=broker, worker=worker).stage(request, StagingPolicySet.from_mapping(raw))
        self.assertEqual(result.result_class, "refused")
        self.assertEqual((broker.calls, worker.prepares), ([], 0))

    def test_unproven_cleanup_is_uncertain_and_emits_no_proof(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        with tempfile.TemporaryDirectory() as temp:
            result = ImagePlanSetStagingService(repository=StageRepository(Path(temp)),
                broker=FakeBroker(), worker=FakeBatchWorker(unsafe_cleanup=True)).stage(request, policy)
        self.assertEqual((result.result_class, result.code, result.proof),
                         ("uncertain", "cleanup_unproven", None))

    def test_complete_helper_cleanup_is_not_downgraded_by_unloaded_cancel_probe(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        with tempfile.TemporaryDirectory() as temp:
            worker = SafeFailureWorker()
            result = ImagePlanSetStagingService(repository=StageRepository(Path(temp)),
                broker=FakeBroker(), worker=worker).stage(request, policy)
        self.assertEqual((result.result_class, result.code, result.proof),
                         ("failed", "pull_failed", None))

    def test_v1_and_v2_share_target_single_flight(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        with tempfile.TemporaryDirectory() as temp:
            repository = StageRepository(Path(temp))
            v1_policy = staging_policy(); v1_request = stage_request(policy=v1_policy)
            self.assertEqual(repository.accept(v1_request)[0], "accepted")
            broker = FakeBroker(); worker = FakeBatchWorker()
            result = ImagePlanSetStagingService(
                repository=repository, broker=broker, worker=worker).stage(request, policy)
        self.assertEqual((result.result_class, result.code), ("refused", "target_busy"))
        self.assertEqual((broker.calls, worker.prepares), ([], 0))

    def test_one_retained_proof_set_gets_one_custody_lease(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        from tests.test_hosting_image_staging_repository import OrderedHostPort, OrderedTargetPort
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        with tempfile.TemporaryDirectory() as temp:
            repository = StageRepository(Path(temp)); result = ImagePlanSetStagingService(
                repository=repository, broker=FakeBroker(), worker=FakeBatchWorker()).stage(
                    request, policy)
            events = []
            with repository.proof_custody_transaction(policy.target.target_identity,
                    target_mutation_port=OrderedTargetPort(events),
                    host_state_port=OrderedHostPort(events)) as port:
                retained = port.validate_retained_proof(
                    stage_request_id=request.request_id,
                    stage_request_digest=request.request_digest,
                    proof_digest=result.proof.proof_digest,
                    stage_generation=result.generation,
                    ledger_authority="feature-050-stage-ledger-v2", ledger_revision=7,
                    supplied_proof=result.proof)
                lease = port.prepare(lease_id="batch-lease-a",
                    holder="activation-owner/batch-a",
                    admission_deadline="2099-01-01T00:00:00Z",
                    activation_request_id="batch-activation-a",
                    activation_request_digest="sha256:" + "c" * 64,
                    stage_request_id=request.request_id,
                    stage_request_digest=request.request_digest,
                    proof_digest=result.proof.proof_digest,
                    stage_generation=result.generation,
                    ledger_authority="feature-050-stage-ledger-v2", ledger_revision=7)
            self.assertEqual(retained.proof_digest, result.proof.proof_digest)
            self.assertEqual(lease.proof_digest, result.proof.proof_digest)
            self.assertEqual([event[1] for event in events], ["target", "host", "host", "target"])

    def test_expired_v2_proof_status_preserves_schema_and_tombstone_code(self):
        from sandbox.hosting.images.staging_models import StageProofTombstone
        from sandbox.hosting.images.staging_repository import StageRepository
        from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
        plan = plan_set(); policy = policy_set(plan)
        with tempfile.TemporaryDirectory() as temp:
            repository = StageRepository(Path(temp))
            first_request = request_set(plan, policy)
            result = ImagePlanSetStagingService(repository=repository,
                broker=FakeBroker(), worker=FakeBatchWorker()).stage(first_request, policy)
            with repository.target_lock(policy.target.target_identity):
                state = repository._load_unlocked(policy.target.target_identity)
                state["tombstones"][first_request.request_id] = StageProofTombstone(
                    first_request.request_id, first_request.request_digest,
                    result.proof.proof_digest).as_mapping()
                del state["proofs"][first_request.request_id]
                del state["records"][first_request.request_id]
                repository._write_unlocked(policy.target.target_identity, state)
            status = ImagePlanSetStagingService(
                repository=repository, broker=None, worker=None).status(first_request)
        self.assertEqual(status.as_mapping(), {
            "schema_version": 2, "ok": False, "result_class": "refused",
            "code": "proof_expired", "request_id": first_request.request_id,
            "generation": 1})

    def test_helper_v2_frame_refuses_boolean_version_and_identity_drift(self):
        from sandbox.hosting.images.staging_helper import _closed_plan_v2
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        worker = __import__("sandbox.hosting.images.staging_worker",
                            fromlist=["StageWorkerV2"]).StageWorkerV2
        class Capture:
            def prepare(self, _remote, frame, *, timeout_seconds):
                self.frame = frame; return object()
        transport = Capture(); worker(transport).prepare(request, policy)
        # Measurement is checked after shape, so malformed frames refuse without file access.
        for mutate in (lambda raw: raw.update(schema_version=True),
                       lambda raw: raw["images"][0].update(config_digest="bad"),
                       lambda raw: raw["service_image_bindings"].append(
                           deepcopy(raw["service_image_bindings"][0]))):
            raw = deepcopy(transport.frame); mutate(raw)
            with self.assertRaisesRegex(ValueError, "protocol_invalid"):
                _closed_plan_v2(raw)
        from sandbox.transports.remote_hosting_images import (
            RemoteImageStageError, parse_stage_response,
        )
        with self.assertRaises(RemoteImageStageError):
            parse_stage_response({"schema_version": True, "ok": False,
                                  "code": "protocol_invalid", "payload": {}})

    def test_real_v2_helper_uses_one_login_three_pulls_and_no_topology_label(self):
        import shutil
        import subprocess
        from sandbox.hosting.images import staging_helper
        from sandbox.hosting.images.staging_worker import StageWorkerV2
        plan = plan_set(); policy = policy_set(plan); request = request_set(plan, policy)
        class Capture:
            def prepare(self, _remote, frame, *, timeout_seconds):
                self.frame = frame; return object()
        transport = Capture(); StageWorkerV2(transport).prepare(request, policy)
        frame = transport.frame
        frame["helper"]["artifact_digest"] = "sha256:" + hashlib.sha256(
            Path(staging_helper.__file__).read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            run_root = Path(temp); calls = []
            def runner(argv, *, environment, input_data=None, timeout=300):
                calls.append(tuple(argv)); command = tuple(argv)
                if command[:2] == ("docker", "login"):
                    config = Path(environment["DOCKER_CONFIG"]); config.mkdir(parents=True)
                    (config / "config.json").write_text("credential-material")
                    return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
                if command[:2] == ("docker", "pull"):
                    return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
                if command[:2] == ("docker", "info"):
                    return subprocess.CompletedProcess(command, 0, stdout=b"daemon-a\n", stderr=b"")
                image = next(item for item in frame["images"]
                             if item["repository_qualified_digest"] == command[3])
                # Docker 29's containerd image store reports the pulled
                # manifest as Image ID; the signed receipt still carries the
                # independent config digest.
                inspected = {"Id": image["manifest_digest"],
                    "RepoDigests": [image["repository_qualified_digest"]],
                    "Os": "linux", "Architecture": "amd64", "Config": {"Labels": {}}}
                return subprocess.CompletedProcess(command, 0,
                    stdout=json.dumps(inspected).encode(), stderr=b"")
            result = staging_helper.execute_v2(frame, b"canary", run_root=run_root,
                runner=runner, anonymous_probe=lambda *_: True,
                cgroup_identity=lambda unit: (
                    "/user.slice/user-1000.slice/user@1000.service/app.slice/" + unit),
                machine_epoch_reader=lambda: "machine-a", remover=shutil.rmtree)
        self.assertTrue(result["ok"])
        self.assertEqual(sum(call[:2] == ("docker", "login") for call in calls), 1)
        self.assertEqual(sum(call[:2] == ("docker", "pull") for call in calls), 3)
        self.assertEqual([row["name"] for row in result["payload"]["observation"]["images"]],
                         ["queue", "web", "worker"])
        for row, expected in zip(result["payload"]["observation"]["images"], frame["images"]):
            self.assertEqual(row["config_digest"], expected["config_digest"])
            self.assertEqual(row["local_image_id"], expected["manifest_digest"])


if __name__ == "__main__":
    unittest.main()
