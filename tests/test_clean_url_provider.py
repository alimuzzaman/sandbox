"""Provider selection seam: 037 T043 / T070-T072, 038 T033 / T062-T063."""

from __future__ import annotations

import unittest

from sandbox.application.clean_url_provider import (
    DEFAULT_PROVIDER, ENV_VARIABLE, resolve_provider,
)


class TestProviderResolution(unittest.TestCase):
    def test_nothing_configured_selects_the_default_provider(self):
        selection = resolve_provider()
        self.assertEqual(selection.provider, DEFAULT_PROVIDER)
        self.assertEqual(selection.source, "default")
        self.assertFalse(selection.adoption)
        self.assertFalse(selection.disabled)

    def test_project_configuration_can_opt_in_to_adoption(self):
        selection = resolve_provider(project={"domains": {"ingress": "herd-valet"}})
        self.assertEqual(selection.provider, "herd-valet")
        self.assertEqual(selection.source, "project")
        self.assertTrue(selection.adoption)

    def test_machine_override_beats_project_configuration(self):
        selection = resolve_provider(
            machine={"domains": {"ingress": "system-nginx"}},
            project={"domains": {"ingress": "herd-valet"}},
        )
        self.assertEqual(selection.provider, "system-nginx")
        self.assertEqual(selection.source, "machine_override")

    def test_environment_beats_every_configuration_layer(self):
        selection = resolve_provider(
            env={ENV_VARIABLE: "Traefik"},
            machine={"domains": {"ingress": "system-nginx"}},
            project={"domains": {"ingress": "herd-valet"}},
        )
        self.assertEqual(selection.provider, "traefik")
        self.assertEqual(selection.source, "environment")

    def test_strategy_key_is_accepted_as_a_provider_selection(self):
        selection = resolve_provider(project={"domains": {"strategy": "dnsmasq"}})
        self.assertEqual(selection.provider, "dnsmasq")
        self.assertTrue(selection.adoption)

    def test_ingress_wins_over_strategy_within_one_layer(self):
        selection = resolve_provider(
            project={"domains": {"ingress": "traefik", "strategy": "dnsmasq"}},
        )
        self.assertEqual(selection.provider, "traefik")

    def test_explicit_default_aliases_do_not_enable_adoption(self):
        for value in ("sandbox-caddy", "default", "SANDBOX", "Caddy"):
            with self.subTest(value=value):
                selection = resolve_provider(machine={"domains": {"ingress": value}})
                self.assertEqual(selection.provider, DEFAULT_PROVIDER)
                self.assertFalse(selection.adoption)
                self.assertEqual(selection.source, "machine_override")

    def test_disabled_is_neither_default_nor_adoption(self):
        selection = resolve_provider(machine={"domains": {"ingress": "disabled"}})
        self.assertTrue(selection.disabled)
        self.assertFalse(selection.adoption)

    def test_a_bare_domains_block_is_accepted_without_reshaping(self):
        selection = resolve_provider(machine={"ingress": "system-caddy"})
        self.assertEqual(selection.provider, "system-caddy")

    def test_blank_and_malformed_layers_fall_through_to_the_default(self):
        for layer in ({}, {"domains": {}}, {"domains": {"ingress": "   "}},
                      {"domains": {"ingress": None}}, {"domains": "nonsense"},
                      None, "nonsense"):
            with self.subTest(layer=layer):
                self.assertEqual(resolve_provider(project=layer).provider,
                                 DEFAULT_PROVIDER)

    def test_resolution_is_pure(self):
        """No host reads: the same inputs always give the same answer."""
        first = resolve_provider(project={"domains": {"ingress": "traefik"}})
        second = resolve_provider(project={"domains": {"ingress": "traefik"}})
        self.assertEqual(first, second)
        self.assertEqual(first.to_dict()["provider"], "traefik")


class TestCoreFacadeDelegates(unittest.TestCase):
    def test_core_mode_helpers_use_the_application_seam(self):
        from unittest import mock

        from sandbox.core import _domains

        patched = mock.patch.object(_domains, "_machine_domains_block",
                                    return_value={})
        with patched, mock.patch.dict(_domains.os.environ, {}, clear=False):
            _domains.os.environ.pop("SANDBOX_CLEAN_URL_MODE", None)
            selection = _domains.clean_url_selection(
                {"domains": {"ingress": "traefik"}})
            self.assertEqual(selection.provider, "traefik")
            self.assertTrue(selection.adoption)
            self.assertEqual(_domains.clean_url_mode({}), DEFAULT_PROVIDER)


if __name__ == "__main__":
    unittest.main()
