"""The public stage command reuses retained proof without reopening credentials."""

from contextlib import contextmanager, redirect_stdout
from io import StringIO
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from sandbox.hosting.images.staging_repository import StageRepository
from sandbox.hosting.images.staging_v2_service import ImagePlanSetStagingService
from tests.hosting_image_fixtures import FakeBroker
from tests.test_hosting_image_staging_v2 import (
    FakeBatchWorker, plan_set, policy_set, request_set,
)


class StageRetryCliTests(unittest.TestCase):
    def _invoke(self, repository, plan, policy, generation, *, reconcile=False):
        from sandbox.commands.hosting import _cmd_host_stage

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            runtime = root / "runtime"
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan.as_mapping()))
            scope = plan.policy.target_scope
            selector = hashlib.sha256(
                f"{scope.remote}\0{scope.project}\0{scope.environment}".encode()).hexdigest()
            policy_path = (runtime / "hosting" / "image-staging" / "policies"
                           / f"{selector}-{plan.plan_set_digest.removeprefix('sha256:')}.json")
            policy_path.parent.mkdir(parents=True)
            policy_path.write_text(json.dumps({"policy": policy.as_mapping(),
                "binding": "must-not-open", "secret_sources": "must-not-open"}))
            args = SimpleNamespace(project_dir=str(project), environment=scope.environment,
                remote=scope.remote, request_id="stage-set-a", expected_generation=generation,
                verified_plan=str(plan_path), stage_status=False, reconcile=reconcile, confirm=True)
            output = StringIO()
            lock_held = []
            @contextmanager
            def target_lock(target):
                self.assertEqual(target, policy.target.target_identity)
                lock_held.append(True)
                try:
                    yield
                finally:
                    lock_held.pop()
            owner = SimpleNamespace(target_mutation_transaction=target_lock)
            recovery = SimpleNamespace(target_mutation_port=lambda kind: owner)
            def reconcile_result(request, supplied_policy, *observers):
                self.assertEqual(lock_held, [True], "reconciliation has no target owner")
                self.assertEqual(supplied_policy, policy)
                return ImagePlanSetStagingService(repository=repository,
                    broker=None, worker=None).status(request)
            with patch("sandbox.core._paths.RUNTIME_DIR", runtime), \
                    patch("sandbox.hosting.images.staging_repository.StageRepository",
                          return_value=repository), \
                    patch("sandbox.commands.hosting.RecoveryRepository", return_value=recovery), \
                    patch.object(ImagePlanSetStagingService, "reconcile_uncertain_failure",
                                 side_effect=reconcile_result), \
                    patch("sandbox.isolation.credential_binding.CredentialBinding.from_dict",
                          side_effect=AssertionError("credential binding opened")), \
                    patch("sandbox.secrets.sources.SourceRegistry",
                          side_effect=AssertionError("secret source opened")), \
                    patch("sandbox.hosting.images.staging_worker.StageWorkerV2",
                          side_effect=AssertionError("worker opened")), redirect_stdout(output):
                try:
                    _cmd_host_stage(args)
                except SystemExit as exc:
                    self.assertEqual(exc.code, 1)
            return json.loads(output.getvalue())

    def test_same_request_reuses_original_success_after_generation_advances(self):
        plan = plan_set()
        policy = policy_set(plan)
        request = request_set(plan, policy)
        with tempfile.TemporaryDirectory() as directory:
            repository = StageRepository(Path(directory))
            worker = FakeBatchWorker()
            original = ImagePlanSetStagingService(repository=repository,
                broker=FakeBroker(), worker=worker).stage(request, policy)
            self.assertTrue(original.ok)
            for generation in (0, original.generation):
                with self.subTest(generation=generation):
                    self.assertEqual(self._invoke(repository, plan, policy, generation),
                                     original.as_mapping())
            self.assertEqual(worker.prepares, 1)
            self.assertEqual(repository.target_revision(request.target.target_identity)[0], 1)

    def test_uncertainty_never_reopens_credentials_and_reconcile_holds_target_owner(self):
        plan = plan_set()
        policy = policy_set(plan)
        request = request_set(plan, policy)
        with tempfile.TemporaryDirectory() as directory:
            repository = StageRepository(Path(directory))
            service = ImagePlanSetStagingService(repository=repository, broker=FakeBroker(),
                                               worker=FakeBatchWorker(unsafe_cleanup=True))
            original = service.stage(request, policy)
            self.assertEqual(original.result_class, "uncertain")
            self.assertEqual(self._invoke(repository, plan, policy, 0), original.as_mapping())
            self.assertEqual(self._invoke(repository, plan, policy, 0, reconcile=True),
                             original.as_mapping())
            result = self._invoke(repository, plan, policy, original.generation)
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "request_conflict")
            self.assertEqual(result["generation"], original.generation)

    def test_expired_proof_reports_current_ledger_generation(self):
        from sandbox.hosting.images.staging_models import StageProofTombstone
        plan = plan_set()
        policy = policy_set(plan)
        request = request_set(plan, policy)
        with tempfile.TemporaryDirectory() as directory:
            repository = StageRepository(Path(directory))
            original = ImagePlanSetStagingService(repository=repository, broker=FakeBroker(),
                worker=FakeBatchWorker()).stage(request, policy)
            target = request.target.target_identity
            with repository.target_lock(target):
                state = repository._load_unlocked(target)
                state["tombstones"][request.request_id] = StageProofTombstone(
                    request.request_id, request.request_digest,
                    original.proof.proof_digest).as_mapping()
                del state["records"][request.request_id]
                del state["proofs"][request.request_id]
                repository._write_unlocked(target, state)
            result = self._invoke(repository, plan, policy, 0)
            self.assertEqual((result["code"], result["generation"]), ("proof_expired", 1))


if __name__ == "__main__":
    unittest.main()
