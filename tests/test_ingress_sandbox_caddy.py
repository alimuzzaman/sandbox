from __future__ import annotations
import unittest


class State:
    def __init__(self, text="# baseline\n"): self.text = text; self.calls = []
    def read(self): return self.text
    def validate(self, text): self.calls.append(("validate", text)); return {"ok": "INVALID" not in text}
    def activate(self, text): self.calls.append(("activate", text)); self.text = text; return {"ok": True}
    @staticmethod
    def block(hostname, port, wildcard):
        names = f"http://{hostname}, http://*.{hostname}" if wildcard else f"http://{hostname}"
        return f"{names} {{\n    reverse_proxy host.docker.internal:{port}\n}}\n"


class TestSandboxCaddyAdapter(unittest.TestCase):
    def adapter(self, state):
        from sandbox.ingress.adapters.sandbox_caddy import SandboxCaddyAdapter
        return SandboxCaddyAdapter(read_current=state.read, validate=state.validate,
                                   activate=state.activate, block_renderer=state.block)

    def plan(self, adapter, port=8123):
        return adapter.plan_route({}, {"hostname": "demo.test",
            "owner": "/tmp/project::default", "wildcard": False}, {"port": port})

    def test_owned_route_add_update_and_idempotent_plan_preserve_baseline(self):
        state = State(); adapter = self.adapter(state)
        first = self.plan(adapter); adapter.activate(first)
        second = self.plan(adapter); self.assertEqual(first["candidate"], second["candidate"])
        updated = self.plan(adapter, 8456); adapter.activate(updated)
        self.assertIn("# baseline", state.text)
        self.assertIn("host.docker.internal:8456", state.text)
        self.assertEqual(state.text.count("sandbox-ingress-route begin"), 1)

    def test_foreign_hostname_route_is_refused_without_activation(self):
        state = State("http://demo.test {\n    respond ok\n}\n")
        with self.assertRaisesRegex(ValueError, "foreign"):
            self.plan(self.adapter(state))
        self.assertEqual(state.calls, [])

    def test_transaction_rollback_restores_exact_prior_config(self):
        from sandbox.ingress.transaction import IngressTransactionRunner
        state = State(); adapter = self.adapter(state); plan = self.plan(adapter)
        result = IngressTransactionRunner(
            baseline_probe=lambda _plan: {"ok": True},
            route_probe=lambda _plan, _observed: {"ok": False},
        ).run(adapter, plan)
        self.assertEqual(result["state"], "rollback_complete")
        self.assertEqual(state.text, "# baseline\n")


if __name__ == "__main__": unittest.main()
