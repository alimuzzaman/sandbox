from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import argparse
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest


class Result:
    def __init__(self, operation):
        self.operation = operation

    def to_dict(self):
        return {"ok": True, "state": "ready", "operation": self.operation,
                "mutated": False}


class Service:
    def __init__(self):
        self.calls = []

    def status(self, project_dir, *, label):
        self.calls.append(("status", project_dir, label))
        return Result("status")

    def plan(self, project_dir, *, label):
        self.calls.append(("plan", project_dir, label))
        return Result("plan")

    def apply(self, project_dir, *, label, interactive):
        self.calls.append(("apply", project_dir, label, interactive))
        return Result("apply")

    def cleanup(self, project_dir, *, label, interactive):
        self.calls.append(("cleanup", project_dir, label, interactive))
        return Result("cleanup")

    def reconsider(self, resolver):
        self.calls.append(("reconsider", resolver))
        return {"ok": True, "state": "ready", "mutated": False}


class TestDomainTransports(unittest.TestCase):
    def test_cli_has_no_proof_promotion_input(self):
        from sandbox.commands import domains

        parser = argparse.ArgumentParser()
        domains.configure_parser(parser)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["apply", "--proof-evidence", "forged"])

    def test_cli_structured_actions_delegate_to_application_service(self):
        from sandbox.commands import domains
        from unittest import mock

        service = Service()
        for action in ("detect", "status", "plan", "apply", "cleanup", "reconsider"):
            args = SimpleNamespace(
                action=action, project_dir="/tmp/project", label="preview",
                resolver="resolved:host", json=True, tld=None,
            )
            output = io.StringIO()
            with mock.patch("sandbox.application.context.domain_service",
                            return_value=service), redirect_stdout(output):
                domains.cmd_domains({}, args)
            self.assertIsInstance(json.loads(output.getvalue()), dict)
        self.assertIn(("status", "/tmp/project", "preview"), service.calls)
        self.assertIn(("apply", "/tmp/project", "preview", False), service.calls)
        self.assertIn(("cleanup", "/tmp/project", "preview", False), service.calls)

    def test_mcp_tools_are_import_safe_and_legacy_setup_delegates_without_prompt(self):
        mcp_root = Path(__file__).parent.parent / "mcp" / "wp-server"
        sys.path.insert(0, str(mcp_root))
        self.addCleanup(lambda: sys.path.remove(str(mcp_root)))
        from dependencies import ToolDependencies
        from tools import domains as domain_tools
        from tools import instances

        class Server:
            def tool(self):
                return lambda function: function

        service = Service()
        dependencies = ToolDependencies({
            "sandbox_root": Path("/tmp/sandbox"), "proxy_tld": "test",
            "core": object(), "load_sandbox_yml": lambda: {},
            "project_instance": lambda *_args: (None, {}),
            "resolve_instance": lambda *_args: {}, "safe_json": json.loads,
            "site_url": lambda *_args: "http://localhost:8123",
            "domain_service": lambda: service,
            "ingress_service": lambda: type("Ingress", (), {
                "detect": lambda self: {"ok": True, "mutated": False},
                "support": lambda self: {"ok": True, "mutated": False},
            })(),
        })
        domain_tools.register(Server(), dependencies)
        instances.register(Server(), dependencies)
        self.assertEqual(domain_tools.domain_status("/tmp/project")["operation"], "status")
        payload = instances.setup_domains(project_dir="/tmp/project", label="preview")
        self.assertEqual(payload["operation"], "apply")
        self.assertIn(("apply", "/tmp/project", "preview", False), service.calls)


if __name__ == "__main__":
    unittest.main()
