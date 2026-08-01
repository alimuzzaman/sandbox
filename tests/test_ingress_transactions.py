from __future__ import annotations

import unittest


class Adapter:
    def __init__(self, fail=None): self.fail = fail; self.calls = []
    def _result(self, name): self.calls.append(name); return {"ok": self.fail != name}
    def validate_current(self, plan): return self._result("current")
    def capture_prior(self, plan): self.calls.append("prior"); return {"old": True}
    def stage_candidate(self, plan): self.calls.append("stage"); return {"stage": True}
    def validate_candidate(self, stage): return self._result("candidate")
    def activate(self, stage): return self._result("activate")
    def observe_route(self, plan): self.calls.append("observe"); return {"route": "demo"}
    def rollback(self, stage, prior): return self._result("rollback")


class TestIngressTransactions(unittest.TestCase):
    def runner(self, *, route_ok=True, baseline_ok=True):
        from sandbox.ingress.transaction import IngressTransactionRunner
        return IngressTransactionRunner(
            baseline_probe=lambda plan: {"ok": baseline_ok},
            route_probe=lambda plan, observed: {"ok": route_ok},
        )

    def test_validates_full_current_and_candidate_before_activation(self):
        adapter = Adapter(); result = self.runner().run(adapter, {})
        self.assertTrue(result["ok"])
        self.assertEqual(adapter.calls, ["current", "prior", "stage", "candidate",
                                         "activate", "observe"])

    def test_candidate_or_health_failure_rolls_back(self):
        adapter = Adapter("candidate"); result = self.runner().run(adapter, {})
        self.assertEqual(result["state"], "rollback_complete")
        self.assertEqual(adapter.calls[-1], "rollback")
        adapter = Adapter(); result = self.runner(route_ok=False).run(adapter, {})
        self.assertEqual(result["state"], "rollback_complete")
        self.assertEqual(adapter.calls[-1], "rollback")

    def test_failed_rollback_is_reported_incomplete(self):
        adapter = Adapter("rollback"); result = self.runner(route_ok=False).run(adapter, {})
        self.assertEqual(result["state"], "rollback_incomplete")


if __name__ == "__main__": unittest.main()
