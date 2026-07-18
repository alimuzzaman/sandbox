import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sandbox.ci.workflow import preflight
from sandbox.commands.ci import _plan_workflow, _remote_ci_submissions
from sandbox.jobs.models import ResolvedTarget
from sandbox.transports.remote_jobs import RemoteJobTransport


class RemoteCIJobTests(unittest.TestCase):
    def test_workflow_matrix_becomes_independent_durable_children(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow = root / "ci.yml"
            workflow.write_text(
                "jobs:\n"
                "  unit:\n"
                "    strategy:\n"
                "      matrix:\n"
                "        node: [20, 22]\n"
                "    steps:\n"
                "      - uses: actions/upload-artifact@v4\n"
                "        with:\n"
                "          path: reports\n"
            )
            target = ResolvedTarget(str(root), "remote", "r", "ci", "remote:r:p", {})
            args = SimpleNamespace(timeout=3600, label_prefix="ci", matrix_filter={}, jobs=None,
                                   allow_deploy=False, keep_on_fail=True, strict_provision=False,
                                   accepted_differences=None, output_profile="smart")
            submissions = _remote_ci_submissions(target, str(root), workflow,
                                                  _plan_workflow(workflow), args)
            self.assertEqual(len(submissions), 2)
            self.assertEqual({item.workspace_mode for item in submissions}, {"isolated"})
            self.assertEqual({item.artifact_paths for item in submissions}, {("reports",)})
            self.assertTrue(all("--matrix-filter" in item.argv for item in submissions))
            self.assertTrue(all(item.deadline_seconds == 3600 for item in submissions))

    def test_remote_matrix_control_contains_explicit_child_plan(self):
        calls = []
        transport = RemoteJobTransport(
            deploy=lambda *_: {"target_path": "/srv/p", "commit": "c", "dirty": False,
                               "dirty_digest": "d", "identity": "sha256:i"},
            ssh_run=lambda _remote, command, timeout: calls.append(command) or SimpleNamespace(
                returncode=0,
                stdout='{"ok":true,"kind":"matrix","parent_job_id":"p","children":[]}\n'),
            remote_lookup=lambda _name: {"provisioned": True},
        )
        from sandbox.jobs.models import JobSubmission, SourceIdentity
        submissions = [JobSubmission("ci", "/p", "identity", "remote", label,
            ("sb", "ci", "run", "workflow.yml"), 60, SourceIdentity("s"),
            remote_name="r", workspace_mode="isolated") for label in ("a", "b")]
        result = transport.submit_many(submissions)
        self.assertEqual(result["parent_job_id"], "p")
        encoded = next(part.split("--spec-json ", 1)[1].split(" ", 1)[0]
                        for part in calls if "--spec-json" in part)
        plan = json.loads(base64.b64decode(encoded).decode())
        self.assertEqual([item["workspace"] for item in plan], ["a", "b"])
        self.assertEqual(plan[0]["argv"], ["sb", "ci", "run", "workflow.yml"])

    def test_remote_matrix_plan_carries_dependencies_and_accepted_differences(self):
        calls = []
        transport = RemoteJobTransport(
            deploy=lambda *_: {"target_path": "/srv/p", "commit": "c", "dirty": False,
                               "dirty_digest": "d", "identity": "sha256:i"},
            ssh_run=lambda _remote, command, timeout: calls.append(command) or SimpleNamespace(
                returncode=0,
                stdout='{"ok":true,"kind":"matrix","parent_job_id":"p","children":[]}',
            ),
            remote_lookup=lambda _name: {"provisioned": True},
        )
        from sandbox.jobs.models import JobSubmission, SourceIdentity
        submissions = [
            JobSubmission("ci", "/p", "identity", "remote", "build", ("echo", "build"), 60,
                SourceIdentity("s"), remote_name="r", workspace_mode="isolated"),
            JobSubmission("ci", "/p", "identity", "remote", "unit", ("echo", "unit"), 60,
                SourceIdentity("s"), remote_name="r", workspace_mode="isolated", depends_on=("build",),
                compatibility_differences=({"id": "act.demo", "accepted": True},)),
        ]
        transport.submit_many(submissions)
        encoded = next(part.split("--spec-json ", 1)[1].split(" ", 1)[0]
                        for part in calls if "--spec-json" in part)
        plan = json.loads(base64.b64decode(encoded).decode())
        unit = next(item for item in plan if item["workspace"] == "unit")
        self.assertEqual(unit["depends_on"], ["build"])
        self.assertEqual(unit["compatibility_differences"][0]["id"], "act.demo")

    def test_remote_preflight_stays_a_gate_before_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            flow = root / "ci.yml"
            flow.write_text("jobs:\n  bad:\n    runs-on: windows-latest\n")
            result = preflight(root, "ci.yml")
            self.assertFalse(result["ok"])
            self.assertIn("act.non-linux-runner", result["blocking"])

    def test_remote_ci_preserves_needs_and_failure_policy_as_durable_edges(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow = root / "ci.yml"
            workflow.write_text(
                "jobs:\n"
                "  build:\n"
                "    strategy:\n"
                "      matrix:\n"
                "        node: [20, 22]\n"
                "    steps:\n"
                "      - run: echo build\n"
                "  unit:\n"
                "    needs: build\n"
                "    strategy:\n"
                "      fail-fast: false\n"
                "    steps:\n"
                "      - run: echo unit\n"
            )
            target = ResolvedTarget(str(root), "remote", "r", "ci", "remote:r:p", {})
            args = SimpleNamespace(timeout=60, label_prefix="ci", matrix_filter={}, jobs=None,
                                   allow_deploy=False, keep_on_fail=False, strict_provision=False,
                                   accepted_differences=None, output_profile="smart")
            submissions = _remote_ci_submissions(target, str(root), workflow,
                                                  _plan_workflow(workflow), args)
            unit = next(item for item in submissions
                        if item.argv[item.argv.index("--job") + 1] == "unit")
            self.assertEqual(len(unit.depends_on), 2)
            self.assertEqual(unit.failure_policy, "continue")
