from __future__ import annotations

from pathlib import Path
import tempfile
import unittest


class TestDomainStatus(unittest.TestCase):
    def _service(self, *, manager="resolved", answers=("127.0.0.77",),
                 observed=None, authority_health="healthy", verified=True,
                 strategy=None, strategy_source="default"):
        from sandbox.application.domain_service import DomainService
        from sandbox.network.models import ResolutionBinding, ResolverObservation
        from sandbox.network.registry import ResolverAdapterRegistry, ResolverAdapterSpec
        from sandbox.network.repository import DomainRepository

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repository = DomainRepository(Path(temporary.name) / "state.json")
        owner = f"{Path('/tmp/project').resolve()}::default"
        binding = ResolutionBinding.create(
            kind="exact", name="demo.test", target="127.0.0.77",
            adapter_id="resolved-adapter", owners=(owner,),
            desired={"route": "demo"},
        ).with_applied({"route": "demo"})
        repository.put_binding(binding)
        adapters = ResolverAdapterRegistry()
        adapter = object()
        adapters.register(ResolverAdapterSpec(
            "resolved-adapter", adapter, ("resolved",), ("linux",), "adoptable",
            frozenset({"exact"}), "proof", 10,
        ))
        observation = ResolverObservation.create(
            owner_id=f"{manager}:host", manager=manager, mode="scoped",
            support_tier="adoptable", current_answers=answers,
        )
        service = DomainService(
            config_loader=lambda root, label=None: {"root": root, "domains": {
                "hostname": "demo.test", "tld": "test", "strategy": strategy,
                "wildcard": False, "hostnameSource": "persisted",
                "strategySource": strategy_source,
            }},
            project_registry=type("Registry", (), {"registry_get": staticmethod(
                lambda root, label=None: {"instance": "demo", "url": "http://localhost:8123"}
            )}),
            adapters=adapters, repository=repository, process=object(), http=object(),
            endpoints=object(), observer=lambda _hostname: observation,
            ingress_offer=lambda _root, _label: {
                "accepted_addresses": ("127.0.0.77",),
                "fallback_url": "http://localhost:8123",
            },
            binding_observer=lambda _binding, _adapter: (
                {"route": "demo"} if observed is None else observed
            ),
            authority_observer=lambda: {"health": authority_health},
            verifier=lambda *_args: verified,
        )
        return service

    def test_healthy_status_reports_actual_expected_ownership_and_pin_source(self):
        result = self._service(
            strategy="resolved-adapter", strategy_source="machine_override",
        ).status("/tmp/project")
        self.assertTrue(result.ok)
        self.assertEqual(result.actual_answers, ("127.0.0.77",))
        self.assertEqual(result.expected_addresses, ("127.0.0.77",))
        self.assertEqual(result.ownership, "owned")
        self.assertEqual(result.strategy_source, "machine_override")

    def test_resolver_owner_change_is_distinct_from_binding_drift(self):
        result = self._service(manager="unknown").status("/tmp/project")
        self.assertEqual(result.reason["code"], "resolver_owner_changed")
        result = self._service(observed={"route": "changed"}).status("/tmp/project")
        self.assertEqual(result.reason["code"], "binding_drifted")

    def test_authority_down_and_answer_mismatch_are_actionable(self):
        result = self._service(authority_health="unhealthy").status("/tmp/project")
        self.assertEqual(result.reason["code"], "authority_unhealthy")
        result = self._service(answers=("127.0.0.1",)).status("/tmp/project")
        self.assertEqual(result.reason["code"], "answer_mismatch")
        self.assertIn("stale cache", result.reason["message"])

    def test_matching_dns_with_failed_ingress_is_not_healthy(self):
        result = self._service(verified=False).status("/tmp/project")
        self.assertEqual(result.reason["code"], "verification_failed")
        self.assertFalse(result.ok)


