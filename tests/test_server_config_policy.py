from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import unittest


class ServerConfigInputTests(unittest.TestCase):
    def test_regular_file_and_stdin_reads_are_exact_and_bounded(self):
        from sandbox.server_config.input import read_fragment_file, read_fragment_stdin

        payload = b"set $cache 1;\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.conf"
            path.write_bytes(payload)
            self.assertEqual(read_fragment_file(path), payload)
            path.write_bytes(b"x" * 262144)
            self.assertEqual(len(read_fragment_file(path)), 262144)
            path.write_bytes(b"x" * 262145)
            with self.assertRaisesRegex(ValueError, "fragment_source_too_large"):
                read_fragment_file(path)

        self.assertEqual(read_fragment_stdin(io.BytesIO(payload)), payload)
        with self.assertRaisesRegex(ValueError, "fragment_source_too_large"):
            read_fragment_stdin(io.BytesIO(b"x" * 262145))

    def test_file_reader_refuses_symlink_directory_and_empty_input(self):
        from sandbox.server_config.input import read_fragment_file

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.conf"
            target.write_bytes(b"safe")
            link = root / "link.conf"
            link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "fragment_source_unsafe"):
                read_fragment_file(link)
            with self.assertRaisesRegex(ValueError, "fragment_source_unsafe"):
                read_fragment_file(root)
            empty = root / "empty.conf"
            empty.write_bytes(b"")
            with self.assertRaisesRegex(ValueError, "fragment_source_empty"):
                read_fragment_file(empty)

    def test_file_reader_refuses_fifo_without_blocking(self):
        from sandbox.server_config.input import read_fragment_file

        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "fragment.pipe"
            os.mkfifo(fifo)
            with self.assertRaisesRegex(ValueError, "fragment_source_unsafe"):
                read_fragment_file(fifo)

    def test_owner_only_export_is_atomic_and_refuses_symlink(self):
        from sandbox.server_config.input import write_fragment_output

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "fragment.conf"
            receipt = write_fragment_output(destination, b"exact\n")
            self.assertEqual(destination.read_bytes(), b"exact\n")
            self.assertEqual(os.stat(destination).st_mode & 0o777, 0o600)
            self.assertEqual(receipt, {"written": True, "basename": "fragment.conf"})
            target = root / "target"
            target.write_bytes(b"unchanged")
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "content_output_unsafe"):
                write_fragment_output(link, b"replacement")
            self.assertEqual(target.read_bytes(), b"unchanged")

    def test_stdin_deadline_timeout(self):
        """T046: Stdin read enforces deadline and fails closed on stall."""
        from sandbox.server_config.input import read_fragment_stdin
        import time

        class StallingStream(io.BytesIO):
            def read(self, size=-1):
                time.sleep(0.05)
                return super().read(size)

        stream = StallingStream(b"set $x 1;\n")
        with self.assertRaisesRegex(ValueError, "stdin_deadline_exceeded"):
            read_fragment_stdin(stream, deadline=0.01)

    def test_file_reader_refuses_device_and_socket(self):
        """T046: File reader refuses character/block devices and special files."""
        from sandbox.server_config.input import read_fragment_file

        with self.assertRaisesRegex(ValueError, "fragment_source_unsafe"):
            read_fragment_file("/dev/null")
        with self.assertRaisesRegex(ValueError, "fragment_source_unsafe"):
            read_fragment_file("/dev/zero")

    def test_unstable_read_detects_mutation(self):
        """T046: Unstable read detects file mutation mid-flight and fails closed."""
        from sandbox.server_config.input import read_fragment_file
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mutating.conf"
            path.write_bytes(b"initial content\n")
            orig_fstat = os.fstat
            calls = [0]
            def side_effect(fd):
                res = orig_fstat(fd)
                calls[0] += 1
                if calls[0] > 1:
                    return os.stat_result((res.st_mode, res.st_ino, res.st_dev, res.st_nlink,
                                           res.st_uid, res.st_gid, res.st_size + 10,
                                           res.st_atime, res.st_mtime + 5, res.st_ctime))
                return res

            with patch("os.fstat", side_effect=side_effect):
                with self.assertRaisesRegex(ValueError, "fragment_source_changed"):
                    read_fragment_file(path)



