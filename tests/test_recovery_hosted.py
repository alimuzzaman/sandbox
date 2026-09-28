import tempfile
import hashlib
from dataclasses import replace
import unittest
from pathlib import Path

from sandbox.recovery.catalog import RecoveryCatalog
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.hosted import (
    HostedCaptureReceipt,
    HostedObservation,
    HostedRecoveryMaterializer,
)
from sandbox.recovery.materialize import ScopedMaterializer, SourceBinding
from sandbox.recovery.models import RecoveryProfile
from sandbox.recovery.service import RecoveryService


def _profile(source_type="filesystem", sources=("host-manifest:site",)):
    return RecoveryProfile(
        "site", "test", source_type, ("host-manifest:site",), sources,
        "full", "stable", (), "encrypted", "target", "verify", "standard",
    )


class _Controller:
    capture_contract_version = 2
    def __init__(self, *, coverage=None, native_format="tar"):
        self.coverage = coverage
        self.native_format = native_format
        self.request_ids = []

    def observe(self, remote, plan):
        binding = SourceBinding(remote, "machine", "revision", "config-digest")
        coverage = self.coverage or {
            artifact.profile_id: tuple(artifact.sources)
            for artifact in plan.artifacts
        }
        return HostedObservation(binding, coverage)

    def capture(self, remote, artifact, destination, binding, request_id, *, backup_operation_id):
        self.request_ids.append(request_id)
        path = destination / "capture.bin"
        path.write_bytes(b"native capture")
        return HostedCaptureReceipt(
            artifact.profile_id, artifact.artifact_id, request_id, (path,),
            tuple(artifact.sources), self.native_format,
            capture_contract_version=2, backup_operation_id=backup_operation_id,
            artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            started_at=1.0, completed_at=2.0,
        )


class _Capture:
    def publish_files(self, set_id, files, **kwargs):
        self.files = files
        self.kwargs = kwargs
        return {"id": set_id, "status": "complete"}


class TestHostedRecoveryMaterializer(unittest.TestCase):
    def _service(self, controller, profile=None):
        profile = profile or _profile()
        root = Path(self.directory) / "materialized"
        capture = _Capture()
        service = RecoveryService(
            RecoveryCatalog(1, (profile,)), capture=capture,
            materializer=ScopedMaterializer(root, HostedRecoveryMaterializer(controller)),
        )
        return service, capture

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.directory = self._temporary.name

    def tearDown(self):
        self._temporary.cleanup()

    def test_native_receipt_and_exact_source_coverage_are_required(self):
        controller = _Controller()
        service, capture = self._service(controller)
        result = service.create_materialized("hosted-set", ("site",), confirm=True,
                                             remote="scaleway-sandbox")
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(controller.request_ids), 1)
        self.assertEqual(capture.kwargs["provenance"]["machine_identity"], "machine")

        controller = _Controller(coverage={"site": ("host-manifest:site", "extra")})
        service, capture = self._service(controller)
        result = service.create_materialized("hosted-set", ("site",), confirm=True,
                                             remote="scaleway-sandbox")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "incomplete_materialization")
        self.assertFalse(hasattr(capture, "files"))

    def test_receipt_format_must_match_profile_source_type(self):
        controller = _Controller(native_format="mariadb-sql")
        service, capture = self._service(controller)
        result = service.create_materialized("hosted-set", ("site",), confirm=True,
                                             remote="scaleway-sandbox")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_materialization_receipt")
        self.assertFalse(hasattr(capture, "files"))

    def test_controller_error_does_not_escape_private_diagnostics(self):
        class FailingController(_Controller):
            def observe(self, remote, plan):
                raise RecoveryError("ssh://user:password@host/private/path", "remote_failed")

        service, _ = self._service(FailingController())
        result = service.create_materialized("hosted-set", ("site",), confirm=True,
                                             remote="scaleway-sandbox")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "materialization_observe_failed")
        self.assertNotIn("private/path", str(result))
        self.assertNotIn("password", str(result))

    def test_request_identity_changes_with_capture_declaration_and_replays_stably(self):
        controller = _Controller()
        materializer = HostedRecoveryMaterializer(controller)
        profile = _profile()
        plan_service, _ = self._service(controller, profile)
        plan = plan_service.catalog
        from sandbox.recovery.planner import build_plan
        artifact = build_plan(plan, ("site",)).artifacts[0]
        binding = SourceBinding("remote", "machine", "revision", "digest")
        first = materializer._request_id("remote", artifact, binding, "set-a")
        second = materializer._request_id("remote", artifact, binding, "set-a")
        self.assertEqual(first, second)
        self.assertNotEqual(first, materializer._request_id("remote", artifact, binding, "set-b"))
        altered = _profile(sources=("host-manifest:other",))
        altered_artifact = build_plan(RecoveryCatalog(1, (altered,)), ("site",)).artifacts[0]
        self.assertNotEqual(first, materializer._request_id("remote", altered_artifact, binding, "set-a"))

    def test_old_controller_refused_before_observation(self):
        controller = _Controller()
        controller.capture_contract_version = 1
        controller.observe = lambda *args: self.fail("unsupported controller was observed")
        service, capture = self._service(controller)
        result = service.create_materialized("set-a", ("site",), confirm=True, remote="remote")
        self.assertEqual(result["error"]["code"], "unsupported_materialization")
        self.assertFalse(hasattr(capture, "files"))

    def test_receipt_identity_digest_and_times_fail_closed(self):
        class InvalidController(_Controller):
            def capture(inner, *args, **kwargs):
                return replace(super(InvalidController, inner).capture(*args, **kwargs), **changes)

        for changes in ({"backup_operation_id": "old-set"}, {"artifact_sha256": "bad"},
                        {"capture_contract_version": 1}, {"started_at": float("nan")},
                        {"completed_at": 0.5}):
            with self.subTest(changes=changes):
                service, capture = self._service(InvalidController())
                result = service.create_materialized("set-a", ("site",), confirm=True, remote="remote")
                self.assertEqual(result["error"]["code"], "invalid_materialization_receipt")
                self.assertFalse(hasattr(capture, "files"))


if __name__ == "__main__":
    unittest.main()
