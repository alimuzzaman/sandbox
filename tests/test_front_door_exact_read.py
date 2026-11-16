"""Route files read back for rollback arrive byte for byte (exact_read).

xspeed-dev 2026-11-16, job 8bd04673: the server-local owner channel redacts process
output, so a route read as text came back with `proxy_pass [REDACTION_FAILED]` and the
rollback's nginx -t refused it ("invalid number of arguments in proxy_pass").
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.hosting.front_door import exact_read, nginx
from sandbox.services.redaction import redact_text
from tests.subprocess_support import synthetic_environment

ROUTE = ("server {\n    listen 443 ssl;\n    location / {\n"
         "        proxy_pass http://127.0.0.1:18199;\n        proxy_http_version 1.1;\n"
         "        proxy_set_header Authorization \"Basic dXNlcjpwYXNz\";\n    }\n}\n")


def _run(path):
    return subprocess.run(["sh", "-c", exact_read.command(str(path))], capture_output=True,
                          text=True, check=False, env=synthetic_environment())


class ExactReadTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_plain_text_through_redaction_is_what_broke_the_rollback(self):
        self.assertNotIn("proxy_pass http://127.0.0.1:18199;", redact_text(ROUTE))

    def test_a_route_survives_the_redacting_owner_channel_byte_for_byte(self):
        path = self.directory / "route.conf"
        path.write_text(ROUTE)
        result = _run(path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(exact_read.decode(redact_text(result.stdout)), ROUTE)

    def test_an_empty_file_and_an_absent_file(self):
        empty = self.directory / "empty.conf"
        empty.write_text("")
        self.assertEqual(exact_read.decode(_run(empty).stdout), "")
        self.assertEqual(_run(self.directory / "missing.conf").returncode, exact_read.ABSENT_EXIT)
        self.assertEqual(_run(self.directory).returncode, exact_read.NOT_A_FILE_EXIT)

    def test_any_change_in_transit_refuses(self):
        path = self.directory / "route.conf"
        path.write_text(ROUTE)
        stdout = _run(path).stdout
        digest, body = stdout.split("\n")[:2]
        flipped = body[:-2] + ("00" if body[-2:] != "00" else "01")
        for bad in (ROUTE, digest + "\n" + flipped + "\n", digest + "\n", "", digest + "\n" + body + "\nextra\n"):
            with self.subTest(bad=bad[:20]), self.assertRaises(exact_read.ExactReadError):
                exact_read.decode(bad)

    def test_nginx_rollback_reads_return_the_exact_route(self):
        path = self.directory / "route.conf"
        path.write_text(ROUTE)
        result = _run(path)
        redacted = subprocess.CompletedProcess(result.args, 0, redact_text(result.stdout), "")
        with patch.object(nginx, "_ssh", return_value=redacted):
            self.assertEqual(nginx.read_route({}, "x"), ROUTE)
            self.assertEqual(nginx.read_route_strict({}, "x"), ROUTE)
        tampered = subprocess.CompletedProcess(result.args, 0, redact_text(ROUTE), "")
        with patch.object(nginx, "_ssh", return_value=tampered):
            with self.assertRaises(nginx.FrontDoorError):
                nginx.read_route({}, "x")

    def test_the_origin_csr_reaches_cloudflare_byte_for_byte(self):
        # Base64 PEM can match a provider-secret pattern (AKIA + 16) and be
        # rewritten by the redacting owner channel.
        csr = ("-----BEGIN CERTIFICATE REQUEST-----\nMIIBAKIAABCDEFGHIJKLMNOPQRSTUV\n"
               "-----END CERTIFICATE REQUEST-----\n")
        self.assertNotEqual(redact_text(csr), csr)
        path = self.directory / "origin.csr"
        path.write_text(csr)
        encoded = redact_text(_run(path).stdout)
        calls = []

        def ssh(entry, command, **kwargs):
            calls.append(command)
            if len(calls) == 1:
                return subprocess.CompletedProcess([], 1, "", "")  # no certificate yet
            if len(calls) == 2:
                return subprocess.CompletedProcess([], 0, encoded, "")
            return subprocess.CompletedProcess([], 0, "", "")

        class Client:
            def create_origin_certificate(self, request, hostnames):
                self.request = request
                return {"id": "c1", "certificate": "-----BEGIN CERTIFICATE-----\nx\n"}

        client = Client()
        with patch.object(nginx, "_ssh", side_effect=ssh):
            nginx.ensure_origin_certificate({}, "x", ["a.example.com"], client)
        self.assertEqual(client.request, csr)


if __name__ == "__main__":
    unittest.main()
