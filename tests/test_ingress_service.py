from __future__ import annotations

from pathlib import Path
import tempfile
import unittest


class Detector:
    def __init__(self, observation): self.observation = observation
    def observe(self): return (self.observation,)


class Adapter:
    def __init__(self, foreign=False, baselines=None):
        self.foreign = foreign; self.current = None
        self.requires_baseline_samples = baselines is not None
        self.baselines = tuple(baselines or ())
    def plan_route(self, selection, naming, backend, prior=None):
        if self.foreign: raise ValueError("foreign route")
        return {"route_id": "adapter-route", "hostname": naming["hostname"],
                "backend": dict(backend), "listen": dict(selection["listen"])}
    def observe_route(self, plan): return self.current
    def cleanup(self, route): self.current = None; return {"ok": True, "mutated": True}
    def baseline_urls(self, _plan): return self.baselines


class Runner:
    def __init__(self): self.calls = 0
    def run(self, adapter, plan):
        self.calls += 1
        adapter.current = {"route": "ok", "backend": plan["backend"]}
        return {"ok": True, "state": "ready", "mutated": True,
                "applied": adapter.current}


class TestIngressServiceMutation(unittest.TestCase):
    def service(self, *, accepted=True, foreign=False, baselines=None):
        from sandbox.application.ingress_service import IngressService
        from sandbox.ingress.models import IngressObservation, ListenerEndpoint, SupportDeclaration
        from sandbox.ingress.registry import IngressAdapterRegistry, IngressAdapterSpec
        from sandbox.ingress.repository import IngressRepository
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        adapter = Adapter(foreign, baselines); runner = Runner()
        declaration = SupportDeclaration(
            "fixture", ("fixture",), ("linux",), "adoptable",
            frozenset({"http", "wildcard"}), "live-proof",
        )
        registry = IngressAdapterRegistry(); registry.register(
            IngressAdapterSpec(declaration, adapter, 10))
        observation = IngressObservation(
            "fixture", "fixture", (ListenerEndpoint("127.0.0.1", 80),),
            "adoptable", frozenset({"http", "wildcard"}),
        )
        repository = IngressRepository(Path(temporary.name) / "state.json")
        service = IngressService(
            detector=Detector(observation), registry=registry, bind_address="127.0.0.1",
            repository=repository, transaction_runner=runner,
            consent_decider=lambda _identity: accepted,
        )
        return service, adapter, runner, repository

    def planned(self, service, backend_port=8123):
        selection = service.select(required_protocols=("http",))
        return service.plan_route(selection, {
            "hostname": "demo.test", "owner": "/tmp/project::default",
            "wildcard": False, "listen": {"address": "127.0.0.1", "port": 80},
        }, {"address": "127.0.0.1", "port": backend_port})

    def test_noninteractive_requires_consent_before_transaction(self):
        service, _adapter, runner, _repository = self.service()
        result = service.apply_route(self.planned(service), interactive=False,
                                     fallback_url="http://localhost:8123")
        self.assertEqual(result["state"], "pending_consent")
        self.assertEqual(runner.calls, 0)

    def test_accept_apply_repeat_and_backend_update(self):
        service, _adapter, runner, _repository = self.service()
        first = service.apply_route(self.planned(service), interactive=True)
        second = service.apply_route(self.planned(service), interactive=False)
        updated = service.apply_route(self.planned(service, 8456), interactive=False)
        self.assertTrue(first["ok"]); self.assertFalse(second["mutated"])
        self.assertTrue(updated["ok"]); self.assertEqual(runner.calls, 2)

    def test_foreign_hostname_collision_prevents_transaction(self):
        service, _adapter, runner, _repository = self.service(foreign=True)
        result = self.planned(service)
        self.assertEqual(result["state"], "foreign_collision")
        self.assertEqual(runner.calls, 0)

    def test_required_foreign_baseline_fails_before_transaction_when_empty(self):
        service, _adapter, runner, _repository = self.service(baselines=())
        result = self.planned(service)
        self.assertEqual(result["reason"]["code"], "baseline_samples_unavailable")
        self.assertEqual(runner.calls, 0)

    def test_observed_foreign_baseline_is_bound_into_the_route_transaction(self):
        service, _adapter, _runner, _repository = self.service(
            baselines=("http://existing.test/",),
        )
        result = self.planned(service)
        self.assertTrue(result["ok"])
        self.assertTrue(result["adapter_plan"]["_baseline_required"])
        self.assertEqual(result["adapter_plan"]["_baseline_urls"],
                         ("http://existing.test/",))

    def test_backend_update_and_identical_repeat_preserve_one_owned_route(self):
        service, adapter, runner, repository = self.service()
        first = service.apply_route(self.planned(service), interactive=True)
        repeated = service.apply_route(self.planned(service), interactive=False)
        updated = service.apply_route(self.planned(service, 8456), interactive=False)
        self.assertTrue(first["ok"])
        self.assertFalse(repeated["mutated"])
        self.assertTrue(updated["mutated"])
        self.assertEqual(runner.calls, 2)
        self.assertEqual(len(repository.snapshot()["routes"]), 1)
        self.assertEqual(adapter.current["backend"]["port"], 8456)

    def test_foreign_wildcard_collision_is_not_applied(self):
        service, _adapter, runner, _repository = self.service(foreign=True)
        result = service.plan_route(
            service.select(required_protocols=("http",)), {
                "hostname": "*.demo.test", "owner": "/tmp/project::default",
                "wildcard": True, "listen": {"address": "127.0.0.1", "port": 80},
            }, {"address": "127.0.0.1", "port": 8123},
        )
        self.assertEqual(result["state"], "foreign_collision")
        self.assertEqual(result["reason"]["code"], "hostname_claimed")
        self.assertEqual(runner.calls, 0)

    def test_pending_credential_returns_only_machine_local_reference(self):
        service, _adapter, _runner, _repository = self.service()
        result = service.plan_route(
            service.select(required_protocols=("http",)), {
                "hostname": "demo.test", "owner": "/tmp/project::default",
                "wildcard": False, "listen": {"address": "127.0.0.1", "port": 80},
            }, {"address": "127.0.0.1", "port": 8123},
            credential_reference="machine/ingress/api-token",
        )
        self.assertEqual(result["state"], "pending_credentials")
        self.assertEqual(result["credential_reference"], "machine/ingress/api-token")
        self.assertNotIn("token=", repr(result))

    def test_cleanup_preserves_drift_and_removes_unchanged(self):
        service, adapter, _runner, repository = self.service()
        applied = service.apply_route(self.planned(service), interactive=True)
        adapter.current = {"route": "foreign"}
        drift = service.cleanup_owner("/tmp/project::default")
        self.assertEqual(drift["state"], "cleanup_incomplete")
        self.assertIsNotNone(repository.route(applied["route_id"]))
        adapter.current = repository.route(applied["route_id"]).last_applied
        clean = service.cleanup_owner("/tmp/project::default")
        self.assertTrue(clean["ok"])
        self.assertIsNone(repository.route(applied["route_id"]))


if __name__ == "__main__": unittest.main()
