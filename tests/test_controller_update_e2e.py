"""End-to-end controller auto-update through a real ``sb`` entry (spec 072,
quickstart.md section 2).

A copy of this working tree's ``sb`` and ``sandbox/`` package becomes commit
C1 of a temporary origin; a marker commit C2 is published on top. A clone at
C1 on a local ``installed`` branch tracking ``origin/main`` runs
``sb remote list`` with a recorded hint for C2. Everything stays inside a
temporary directory: the GitHub origin URL is mapped to the local bare repo.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.controller_update_support import GITHUB_URL, git_env  # noqa: E402
from tests.subprocess_support import synthetic_environment  # noqa: E402

_COPIED = ("sb", "sandbox", "sandbox.yml", "requirements-cli.txt", "VERSION", "config", ".gitignore")
_IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
UPDATED = "sb: controller updated"


@unittest.skipUnless(shutil.which("git"), "git is required")
class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="sb-ctl-e2e-")
        cls.base = Path(cls._tmp.name).resolve()
        cls.env = git_env(cls.base)
        src = cls.base / "src"
        src.mkdir()
        for name in _COPIED:
            source = ROOT / name
            if source.is_dir():
                shutil.copytree(source, src / name, ignore=_IGNORED, symlinks=True)
            elif source.exists():
                shutil.copy2(source, src / name)
        cls.git(src, "init", "-q", "-b", "main")
        cls.git(src, "add", "-A")
        cls.git(src, "commit", "-q", "-m", "c1")
        cls.c1 = cls.git(src, "rev-parse", "HEAD")
        (src / "UPDATE_MARKER").write_text("c2\n", encoding="utf-8")
        cls.git(src, "add", "UPDATE_MARKER")
        cls.git(src, "commit", "-q", "-m", "c2")
        cls.c2 = cls.git(src, "rev-parse", "HEAD")
        cls.bare = cls.base / "origin.git"
        cls.git(cls.base, "clone", "-q", "--bare", str(src), str(cls.bare))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def git(cls, cwd, *args):
        result = subprocess.run(["git", *args], cwd=str(cwd), env=cls.env, capture_output=True,
                                text=True, stdin=subprocess.DEVNULL, timeout=120, check=False)
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)}: {result.stderr.strip()}")
        return result.stdout.strip()

    def setUp(self):
        self.case = Path(tempfile.mkdtemp(prefix="case-", dir=self.base))
        self.ctl = self.case / "ctl"
        self.home = self.case / "home"
        self.git(self.base, "clone", "-q", str(self.bare), str(self.ctl))
        self.git(self.ctl, "remote", "set-url", "origin", GITHUB_URL)
        self.git(self.ctl, "config", f"url.file://{self.bare}.insteadOf", GITHUB_URL)
        self.git(self.ctl, "checkout", "-q", "-B", "installed", self.c1)
        self.git(self.ctl, "branch", "-q", "-u", "origin/main", "installed")
        state = self.home / "runtime" / "controller-update" / "state.json"
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({"schema_version": 1, "hints": {"demo": {
            "revision": self.c2, "source": "probe", "recorded_at": time.time()}},
            "refusals": []}), encoding="utf-8")

    def sb(self, *args, extra_env=None):
        env = dict(synthetic_environment())
        env.update({"SANDBOX_HOME": str(self.home), "HOME": str(self.case),
                    "GIT_CONFIG_GLOBAL": self.env["GIT_CONFIG_GLOBAL"],
                    "GIT_CONFIG_NOSYSTEM": "1"})
        env.update(extra_env or {})
        return subprocess.run([sys.executable, str(self.ctl / "sb"), *args], cwd=str(self.case),
                              env=env, capture_output=True, text=True, timeout=300,
                              stdin=subprocess.DEVNULL, check=False)

    def head(self):
        return self.git(self.ctl, "rev-parse", "HEAD")

    def test_update_then_rerun_once_then_quiet(self):
        baseline = self.sb("remote", "list", extra_env={"SANDBOX_AUTO_UPDATE": "0"})
        self.assertEqual(self.head(), self.c1)
        self.assertNotIn(UPDATED, baseline.stderr)

        first = self.sb("remote", "list")
        self.assertEqual(first.stderr.count(UPDATED), 1, first.stderr)
        self.assertIn(f"{self.c1[:7]} -> {self.c2[:7]} from origin/main", first.stderr)
        self.assertEqual(self.head(), self.c2)
        self.assertEqual(first.returncode, baseline.returncode, first.stderr)
        self.assertEqual(first.stdout, baseline.stdout)
        self.assertEqual(self.git(self.ctl, "rev-parse", "--abbrev-ref", "HEAD"), "installed")

        second = self.sb("remote", "list")
        self.assertNotIn("sb: controller", second.stderr)
        self.assertEqual(self.head(), self.c2)

    def test_machine_output_is_identical_and_never_updates(self):
        off = self.sb("remote", "list", "--json", extra_env={"SANDBOX_AUTO_UPDATE": "0"})
        on = self.sb("remote", "list", "--json")
        self.assertEqual(self.head(), self.c1)
        self.assertEqual((on.stdout, on.stderr, on.returncode),
                         (off.stdout, off.stderr, off.returncode))

    def test_dirty_checkout_prints_one_notice_and_does_not_move(self):
        self.git(self.ctl, "fetch", "-q", "origin")
        (self.ctl / "VERSION").write_text("local edit\n", encoding="utf-8")
        (self.ctl / "UPDATE_MARKER").write_text("untracked in the way\n", encoding="utf-8")
        result = self.sb("remote", "list")
        self.assertEqual(self.head(), self.c1)
        self.assertEqual(result.stderr.count("sb: controller not updated"), 1, result.stderr)
        self.assertIn("uncommitted changes", result.stderr)


if __name__ == "__main__":
    unittest.main()
