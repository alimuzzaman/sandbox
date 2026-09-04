import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from tests.subprocess_support import run_test_process


ROOT = Path(__file__).parent.parent
PROVISIONER = ROOT / "scripts" / "provision_image_stage_helper.py"
HELPER = ROOT / "sandbox" / "hosting" / "images" / "staging_helper.py"


class TestImageStageHelperProvisioning(unittest.TestCase):
    def _home(self, parent: str) -> Path:
        home = Path(parent).resolve() / "home" / "alim" / "sandbox"
        source = home / "sb-src" / "sandbox" / "hosting" / "images"
        source.mkdir(parents=True)
        (source / "staging_helper.py").write_bytes(HELPER.read_bytes())
        return home

    def _run(self, home: Path, revision: str) -> subprocess.CompletedProcess:
        return run_test_process(
            (sys.executable, str(PROVISIONER), "--sandbox-home", str(home),
             "--runtime-revision", revision),
            capture_output=True, text=True, check=False, timeout=10,
        )

    def test_exact_revision_gets_immutable_owner_only_helper_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory)
            revision = "a" * 40
            result = self._run(home, revision)
            self.assertEqual(result.returncode, 0, result.stderr)
            digest = hashlib.sha256(HELPER.read_bytes()).hexdigest()
            root = (home / "runtime" / "helpers" / "image-stage"
                    / f"sha256-{digest}-revision-{revision}")
            self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((root / "staging_helper.py").stat().st_mode), 0o500)
            self.assertEqual((root / "staging_helper.py").stat().st_uid, os.geteuid())
            for name, schema, entry in (
                    ("manifest.json", 1, "sandbox-image-stage-helper-v1"),
                    ("manifest-v2.json", 2, "sandbox-image-stage-helper-v2")):
                path = root / name
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                payload = json.loads(path.read_text())
                self.assertEqual(payload["schema_version"], schema)
                self.assertEqual(payload["entry"], entry)
                self.assertEqual(payload["runtime_revision"], revision)

    def test_revision_change_creates_new_identity_without_rewriting_active_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory)
            first = "a" * 40
            second = "b" * 40
            self.assertEqual(self._run(home, first).returncode, 0)
            digest = hashlib.sha256(HELPER.read_bytes()).hexdigest()
            first_manifest = (home / "runtime" / "helpers" / "image-stage"
                              / f"sha256-{digest}-revision-{first}" / "manifest-v2.json")
            before = first_manifest.read_bytes()
            self.assertEqual(self._run(home, second).returncode, 0)
            self.assertEqual(first_manifest.read_bytes(), before)
            self.assertTrue((home / "runtime" / "helpers" / "image-stage"
                             / f"sha256-{digest}-revision-{second}" / "manifest-v2.json").is_file())

    def test_unsafe_existing_helper_or_manifest_refuses_without_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory)
            revision = "c" * 40
            self.assertEqual(self._run(home, revision).returncode, 0)
            digest = hashlib.sha256(HELPER.read_bytes()).hexdigest()
            root = (home / "runtime" / "helpers" / "image-stage"
                    / f"sha256-{digest}-revision-{revision}")
            helper = root / "staging_helper.py"
            helper.chmod(0o700)
            result = self._run(home, revision)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(stat.S_IMODE(helper.stat().st_mode), 0o700)

            helper.chmod(0o500)
            manifest = root / "manifest-v2.json"
            payload = json.loads(manifest.read_text())
            payload["runtime_revision"] = "d" * 40
            manifest.write_text(json.dumps(payload))
            manifest.chmod(0o600)
            result = self._run(home, revision)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(manifest.read_text())["runtime_revision"], "d" * 40)

    def test_invalid_revision_refuses_before_creating_runtime_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory)
            result = self._run(home, "not-a-revision")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((home / "runtime").exists())

    def test_writable_pre_home_ancestor_refuses_without_authority_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory)
            home.parent.chmod(0o770)
            result = self._run(home, "e" * 40)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((home / "runtime").exists())

    def test_partial_final_bundle_refuses_and_unpublished_temp_is_not_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory)
            revision = "f" * 40
            digest = hashlib.sha256(HELPER.read_bytes()).hexdigest()
            root = (home / "runtime" / "helpers" / "image-stage"
                    / f"sha256-{digest}-revision-{revision}")
            root.mkdir(parents=True)
            for path in (home, home / "runtime", home / "runtime" / "helpers",
                         home / "runtime" / "helpers" / "image-stage", root):
                path.chmod(0o700)
            (root / "staging_helper.py").write_bytes(b"partial")
            (root / "staging_helper.py").chmod(0o500)
            result = self._run(home, revision)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((root / "staging_helper.py").read_bytes(), b"partial")
            self.assertEqual(list(root.parent.glob(".image-stage-helper.*")), [])

    def test_live_shaped_home_and_runtime_remove_write_bits_only(self):
        with tempfile.TemporaryDirectory() as directory:
            home = self._home(directory)
            home.chmod(0o775)
            runtime = home / "runtime"
            runtime.mkdir(mode=0o775)
            runtime.chmod(0o775)
            result = self._run(home, "9" * 40)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(runtime.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE((runtime / "helpers").stat().st_mode), 0o700)

    def test_atomic_publication_never_replaces_conflicting_final_directory(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("helper_provisioner", PROVISIONER)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            source = parent / "source"; target = parent / "target"
            source.mkdir(); target.mkdir()
            (source / "value").write_text("new")
            (target / "value").write_text("incumbent")
            with self.assertRaises(FileExistsError):
                module._rename_no_replace(source, target)
            self.assertEqual((target / "value").read_text(), "incumbent")
            self.assertEqual((source / "value").read_text(), "new")


if __name__ == "__main__":
    unittest.main()
