"""Adapter-neutral validate/stage/activate/health/rollback transaction."""

from __future__ import annotations


class IngressTransactionRunner:
    def __init__(self, *, baseline_probe, route_probe):
        self.baseline_probe = baseline_probe
        self.route_probe = route_probe

    def run(self, adapter, plan):
        stage = prior = None
        try:
            current = adapter.validate_current(plan)
            if not current.get("ok"):
                raise RuntimeError("current configuration validation failed")
            prior = adapter.capture_prior(plan)
            baseline = self.baseline_probe(plan)
            if not baseline.get("ok"):
                raise RuntimeError("baseline route health failed")
            stage = adapter.stage_candidate(plan)
            candidate = adapter.validate_candidate(stage)
            if not candidate.get("ok"):
                raise RuntimeError("candidate configuration validation failed")
            activation = adapter.activate(stage)
            if not activation.get("ok"):
                raise RuntimeError("route activation failed")
            observed = adapter.observe_route(plan)
            route_health = self.route_probe(plan, observed)
            baseline_after = self.baseline_probe(plan)
            if not route_health.get("ok") or not baseline_after.get("ok"):
                raise RuntimeError("post-activation health failed")
            return {"ok": True, "state": "ready", "mutated": True,
                    "applied": observed, "rollback": None}
        except Exception as exc:
            if stage is None:
                return {"ok": False, "state": "fallback", "mutated": False,
                        "error": str(exc), "rollback": None}
            rollback = adapter.rollback(stage, prior)
            healthy = self.baseline_probe(plan)
            complete = bool(rollback.get("ok") and healthy.get("ok"))
            return {"ok": False,
                    "state": "rollback_complete" if complete else "rollback_incomplete",
                    "mutated": True, "error": str(exc), "rollback": rollback}
