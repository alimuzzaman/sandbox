from __future__ import annotations

import inspect
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock


class QualifiedAdapter:
    def __init__(self, *, helper=True, authorized=True, preflight=None,
                 preflights=None):
        self.helper = helper
        self.authorized = authorized
        self.preflight = preflight or {
            "schema": "sandbox-resolved-service-v1",
            "owner_id": "systemd-resolved:host",
            "unit": "systemd-resolved.service",
            "pid": 321,
            "start_ticks": 654321,
            "uid": 0,
            "control_group": "/system.slice/systemd-resolved.service",
        }
        self.calls = []
        self.preflights = [dict(value) for value in (preflights or ())]

    def qualification_preflight(self, observation):
        self.calls.append(("qualification_preflight", observation.owner_id))
        if self.preflights:
            return self.preflights.pop(0)
        return dict(self.preflight)

    def plan(self, suffix, address, port):
        return {"kind": "resolved-route", "suffix": suffix,
                "address": address, "port": port, "global_takeover": False}

    def ensure_helper(self, *, interactive):
        self.calls.append(("ensure_helper", interactive))
        return {"ok": self.helper, "mutated": False}

    def ensure_authorized(self, plan, *, interactive):
        self.calls.append(("ensure_authorized", interactive))
        return {"ok": self.authorized, "mutated": False}

    def revoke_authorization(self, plan):
        self.calls.append(("revoke_authorization",))
        return {"ok": True, "mutated": False}

    def apply(self, plan):
        self.calls.append(("apply",))
        return {"ok": True, "mutated": True, "applied": {"route": "owned"}}

    def rollback(self, plan):
        self.calls.append(("rollback",))
        return {"ok": True, "mutated": True}

    def observe(self, binding):
        return None


class Endpoints:
    def __init__(self):
        self.calls = 0

    def allocate(self):
        self.calls += 1
        return "127.0.0.55", 45353


class Authority:
    def __init__(self, state=None):
        self.state = dict(state or {})
        self.calls = []

    def status(self):
        return dict(self.state)

    def ensure(self, bindings, **endpoint):
        self.calls.append(("ensure", bindings, endpoint))
        return {"ok": True}


def observation(*, owner="systemd-resolved:host", manager="resolved",
                mode="stub"):
    from sandbox.network.models import ResolverObservation

    return ResolverObservation.create(
        owner_id=owner, manager=manager, mode=mode,
        support_tier="adoptable",
        extension={"kind": "route-only-domain", "global_takeover": False},
        evidence=("bounded synthetic observation",),
    )


def service(*, adapter=None, observed=None, strategy="systemd-resolved",
            wildcard=False, platform="linux", authority=None):
    from sandbox.application.domain_service import DomainService
    from sandbox.network.manifest import built_in_resolver_registry
    from sandbox.network.repository import DomainRepository

    temporary = tempfile.TemporaryDirectory()
    implementation = adapter or QualifiedAdapter()
    endpoints = Endpoints()
    authority = authority or Authority()
    policy = {
        "hostname": "stable.test", "tld": "test", "strategy": strategy,
        "wildcard": wildcard, "suffixClass": "test",
        "hostnameSource": "persisted", "strategySource": (
            "machine_override" if strategy else "default"
        ),
    }
    instance = DomainService(
        config_loader=lambda root, label=None: {
            "root": root, "slug": "stable", "domains": policy,
        },
        project_registry=type("Registry", (), {
            "registry_get": staticmethod(lambda root, label=None: {
                "url": "http://localhost:8123", "instance": "stable",
                "domain": "stable.test",
            }),
        }),
        adapters=built_in_resolver_registry({"systemd-resolved": implementation}),
        repository=DomainRepository(Path(temporary.name) / "state.json"),
        process=object(), http=object(), endpoints=endpoints,
        observer=lambda _hostname: observed or observation(),
        ingress_offer=lambda *_args: {
            "accepted_addresses": ("127.0.0.77",),
            "fallback_url": "http://localhost:8123",
            "capabilities": {"wildcard": False},
        },
        authority=authority, verifier=lambda *_args: True,
        consent_decider=lambda _owner: True,
        platform=platform,
    )
    instance._qualification_temporary = temporary
    return instance, implementation, endpoints, authority


