from __future__ import annotations

from types import SimpleNamespace
import unittest


class Domains:
    def __init__(self, events, ok=True, root=None):
        self.events = events; self.ok = ok; self.cleanups = 0; self.root = root
        self.apply_roots = []
    def apply(self, project_dir, **kwargs):
        self.apply_roots.append(project_dir)
        self.events.append("dns")
        return SimpleNamespace(ok=self.ok, state="ready" if self.ok else "fallback",
            mutated=self.ok, hostname="demo.test", actual_answers=("127.0.0.1",),
            fallback_url="http://localhost:8123", reason={"code": "ready"})
    def cleanup(self, *args, **kwargs):
        self.events.append("dns-cleanup"); self.cleanups += 1
        return SimpleNamespace(to_dict=lambda: {"ok": True})
    def ingress_policy(self, *_args, **_kwargs):
        return {"pin": "fixture", "pin_source": "machine_override",
                "project_root": self.root} if self.root else {
                    "pin": "fixture", "pin_source": "machine_override"}


class Ingress:
    def __init__(self, events, route_ok=True):
        self.events = events; self.route_ok = route_ok; self.selection_kwargs = None
    def select(self, **kwargs):
        self.selection_kwargs = kwargs
        self.events.append("select")
        return SimpleNamespace(adapter_id="fixture", accepted_addresses=("127.0.0.1",),
            listen_addresses=("127.0.0.1",),
            reason_code="selected", required_protocols=frozenset({"http"}),
            required_capabilities=frozenset({"http"}))
    def naming_offer(self, selection, **kwargs): self.events.append("offer"); return {"accepted_addresses": selection.accepted_addresses}
    def authorize(self, selection, **kwargs): self.events.append("consent"); return {"ok": True}
    def plan_route(self, *args):
        self.events.append("route-plan"); self.planned = args; return {"ok": True}
    def apply_route(self, *args, **kwargs):
        self.events.append("route-apply")
        return {"ok": self.route_ok, "state": "ready" if self.route_ok else "rollback_complete",
                "mutated": self.route_ok}


class TestCleanUrlService(unittest.TestCase):
    def test_sequence_is_offer_then_dns_then_route(self):
        from sandbox.application.clean_url_service import CleanUrlService
        events = []; service = CleanUrlService(ingress=Ingress(events), domains=Domains(events))
        result = service.apply("/tmp/project", backend={"address": "127.0.0.1", "port": 8123},
                               fallback_url="http://localhost:8123")
        self.assertTrue(result["ok"])
        self.assertEqual(events, ["select", "offer", "consent", "dns", "route-plan", "route-apply"])

    def test_project_pin_is_carried_into_one_ingress_selection(self):
        from sandbox.application.clean_url_service import CleanUrlService
        events = []; ingress = Ingress(events)
        service = CleanUrlService(ingress=ingress, domains=Domains(events))
        result = service.apply("/tmp/project", backend={"address": "127.0.0.1", "port": 8123},
                               fallback_url="http://localhost:8123")
        self.assertTrue(result["ok"])
        self.assertEqual(ingress.selection_kwargs["pin"], "fixture")
        self.assertEqual(ingress.selection_kwargs["pin_source"], "machine_override")

    def test_nested_invocation_uses_domain_services_canonical_project_owner(self):
        from sandbox.application.clean_url_service import CleanUrlService

        events = []; domains = Domains(events, root="/canonical/project")
        ingress = Ingress(events)
        service = CleanUrlService(ingress=ingress, domains=domains)
        service.apply("/canonical/project/subdir", backend={"address": "127.0.0.1", "port": 8123},
                      fallback_url="http://localhost:8123")
        self.assertEqual(domains.apply_roots, ["/canonical/project"])
        self.assertEqual(ingress.planned[1]["owner"], "/canonical/project::default")

    def test_dns_failure_never_plans_or_applies_route(self):
        from sandbox.application.clean_url_service import CleanUrlService
        events = []; service = CleanUrlService(
            ingress=Ingress(events), domains=Domains(events, ok=False))
        result = service.apply("/tmp/project", backend={"address": "127.0.0.1", "port": 8123},
                               fallback_url="http://localhost:8123")
        self.assertFalse(result["ok"])
        self.assertEqual(events, ["select", "offer", "consent", "dns"])

    def test_route_failure_cleans_new_dns_ownership(self):
        from sandbox.application.clean_url_service import CleanUrlService
        events = []; domains = Domains(events)
        service = CleanUrlService(ingress=Ingress(events, route_ok=False), domains=domains)
        result = service.apply("/tmp/project", backend={"address": "127.0.0.1", "port": 8123},
                               fallback_url="http://localhost:8123")
        self.assertFalse(result["ok"]); self.assertEqual(domains.cleanups, 1)
        self.assertEqual(events[-1], "dns-cleanup")


if __name__ == "__main__": unittest.main()
