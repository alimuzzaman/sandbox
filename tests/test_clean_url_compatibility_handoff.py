import unittest
from unittest import mock


class _CleanUrl:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def apply(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


class _CleanUrlSequence:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def apply(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.results.pop(0)


class _IngressCleanup:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def cleanup_owner(self, owner):
        self.calls.append(owner)
        return {"ok": self.ok,
                "state": "ready" if self.ok else "cleanup_incomplete",
                "mutated": self.ok}


class _DomainCleanup:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def cleanup(self, root, **kwargs):
        self.calls.append((root, kwargs))
        return {"ok": self.ok,
                "state": "ready" if self.ok else "cleanup_incomplete",
                "mutated": self.ok}


class TestCleanUrlCompatibilityHandoff(unittest.TestCase):
    def test_legacy_facade_delegates_route_and_dns_to_composed_service(self):
        from sandbox.core import _domains

        service = _CleanUrl({"ok": True, "state": "ready", "mutated": True,
                             "reason": {"code": "ready"}})
        cfg = {"instances": {"demo": {"wordpress_port": 8123}}}
        with mock.patch.object(_domains, "registry_find_instance", return_value={
                "root": "/project", "label": "preview", "wordpress_port": 8123,
                "url": "http://localhost:8123"}), \
             mock.patch.object(_domains, "resolve_instances", return_value=cfg["instances"]):
            result = _domains.clean_url_compatibility_handoff(
                cfg, "demo", service_factory=lambda _cfg: service,
            )

        self.assertTrue(result["ok"])
        args, kwargs = service.calls[0]
        self.assertEqual(args, ("/project",))
        self.assertEqual(kwargs["label"], "preview")
        self.assertEqual(kwargs["backend"], {"address": "127.0.0.1", "port": 8123})
        self.assertEqual(kwargs["protocols"], ("http",))

    def test_composed_secure_success_skips_aggregate_proxy_and_certificate_helpers(self):
        from sandbox.core import _domains

        local = {"instances": {}}
        cfg = {"instances": {"demo": {"wordpress_port": 8123}}}
        with mock.patch.object(_domains, "resolve_instances", return_value=cfg["instances"]), \
             mock.patch.object(_domains, "clean_url_compatibility_handoff", return_value={
                 "ok": True, "hostname": "demo.test", "url": "https://demo.test"}), \
             mock.patch.object(_domains, "_local_yaml", return_value=local), \
             mock.patch.object(_domains, "_write_local_yaml") as write_local, \
             mock.patch.object(_domains, "registry_find_instance", return_value={
                 "root": "/project", "label": "default"}), \
             mock.patch.object(_domains, "registry_put") as registry_put, \
             mock.patch.object(_domains, "_ensure_url_proxy") as proxy, \
             mock.patch.object(_domains, "_mint_cert") as mint, \
             mock.patch.object(_domains, "regen_caddyfile") as regenerate, \
             mock.patch.object(_domains, "reload_proxy") as reload:
            self.assertTrue(_domains._secure_at_create(cfg, "demo"))

        self.assertEqual(local["instances"]["demo"]["url"], "https://demo.test")
        write_local.assert_called_once_with(local)
        registry_put.assert_called_once_with("/project", label="default",
                                             domain="demo.test", url="https://demo.test")
        proxy.assert_not_called(); mint.assert_not_called()
        regenerate.assert_not_called(); reload.assert_not_called()

    def test_composed_fallback_never_enters_legacy_secure_path(self):
        from sandbox.core import _domains

        local = {"instances": {}}
        cfg = {"instances": {"demo": {"wordpress_port": 8123, "tld": "tst"}}}
        with mock.patch.object(_domains, "resolve_instances", side_effect=lambda _cfg: cfg["instances"]), \
             mock.patch.object(_domains, "clean_url_compatibility_handoff", return_value={
                 "ok": False, "state": "fallback"}), \
             mock.patch.object(_domains.shutil, "which", return_value="/usr/bin/mkcert"), \
             mock.patch.object(_domains, "_local_yaml", return_value=local), \
             mock.patch.object(_domains, "_write_local_yaml"), \
             mock.patch.object(_domains, "load_config", return_value=cfg), \
             mock.patch.object(_domains, "_ensure_url_proxy", return_value=(True, cfg)) as proxy, \
             mock.patch.object(_domains, "_mint_cert") as mint, \
             mock.patch.object(_domains, "regen_caddyfile") as regenerate, \
             mock.patch.object(_domains, "reload_proxy") as reload:
            self.assertFalse(_domains._secure_at_create(cfg, "demo"))

        proxy.assert_not_called(); mint.assert_not_called()
        regenerate.assert_not_called(); reload.assert_not_called()

    def test_missing_owner_preserves_legacy_fallback_without_service_mutation(self):
        from sandbox.core import _domains

        with mock.patch.object(_domains, "registry_find_instance", return_value=None):
            result = _domains.clean_url_compatibility_handoff(
                {}, "missing", service_factory=lambda _cfg: self.fail("must not compose"),
            )
        self.assertEqual(result["state"], "fallback")
        self.assertFalse(result["mutated"])

    def test_batch_failure_rolls_back_new_composed_state_before_legacy_fallback(self):
        from sandbox.core import _domains

        service = _CleanUrlSequence(
            {"ok": True, "state": "ready", "mutated": True,
             "hostname": "one.test"},
            {"ok": False, "state": "fallback", "mutated": False,
             "reason": {"code": "resolver_not_adoptable"}},
        )
        ingress, domains = _IngressCleanup(), _DomainCleanup()
        owners = {
            "one": {"root": "/project/one", "label": "default",
                    "wordpress_port": 8123},
            "two": {"root": "/project/two", "label": "preview",
                    "wordpress_port": 8124},
        }
        with mock.patch.object(_domains, "_compatibility_targets",
                               return_value=("one", "two")), \
             mock.patch.object(_domains, "registry_find_instance",
                               side_effect=lambda name: owners[name]), \
             mock.patch.object(_domains, "resolve_instances", return_value={
                 "one": {"wordpress_port": 8123},
                 "two": {"wordpress_port": 8124},
             }):
            result = _domains.clean_url_lifecycle_handoff(
                {}, "setup", service_factory=lambda _cfg: service,
                ingress_factory=lambda _cfg: ingress,
                domain_factory=lambda _cfg: domains,
            )

        self.assertFalse(result["ok"])
        self.assertTrue(result["safe_to_fallback"])
        self.assertTrue(result["rollback"]["complete"])
        self.assertEqual(ingress.calls, ["/project/one::default"])
        self.assertEqual(domains.calls, [
            ("/project/one", {"label": "default", "interactive": False}),
        ])

    def test_incomplete_composed_rollback_blocks_legacy_fallback(self):
        from sandbox.core import _domains

        service = _CleanUrl({"ok": False, "state": "fallback", "mutated": True,
                             "reason": {"code": "verification_failed"}})
        owner = {"root": "/project", "label": "default",
                 "wordpress_port": 8123}
        with mock.patch.object(_domains, "_compatibility_targets",
                               return_value=("demo",)), \
             mock.patch.object(_domains, "registry_find_instance", return_value=owner), \
             mock.patch.object(_domains, "resolve_instances",
                               return_value={"demo": {"wordpress_port": 8123}}):
            result = _domains.clean_url_lifecycle_handoff(
                {}, "setup", service_factory=lambda _cfg: service,
                ingress_factory=lambda _cfg: _IngressCleanup(ok=False),
                domain_factory=lambda _cfg: _DomainCleanup(),
            )

        self.assertEqual(result["state"], "rollback_incomplete")
        self.assertFalse(result["safe_to_fallback"])

    def test_http_setup_success_skips_legacy_proxy_path(self):
        from sandbox.core import _domains

        lifecycle = {"ok": True, "state": "ready", "mutated": True,
                     "results": [{"instance": "demo", "hostname": "demo.test",
                                  "protocols": ("http",)}]}
        with mock.patch.object(_domains, "clean_url_lifecycle_handoff",
                               return_value=lifecycle) as handoff, \
             mock.patch.object(_domains, "_persist_composed_clean_urls",
                               return_value={"refreshed": True}) as persist, \
             mock.patch.object(_domains, "revoke_legacy_sudoers",
                               return_value=(True, "absent")), \
             mock.patch.object(_domains, "_ensure_url_proxy") as legacy:
            result = _domains.clean_url_setup({}, interactive=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "application")
        handoff.assert_called_once_with(
            {}, "setup", interactive=True, protocols=("http",),
        )
        persist.assert_called_once_with({}, lifecycle)
        legacy.assert_not_called()

    def test_up_success_uses_composed_service_without_legacy_proxy(self):
        from sandbox.core import _domains

        lifecycle = {"ok": True, "state": "ready", "mutated": False,
                     "results": []}
        with mock.patch.object(_domains, "resolve_instances", return_value={}), \
             mock.patch.object(_domains, "clean_url_lifecycle_handoff",
                               return_value=lifecycle), \
             mock.patch.object(_domains, "_persist_composed_clean_urls"), \
             mock.patch.object(_domains, "proxy_available") as availability, \
             mock.patch.object(_domains, "reload_proxy") as reload:
            self.assertTrue(_domains.proxy_up({}))

        availability.assert_not_called()
        reload.assert_not_called()

    def test_down_cleans_composed_owners_without_touching_legacy_proxy(self):
        from sandbox.core import _domains

        events = []
        lifecycle = {"ok": True, "state": "ready", "mutated": True,
                     "reason": {"code": "cleanup_complete"}}
        completed = mock.Mock(returncode=0)
        with mock.patch.object(
                _domains, "clean_url_lifecycle_handoff",
                side_effect=lambda *_args, **_kwargs: events.append("application") or lifecycle,
             ), mock.patch.object(
                _domains.subprocess, "run",
                side_effect=lambda *_args, **_kwargs: events.append("legacy") or completed,
             ):
            self.assertTrue(_domains.proxy_down({}))

        self.assertEqual(events, ["application"])

    def test_teardown_refuses_legacy_dns_removal_after_incomplete_cleanup(self):
        from sandbox.core import _domains

        lifecycle = {"ok": False, "state": "cleanup_incomplete", "mutated": False,
                     "safe_to_fallback": False,
                     "reason": {"code": "cleanup_incomplete"}}
        with mock.patch.object(_domains, "clean_url_lifecycle_handoff",
                               return_value=lifecycle), \
             mock.patch.object(_domains.subprocess, "run") as legacy, \
             mock.patch.object(_domains, "info"):
            self.assertFalse(_domains.proxy_teardown({}))

        legacy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
