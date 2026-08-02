from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest


class Process:
    def __init__(self, code=0, outputs=None):
        self.code = code; self.calls = []; self.outputs = list(outputs or ())
    def run(self, argv, *, timeout):
        self.calls.append((tuple(argv), timeout))
        stdout = self.outputs.pop(0) if self.outputs else "a" * 64
        return subprocess.CompletedProcess(argv, self.code, stdout,
                                           "failure" if self.code else "")


class TestIngressFileAdapters(unittest.TestCase):
    def adapter(self, cls, root, process=None):
        return cls(helper="/fixed/helper", process=process or Process(), network_root=root)

    def plan(self, adapter, wildcard=False):
        return adapter.plan_route(
            {"listen": {"address": "127.0.0.1", "port": 80},
             "authority": {"pid": 123, "start": "456",
                           "executable_digest": "e" * 64,
                           "socket_ids": ("789",),
                           "observation_fingerprint": "f" * 64}},
            {"hostname": "demo.test", "owner": "/tmp/project::default",
             "wildcard": wildcard},
            {"address": "127.0.0.1", "port": 8123},
        )

    def test_live_proven_caddy_fragment_is_attributable_and_exact(self):
        from sandbox.ingress.adapters.caddy import CaddyAdapter
        with tempfile.TemporaryDirectory() as tmp:
            plan = self.plan(self.adapter(CaddyAdapter, tmp))
            self.assertTrue(plan["content"].startswith(
                f"# sandbox-ingress v1 route={plan['route_id']}\n"))
            self.assertIn("http://demo.test", plan["content"])
            self.assertIn("    bind 127.0.0.1", plan["content"])
            self.assertIn("127.0.0.1:8123", plan["content"])

    def test_system_caddy_wildcard_is_refused_until_separately_proven(self):
        from sandbox.ingress.adapters.caddy import CaddyAdapter
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "wildcard"):
                self.plan(self.adapter(CaddyAdapter, tmp), wildcard=True)

    def test_non_loopback_backend_is_refused_before_helper(self):
        from sandbox.ingress.adapters.caddy import CaddyAdapter
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self.adapter(CaddyAdapter, tmp)
            with self.assertRaisesRegex(ValueError, "loopback"):
                adapter.plan_route(
                    {"listen": {"address": "0.0.0.0", "port": 80}},
                    {"hostname": "demo.test", "owner": "owner"},
                    {"address": "10.0.0.2", "port": 8123},
                )

    def test_transaction_phases_use_only_fixed_helper_verbs(self):
        from sandbox.ingress.adapters.caddy import CaddyAdapter
        with tempfile.TemporaryDirectory() as tmp:
            process = Process(); adapter = self.adapter(CaddyAdapter, tmp, process)
            plan = self.plan(adapter)
            self.assertTrue(adapter.validate_current(plan)["ok"])
            stage = adapter.stage_candidate(plan)
            self.assertTrue(adapter.validate_candidate(stage)["ok"])
            self.assertTrue(adapter.activate(stage)["ok"])
            self.assertTrue(adapter.rollback(stage, {})["ok"])
            self.assertEqual([call[0][3] for call in process.calls],
                             ["validate-current", "prepare", "activate", "rollback"])
            self.assertNotIn("candidate", plan)

    def test_prepare_binds_candidate_to_owner_hostname_backend_and_digest(self):
        from sandbox.ingress.adapters.caddy import CaddyAdapter
        with tempfile.TemporaryDirectory() as tmp:
            process = Process(); adapter = self.adapter(CaddyAdapter, tmp, process)
            plan = self.plan(adapter)
            stage = adapter.stage_candidate(plan)
            adapter.validate_candidate(stage)
            argv = process.calls[-1][0]
            self.assertEqual(argv[3:7], ("prepare", str(Path(tmp).resolve()),
                                        "system-caddy", plan["route_id"]))
            self.assertEqual(argv[7:13], ("/tmp/project::default", "demo.test",
                                          "127.0.0.1", "8123", "127.0.0.1", "80"))
            self.assertEqual(argv[13], plan["content_digest"])
            self.assertEqual(argv[14:], ("123", "456", "e" * 64,
                                         "789", "f" * 64))

    def test_caddy_baseline_is_only_the_explicit_loopback_backend(self):
        from sandbox.ingress.adapters.caddy import CaddyAdapter
        with tempfile.TemporaryDirectory() as tmp:
            process = Process(outputs=["http://existing.test/\n"])
            adapter = self.adapter(CaddyAdapter, tmp, process)
            plan = self.plan(adapter)
            self.assertEqual(adapter.baseline_urls(plan), ({
                "address": "127.0.0.1", "port": 8123, "host": "localhost",
            },))
            self.assertEqual(process.calls, [])

    def test_no_user_writable_candidate_crosses_the_helper_boundary(self):
        from sandbox.ingress.adapters.caddy import CaddyAdapter
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self.adapter(CaddyAdapter, tmp)
            plan = self.plan(adapter)
            self.assertEqual(adapter.stage_candidate(plan), plan)
            self.assertNotIn("candidate", plan)

    def test_authorization_is_exact_and_interactive_only_when_receipt_missing(self):
        from sandbox.ingress.adapters.caddy import CaddyAdapter
        with tempfile.TemporaryDirectory() as tmp:
            process = Process(code=1)
            adapter = self.adapter(CaddyAdapter, tmp, process)
            plan = self.plan(adapter)
            pending = adapter.authorize_plan(plan, interactive=False)
            self.assertEqual(pending["state"], "pending_privilege")
            self.assertEqual(process.calls[0][0][3], "authorization-status")
            approved = adapter.authorize_plan(plan, interactive=True)
            self.assertFalse(approved["ok"])
            self.assertEqual(process.calls[-1][0][:3], ("sudo", "/fixed/helper", "authorize"))


if __name__ == "__main__": unittest.main()
