from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest


class Process:
    def __init__(self, code=0): self.code = code; self.calls = []
    def run(self, argv, *, timeout):
        self.calls.append((tuple(argv), timeout))
        return subprocess.CompletedProcess(argv, self.code, "a" * 64, "failure" if self.code else "")


class TestIngressFileAdapters(unittest.TestCase):
    def adapter(self, cls, root, process=None):
        return cls(helper="/fixed/helper", process=process or Process(), network_root=root)

    def plan(self, adapter, wildcard=False):
        return adapter.plan_route(
            {"listen": {"address": "127.0.0.1", "port": 80}},
            {"hostname": "demo.test", "owner": "/tmp/project::default",
             "wildcard": wildcard},
            {"address": "127.0.0.1", "port": 8123},
        )

    def test_product_fragments_are_attributable_and_scoped(self):
        from sandbox.ingress.adapters.nginx import NginxAdapter
        from sandbox.ingress.adapters.apache import ApacheAdapter
        from sandbox.ingress.adapters.caddy import CaddyAdapter
        from sandbox.ingress.adapters.traefik import TraefikAdapter
        with tempfile.TemporaryDirectory() as tmp:
            for cls in (NginxAdapter, ApacheAdapter, CaddyAdapter, TraefikAdapter):
                plan = self.plan(self.adapter(cls, tmp), wildcard=True)
                self.assertTrue(plan["content"].startswith(
                    f"# sandbox-ingress v1 route={plan['route_id']}\n"))
                self.assertIn("demo.test", plan["content"])
                self.assertIn("127.0.0.1:8123", plan["content"])

    def test_non_loopback_backend_is_refused_before_helper(self):
        from sandbox.ingress.adapters.nginx import NginxAdapter
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self.adapter(NginxAdapter, tmp)
            with self.assertRaisesRegex(ValueError, "loopback"):
                adapter.plan_route(
                    {"listen": {"address": "0.0.0.0", "port": 80}},
                    {"hostname": "demo.test", "owner": "owner"},
                    {"address": "10.0.0.2", "port": 8123},
                )

    def test_transaction_phases_use_only_fixed_helper_verbs(self):
        from sandbox.ingress.adapters.nginx import NginxAdapter
        with tempfile.TemporaryDirectory() as tmp:
            process = Process(); adapter = self.adapter(NginxAdapter, tmp, process)
            plan = self.plan(adapter)
            self.assertTrue(adapter.validate_current(plan)["ok"])
            stage = adapter.stage_candidate(plan)
            self.assertTrue(adapter.validate_candidate(stage)["ok"])
            self.assertTrue(adapter.activate(stage)["ok"])
            self.assertTrue(adapter.rollback(stage, {})["ok"])
            self.assertEqual([call[0][3] for call in process.calls],
                             ["validate-current", "prepare", "activate", "rollback"])
            self.assertEqual(Path(plan["candidate"]).stat().st_mode & 0o777, 0o600)

    def test_every_rendered_product_candidate_passes_independent_helper_schema(self):
        from sandbox.ingress.adapters.nginx import NginxAdapter
        from sandbox.ingress.adapters.apache import ApacheAdapter
        from sandbox.ingress.adapters.caddy import CaddyAdapter
        from sandbox.ingress.adapters.traefik import TraefikAdapter
        helper = Path(__file__).parent.parent / "tools/ingress-helper.sh"
        with tempfile.TemporaryDirectory() as tmp:
            for cls in (NginxAdapter, ApacheAdapter, CaddyAdapter, TraefikAdapter):
                adapter = self.adapter(cls, tmp)
                plan = self.plan(adapter, wildcard=True)
                adapter.stage_candidate(plan)
                result = subprocess.run(
                    [str(helper), "check-candidate", tmp, plan["candidate"],
                     plan["adapter_id"], plan["route_id"]],
                    capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(result.returncode, 0,
                                 f"{plan['adapter_id']}: {result.stderr}")


if __name__ == "__main__": unittest.main()