class TestResolverProductionQualification(unittest.TestCase):
    def _service(self, **kwargs):
        result = service(**kwargs)
        self.addCleanup(result[0]._qualification_temporary.cleanup)
        return result

    def test_registry_has_only_checked_in_resolved_exact_qualification(self):
        from sandbox.network.manifest import built_in_resolver_registry
        from sandbox.network.qualification import SYSTEMD_RESOLVED_QUALIFICATION

        registry = built_in_resolver_registry({
            "systemd-resolved": QualifiedAdapter(),
            "networkmanager": object(),
        })
        resolved = registry.get("systemd-resolved")
        self.assertTrue(resolved.adoptable)
        self.assertEqual(resolved.evidence_id, "038-t034-ubuntu-2404")
        self.assertEqual(resolved.platforms, ("linux",))
        self.assertEqual(resolved.capabilities, frozenset({"exact"}))
        self.assertEqual(SYSTEMD_RESOLVED_QUALIFICATION.evidence_id,
                         "038-t034-ubuntu-2404")
        for item in registry.items():
            if item.adapter_id != "systemd-resolved":
                self.assertFalse(item.adoptable, item.adapter_id)

    def test_registry_accepts_no_runtime_proof_input_or_attestation_type(self):
        import sandbox.network.manifest as manifest

        self.assertFalse(hasattr(manifest, "ResolverProofAttestation"))
        self.assertNotIn(
            "proof_attestation",
            inspect.signature(manifest.built_in_resolver_registry).parameters,
        )
        for value in (
            "038-t034-ubuntu-2404",
            {"systemd-resolved": "038-t034-ubuntu-2404"},
            object(),
        ):
            with self.subTest(value=type(value).__name__), self.assertRaises(TypeError):
                manifest.built_in_resolver_registry(
                    {"systemd-resolved": QualifiedAdapter()},
                    proof_attestation=value,
                )

        with mock.patch.dict(os.environ, {
            "SANDBOX_RESOLVER_EVIDENCE": "forged",
            "SANDBOX_CLEAN_URL_MODE": "networkmanager",
        }, clear=False):
            registry = manifest.built_in_resolver_registry({
                "systemd-resolved": QualifiedAdapter(),
                "networkmanager": QualifiedAdapter(),
            })
        self.assertTrue(registry.get("systemd-resolved").adoptable)
        self.assertEqual(registry.get("systemd-resolved").capabilities,
                         frozenset({"exact"}))
        self.assertFalse(registry.get("networkmanager").adoptable)

    def test_only_observed_linux_resolved_exact_name_reaches_plan(self):
        qualified, _adapter, endpoints, _authority = self._service()
        result = qualified.plan("/tmp/project")
        self.assertEqual(result.state, "pending_consent")
        self.assertEqual(endpoints.calls, 1)

        cases = (
            ({"observed": observation(owner="resolved:changed")},
             "resolver_not_qualified"),
            ({"observed": observation(owner="networkmanager:host",
                                       manager="networkmanager")},
             "resolver_not_qualified"),
            ({"platform": "darwin"}, "resolver_not_qualified"),
            ({"wildcard": True}, "wildcard_unsupported"),
        )
        for arguments, reason in cases:
            with self.subTest(arguments=arguments):
                refused, _adapter, endpoints, _authority = self._service(**arguments)
                result = refused.plan("/tmp/project")
                self.assertEqual(result.reason["code"], reason)
                self.assertEqual(endpoints.calls, 0)

    def test_missing_helper_authorization_control_is_refused_before_allocation(self):
        class MissingAuthorization(QualifiedAdapter):
            ensure_authorized = None

        refused, adapter, endpoints, authority = self._service(
            adapter=MissingAuthorization(),
        )
        result = refused.apply("/tmp/project", interactive=True)
        self.assertEqual(result.reason["code"], "resolver_not_qualified")
        self.assertEqual(endpoints.calls, 0)
        self.assertEqual(adapter.calls, [])
        self.assertEqual(authority.calls, [])

    def test_foreign_authority_state_is_refused_before_allocation_or_mutation(self):
        refused, adapter, endpoints, authority = self._service(
            authority=Authority({"health": "foreign_collision"}),
        )
        result = refused.apply("/tmp/project", interactive=True)
        self.assertEqual(result.state, "foreign_collision")
        self.assertEqual(result.reason["code"], "authority_endpoint_collision")
        self.assertEqual(endpoints.calls, 0)
        self.assertEqual(adapter.calls, [
            ("qualification_preflight", "systemd-resolved:host"),
        ])
        self.assertEqual(authority.calls, [])

    def test_foreign_service_owner_is_refused_before_name_or_authority_mutation(self):
        adapter = QualifiedAdapter(preflight={
            "schema": "sandbox-resolved-service-v1",
            "owner_id": "networkmanager:host",
            "unit": "NetworkManager.service",
            "pid": 654,
            "start_ticks": 987654,
            "uid": 0,
            "control_group": "/system.slice/NetworkManager.service",
        })
        refused, adapter, endpoints, authority = self._service(adapter=adapter)

        result = refused.apply("/tmp/project", interactive=True)

        self.assertEqual(result.reason["code"], "resolver_not_qualified")
        self.assertEqual(endpoints.calls, 0)
        self.assertEqual(authority.calls, [])
        self.assertEqual(adapter.calls, [
            ("qualification_preflight", "systemd-resolved:host"),
        ])

    def test_service_identity_change_is_refused_before_dns_or_authority_mutation(self):
        first = {
            "schema": "sandbox-resolved-service-v1",
            "owner_id": "systemd-resolved:host",
            "unit": "systemd-resolved.service",
            "pid": 321,
            "start_ticks": 654321,
            "uid": 992,
            "control_group": "/system.slice/systemd-resolved.service",
        }
        changed = {**first, "pid": 654, "start_ticks": 987654}
        refused, adapter, endpoints, authority = self._service(
            adapter=QualifiedAdapter(preflights=(first, changed)),
        )

        result = refused.apply("/tmp/project", interactive=True)

        self.assertEqual(result.reason["code"], "resolver_changed")
        self.assertEqual(endpoints.calls, 1)
        self.assertEqual(authority.calls, [])
        self.assertEqual(adapter.calls, [
            ("qualification_preflight", "systemd-resolved:host"),
            ("qualification_preflight", "systemd-resolved:host"),
        ])

    def test_unselected_resolver_remains_default_and_never_auto_adopts(self):
        unselected, adapter, endpoints, authority = self._service(strategy=None)
        result = unselected.apply("/tmp/project", interactive=True)
        self.assertEqual(result.state, "fallback")
        self.assertEqual(result.reason["code"], "resolver_not_selected")
        self.assertEqual(endpoints.calls, 0)
        self.assertEqual(adapter.calls, [])
        self.assertEqual(authority.calls, [])

    def test_domains_use_resolved_preserves_hostname_without_reprovision(self):
        from sandbox.commands.domains import _use_provider
        from sandbox.core import _config, _domains

        local = {"domains": {"hostname": "stable.test", "ingress": "system-caddy"}}
        policy = type("Policy", (), {"ingress_policy": lambda *_args, **_kwargs: {}})()
        args = SimpleNamespace(
            tld="systemd-resolved", resolver=None,
            project_dir="/tmp/project", label="default",
        )
        with mock.patch.object(_config, "_local_yaml", return_value=local), \
             mock.patch.object(_config, "_write_local_yaml") as write, \
             mock.patch.object(_domains, "clean_url_mode",
                               return_value="systemd-resolved"), \
             mock.patch("sandbox.application.context.domain_service",
                        return_value=policy):
            result = _use_provider(args)

        self.assertEqual(result["provider"], "systemd-resolved")
        write.assert_called_once_with({
            "domains": {"hostname": "stable.test",
                        "strategy": "systemd-resolved"},
        })


if __name__ == "__main__":
    unittest.main()
