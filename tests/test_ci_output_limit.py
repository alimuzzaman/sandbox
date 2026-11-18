"""`sb ci run --output-limit N`: how much of each cell's act output is kept.

Stdlib `unittest` only, no docker or act.
"""
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.commands.ci as ci  # noqa: E402
from sandbox.jobs.models import ResolvedTarget  # noqa: E402
from tests.subprocess_support import synthetic_environment  # noqa: E402

# 10000 distinct characters ending in a marker, so a cut shows where it happened.
OUTPUT = "".join(f"{n:05d}\n"[-6:] for n in range(1666)) + "TAIL"


def _run_cell(stdout=OUTPUT, side_effect=None, **kwargs):
    with patch("sandbox.commands.ci._core") as core, \
            patch("sandbox.commands.ci.subprocess.run") as run, \
            patch("sandbox.commands.ci._act_binary", return_value="act"), \
            tempfile.TemporaryDirectory() as temp:
        core.return_value.project_lock.return_value = nullcontext()
        run.return_value = SimpleNamespace(returncode=1, stdout=stdout, stderr="")
        run.side_effect = side_effect
        root = Path(temp)
        return ci._run_cell_with_act({"wordpress_port": 8197, "instance": "ci"},
                                     {"cell": {}, "job_id": "build"}, root,
                                     root / "workflow.yml", root / "secrets.env",
                                     False, 30, **kwargs)


class CellOutputLimitTests(unittest.TestCase):
    def test_default_keeps_the_last_6000_characters(self):
        self.assertEqual(ci.DEFAULT_OUTPUT_LIMIT, 6000)
        self.assertEqual(_run_cell()["output"], OUTPUT[-6000:])
        self.assertEqual(_run_cell(output_limit=ci.DEFAULT_OUTPUT_LIMIT)["output"],
                         OUTPUT[-6000:])

    def test_a_limit_keeps_the_last_n_characters(self):
        self.assertEqual(_run_cell(output_limit=9000)["output"], OUTPUT[-9000:])
        self.assertEqual(_run_cell(output_limit=10)["output"], OUTPUT[-10:])

    def test_zero_keeps_everything(self):
        self.assertEqual(_run_cell(output_limit=0)["output"], OUTPUT)

    def test_a_timeout_keeps_the_partial_output(self):
        # On POSIX, subprocess.run's TimeoutExpired carries bytes even with text=True.
        expired = subprocess.TimeoutExpired(["act"], 30, output=OUTPUT.encode(),
                                            stderr=b"killed\n")
        result = _run_cell(side_effect=expired, output_limit=0)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "timed out after 30s")
        self.assertEqual(result["output"], OUTPUT + "\nkilled")
        # Partial bytes get text=True's universal newlines, like a cell that finished.
        progress = _run_cell(side_effect=subprocess.TimeoutExpired(
            ["act"], 30, output=b"pull 10%\rpull 90%\r\ndone\r\n", stderr=None), output_limit=0)
        self.assertEqual(progress["output"], "pull 10%\npull 90%\ndone")
        limited = _run_cell(side_effect=subprocess.TimeoutExpired(
            ["act"], 30, output=OUTPUT.encode(), stderr=None))
        self.assertEqual(limited["output"], OUTPUT[-6000:])


