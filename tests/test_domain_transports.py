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
    def test_cli_and_mcp_preserve_the_same_closed_diagnostic_envelope(self):
        from sandbox.commands import domains
        from sandbox.network.models import DomainResult
        from unittest import mock

        diagnostic = DomainResult(
            ok=True, state="ready", hostname="demo.test",
            hostname_source="project", strategy="resolved",
            strategy_source="project", resolver={}, actual_answers=(),
            expected_addresses=("127.0.0.1",), ownership="owned",
            health="healthy", fallback_url="http://localhost:8123",
            reason={"code": "ready", "message": "secret body"}, mutated=False,
            ingress={"state": "reachable", "endpoint": "127.0.0.1:80",
                     "exception": "secret exception"},
            application={"state": "ready", "headers": {"authorization": "secret"},
                         "body": "secret body"},
        )

        class DiagnosticService:
            def status(self, project_dir, *, label):
                return diagnostic

        service = DiagnosticService()
        args = SimpleNamespace(
            action="status", project_dir="/tmp/project", label="default",
            resolver=None, json=True, tld=None,
        )
        output = io.StringIO()
        with mock.patch("sandbox.application.context.domain_service",
                        return_value=service), redirect_stdout(output):
            domains.cmd_domains({}, args)
        cli_payload = json.loads(output.getvalue())

        mcp_root = Path(__file__).parent.parent / "mcp" / "wp-server"
        sys.path.insert(0, str(mcp_root))
        self.addCleanup(lambda: sys.path.remove(str(mcp_root)))
        from tools import domains as domain_tools
        previous = domain_tools._domain_service
        self.addCleanup(lambda: setattr(domain_tools, "_domain_service", previous))
        domain_tools._domain_service = lambda: service
        mcp_payload = domain_tools.domain_status("/tmp/project")

        for payload in (cli_payload, mcp_payload):
            self.assertEqual(payload["ingress"], {"state": "reachable"})
            self.assertEqual(payload["application"], {"state": "ready"})
            self.assertEqual(payload["reason"], {"code": "ready"})
            self.assertNotIn("secret", json.dumps(payload, sort_keys=True))
        self.assertEqual(
            {key: cli_payload[key] for key in ("ingress", "application", "reason")},
            {key: mcp_payload[key] for key in ("ingress", "application", "reason")},
        )

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
