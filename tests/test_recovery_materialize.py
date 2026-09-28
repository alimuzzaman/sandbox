import tempfile
import unittest
import os
from pathlib import Path

from sandbox.recovery.catalog import RecoveryCatalog
from sandbox.recovery.materialize import ScopedMaterializer, SourceBinding
from sandbox.recovery.models import RecoveryProfile
from sandbox.recovery.service import RecoveryService


class TestScopedMaterialization(unittest.TestCase):
    def run_capture(self, mode="ok"):
        profile = RecoveryProfile("site", "test", "filesystem", ("host-manifest:site",),
                                  ("database", "files"), "full", "stable", (), "encrypted",
                                  "target", "verify", "standard")
        class Adapter:
            observations = 0

            def observe(self, remote, plan):
                self.observations += 1
                return SourceBinding(remote, "machine", "revision",
                                     "changed" if mode == "changed" and self.observations > 1 else "source")

            def capture(self, remote, artifact, destination, binding, *, backup_operation_id):
                assert backup_operation_id == "set"
                if mode == "missing":
                    return ()
                path = destination / "validated.tar"
                path.write_bytes(b"validated native artifact")
                if mode == "empty":
                    path.write_bytes(b"")
                if mode == "escape":
                    path = destination.parent / "outside"
                    path.write_bytes(b"outside")
                if mode == "symlink":
                    link = destination / "link"
                    link.symlink_to(path)
                    path = link
                return (path,)

        class Capture:
            called = False

            def publish_files(self, set_id, files, **kwargs):
                self.called = True
                assert all(path.is_file() for path in files.values())
                self.kwargs = kwargs
                return {"status": "complete", "id": set_id}

        capture = Capture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "materialized"
            service = RecoveryService(RecoveryCatalog(1, (profile,)), capture=capture,
                                      materializer=ScopedMaterializer(root, Adapter()))
            result = service.create_materialized("set", ("site",), confirm=True, remote="remote")
            self.assertEqual(list(root.iterdir()), [])
        return result, capture

    def test_symbolic_bindings_survive_controller_capture(self):
        result, capture = self.run_capture()
        self.assertTrue(result["ok"], result)
        self.assertEqual(capture.kwargs["profile_bindings"]["site"]["allowed_roots"],
                         ["host-manifest:site"])
        self.assertEqual(capture.kwargs["provenance"]["revision"], "revision")

    def test_incomplete_changed_and_unowned_artifacts_never_publish(self):
        for mode, code in (("missing", "incomplete_materialization"), ("changed", "source_changed"),
                           ("empty", "invalid_artifact"), ("escape", "invalid_artifact"),
                           ("symlink", "invalid_artifact")):
            with self.subTest(mode=mode):
                result, capture = self.run_capture(mode)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], code)
                self.assertFalse(capture.called)

    def test_missing_controller_does_not_bypass_gate(self):
        service = RecoveryService(RecoveryCatalog(1, ()))
        result = service.create_materialized("set", (), confirm=True, remote="remote")
        self.assertEqual(result["error"]["code"], "missing_profiles")

    def test_existing_materialization_root_is_tightened(self):
        profile = RecoveryProfile("site", "test", "filesystem", ("host-manifest:site",),
                                  ("source",), "full", "stable", (), "encrypted",
                                  "target", "verify", "standard")
        class Adapter:
            def observe(self, remote, plan):
                return SourceBinding(remote, "machine", "revision", "source")
            def capture(self, remote, artifact, destination, binding, *, backup_operation_id):
                assert backup_operation_id == "set"
                path = destination / "validated.tar"
                path.write_bytes(b"validated")
                return (path,)
        class Capture:
            def publish_files(self, *args, **kwargs):
                return {"status": "complete"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "materialized"
            root.mkdir(mode=0o755)
            # The mode may be reduced by the process umask; explicitly widen it
            # before invoking the coordinator so the assertion is meaningful.
            os.chmod(root, 0o755)
            service = RecoveryService(RecoveryCatalog(1, (profile,)), capture=Capture(),
                                      materializer=ScopedMaterializer(root, Adapter()))
            result = service.create_materialized("set", ("site",), confirm=True, remote="remote")
            self.assertTrue(result["ok"], result)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_unexpected_controller_failure_is_bounded(self):
        profile = RecoveryProfile("site", "test", "filesystem", ("host-manifest:site",),
                                  ("source",), "full", "stable", (), "encrypted",
                                  "target", "verify", "standard")
        class Adapter:
            def observe(self, remote, plan):
                raise RuntimeError("private controller path")
        with tempfile.TemporaryDirectory() as directory:
            service = RecoveryService(
                RecoveryCatalog(1, (profile,)), capture=object(),
                materializer=ScopedMaterializer(Path(directory) / "materialized", Adapter()),
            )
            result = service.create_materialized("set", ("site",), confirm=True, remote="remote")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "materialization_failed")
        self.assertNotIn("private controller path", str(result))