class OutputLimitFlagTests(unittest.TestCase):
    WORKFLOW = "on: push\njobs:\n  test:\n    runs-on: ubuntu-22.04\n    steps: [{run: make}]\n"

    def workflow(self):
        temp = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        path = temp / "ci.yml"
        path.write_text(self.WORKFLOW)
        return path

    def run_args(self, path, **extra):
        values = dict(action="run", workflow=str(path), json=True, jobs=["test"],
                      matrix_filter=None, container_limits=None, as_runner=False,
                      run_async=True, if_event=None, event=None, dry_run=False,
                      runtime="none", local=True, remote=None, project_dir=str(path.parent),
                      secrets_stdin=False, list_secrets=False)
        values.update(extra)
        return SimpleNamespace(**values)

    def async_argv(self, **extra):
        path = self.workflow()
        with patch("sandbox.commands.ci.launch_background_job", return_value="j1") as launch, \
                patch("sandbox.commands.ci._act_binary", return_value="act"), \
                patch("sys.stdout", io.StringIO()):
            ci.cmd_ci({}, self.run_args(path, **extra))
        return launch.call_args.args[0]

    def test_ci_help_names_the_flag_and_its_default(self):
        result = subprocess.run([str(ROOT / "sb"), "ci", "--help"], capture_output=True,
                                text=True, timeout=90, cwd="/tmp",
                                env=synthetic_environment({"SANDBOX_INSTANCE": ""}))
        self.assertEqual(result.returncode, 0, result.stderr)
        text = " ".join(result.stdout.split())
        self.assertIn("--output-limit", text)
        self.assertIn("default: 6000", text)

    def test_async_reinvocation_carries_the_limit(self):
        argv = self.async_argv(output_limit=0)
        self.assertEqual(argv[argv.index("--output-limit") + 1], "0")
        self.assertNotIn("--output-limit", self.async_argv(output_limit=6000))

    def test_a_negative_limit_is_refused_before_the_launch(self):
        path = self.workflow()
        for extra in ({"if_event": "pull_request"}, {"run_async": False, "local": False}):
            with self.subTest(**extra), \
                    patch("sandbox.commands.ci.launch_background_job") as launch, \
                    patch("sys.stderr", io.StringIO()) as stderr, \
                    patch("sys.stdout", io.StringIO()) as stdout, \
                    self.assertRaises(SystemExit):
                ci.cmd_ci({}, self.run_args(path, output_limit=-1, **extra))
            launch.assert_not_called()
            self.assertIn("--output-limit", stderr.getvalue())
            self.assertNotIn("skipped", stdout.getvalue())
        with patch("sandbox.commands.ci.launch_background_job") as launch, \
                patch("sandbox.commands.ci._act_binary", return_value="act"), \
                patch("sys.stderr", io.StringIO()) as stderr, \
                self.assertRaises(SystemExit):
            ci.cmd_ci({}, self.run_args(path, output_limit=-1))
        launch.assert_not_called()
        self.assertIn("--output-limit", stderr.getvalue())

    def test_local_run_hands_the_limit_to_each_cell(self):
        path = self.workflow()
        seen = {}

        class Stop(Exception):
            pass

        def run_across(cfg, root, specs, worker_fn, **_kw):
            with patch("sandbox.commands.ci._run_cell_with_act",
                       side_effect=lambda *a, **kw: seen.update(kw) or {}):
                worker_fn({}, specs[0])
            raise Stop  # the cell's kwargs are all this test needs

        with patch("sandbox.commands.ci._act_binary", return_value="act"), \
                patch("sandbox.commands.ci.run_across_instances", side_effect=run_across), \
                patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()), \
                self.assertRaises(Stop):
            ci.cmd_ci({}, self.run_args(path, run_async=False, output_limit=123))
        self.assertEqual(seen.get("output_limit"), 123)

    def remote_argv(self, **extra):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow = root / "ci.yml"
            workflow.write_text(self.WORKFLOW)
            target = ResolvedTarget(str(root), "remote", "r", "ci", "remote:r:p",
                                    {"identity": "project:ci"})
            args = SimpleNamespace(timeout=60, label_prefix=None, matrix_filter={}, jobs=None,
                                   allow_deploy=False, keep_on_fail=False,
                                   strict_provision=False, accepted_differences=None,
                                   output_profile="smart", **extra)
            return ci._remote_ci_submissions(target, str(root), workflow,
                                             ci._plan_workflow(workflow), args)[0].argv

    def test_remote_children_carry_the_limit(self):
        argv = self.remote_argv(output_limit=0)
        self.assertEqual(argv[argv.index("--output-limit") + 1], "0")
        # The default adds nothing, so an older remote runtime keeps accepting the argv.
        self.assertNotIn("--output-limit", self.remote_argv(output_limit=6000))
        self.assertNotIn("--output-limit", self.remote_argv())


class McpOutputLimitTests(unittest.TestCase):
    def test_mcp_ci_run_forwards_the_limit(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_remote_ci_jobs import _load_mcp_ci_tool
        module = _load_mcp_ci_tool()
        completed = SimpleNamespace(returncode=0, stdout='{"ok": true}\n', stderr="")
        with patch.object(module.subprocess, "run", return_value=completed) as run:
            module.ci_run("/tmp/project", "ci.yml", local=True, output_limit=0)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--output-limit") + 1], "0")
        with patch.object(module.subprocess, "run", return_value=completed) as run:
            module.ci_run("/tmp/project", "ci.yml", local=True)
        self.assertNotIn("--output-limit", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
