from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parent.parent
HELPER = ROOT / "tools" / "ingress-helper.sh"


class TestIngressHelper(unittest.TestCase):
    def run_helper(self, *args):
        return subprocess.run([str(HELPER), *map(str, args)], capture_output=True,
                              text=True, timeout=5,
                              env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")})

    def test_fixed_candidate_path_owner_mode_header_and_adapter(self):
        route = "a" * 64
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); candidate = root / "ingress/candidates/system-nginx" / f"{route}.conf"
            candidate.parent.mkdir(parents=True)
            candidate.write_text(
                f"# sandbox-ingress v1 route={route}\nserver {{\n"
                "    listen 127.0.0.1:80;\n    server_name demo.test;\n"
                "    location / {\n        proxy_pass http://127.0.0.1:8123;\n"
                "        proxy_set_header Host $host;\n"
                "        proxy_set_header X-Forwarded-Proto $scheme;\n    }\n}\n"
            )
            candidate.chmod(0o600)
            accepted = self.run_helper("check-candidate", root, candidate, "system-nginx", route)
            outside = root / "outside.conf"; outside.write_text(candidate.read_text()); outside.chmod(0o600)
            rejected = self.run_helper("check-candidate", root, outside, "system-nginx", route)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertNotEqual(rejected.returncode, 0)

    def test_unknown_verbs_adapters_routes_and_digests_are_rejected(self):
        for args in (("shell",), ("validate-current", "../../nginx"),
                     ("observe", "system-nginx", "not-a-route"),
                     ("cleanup", "/tmp", "system-nginx", "a" * 64, "secret")):
            self.assertNotEqual(self.run_helper(*args).returncode, 0)

    def test_symlink_and_writable_candidate_are_rejected(self):
        route = "b" * 64
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); directory = root / "ingress/candidates/system-nginx"; directory.mkdir(parents=True)
            actual = directory / "actual"; actual.write_text(f"# sandbox-ingress v1 route={route}\n"); actual.chmod(0o666)
            candidate = directory / f"{route}.conf"; candidate.symlink_to(actual)
            result = self.run_helper("check-candidate", root, candidate, "system-nginx", route)
        self.assertNotEqual(result.returncode, 0)

    def test_helper_source_contains_only_allowlisted_service_and_config_surfaces(self):
        text = HELPER.read_text()
        for service in ("nginx.service", "apache2.service", "caddy.service"):
            self.assertIn(service, text)
        self.assertNotIn("eval ", text)
        self.assertNotIn("sh -c", text)

    def test_candidate_with_extra_privileged_directive_is_rejected(self):
        route = "c" * 64
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); candidate = root / "ingress/candidates/system-nginx" / f"{route}.conf"
            candidate.parent.mkdir(parents=True)
            candidate.write_text(
                f"# sandbox-ingress v1 route={route}\nserver {{\n"
                "    listen 127.0.0.1:80;\n    server_name demo.test;\n"
                "    location / {\n        proxy_pass http://127.0.0.1:8123;\n"
                "        proxy_set_header Host $host;\n"
                "        proxy_set_header X-Forwarded-Proto $scheme;\n"
                "        include /etc/shadow;\n    }\n}\n"
            )
            candidate.chmod(0o600)
            result = self.run_helper("check-candidate", root, candidate, "system-nginx", route)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__": unittest.main()
