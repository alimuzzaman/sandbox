"""Guards for the Docker/Caddy clean-URL default.

These tests exist because the default path was once disabled in place, which
silently downgraded every instance to `http://localhost:<port>`. They fail loudly
if that happens again. Policy: docs/clean-url-default.md; specs 037 FR-007/FR-031
-FR-034, 038 FR-029-FR-033, 039 FR-041/FR-042; constitution principle VI.
"""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tools" / "proxy-helper.sh"
INSTALLED = "/usr/local/libexec/sandbox-proxy-helper"


class TestProxyHelperIsUsable(unittest.TestCase):
    def test_helper_still_implements_the_default_host_actions(self):
        text = HELPER.read_text()
        for action in ("alias-up)", "alias-down)", "dns-up)", "dns-down)",
                       "dns-flush)", "install)", "installed-status)"):
            self.assertIn(action, text, f"proxy-helper.sh lost the {action} action")
        self.assertNotIn("retired privileged compatibility entry point",
                         text.lower(),
                         "proxy-helper.sh was replaced by a refusal stub again")

    def test_privileged_target_is_the_root_owned_copy_not_the_checkout(self):
        text = HELPER.read_text()
        self.assertIn(f"INSTALLED_HELPER={INSTALLED}", text)
        self.assertIn("require_installed_helper", text)
        allowed = [line for line in text.splitlines() if line.strip().startswith("allowed=")]
        self.assertTrue(allowed, "the NOPASSWD rule template disappeared")
        for line in allowed:
            self.assertNotIn("tools/proxy-helper.sh", line,
                             "the NOPASSWD rule must never name the writable checkout")
            self.assertIn("$INSTALLED_HELPER", line)


class TestDefaultProviderWiring(unittest.TestCase):
    def test_ensure_url_proxy_is_not_a_disabled_stub(self):
        from sandbox.core import _domains

        source = inspect.getsource(_domains._ensure_url_proxy)
        for expected in ("proxy_apply(", "regen_caddyfile(", "dns-up", "alias-up",
                         "PROXY_HELPER_INSTALLED"):
            self.assertIn(expected, source,
                          "the default clean-URL bootstrap was stubbed out again")

    def test_default_mode_is_sandbox_caddy(self):
        from sandbox.core import _domains

        self.assertEqual(_domains.clean_url_mode({}), "sandbox-caddy")
        self.assertFalse(_domains._adoption_selected({}))

    def test_switch_surface_exists(self):
        from sandbox.commands import domains as domains_cmd

        self.assertIn("use", domains_cmd.DOMAIN_ACTIONS)
        self.assertEqual(domains_cmd.DEFAULT_PROVIDER, "sandbox-caddy")


class TestPublishedListenerCheck(unittest.TestCase):
    """037 T076 / FR-034: a published-but-not-listening endpoint must be named,
    not left to surface as a bare connection refusal."""

    def test_healthy_endpoints_report_ok(self):
        from sandbox.core import _domains

        check = _domains._published_listener_check(connector=lambda *_: True)
        self.assertTrue(check["ok"])
        self.assertIn(_domains.PROXY_BIND_IP, check["label"])

    def test_dead_endpoint_names_the_owner_and_the_recovery(self):
        from sandbox.core import _domains

        check = _domains._published_listener_check(
            connector=lambda *_: False,
            listeners={80: "nginx (127.0.0.1:80)", 443: "nginx (127.0.0.1:443)"},
        )
        self.assertFalse(check["ok"])
        self.assertIn("nginx", check["label"])
        self.assertIn("domains use", check["hint"])

    def test_unidentifiable_owner_still_reports_the_failure(self):
        from sandbox.core import _domains

        check = _domains._published_listener_check(
            connector=lambda *_: False, listeners={},
        )
        self.assertFalse(check["ok"])
        self.assertIn("no other listener identified", check["label"])

    def test_check_is_wired_into_doctor_when_the_proxy_runs(self):
        from unittest import mock

        from sandbox.core import _domains

        sentinel = {"label": "sentinel", "ok": False, "hint": ""}
        with mock.patch.object(_domains, "_proxy_container_running", return_value=True), \
             mock.patch.object(_domains, "_caddyfile_readable_in_container", return_value=True), \
             mock.patch.object(_domains, "resolve_instances", return_value={}), \
             mock.patch.object(_domains, "_published_listener_check", return_value=sentinel):
            checks = _domains.proxy_health_checks({})

        self.assertIn(sentinel, checks)


class TestPolicyStaysDocumented(unittest.TestCase):
    """Spec, docs, and code must move together (constitution principle V)."""

    def test_policy_doc_exists_and_names_the_default(self):
        doc = (ROOT / "docs" / "clean-url-default.md").read_text()
        self.assertIn("sandbox-caddy", doc)
        self.assertIn("./sb domains use", doc)

    def test_specs_record_the_default_and_the_removal_gate(self):
        ingress = (ROOT / "specs" / "037-host-ingress-adoption" / "spec.md").read_text()
        resolution = (ROOT / "specs" / "038-tld-dns-adoption" / "spec.md").read_text()
        native = (ROOT / "specs" / "039-native-runtime-adoption" / "spec.md").read_text()
        self.assertIn("MUST be the default ingress on every supported", ingress)
        self.assertIn("Incumbent adoption MUST be opt-in", ingress)
        self.assertIn("MUST be the default strategy on every", resolution)
        self.assertIn("Docker Compose MUST remain the default runtime", native)

    def test_agent_guides_point_at_the_policy(self):
        for name in ("CLAUDE.md", "AGENTS.md"):
            text = (ROOT / name).read_text()
            self.assertIn("docs/clean-url-default.md", text,
                          f"{name} must point continuation work at the policy")


if __name__ == "__main__":
    unittest.main()
