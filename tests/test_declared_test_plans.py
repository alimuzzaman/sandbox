import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands.jobs_runtime import cmd_declared_test_plan


def _target():
    return SimpleNamespace(
        kind="local", project_root="/project", remote_name=None, workspace_label="default",
        runtime_policy={
            "executionProfile": "unit", "outputProfile": "smart", "maxParallel": 4,
            "executionProfiles": {"unit": {"timeoutSeconds": 120}},
            "outputProfiles": {"smart": {"mode": "smart"}},
            "testPlans": {
                "verify": {
                    "executionProfile": "unit", "outputProfile": "smart", "maxParallel": 2,
                    "steps": [
                        {"id": "lint", "argv": ["npm", "run", "lint"], "parallelSafe": True},
                        {"id": "unit", "argv": ["npm", "test"], "parallelSafe": True,
                         "artifacts": ["reports"]},
                        {"id": "integration", "argv": ["php", "vendor/bin/phpunit"],
                         "needs": ["unit"]},
                    ],
                },
            },
        },
    )


class DeclaredTestPlanTests(unittest.TestCase):
    def test_declared_plan_becomes_isolated_parent_and_child_jobs(self):
        captured = []
        service = SimpleNamespace(submit_matrix=lambda submissions: captured.extend(submissions) or {
            "ok": True, "parent_job_id": "a" * 32, "children": []})
        dependencies = {"target_service": SimpleNamespace(resolve=lambda _request: _target()),
                        "job_service": service}
        args = SimpleNamespace(plan="verify", project_dir="/project", local=True, remote=None,
                               timeout=None, output_profile=None, json=True)
        output = StringIO()
        with patch("sandbox.commands.jobs_runtime.durable_job_dependencies", return_value=dependencies), \
                redirect_stdout(output):
            cmd_declared_test_plan(None, args)

        self.assertEqual([item.workspace_label for item in captured],
                         ["verify-lint", "verify-unit", "verify-integration"])
        self.assertTrue(all(item.workspace_mode == "isolated" for item in captured))
        self.assertEqual(captured[0].depends_on, ())
        self.assertEqual(captured[1].depends_on, ())
        # The non-parallel integration step remains ordered and its explicit
        # prerequisite is mapped from stable step ID to workspace label.
        self.assertEqual(captured[2].depends_on, ("verify-unit", "verify-lint"))
        self.assertEqual(captured[1].artifact_paths, ("reports",))
        self.assertEqual(captured[0].deadline_seconds, 120)
        self.assertEqual(captured[0].deadline_source, "plan:verify")
        self.assertIn('"plan": "verify"', output.getvalue())

    def test_declared_plan_explicit_timeout_overrides_its_profile(self):
        captured = []
        dependencies = {
            "target_service": SimpleNamespace(resolve=lambda _request: _target()),
            "job_service": SimpleNamespace(submit_matrix=lambda submissions: captured.extend(submissions) or {
                "ok": True, "parent_job_id": "b" * 32, "children": []}),
        }
        args = SimpleNamespace(plan="verify", project_dir="/project", local=True, remote=None,
                               timeout=30, output_profile=None, json=False)
        with patch("sandbox.commands.jobs_runtime.durable_job_dependencies", return_value=dependencies), \
                redirect_stdout(StringIO()):
            cmd_declared_test_plan(None, args)
        self.assertEqual({item.deadline_seconds for item in captured}, {30})
        self.assertEqual({item.deadline_source for item in captured}, {"explicit"})

    def test_test_matrix_routes_plan_flag_without_an_explicit_command(self):
        import sandbox.commands.debug as debug

        args = SimpleNamespace(mode="matrix", passthrough=["--local", "--plan", "verify", "--json"],
                               project_dir="/project", local=False, remote=None, workspace=[], timeout=None,
                               output_profile=None, json=False)
        with patch("sandbox.commands.jobs_runtime.cmd_declared_test_plan") as declared:
            debug.cmd_test(None, args)
        self.assertEqual(declared.call_args.args[1].plan, "verify")
        self.assertTrue(declared.call_args.args[1].local)
        self.assertTrue(declared.call_args.args[1].json)
        self.assertIsNone(declared.call_args.args[1].output_profile)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
