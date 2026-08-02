from __future__ import annotations

import os
import hashlib
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
                              env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp"),
                                   "SUDO_UID": str(os.getuid())})

    def test_only_live_proven_system_caddy_is_allowlisted(self):
        text = HELPER.read_text()
        self.assertIn('[ "$1" = system-caddy ]', text)
        for adapter in ("system-nginx", "system-apache", "traefik"):
            self.assertNotIn(f"{adapter})", text)

    def test_unknown_verbs_adapters_routes_and_digests_are_rejected(self):
        for args in (("shell",), ("validate-current", "../../nginx"),
                     ("observe", "system-nginx", "not-a-route"),
                     ("cleanup", "/tmp", "system-nginx", "a" * 64, "secret")):
            self.assertNotEqual(self.run_helper(*args).returncode, 0)

    def test_helper_source_contains_only_allowlisted_service_and_config_surfaces(self):
        text = HELPER.read_text()
        self.assertIn("caddy.service", text)
        for service in ("nginx.service", "apache2.service", "traefik.service"):
            self.assertNotIn(service, text)
        self.assertNotIn("eval ", text)
        self.assertNotIn("sh -c", text)

    def test_root_renders_only_the_authorized_scalar_plan(self):
        text = HELPER.read_text()
        self.assertIn('render_candidate "$transaction/staged"', text)
        self.assertIn('bind %s', text)
        self.assertNotIn("candidate_path", text)
        self.assertNotIn("check-candidate", text)
        self.assertNotIn('FILE ADAPTER', text)

    def test_privileged_prepare_requires_receipt_before_root_rendering(self):
        text = HELPER.read_text()
        prepare = text[text.index('    prepare)'):text.index('    activate)')]
        self.assertLess(
            prepare.index('require_plan_receipt "$@"'),
            prepare.index('render_candidate "$transaction/staged"'),
        )
        self.assertNotIn("candidate_path", prepare)
        self.assertNotIn('install -o root -g root -m 0600 "$candidate"', prepare)
        self.assertNotIn("rm -rf", prepare)

    def test_install_writes_a_scoped_sudoers_alias_without_install_privilege(self):
        text = HELPER.read_text()
        install = text[text.index('    install)'):text.index('    preflight)')]
        self.assertIn("/etc/sandbox-ingress/owners/$uid.root", install)
        self.assertIn("/etc/sandbox-ingress/authorizations", install)
        self.assertIn("/etc/sandbox-ingress/applied", install)
        self.assertIn("visudo -cf", install)
        sudoers_line = next(line for line in install.splitlines() if "Cmnd_Alias" in line)
        self.assertNotIn(" install ", sudoers_line)
        self.assertNotIn(" authorize ", sudoers_line)
        self.assertIn("authorization-status", sudoers_line)

    def test_every_mutating_nopasswd_verb_requires_a_root_receipt(self):
        text = HELPER.read_text()
        for start, end, marker in (
            ('    prepare)', '    activate)', 'require_plan_receipt'),
            ('    activate)', '    observe)', 'authorization_path'),
            ('    cleanup)', '    *) usage', 'require_applied_receipt'),
        ):
            section = text[text.index(start):text.index(end)]
            self.assertIn(marker, section)

    def test_authorization_is_bound_to_exact_systemd_socket_identity(self):
        text = HELPER.read_text()
        self.assertIn("selected listener is not owned by caddy.service", text)
        self.assertIn("/proc/{pid}/fd", text)
        self.assertIn("/proc/net/tcp6", text)
        self.assertIn("selected Caddy process was replaced", text)
        self.assertIn("Caddy executable digest changed", text)

    def test_hostname_collision_uses_full_adapted_policy(self):
        text = HELPER.read_text()
        self.assertIn("hostname_unclaimed", text)
        self.assertIn("caddy adapt --config /etc/caddy/Caddyfile", text)
        self.assertIn("hostname is already claimed by incumbent Caddy policy", text)
        authorize = text[text.index('    authorize)'):text.index('    authorization-status)')]
        prepare = text[text.index('    prepare)'):text.index('    activate)')]
        self.assertIn('hostname_unclaimed "$5" "$3"', authorize)
        self.assertIn('hostname_unclaimed "$5" "$3"', prepare)

    def test_applied_cas_receipt_is_separate_from_immutable_authorization(self):
        text = HELPER.read_text()
        self.assertIn("/etc/sandbox-ingress/authorizations", text)
        self.assertIn("/etc/sandbox-ingress/applied", text)
        activate = text[text.index('    activate)'):text.index('    observe)')]
        cleanup = text[text.index('    cleanup)'):text.index('    *) usage')]
        self.assertIn('mv -f "$temporary" "$applied"', activate)
        self.assertIn('require_applied_receipt', cleanup)
        self.assertIn('owned route drifted', cleanup)


