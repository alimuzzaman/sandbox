"""The side-by-side release program runs for real against a private home."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.jobs import scope
from sandbox.remote_runtime import releases
from tests.subprocess_support import run_test_process

OLD, NEW, OTHER = "a" * 24, "b" * 24, "c" * 24


def _run(home: Path, *args: str, env=None):
    return run_test_process([sys.executable, "-c", releases.PROGRAM, str(home), *args],
                            capture_output=True, text=True, timeout=60,
                            env=env or {"PATH": "/usr/bin:/bin"})


def _complete(home: Path, name: str, text: str = "new") -> Path:
    root = home / "releases"
    root.mkdir(mode=0o700, exist_ok=True)
    release = root / name
    release.mkdir(mode=0o700)
    (release / "id").write_text(text)
    fd = os.open(release / ".release.json", os.O_CREAT | os.O_WRONLY, 0o600)
    os.write(fd, json.dumps({"name": name, "state": "complete",
                             "runtime_revision": name}).encode())
    os.close(fd)
    return release


class ReleaseLayoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.home())
        self.base = Path(self.tmp.name).resolve()
        self.home = self.base / "sandbox"
        (self.home / "runtime").mkdir(parents=True)
        self.home.chmod(0o700)
        self.unit = self.base / "unit"
        self.unit.write_text(f"Environment=SANDBOX_REMOTE_MCP_RUNTIME_REVISION={OLD}\n")
        self.origin = self.home / "runtime" / "owner-origin.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _legacy(self):
        (self.home / "sb-src").mkdir()
        (self.home / "sb-src" / "id").write_text("old")

    def _activate(self, name):
        result = _run(self.home, "activate", name, str(self.unit), str(self.origin))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_conversion_keeps_sb_src_usable_and_reverts_to_the_plain_directory(self):
        self._legacy()
        (self.home / "runtime" / "source-revision.json").write_text('{"x":1}')
        _complete(self.home, NEW)
        state = self._activate(NEW)
        self.assertEqual(os.readlink(self.home / "sb-src"), "current")
        self.assertEqual(os.readlink(self.home / "current"), f"releases/{NEW}")
        self.assertEqual(os.readlink(self.home / "previous"), f"releases/{OLD}")
        self.assertEqual((self.home / "sb-src" / "id").read_text(), "new")
        self.assertEqual((self.home / "releases" / OLD / "id").read_text(), "old")
        self.assertEqual((self.home / "releases" / OLD / ".service-unit").read_text(),
                         self.unit.read_text())
        rollback = _run(self.home, "rollback-target")
        self.assertEqual(rollback.stdout.strip(), OLD)
        reverted = _run(self.home, "revert", state)
        self.assertEqual(reverted.returncode, 0, reverted.stderr)
        self.assertFalse((self.home / "sb-src").is_symlink())
        self.assertEqual((self.home / "sb-src" / "id").read_text(), "old")
        self.assertEqual(sorted(p.name for p in (self.home / "sb-src").iterdir()), ["id"])
        self.assertFalse(os.path.lexists(self.home / "current"))
        self.assertFalse(os.path.lexists(self.home / "previous"))
        self.assertFalse((self.home / "releases" / OLD).exists())

    def test_flip_moves_current_and_previous_and_revert_restores_both(self):
        self._legacy()
        _complete(self.home, NEW)
        self._activate(NEW)
        _complete(self.home, OTHER, "other")
        state = self._activate(OTHER)
        self.assertEqual((self.home / "sb-src" / "id").read_text(), "other")
        self.assertEqual(os.readlink(self.home / "previous"), f"releases/{NEW}")
        self.assertEqual(_run(self.home, "revert", state).returncode, 0)
        self.assertEqual(os.readlink(self.home / "current"), f"releases/{NEW}")
        self.assertEqual(os.readlink(self.home / "previous"), f"releases/{OLD}")

    def test_incomplete_release_is_refused_before_any_change(self):
        self._legacy()
        (self.home / "releases").mkdir(mode=0o700)
        (self.home / "releases" / NEW).mkdir()
        result = _run(self.home, "activate", NEW, str(self.unit), str(self.origin))
        self.assertEqual(result.returncode, 3)
        self.assertFalse((self.home / "sb-src").is_symlink())
        self.assertFalse(os.path.lexists(self.home / "current"))

    def test_restore_puts_back_the_recorded_unit_and_receipts(self):
        self._legacy()
        _complete(self.home, NEW)
        self._activate(NEW)
        receipt = self.home / "runtime" / "source-revision.json"
        self.unit.write_text("unit-new\n")
        receipt.write_text("receipt-new")
        recorded = _run(self.home, "record", NEW, str(self.unit), str(receipt), str(self.origin))
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        self.unit.write_text("changed\n")
        receipt.unlink()
        restored = _run(self.home, "restore", NEW, str(self.unit), str(receipt), str(self.origin))
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(self.unit.read_text(), "unit-new\n")
        self.assertEqual(receipt.read_text(), "receipt-new")
        self.assertFalse(self.origin.exists())

    def test_gc_keeps_current_previous_and_the_last_three(self):
        self._legacy()
        names = [format(i, "x") * 24 for i in range(1, 7)]
        for index, name in enumerate(names):
            _complete(self.home, name)
            self._activate(name)
            os.utime(self.home / "releases" / name / ".release.json", (1000 + index, 1000 + index))
        os.utime(self.home / "releases" / OLD / ".release.json", (1, 1))
        result = _run(self.home, "gc")
        self.assertEqual(result.returncode, 0, result.stderr)
        left = sorted(p.name for p in (self.home / "releases").iterdir() if not p.name.startswith("."))
        self.assertEqual(left, sorted(names[-3:]))
        self.assertEqual(os.readlink(self.home / "current"), f"releases/{names[-1]}")

    def test_prepare_builds_in_place_and_reuses_a_complete_release(self):
        stage_name = ".sb-src-stage-" + "f" * 12 + "-" + "e" * 32
        stage = self.home / stage_name
        tree = stage / "tree"
        (tree / "scripts").mkdir(parents=True)
        (tree / "sandbox" / "hermes").mkdir(parents=True)
        (tree / "sandbox" / "hermes" / "cron-catalog.json").write_text("{}")
        (tree / "sb").write_text("#!/bin/sh\necho \"$PWD\" >> \"$BUILD_LOG\"\n")
        (tree / "sb").chmod(0o700)
        # The helper must be handed the release it is building as its source.
        (tree / "scripts" / "provision_image_stage_helper.py").write_text(
            "import sys\nargs=sys.argv\nroot=args[args.index('--source-root')+1]\n"
            "assert root.endswith('/releases/' + 'b'*24), root\n")
        stage.chmod(0o700)
        tree.chmod(0o700)
        receipt = '{"r":1}'
        fd = os.open(stage / "receipt.json", os.O_CREAT | os.O_WRONLY, 0o600)
        os.write(fd, receipt.encode())
        os.close(fd)
        fake = self.base / "bin"
        fake.mkdir()
        log = self.base / "build.log"
        # python3 -m venv makes a venv whose python records its calls.
        (fake / "python3").write_text(
            "#!/bin/sh\nif [ \"$1\" = -m ] && [ \"$2\" = venv ]; then mkdir -p \"$3/bin\"; "
            "printf '#!/bin/sh\\necho venv \"$@\" >> \"$BUILD_LOG\"\\n' > \"$3/bin/python\"; "
            "chmod 700 \"$3/bin/python\"; exit 0; fi\n"
            f"exec {sys.executable} \"$@\"\n")
        (fake / "python3").chmod(0o700)
        env = {"PATH": f"{fake}:/usr/bin:/bin", "BUILD_LOG": str(log), "HOME": str(self.base)}
        args = ("prepare", stage_name, receipt, NEW, "f" * 40)
        built = _run(self.home, *args, env=env)
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertEqual(json.loads(built.stdout), {"release": NEW, "state": "built"})
        release = self.home / "releases" / NEW
        self.assertTrue((release / ".release.json").is_file())
        self.assertIn(str(release), log.read_text())
        self.assertFalse(stage.exists())
        stage.mkdir(mode=0o700)
        tree.mkdir(mode=0o700)
        for relative in ("sb", "scripts/provision_image_stage_helper.py"):
            (tree / relative).parent.mkdir(parents=True, exist_ok=True)
            (tree / relative).write_text("")
        fd = os.open(stage / "receipt.json", os.O_CREAT | os.O_WRONLY, 0o600)
        os.write(fd, receipt.encode())
        os.close(fd)
        reused = _run(self.home, *args, env=env)
        self.assertEqual(json.loads(reused.stdout), {"release": NEW, "state": "reused"})

    def test_real_image_helper_provisions_from_the_release_without_sb_src(self):
        root = Path(__file__).resolve().parents[1]
        release = _complete(self.home, NEW)
        (release / "sandbox" / "hosting" / "images").mkdir(parents=True)
        (release / "sandbox" / "hosting" / "images" / "staging_helper.py").write_bytes(
            (root / "sandbox" / "hosting" / "images" / "staging_helper.py").read_bytes())
        self.assertFalse(os.path.lexists(self.home / "sb-src"))
        result = run_test_process(
            [sys.executable, str(root / "scripts" / "provision_image_stage_helper.py"),
             "--sandbox-home", str(self.home), "--runtime-revision", "f" * 40,
             "--source-root", str(release)],
            capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(result.returncode, 0, result.stderr)
        bundles = list((self.home / "runtime" / "helpers" / "image-stage").iterdir())
        self.assertEqual(len(bundles), 1)
        self.assertTrue(bundles[0].name.endswith("-revision-" + "f" * 40))

    def test_prepare_refuses_a_mismatched_receipt(self):
        stage_name = ".sb-src-stage-" + "f" * 12 + "-" + "e" * 32
        tree = self.home / stage_name / "tree"
        (tree / "scripts").mkdir(parents=True)
        (tree / "sb").write_text("")
        (tree / "scripts" / "provision_image_stage_helper.py").write_text("")
        (self.home / stage_name).chmod(0o700)
        tree.chmod(0o700)
        fd = os.open(self.home / stage_name / "receipt.json", os.O_CREAT | os.O_WRONLY, 0o600)
        os.write(fd, b"other")
        os.close(fd)
        result = _run(self.home, "prepare", stage_name, "expected", NEW, "f" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / "releases" / NEW).exists())


class SupervisorScopeTest(unittest.TestCase):
    def setUp(self):
        scope._probe_ok = False

    def test_service_cgroup_detection(self):
        self.assertTrue(scope.in_service_cgroup(
            "/user.slice/user-1.slice/user@1.service/app.slice/sandbox-mcp-remote.service"))
        self.assertFalse(scope.in_service_cgroup(
            "/user.slice/user-1.slice/user@1.service/app.slice/sandbox-job-x.scope"))
        self.assertFalse(scope.in_service_cgroup("/user.slice/user-1.slice/user@1.service"))
        self.assertFalse(scope.in_service_cgroup("/user.slice/user-1.slice/session-4.scope"))

    def test_linux_launch_gets_its_own_scope(self):
        with patch.object(scope.sys, "platform", "linux"), \
                patch.object(scope.shutil, "which", return_value="/usr/bin/systemd-run"), \
                patch.object(scope, "_probe", return_value=True), \
                patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1"}):
            argv, env = scope.scoped_launch(["py", "-m", "sandbox.jobs.supervisor", "d"],
                                            job_id="d" * 32)
        self.assertEqual(argv[:5], ["systemd-run", "--user", "--scope", "--quiet", "--collect"])
        self.assertTrue(argv[5].startswith("--unit=sandbox-job-" + "d" * 16 + "-"))
        self.assertEqual(argv[-4:], ["py", "-m", "sandbox.jobs.supervisor", "d"])
        self.assertIsNone(env)

    def test_added_manager_variables_are_stripped_from_the_supervisor(self):
        with patch.object(scope.sys, "platform", "linux"), \
                patch.object(scope.shutil, "which", return_value="/usr/bin/systemd-run"), \
                patch.object(scope, "_probe", return_value=True), \
                patch.object(scope.os.path, "isdir", return_value=True), \
                patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
            argv, env = scope.scoped_launch(["py"], job_id="d" * 32)
        index = argv.index("--")
        self.assertEqual(argv[index + 1:], ["env", "-u", "XDG_RUNTIME_DIR", "py"])
        self.assertIn("XDG_RUNTIME_DIR", env)

    def test_no_scope_inside_a_service_refuses_and_elsewhere_runs_plain(self):
        with patch.object(scope.sys, "platform", "linux"), \
                patch.object(scope.shutil, "which", return_value=None):
            with patch.object(scope, "in_service_cgroup", return_value=True), \
                    self.assertRaises(scope.ScopeUnavailable):
                scope.scoped_launch(["py"], job_id="d" * 32)
            with patch.object(scope, "in_service_cgroup", return_value=False):
                self.assertEqual(scope.scoped_launch(["py"], job_id="d" * 32), (["py"], None))

    def test_off_linux_is_unchanged(self):
        with patch.object(scope.sys, "platform", "darwin"):
            self.assertEqual(scope.scoped_launch(["py"], job_id="x"), (["py"], None))


if __name__ == "__main__":
    unittest.main()
