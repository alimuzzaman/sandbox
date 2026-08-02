from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from types import SimpleNamespace
import unittest
from unittest import mock


class Service:
    def detect(self): return {"ok": True, "operation": "ingress_detect", "state": "ready", "observations": [], "mutated": False}
    def support(self): return {"ok": True, "operation": "ingress_support", "state": "ready", "adapters": [], "mutated": False}
    def select(self, **_kwargs):
        from sandbox.ingress.models import IngressSelection
        return IngressSelection(frozenset({"http"}), frozenset({"http"}), None,
                                (), "pin_unavailable", None, "fixture", "project")
    def cleanup_owner(self, owner):
        return {"ok": True, "operation": "ingress_cleanup", "state": "ready",
                "mutated": False, "owner": owner}
    def reconcile_owner(self, owner):
        return {"ok": True, "operation": "ingress_reconcile", "state": "ready",
                "mutated": False, "owner": owner, "recovery": {"residual": []}}
    def reconsider(self, identity):
        return {"ok": True, "state": "ready", "mutated": True,
                "consent_identity": identity}


class TestIngressCliMcp(unittest.TestCase):
    def test_concrete_domain_and_ingress_composition_roots_are_distinct_services(self):
        from sandbox.application.context import domain_service, ingress_service
        from unittest import mock
        import sandbox_core as sc
        with mock.patch.object(sc, "sandbox_base", return_value=__import__("pathlib").Path("/tmp/sandbox-context-test")):
            domain = domain_service({})
            ingress = ingress_service({})
        self.assertEqual(type(domain).__name__, "DomainService")
        self.assertEqual(type(ingress).__name__, "IngressService")

    def test_cli_read_only_ingress_actions_emit_one_json_object(self):
        from sandbox.commands.domains import cmd_domains
        for subaction in ("detect", "status", "support"):
            args = SimpleNamespace(action="ingress", tld=subaction, json=True,
                                   project_dir=None, label="default", resolver=None)
            output = io.StringIO()
            with mock.patch("sandbox.application.context.ingress_service", return_value=Service()), \
                    redirect_stdout(output):
                cmd_domains({}, args)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["mutated"])

    def test_cli_cleanup_reconcile_and_reconsider_have_typed_contracts(self):
        from sandbox.commands.domains import cmd_domains
        for subaction in ("cleanup", "reconcile", "reconsider"):
            args = SimpleNamespace(action="ingress", tld=subaction, json=True,
                                   project_dir="/tmp/project", label="default",
                                   resolver="fixture:identity")
            output = io.StringIO()
            with mock.patch("sandbox.application.context.ingress_service", return_value=Service()), \
                    redirect_stdout(output):
                cmd_domains({}, args)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertIn("ingress_", payload["operation"])

    def test_cli_and_mcp_plan_and_apply_report_the_same_safe_fallback_shape(self):
        from sandbox.commands.domains import cmd_domains
        from pathlib import Path
        import sys
        mcp_root = Path(__file__).parent.parent / "mcp" / "wp-server"
        if str(mcp_root) not in sys.path:
            sys.path.insert(0, str(mcp_root))
        from tools import domains as mcp_domains

        args = SimpleNamespace(action="ingress", tld="apply", json=True,
                               project_dir=None, label="default", resolver=None)
        output = io.StringIO()
        with mock.patch("sandbox.application.context.ingress_service", return_value=Service()), \
                redirect_stdout(output):
            cmd_domains({}, args)
        cli_apply = json.loads(output.getvalue())
        self.assertEqual(cli_apply, mcp_domains.ingress_apply())

        previous = mcp_domains._ingress_service
        try:
            mcp_domains._ingress_service = lambda: Service()
            mcp_plan = mcp_domains.ingress_plan(("http",))
        finally:
            mcp_domains._ingress_service = previous
        self.assertEqual(mcp_plan["reason"]["code"], "pin_unavailable")
        self.assertEqual(mcp_plan["pin_source"], "project")

    def test_mcp_cleanup_reconcile_and_reconsider_never_need_app_imports_or_secrets(self):
        from pathlib import Path
        import sys
        mcp_root = Path(__file__).parent.parent / "mcp" / "wp-server"
        if str(mcp_root) not in sys.path:
            sys.path.insert(0, str(mcp_root))
        from tools import domains as mcp_domains
        previous = mcp_domains._ingress_service
        try:
            mcp_domains._ingress_service = lambda: Service()
            self.assertTrue(mcp_domains.ingress_cleanup("/tmp/project")["ok"])
            self.assertTrue(mcp_domains.ingress_reconcile("/tmp/project")["ok"])
            self.assertTrue(mcp_domains.ingress_reconsider("fixture:identity")["mutated"])
        finally:
            mcp_domains._ingress_service = previous
        source = (mcp_root / "tools" / "domains.py").read_text()
        self.assertNotIn("from app import", source)
        self.assertNotIn("credential_value", source)

    def test_support_matrix_and_fallback_results_are_transport_safe_and_secret_free(self):
        from sandbox.application.ingress_service import IngressService
        from sandbox.ingress.manifest import built_in_ingress_registry

        service = IngressService(
            detector=type("Detector", (), {"observe": lambda self: ()})(),
            registry=built_in_ingress_registry(),
        )
        support = service.support()
        tiers = {item["adapter_id"]: item["support_tier"] for item in support["adapters"]}
        self.assertEqual(tiers["nginx-proxy-manager"], "credential_pending")
        self.assertEqual(tiers["ddev"], "detect_only")
        self.assertEqual(tiers["laragon"], "detect_only")
        fallback = service.plan_route(
            service.select(project_pin="missing"), {}, {"address": "127.0.0.1", "port": 8123},
        )
        self.assertEqual(fallback["state"], "fallback")
        self.assertNotIn("hunter2", repr((support, fallback)).lower())


if __name__ == "__main__": unittest.main()
