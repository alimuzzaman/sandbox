"""Remote CI must select server execution before any laptop secret lookup.

Actual command routing is exercised; remote submission is a bounded fixture,
not accepted-job or server-runtime evidence. No provider/runtime is invoked.
"""
from argparse import Namespace
from contextlib import ExitStack
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sandbox.commands import ci


class CISecretExecutionScopeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="ci-secret-scope-")))
        self.workflow = self.root / "fixture.yml"
        self.workflow.write_text("jobs: {}\n")
        self.args = Namespace(action="run", workflow=str(self.workflow), project_dir=str(self.root),
            runtime="sandbox", remote="fixture-server", local=False, json=True)
        self.plan = {"jobs": [{"id": "test", "cells": [{}], "steps": [],
                               "secrets_needed": ["XSPEED_JEV_API_KEY"]}],
                     "secrets_needed": ["XSPEED_JEV_API_KEY"]}
        self.core = SimpleNamespace(ConfigError=ValueError,
                                    load_project_config=Mock(return_value={"root": str(self.root)}))
        self.enterContext(patch.object(ci, "_core", return_value=self.core))
        self.enterContext(patch.object(ci, "_plan_workflow", return_value=self.plan))
        self.enterContext(patch("sandbox.ci.workflow.preflight", return_value={"blocking": []}))

    def test_remote_command_routes_before_all_controller_secret_and_act_access(self):
        target = SimpleNamespace(kind="remote", remote_name="fixture-server", workspace_label="ci")
        selector = Mock()
        selector.resolve.return_value = target
        with ExitStack() as stack:
            stack.enter_context(patch("sandbox.application.context.durable_job_dependencies",
                                     return_value={"target_service": selector}))
            for name in ("_resolve_secret", "_local_yaml", "_write_secrets_file", "_act_binary"):
                stack.enter_context(patch.object(ci, name,
                    side_effect=AssertionError("controller secret/runtime access: " + name)))
            submit = stack.enter_context(patch.object(ci, "_run_remote_ci"))
            ci.cmd_ci({}, self.args)
        submit.assert_called_once_with(target, str(self.root), self.workflow, self.plan,
                                       self.args, as_json=True)
        self.assertEqual(selector.resolve.call_args.args[0].remote, "fixture-server")

    def test_personal_local_missing_secret_still_refuses_before_execution(self):
        self.args.runtime, self.args.remote, self.args.local = "none", None, True
        with patch.object(ci, "_run_remote_ci") as remote, \
             patch.object(ci, "_act_binary", return_value="/fixture/act"), \
             patch.object(ci, "_runtime_secrets_needed", return_value=["XSPEED_JEV_API_KEY"]), \
             patch.object(ci, "_local_yaml", return_value={}) as local_config, \
             patch.object(ci, "_write_secrets_file", side_effect=AssertionError("secret file write")), \
             patch.object(ci, "die", side_effect=RuntimeError) as refusal:
            with self.assertRaises(RuntimeError):
                ci.cmd_ci({}, self.args)
        remote.assert_not_called()
        local_config.assert_called_once()
        self.assertIn("explicitly allowlisted", refusal.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