class ServerConfigPolicyTests(unittest.TestCase):
    def test_name_boundary_is_exact_and_credential_like_names_fail_closed(self):
        from sandbox.server_config.policy import validate_fragment_name

        self.assertEqual(validate_fragment_name("xspeed-static-cache"), "xspeed-static-cache")
        for value in ("", "A", "-bad", "bad-", "two--hyphens", "a" * 65,
                      "../../cache", "api-token"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_fragment_name(value)

    def test_bytes_are_strict_text_and_secret_like_content_is_refused_without_echo(self):
        from sandbox.server_config.policy import validate_fragment_bytes

        self.assertEqual(validate_fragment_bytes(b"set $cache 1;\n"), "set $cache 1;\n")
        cases = (
            b"", b"\xef\xbb\xbfset $x 1;", b"set\x00$x 1;", b"set\x01$x 1;",
            b"set\x7f$x 1;", b"set\xc2\x80$x 1;", b"\xff",
            b"set $x 'unterminated;", b"set $x trailing\\",
            b"token=synthetic-value",
        )
        for payload in cases:
            with self.subTest(payload=payload[:4]):
                with self.assertRaises(ValueError) as raised:
                    validate_fragment_bytes(payload)
                self.assertNotIn("synthetic-value", str(raised.exception))

    def test_common_policy_accepts_cache_scope_and_rejects_server_authority(self):
        from sandbox.server_config.policy import validate_common_authority

        accepted = validate_common_authority(
            "set $xspeed_cache 0;\n"
            "if ($request_method = GET) { set $xspeed_cache 1; }\n"
            "location ^~ /wp-content/cache/ {\n"
            "  internal; add_header X-XSpeed-Cache HIT always;\n"
            "  access_log wp-content/uploads/xspeed-cache.log;\n"
            "}\n",
            server_type="nginx",
        )
        self.assertEqual(accepted["authority"], "wordpress-cache-v1")
        self.assertTrue(accepted["checks_digest"].startswith("sha256:"))
        for text in (
            "listen 443 ssl;", "include /etc/passwd;", "proxy_pass http://other;",
            "location /wp-admin { return 200; }", "ssl_certificate /tmp/cert;",
            "access_log /var/log/private.log;", "exec /bin/sh;",
            "root /tmp/other;", "rewrite ^ /wp-login.php last;",
            "add_header X-Caller-Controlled yes;", "totally_unknown on;",
            "if ($request_method = GET) { proxy_pass http://other; }",
            "location /cache { include extra.conf; }",
            "location /cache { root /tmp/other; }",
            "set $x 1; add_header X-Caller-Controlled yes;",
            "access_log /tmp/wp-content/uploads/hit.log;",
            "rewrite ^ /tmp/wp-content/cache/page last;",
            "rewrite ^ /tmp/other last;", "rewrite ^ /index.php last;",
            "try_files /tmp/other =404;", "access_log /wp-content/uploads/hit.log;",
            "access_log wp-content/uploads/$host/hit.log;",
            "access_log wp-content/uploads/hit.log $cookie_session;",
            "rewrite ^ wp-content/cache/$uri last;",
            "add_header X-XSpeed-Cache $cookie_session;",
            "header set X-XSpeed-Cache $cookie_session;",
            "add_header X-XSpeed-Cache " + "x" * 5000 + ";",
            "if (-f /home/other/site/private.txt) { set $xspeed_cache 1; }",
            "location /checkout { internal; }",
            "context /checkout { cache enable; }",
            "} } internal;",
            "location ^~ /wp-content/cache/ { set $xspeed_cache 1; }",
            "if ($request_method = GET) { internal; }",
            "rewrite ^ /wp-content/cache/%2e%2e/wp-config.php last;",
            "location ^~ /wp-content/cache/%2e%2e/private/ { internal; }",
        ):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    validate_common_authority(text, server_type="nginx")

        ols = validate_common_authority(
            "rewrite {\n enable 1\n RewriteCond %{REQUEST_METHOD} ^GET$\n"
            " RewriteRule ^ wp-content/cache/page.html [L]\n}\n",
            server_type="litespeed",
        )
        self.assertEqual(ols["authority"], "wordpress-cache-v1")
        for target in (
            "checkout", "index.php", "wp-content/uploads/private.txt",
            "wp-content/cache/%2e%2e/wp-config.php",
        ):
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, "authority_path_forbidden"):
                    validate_common_authority(
                        "rewrite {\n RewriteRule ^ " + target + " [L]\n}\n",
                        server_type="litespeed",
                    )

    def test_complete_set_conflicts_require_names_and_are_order_canonical(self):
        from sandbox.server_config.policy import validate_set_conflicts

        alpha = {"name": "alpha-cache", "variable": ("xspeed_alpha",)}
        beta = {"name": "beta-cache", "variable": ("xspeed_beta",)}
        self.assertEqual(
            validate_set_conflicts((alpha, beta))["checks_digest"],
            validate_set_conflicts((beta, alpha))["checks_digest"],
        )
        with self.assertRaisesRegex(ValueError, "fragment_set_conflict"):
            validate_set_conflicts(({"variable": ("xspeed",)},))
        with self.assertRaisesRegex(ValueError, "fragment_set_conflict"):
            validate_set_conflicts((alpha, {"name": "beta-cache", "variable": ("xspeed_alpha",)}))

    def test_adversarial_forbidden_directives_matrix(self):
        """T047: Deny-by-default rejects upstream, resolver, tls, caddy, autologin, health, login, outside-docroot for both adapters."""
        from sandbox.server_config.policy import validate_common_authority

        forbidden_nginx = [
            "upstream backend { server 127.0.0.1:8080; }",
            "resolver 1.1.1.1;",
            "ssl_certificate /etc/ssl/cert.pem;",
            "ssl_certificate_key /etc/ssl/key.pem;",
            "caddy_directive on;",
            "location /autologin { return 200; }",
            "location /health { return 200; }",
            "location /wp-login.php { return 200; }",
            "location ^~ /outside/ { root /var/www; }",
            "add_header Set-Cookie 'session=1';",
            "access_log /var/log/nginx/access.log;",
            "auth_basic 'Restricted';",
        ]
        for config in forbidden_nginx:
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    validate_common_authority(config, server_type="nginx")

        forbidden_ols = [
            "listener HTTP { address *:80 }",
            "admin { allow 127.0.0.1 }",
            "extprocessor lsphp { type lsapi }",
            "virtualhost other { docRoot /var/www/ }",
            "vhTemplate docker { templateFile conf/docker.conf }",
            "accessControl { allow * }",
            "module cache { ls_enabled 1 }",
            "context /autologin/ { allowBrowse 1 }",
            "context /health/ { allowBrowse 1 }",
            "context /wp-login.php/ { allowBrowse 1 }",
        ]
        for config in forbidden_ols:
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    validate_common_authority(config, server_type="litespeed")

    def test_high_confidence_secret_like_content_and_clean_near_match(self):
        """T046: High-confidence secrets fail closed while clean near-matches pass."""
        from sandbox.server_config.policy import validate_fragment_bytes

        # Secrets that must fail closed
        secret_payloads = [
            b"AKIAIOSFODNN7EXAMPLE",
            b"-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...",
            b"Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0...",
            b"db_password=mysecretpassword123",
        ]
        for payload in secret_payloads:
            with self.subTest(payload=payload[:10]):
                with self.assertRaisesRegex(ValueError, "fragment_secret_like_input"):
                    validate_fragment_bytes(payload)

        # Clean near-match that must pass
        clean_config = b"set $xspeed_cache_hit 1;\n"
        self.assertEqual(validate_fragment_bytes(clean_config), "set $xspeed_cache_hit 1;\n")

    def test_content_free_classification_errors(self):
        """T046: Exceptions never echo raw fragment bytes, caller paths, or secrets."""
        from sandbox.server_config.policy import validate_fragment_bytes, validate_fragment_name

        secret_str = "super_secret_token_value_xyz"
        with self.assertRaises(ValueError) as ctx:
            validate_fragment_bytes(f"token={secret_str}\n".encode("utf-8"))
        self.assertNotIn(secret_str, str(ctx.exception))

        input_name = "custom-auth-token-secret"
        with self.assertRaises(ValueError) as ctx:
            validate_fragment_name(input_name)
        self.assertNotIn(input_name, str(ctx.exception))
        self.assertEqual(str(ctx.exception), "fragment_secret_like_input")



if __name__ == "__main__":
    unittest.main()
