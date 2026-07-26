import base64
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.ci.workflow import preflight
from sandbox.commands.ci import _plan_workflow, _remote_ci_submissions
from sandbox.commands.jobs_runtime import cmd_job_matrix
from sandbox.jobs.models import ResolvedTarget
from sandbox.transports.remote_jobs import RemoteJobTransport


def _load_mcp_ci_tool():
    fake_app = types.ModuleType("app")
    fake_app.SANDBOX_ROOT = Path("/tmp/sandbox")
    fake_app._require_project_capability = lambda *_args: None
    fake_app._safe_json = json.loads
    fake_app.mcp = SimpleNamespace(tool=lambda: (lambda function: function))
    path = Path(__file__).parent.parent / "mcp" / "wp-server" / "tools" / "ci.py"
    spec = importlib.util.spec_from_file_location("sandbox_test_mcp_ci", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"app": fake_app}):
        spec.loader.exec_module(module)
    return module


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
            remote_sb_path=lambda _remote: "/srv/sandbox/sb-src/sb",
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
        self.assertEqual(plan[0]["argv"], ["/srv/sandbox/sb-src/sb", "ci", "run", "workflow.yml"])

    def test_legacy_encoded_matrix_plan_defaults_remain_readable(self):
        plan = [{
            "kind": "ci", "workspace": "legacy-cell", "argv": ["echo", "legacy"],
            "timeout": 60, "workspace_mode": "isolated", "output_profile": "smart",
            "deadline_source": "explicit", "cleanup_policy": "retain",
            "depends_on": [], "failure_policy": "fail-fast",
            "compatibility_differences": [], "artifact_paths": [],
            "source": {"identity": "source"},
        }]
        encoded = base64.b64encode(json.dumps(plan).encode()).decode()
        captured = []
        service = SimpleNamespace(submit_matrix=lambda submissions, **_kwargs: captured.extend(submissions) or {
            "ok": True, "parent_job_id": "p" * 32, "children": []})
        target = ResolvedTarget("/tmp/project", "local", None, "default", "local:p", {})
        dependencies = {"target_service": SimpleNamespace(resolve=lambda _request: target),
                        "job_service": service}
        args = SimpleNamespace(spec_json=encoded, command=[], workspace=None, project_dir="/tmp/project",
                               local=True, remote=None, timeout=60, output_profile="smart", json=True)
        with patch("sandbox.commands.jobs_runtime.durable_job_dependencies", return_value=dependencies), \
                redirect_stdout(StringIO()):
            cmd_job_matrix(None, args)
        self.assertEqual(captured[0].cwd_relative, ".")
        self.assertEqual(captured[0].execution_profile, "exec")
        self.assertEqual(captured[0].stall_seconds, 300)
        self.assertEqual(captured[0].environment_keys, ())

    def test_encoded_matrix_allows_sibling_of_declared_deployment_root(self):
        plan = [{
            "kind": "test", "workspace": "cell", "argv": ["echo", "ok"],
            "project_dir": "/tmp/deployed-workspace-cell", "timeout": 60,
            "source": {"identity": "source"},
        }]
        encoded = base64.b64encode(json.dumps(plan).encode()).decode()
        captured = []
        service = SimpleNamespace(submit_matrix=lambda submissions, **_kwargs: captured.extend(submissions) or {
            "ok": True, "parent_job_id": "p" * 32, "children": []})
        # Project discovery can canonicalize a copied checkout to a different
        # root. The explicitly supplied deployment root remains the boundary
        # for deterministic sibling workspaces.
        target = ResolvedTarget("/tmp/canonical-root", "local", None, "default", "local:p", {})
        dependencies = {"target_service": SimpleNamespace(resolve=lambda _request: target),
                        "job_service": service}
        args = SimpleNamespace(spec_json=encoded, command=[], workspace=None, project_dir="/tmp/deployed",
                               local=True, remote=None, timeout=60, output_profile="smart", json=True)
        with patch("sandbox.commands.jobs_runtime.durable_job_dependencies", return_value=dependencies), \
                redirect_stdout(StringIO()):
            cmd_job_matrix(None, args)
        self.assertEqual(captured[0].project_root,
                         str(Path("/tmp/deployed-workspace-cell").resolve()))

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
                cwd_relative="work", execution_profile="ci", output_profile="errors",
                stall_seconds=45, cancel_on_stall=True, environment_keys=("CI",),
                cleanup_policy="on-success", artifact_paths=("reports",),
                compatibility_differences=({"id": "act.demo", "accepted": True},)),
        ]
        transport.submit_many(submissions)
        encoded = next(part.split("--spec-json ", 1)[1].split(" ", 1)[0]
                        for part in calls if "--spec-json" in part)
        plan = json.loads(base64.b64decode(encoded).decode())
        unit = next(item for item in plan if item["workspace"] == "unit")
        self.assertEqual(unit["depends_on"], ["build"])
        self.assertEqual(unit["compatibility_differences"][0]["id"], "act.demo")
        self.assertEqual(unit["cwd_relative"], "work")
        self.assertEqual(unit["execution_profile"], "ci")
        self.assertEqual(unit["output_profile"], "errors")
        self.assertEqual(unit["stall_seconds"], 45)
        self.assertTrue(unit["cancel_on_stall"])
        self.assertEqual(unit["environment_keys"], ["CI"])
        self.assertEqual(unit["cleanup_policy"], "on-success")
        self.assertEqual(unit["artifact_paths"], ["reports"])

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

    def test_remote_ci_child_labels_keep_the_requested_workspace_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow = root / "ci.yml"
            workflow.write_text("jobs:\n  unit:\n    steps:\n      - run: echo unit\n")
            target = ResolvedTarget(str(root), "remote", "r", "lenzora-ci", "remote:r:p", {})
            args = SimpleNamespace(timeout=60, label_prefix=None, matrix_filter={}, jobs=None,
                                   allow_deploy=False, keep_on_fail=False, strict_provision=False,
                                   accepted_differences=None, output_profile="smart")
            submission = _remote_ci_submissions(target, str(root), workflow,
                                                _plan_workflow(workflow), args)[0]
            self.assertTrue(submission.workspace_label.startswith("lenzora-ci-"))
            self.assertLessEqual(len(submission.workspace_label), 21)

    def test_multiline_upload_artifact_paths_become_separate_literal_declarations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow = root / "ci.yml"
            workflow.write_text(
                "jobs:\n  unit:\n    steps:\n      - uses: actions/upload-artifact@v4\n"
                "        with:\n          path: |\n            reports\n            coverage.xml\n"
            )
            target = ResolvedTarget(str(root), "remote", "r", "ci", "remote:r:p", {})
            args = SimpleNamespace(timeout=60, label_prefix=None, matrix_filter={}, jobs=None,
                                   allow_deploy=False, keep_on_fail=False, strict_provision=False,
                                   accepted_differences=None, output_profile="smart")
            submission = _remote_ci_submissions(target, str(root), workflow,
                                                _plan_workflow(workflow), args)[0]
            self.assertEqual(submission.artifact_paths, ("coverage.xml", "reports"))

    def test_mcp_ci_accepts_local_cells_and_remote_durable_parent_reports(self):
        module = _load_mcp_ci_tool()
        reports = [
            {"ok": True, "cells": [{"label": "local", "status": "passed"}]},
            {"ok": True, "parent_job_id": "p" * 32,
             "children": [{"job_id": "c" * 32}], "summary": {"submitted": 1}},
        ]
        for report in reports:
            completed = SimpleNamespace(returncode=0, stdout=json.dumps(report) + "\n", stderr="")
            with patch.object(module.subprocess, "run", return_value=completed):
                result = module.ci_run("/tmp/project", "ci.yml", local="cells" in report,
                                       remote=None if "cells" in report else "remote-1")
            self.assertEqual(result, report)

    def test_mcp_ci_docstring_describes_local_and_remote_async_shapes(self):
        module = _load_mcp_ci_tool()
        self.assertIn("cells", module.ci_run.__doc__)
        self.assertIn("parent_job_id", module.ci_run.__doc__)
        self.assertIn("children", module.ci_run.__doc__)

    def test_mcp_async_remote_ci_accepts_parent_job_id(self):
        module = _load_mcp_ci_tool()
        report = {"ok": True, "parent_job_id": "p" * 32,
                  "children": [{"job_id": "c" * 32}]}
        completed = SimpleNamespace(returncode=0, stdout=json.dumps(report) + "\n", stderr="")
        with patch.object(module.subprocess, "run", return_value=completed):
            result = module.ci_run("/tmp/project", "ci.yml", remote="remote-1", async_=True)
        self.assertEqual(result, report)
