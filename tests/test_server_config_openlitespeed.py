import unittest
from tests.server_config_fixtures import fragment, FIXED_INCARNATION, FIXED_NOW
from sandbox.server_config.models import ServerType

try:
    from sandbox.server_config.adapters.openlitespeed import OpenLiteSpeedAdapter
except ImportError:
    OpenLiteSpeedAdapter = None


class TestOpenLiteSpeedAdapter(unittest.TestCase):
    """T035: OpenLiteSpeed vhost-local rewrite/cache subset, denial, render, and ignored-directive tests."""

    def setUp(self):
        if OpenLiteSpeedAdapter is None:
            self.fail("sandbox.server_config.adapters.openlitespeed not implemented yet")
        self.adapter = OpenLiteSpeedAdapter()

    def test_subset_tokenizer_valid_fixture(self):
        """1. Subset tokenizer: Test that the OLS adapter can tokenize configuration text."""
        config_text = "rewrite {\n  enable 1\n  RewriteRule ^/cache/ - [E=Cache-Control:max-age=3600]\n}"
        tokens = self.adapter.tokenize(config_text)
        self.assertTrue(len(tokens) > 0)

    def test_deny_by_default(self):
        """2. Deny-by-default: Reject global, listener, admin, and external-processor directives."""
        forbidden_configs = [
            "listener HTTP { address *:80 }",
            "admin { allow 127.0.0.1 }",
            "extprocessor lsphp { type lsapi }",
            "virtualhost other { docRoot /var/www/ }",
            "vhTemplate docker { templateFile conf/docker.conf }",
            "accessControl { allow * }",
        ]
        for config in forbidden_configs:
            with self.subTest(config=config):
                with self.assertRaises(Exception):
                    self.adapter.validate(config)

    def test_accepted_directives(self):
        """3. Accepted directives: Test that wordpress-cache-v1 rewrite/context directives are accepted."""
        accepted_configs = [
            "rewrite {\n  enable 1\n  RewriteRule .* - [E=Cache-Control:max-age=3600]\n}",
            "context /wp-content/cache/ {\n  allowBrowse 1\n}",
        ]
        for config in accepted_configs:
            with self.subTest(config=config):
                self.assertTrue(self.adapter.validate(config))

    def test_complete_candidate_renderer(self):
        """4. Complete-candidate renderer: Test deterministic ordering and provenance markers."""
        f1 = fragment(name="01-first", content=b"rewrite {\n  enable 1\n}")
        f2 = fragment(name="02-second", content=b"context /wp-content/cache/ {\n  allowBrowse 1\n}")
        object.__setattr__(f1, "content", b"rewrite {\n  enable 1\n}")
        object.__setattr__(f2, "content", b"context /wp-content/cache/ {\n  allowBrowse 1\n}")

        rendered = self.adapter.render([f2, f1])
        rendered_text = rendered.files[0].content.decode("utf-8") if hasattr(rendered, "files") and rendered.files else rendered.content.decode("utf-8")

        self.assertIn("01-first", rendered_text)
        self.assertIn("02-second", rendered_text)
        pos1 = rendered_text.find("01-first")
        pos2 = rendered_text.find("02-second")
        self.assertLess(pos1, pos2)
        self.assertIn("sandbox-fragment", rendered_text)

    def test_ignored_directive_detection(self):
        """5. Ignored directive: Test that directives not recognized in vhost scope raise validation error."""
        invalid_config = "custom_unknown_directive 12345;"
        with self.assertRaises(Exception):
            self.adapter.validate(invalid_config)

    def test_ignored_rule_refuses_before_restart(self):
        """T049: Ignored OLS rule fails validation and produces refusal before activation."""
        ignored_syntax = "virtualhost other { docRoot /var/www; }"
        with self.assertRaises(Exception):
            self.adapter.validate(ignored_syntax)

    def test_zero_restart_on_ols_validation_failure(self):
        """T049: Zero restart of target service occurs when validation fails."""
        mock_gateway = unittest.mock.Mock()
        adapter = OpenLiteSpeedAdapter(gateway=mock_gateway)
        try:
            adapter.validate("invalid_directive 123;")
        except Exception:
            pass
        mock_gateway.restart_target_service.assert_not_called()



if __name__ == "__main__":
    unittest.main()