if __name__ == "__main__": unittest.main()


class TestCaddyfileImportPolicy(unittest.TestCase):
    """The helper must accept Caddy's own packaged import line. Requiring a bare
    `conf.d/*` rejected `import /etc/caddy/conf.d/*.caddy`, so the documented
    conformance host failed its own preflight."""

    PATTERN = (r'^[[:space:]]*import[[:space:]]+(/etc/caddy/)?conf\.d/'
               r'\*(\.[A-Za-z0-9]+)?[[:space:]]*$')

    def _matches(self, line: str) -> bool:
        import subprocess

        result = subprocess.run(
            ("grep", "-Eq", self.PATTERN), input=line, text=True,
            capture_output=True, timeout=10,
        )
        return result.returncode == 0

    def test_helper_uses_the_suffix_tolerant_pattern(self):
        from pathlib import Path

        helper = (Path(__file__).resolve().parents[1] / "tools"
                  / "ingress-helper.sh").read_text()
        self.assertIn(r"conf\.d/\*(\.[A-Za-z0-9]+)?", helper)

    def test_packaged_and_bare_forms_are_accepted(self):
        for line in ("import /etc/caddy/conf.d/*.caddy",
                     "import /etc/caddy/conf.d/*",
                     "import conf.d/*.caddy",
                     "    import /etc/caddy/conf.d/*.caddy   "):
            with self.subTest(line=line):
                self.assertTrue(self._matches(line))

    def test_foreign_imports_are_still_rejected(self):
        for line in ("import /etc/caddy/other/*.caddy",
                     "import /home/user/evil.caddy",
                     "import /etc/caddy/conf.d/../evil.caddy",
                     "import /etc/caddy/conf.d/*.caddy extra"):
            with self.subTest(line=line):
                self.assertFalse(self._matches(line))


class TestListenAddressValidator(unittest.TestCase):
    """The listen endpoint is the incumbent's own socket, which may be a
    wildcard. A routable address is still refused."""

    @staticmethod
    def _validate(address: str) -> int:
        import subprocess

        script = (
            "import ipaddress, sys\n"
            "try:\n"
            "    value = ipaddress.ip_address(sys.argv[1])\n"
            "except ValueError:\n"
            "    raise SystemExit(1)\n"
            "raise SystemExit(0 if (value.is_loopback or value.is_unspecified) else 1)\n"
        )
        return subprocess.run(("python3", "-c", script, address), timeout=10).returncode

    def test_helper_uses_the_dedicated_validator(self):
        from pathlib import Path

        helper = (Path(__file__).resolve().parents[1] / "tools"
                  / "ingress-helper.sh").read_text()
        self.assertIn("valid_listen_address()", helper)
        self.assertIn('valid_listen_address "$listen"', helper)

    def test_loopback_and_wildcard_are_accepted(self):
        for address in ("127.0.0.1", "127.0.0.77", "::1", "0.0.0.0", "::"):
            with self.subTest(address=address):
                self.assertEqual(self._validate(address), 0)

    def test_routable_and_invalid_addresses_are_refused(self):
        for address in ("203.0.113.5", "10.0.0.4", "2001:db8::1", "nonsense"):
            with self.subTest(address=address):
                self.assertEqual(self._validate(address), 1)