class TestDomainDiagnostic(unittest.TestCase):
    class Process:
        def __init__(self, *, stdout="127.0.0.1", returncode=0):
            self.stdout = stdout
            self.returncode = returncode
            self.calls = []

        def run(self, argv, *, timeout):
            self.calls.append((tuple(argv), timeout))
            from types import SimpleNamespace
            return SimpleNamespace(returncode=self.returncode, stdout=self.stdout)

    class Http:
        def __init__(self, result):
            self.result = result
            self.calls = []

        def probe_route_diagnostic(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return self.result

    def _verify(self, *, process=None, http=None):
        from sandbox.network.verification import DomainVerifier
        process = process or self.Process()
        http = http or self.Http({
            "ingress": "reachable", "application": "ready", "reason": "ready",
        })
        return DomainVerifier(process=process, http=http, platform="linux"), process, http

    def test_dns_unavailable_and_answer_mismatch_do_not_probe_http(self):
        verifier, process, http = self._verify(
            process=self.Process(returncode=1),
        )
        unavailable = verifier.diagnose(
            "demo.test", ("127.0.0.1",),
            {"address": "127.0.0.1", "port": 80, "protocol": "http"},
        )
        self.assertEqual(unavailable, {
            "ingress": {"state": "unavailable"},
            "application": {"state": "not_attempted"},
            "reason": {"code": "fresh_dns_unavailable"},
        })
        self.assertEqual(http.calls, [])

        process.returncode = 0
        process.stdout = "127.0.0.2"
        mismatch = verifier.diagnose(
            "demo.test", ("127.0.0.1",),
            {"address": "127.0.0.1", "port": 80, "protocol": "http"},
        )
        self.assertEqual(mismatch["reason"]["code"], "answer_mismatch")
        self.assertEqual(http.calls, [])

    def test_missing_or_malformed_selected_target_fails_closed(self):
        verifier, _process, http = self._verify()
        for target in (None, {}, {"address": "0.0.0.0", "port": 80},
                       {"address": "127.0.0.1", "port": "bad"},
                       {"address": "127.0.0.1", "port": 80, "protocol": "ftp"}):
            with self.subTest(target=target):
                result = verifier.diagnose("demo.test", ("127.0.0.1",), target)
                self.assertEqual(result, {
                    "ingress": {"state": "unavailable"},
                    "application": {"state": "not_attempted"},
                    "reason": {"code": "ingress_probe_unavailable"},
                })
        self.assertEqual(http.calls, [])

    def test_all_listener_and_application_classes_are_closed(self):
        from sandbox.network.verification import DomainVerifier
        target = {"address": "127.0.0.1", "port": 80, "protocol": "http"}
        cases = (
            ({"ingress": "unreachable", "application": "not_attempted",
              "reason": "ingress_listener_unreachable"}, "ingress_listener_unreachable"),
            ({"ingress": "timed_out", "application": "not_attempted",
              "reason": "ingress_connect_timeout"}, "ingress_connect_timeout"),
            ({"ingress": "reachable", "application": "timed_out",
              "reason": "application_response_timeout"}, "application_response_timeout"),
            ({"ingress": "reachable", "application": "http_unhealthy",
              "reason": "application_http_unhealthy"}, "application_http_unhealthy"),
            ({"ingress": "reachable", "application": "protocol_error",
              "reason": "ingress_probe_unavailable"}, "ingress_probe_unavailable"),
            ({"ingress": "reachable", "application": "ready", "reason": "ready"}, "ready"),
        )
        for observed, reason in cases:
            verifier, _process, http = self._verify(http=self.Http({
                **observed, "url": "https://secret.invalid/path",
                "headers": {"authorization": "secret"},
            }))
            result = verifier.diagnose("demo.test", ("127.0.0.1",), target)
            self.assertEqual(result["reason"]["code"], reason)
            self.assertEqual(set(result), {"ingress", "application", "reason"})
            self.assertEqual(set(result["ingress"]), {"state"})
            self.assertEqual(set(result["application"]), {"state"})
            self.assertEqual(set(result["reason"]), {"code"})

        verifier, _process, _http = self._verify(http=self.Http({
            "ingress": "reachable", "application": "ready",
            "reason": "application_response_timeout", "raw": "https://secret.invalid",
        }))
        contradictory = verifier.diagnose("demo.test", ("127.0.0.1",), target)
        self.assertEqual(contradictory, {
            "ingress": {"state": "unavailable"},
            "application": {"state": "not_attempted"},
            "reason": {"code": "ingress_probe_unavailable"},
        })

    def test_http_probe_receives_exact_endpoint_without_proxy_or_redirect_inputs(self):
        verifier, _process, http = self._verify()
        result = verifier.diagnose(
            "demo.test", ("127.0.0.1",),
            {"address": "127.0.0.1", "port": 18080, "protocol": "http"},
        )
        self.assertEqual(result["reason"]["code"], "ready")
        args, kwargs = http.calls[0]
        self.assertEqual(args, ("127.0.0.1", 18080, "demo.test"))
        self.assertEqual(kwargs, {"timeout": 5, "protocol": "http"})


if __name__ == "__main__":
    unittest.main()
