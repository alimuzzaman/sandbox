import unittest
from tests.server_config_fixtures import fragment, FIXED_INCARNATION, FIXED_NOW
from sandbox.server_config.models import ServerType

try:
    from sandbox.server_config.adapters.nginx import NginxAdapter
except ImportError:
    NginxAdapter = None


class TestNginxAdapter(unittest.TestCase):
    """T021: nginx subset parser, deny-by-default, renderer, and inclusion."""

    def setUp(self):
        if NginxAdapter is None:
            self.fail("sandbox.server_config.adapters.nginx not implemented yet")
        self.adapter = NginxAdapter()

    def test_subset_tokenizer_valid_fixture(self):
        """1. Subset tokenizer: Test that the nginx adapter can tokenize configuration text."""
        config_text = "set $xspeed_cache 0;\nif (-f $document_root/wp-content/cache/page) { set $xspeed_cache 1; }"
        tokens = self.adapter.tokenize(config_text)
        self.assertEqual(len(tokens), 2)
        self.assertEqual(tokens[0].directive, "set")
        self.assertEqual(tokens[1].directive, "if")

    def test_deny_by_default(self):
        """2. Deny-by-default directive/context: Test that unknown/forbidden directives are rejected."""
        forbidden_configs = [
            "listen 80;",
            "proxy_pass http://backend;",
            "server { }",
            "include /etc/nginx/conf.d/*.conf;",
            "ssl_certificate /path/cert.pem;",
            "root /var/www/html;"
        ]
        for config in forbidden_configs:
            with self.subTest(config=config):
                with self.assertRaises(Exception):
                    self.adapter.validate(config)

    def test_accepted_directives(self):
        """3. Accepted directives: Test that wordpress-cache-v1 directives are accepted."""
        accepted_configs = [
            "set $var value;",
            "if (-f wp-content/cache/page) { set $var value; }",
            "rewrite ^/path /wp-content/cache/new last;",
            "location ^~ wp-content/cache/ { add_header x-xspeed-cache HIT; access_log wp-content/uploads/log; }",
        ]
        for config in accepted_configs:
            with self.subTest(config=config):
                self.assertTrue(self.adapter.validate(config))

    def test_complete_candidate_renderer(self):
        """4. Complete-candidate renderer: Test deterministic ordering and provenance markers."""
        f1 = fragment(name="01-first", content=b"set $a 1;")
        f2 = fragment(name="02-second", content=b"set $b 2;")
        object.__setattr__(f1, "content", b"set $a 1;")
        object.__setattr__(f2, "content", b"set $b 2;")
        
        result = self.adapter.render([f2, f1])
        rendered = result.files[0].content.decode("utf-8")
        
        self.assertIn("# --- BEGIN sandbox-fragment: 01-first ---", rendered)
        self.assertIn("# --- BEGIN sandbox-fragment: 02-second ---", rendered)
        self.assertTrue(rendered.find("01-first") < rendered.find("02-second"))

    def test_protected_base_route(self):
        """5. Protected-base-route: Test that fragments cannot override protected WordPress routes."""
        protected_routes = [
            "location ^~ /wp-admin/ { }",
            "location ^~ /wp-json/ { }",
            "location ^~ /ping { }"
        ]
        for config in protected_routes:
            with self.subTest(config=config):
                with self.assertRaises(Exception):
                    self.adapter.validate(config)

    def test_duplicate_detection(self):
        """6. Duplicate detection: Test that duplicate location blocks or conflicting variable names are rejected."""
        f1 = fragment(name="01-a", content=b"location ^~ wp-content/cache/same/ { access_log wp-content/uploads/a; }")
        f2 = fragment(name="02-a", content=b"location ^~ wp-content/cache/same/ { access_log wp-content/uploads/b; }")
        object.__setattr__(f1, "content", b"location ^~ wp-content/cache/same/ { access_log wp-content/uploads/a; }")
        object.__setattr__(f2, "content", b"location ^~ wp-content/cache/same/ { access_log wp-content/uploads/b; }")
        
        with self.assertRaises(Exception):
            self.adapter.render([f1, f2])

    def test_inclusion_proof(self):
        """7. Inclusion proof: Test that each fragment marker appears exactly once in the rendered candidate."""
        f1 = fragment(name="01-a", content=b"set $a 1;")
        f2 = fragment(name="02-a", content=b"set $b 2;")
        object.__setattr__(f1, "content", b"set $a 1;")
        object.__setattr__(f2, "content", b"set $b 2;")
        
        result = self.adapter.render([f1, f2])
        rendered = result.files[0].content.decode("utf-8")
        
        self.assertEqual(rendered.count("# --- BEGIN sandbox-fragment: 01-a ---"), 1)
        self.assertEqual(rendered.count("# --- END sandbox-fragment: 01-a ---"), 1)
        self.assertEqual(rendered.count("# --- BEGIN sandbox-fragment: 02-a ---"), 1)
        self.assertEqual(rendered.count("# --- END sandbox-fragment: 02-a ---"), 1)

    def test_native_validation_failure_refuses_before_live(self):
        """T049: Native nginx syntax error fails validation and produces refusal before activation."""
        invalid_syntax = "location ^~ /wp-content/cache/ { unclosed_directive"
        with self.assertRaises(Exception):
            self.adapter.validate(invalid_syntax)

    def test_zero_reload_on_validation_failure(self):
        """T049: Zero reload is triggered when fragment validation fails."""
        mock_gateway = unittest.mock.Mock()
        adapter = NginxAdapter(gateway=mock_gateway)
        try:
            adapter.validate("invalid { directive }")
        except Exception:
            pass
        mock_gateway.reload_service.assert_not_called()


if __name__ == '__main__':
    unittest.main()
