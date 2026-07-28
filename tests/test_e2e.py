"""Unit tests for the e2e multi-worker runner (docs/ci-e2e-runner-spec.md §2).

Stdlib `unittest` only, no docker — pure config-discovery / convention-
detection logic. Run from the repo root:

    .cli-venv/bin/python -m unittest discover -s tests -v
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.commands.e2e as e2e  # noqa: E402


class TestFindPlaywrightConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_finds_config_at_root(self):
        (self.root / "playwright.config.js").write_text("module.exports = {}")
        found = e2e._find_playwright_config(self.root)
        self.assertEqual(found, self.root / "playwright.config.js")

    def test_finds_config_under_tests_subdir(self):
        # Templately's real layout: tests/playwright.config.js, not at root.
        (self.root / "tests").mkdir()
        (self.root / "tests" / "playwright.config.js").write_text("module.exports = {}")
        found = e2e._find_playwright_config(self.root)
        self.assertEqual(found, self.root / "tests" / "playwright.config.js")

    def test_explicit_override_wins(self):
        (self.root / "tests").mkdir()
        (self.root / "tests" / "playwright.config.js").write_text("module.exports = {}")
        (self.root / "custom.config.js").write_text("module.exports = {}")
        found = e2e._find_playwright_config(self.root, explicit="custom.config.js")
        self.assertEqual(found, self.root / "custom.config.js")

    def test_none_when_missing(self):
        self.assertIsNone(e2e._find_playwright_config(self.root))


class TestDetectWpEnvPortConvention(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_detects_wp_env_url_require(self):
        # Mirrors Templately's real tests/playwright.config.js.
        cfg = self.root / "playwright.config.js"
        cfg.write_text(
            "const { resolveWpEnvConfig } = require('./e2e/utils/wp-env-url');\n"
            "module.exports = {};\n"
        )
        self.assertTrue(e2e._detect_wp_env_port_convention(cfg))

    def test_no_false_positive_on_ordinary_config(self):
        cfg = self.root / "playwright.config.js"
        cfg.write_text("module.exports = { use: { baseURL: process.env.BASE_URL } };\n")
        self.assertFalse(e2e._detect_wp_env_port_convention(cfg))


class TestWriteWpEnvPort(unittest.TestCase):
    def test_writes_expected_shape(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            entry = {"url": "https://x.tst", "login_url": "https://x.tst/?a=1",
                     "instance": "proj-e2e-w0"}
            e2e._write_wp_env_port(root, entry)
            data = json.loads((root / ".wp-env-port").read_text())
            self.assertEqual(data["baseUrl"], "https://x.tst")
            self.assertEqual(data["loginUrl"], "https://x.tst/?a=1")
            self.assertEqual(data["runtime"], "sandbox")
            self.assertEqual(data["instance"], "proj-e2e-w0")


class TestDurableRemoteE2EShards(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.config = self.root / "playwright.config.js"
        self.config.write_text("module.exports = {}\n")
        self.target = SimpleNamespace(project_root=str(self.root), remote_name="vps",
                                      workspace_label="e2e-dev")
        self.args = SimpleNamespace(concurrency=2, grep="smoke", keep_on_fail=True,
                                    strict_provision=True, passthrough=["--", "--headed"])

    def tearDown(self):
        self._tmp.cleanup()

    def test_remote_submission_assigns_one_isolated_leaf_per_shard(self):
        submissions = e2e._remote_shard_submissions(
            target=self.target, config_path=self.config, root_path=self.root,
            workers=3, timeout=120, args=self.args,
        )
        self.assertEqual(len(submissions), 3)
        self.assertEqual(len({item.workspace_label for item in submissions}), 3)
        self.assertTrue(all(item.workspace_mode == "isolated" for item in submissions))
        for index, submission in enumerate(submissions):
            self.assertEqual(submission.argv[0:7],
                             ("sb", "e2e", "--local", "--project-dir", ".", "--workers", "1"))
            self.assertIn(str(index), submission.argv)
            self.assertIn("3", submission.argv)
            self.assertIn("--shard-index", submission.argv)
            self.assertIn("--shard-total", submission.argv)
            self.assertIn("--headed", submission.argv)

    def test_single_shard_specs_keep_original_playwright_partition(self):
        self.assertEqual(e2e._shard_specs(4, 2, 4),
                         [{"label": "e2e-w2", "index": 2, "total": 4}])
        with self.assertRaisesRegex(ValueError, "together"):
            e2e._shard_specs(2, 0, None)
        with self.assertRaisesRegex(ValueError, "within"):
            e2e._shard_specs(2, 2, 2)

    def test_remote_coordinator_submits_matrix_parent_not_opaque_wrapper(self):
        captured = []

        class Transport:
            def __init__(self, **_kwargs):
                pass

            def submit_many(self, submissions):
                captured.extend(submissions)
                return {"ok": True, "kind": "matrix", "parent_job_id": "parent-1",
                        "children": [{"job_id": "child-1"}, {"job_id": "child-2"}]}

        args = SimpleNamespace(project_dir=str(self.root), playwright_config=None,
                               local=False, remote="vps", workspace=None, timeout=120,
                               workers=2, concurrency=None, grep=None, keep_on_fail=False,
                               strict_provision=False, passthrough=[], json=True,
                               shard_index=None, shard_total=None)
        with patch.object(e2e, "_core", return_value=SimpleNamespace(
                load_project_config=lambda _path: {"root": str(self.root)})), \
                patch("sandbox.application.context.durable_job_dependencies", return_value={
                    "target_service": SimpleNamespace(resolve=lambda _request: SimpleNamespace(
                        kind="remote", project_root=str(self.root), remote_name="vps",
                        workspace_label="e2e-dev")),
                }), \
                patch("sandbox.transports.remote_jobs.RemoteJobTransport", Transport):
            output = StringIO()
            with redirect_stdout(output):
                e2e.cmd_e2e({}, args)
        payload = __import__("json").loads(output.getvalue())
        self.assertEqual(payload["parent_job_id"], "parent-1")
        self.assertEqual(payload["workers"], 2)
        self.assertEqual(len(captured), 2)


class TestAggregateResult(unittest.TestCase):
    def test_failed_worker_retains_bounded_diagnostic_output(self):
        report = e2e._aggregate_result({
            "ok": False,
            "concurrency": 2,
            "units": [{
                "label": "e2e-w0",
                "status": "failed",
                "error": None,
                "provision": {"url": "http://localhost:8080"},
                "result": {"exit_code": 1, "output": "playwright failure"},
            }],
        }, 1)

        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["by_worker"][0]["output"], "playwright failure")


if __name__ == "__main__":
    unittest.main()
