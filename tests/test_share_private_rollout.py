"""Spec 081 rollout fixes: delivery verification follows `share.private`, and a failed
apply of a private manifest never leaves the public name serving the application.

The 2026-11-11 mockups rollout failed both ways: verification expected the private
primary to answer like a public site, and the rollback put the previous public route in
front of the newly built, unlocked runtime.
"""
import datetime as dt
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox.commands.hosting as hosting_cmd  # noqa: E402
import sandbox.core._hosting as hosting  # noqa: E402
from sandbox.hosting import target_lease  # noqa: E402
from sandbox.hosting.front_door import nginx  # noqa: E402
from tests.test_hosting import _HostingOwnerFixture, _manifest, _public_acme_manifest  # noqa: E402

PRIMARY = "site.example.test"
ALIAS = "www.example.test"
GATE = "gate.example.test"
APPLY_LOG = "/srv/sandbox/runtime/hosts/example-site/production/apply.log"
ROUTE = "sandbox-host-example-site-production"


TAG = "0123456789abcdef" * 2


def _private_manifest(private=True, *, proxied=False):
    enabled_until = (dt.date.today() + dt.timedelta(days=10)).isoformat()
    manifest = (_manifest if proxied else _public_acme_manifest)(
        f"        - hostname: {ALIAS}\n          mode: serve\n"
        f"        - hostname: {GATE}\n          mode: serve")
    manifest = manifest.replace("primary: আমারসোনার.বাংলা", f"primary: {PRIMARY}")
    return manifest + (
        "    share:\n"
        f"      hostname: {GATE}\n"
        f"      private: {'true' if private else 'false'}\n"
        "      agent_mint: true\n"
        "      image: python:3.13-alpine@sha256:" + "a" * 64 + "\n"
        f"      enabled_until: {enabled_until}\n"
        "      allowed_host_suffixes: [example.test]\n")


def _serves_app(text, port):
    return text is not None and re.search(rf"127\.0\.0\.1:{port}(?![0-9])", text) is not None


class _Response:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _http_error(code, gate=False, tag=None):
    headers = Message()
    if gate:
        headers["X-Sandbox-Gate"] = "1"
    if tag:
        headers["X-Sandbox-Route"] = tag
    return urllib.error.HTTPError("https://x/", code, "status", headers, io.BytesIO())


def _target(url):
    """A probe URL without its random cache-busting query (checked to be there)."""
    base, _, query = url.partition("?")
    assert re.fullmatch(r"sandbox-verify=[0-9a-f]{16}", query), url
    return base


@patch("sandbox.commands.hosting.time.sleep")
@patch("sandbox.commands.hosting.urllib.request.build_opener")
class PrivateVerificationTests(unittest.TestCase):
    def verify(self, build_opener, answers, route, **kwargs):
        build_opener.return_value.open.side_effect = answers
        hosting_cmd._verify_edge([route], **kwargs)
        return [_target(call.args[0].full_url)
                for call in build_opener.return_value.open.call_args_list]

    def test_a_private_primary_passes_on_404(self, build_opener, _sleep):
        urls = self.verify(build_opener, [_http_error(404)], {"hostname": PRIMARY, "mode": "serve"},
                           private_hostnames=frozenset({PRIMARY}))
        self.assertEqual(urls, [f"https://{PRIMARY}/"])

    def test_a_private_primary_that_serves_the_app_fails(self, build_opener, _sleep):
        build_opener.return_value.open.side_effect = lambda *_a, **_k: _Response(200)
        with self.assertRaisesRegex(RuntimeError, f"edge verification failed for {re.escape(PRIMARY)}: "
                                                  "HTTP 200 without a session; a private route must answer 404"):
            hosting_cmd._verify_edge([{"hostname": PRIMARY, "mode": "serve"}],
                                     private_hostnames=frozenset({PRIMARY}))

    def test_a_private_redirect_alias_that_redirects_fails(self, build_opener, _sleep):
        build_opener.return_value.open.side_effect = lambda *_a, **_k: (_ for _ in ()).throw(_http_error(308))
        with self.assertRaisesRegex(RuntimeError, "HTTP 308 redirect; a private route must answer 404"):
            hosting_cmd._verify_edge([{"hostname": "old.example.test", "mode": "redirect"}],
                                     private_hostnames=frozenset({"old.example.test"}))

    def test_the_gate_must_answer_its_own_404(self, build_opener, _sleep):
        urls = self.verify(build_opener, [_http_error(404, gate=True)], {"hostname": GATE, "mode": "serve"},
                           gate_hostnames=frozenset({GATE}))
        self.assertEqual(urls, [f"https://{GATE}/__sandbox/gate-check"])
        build_opener.return_value.open.reset_mock()
        build_opener.return_value.open.reset_mock()
        build_opener.return_value.open.side_effect = lambda *_a, **_k: (_ for _ in ()).throw(_http_error(404))
        with self.assertRaisesRegex(RuntimeError, "HTTP 404, but not from the gate"):
            hosting_cmd._verify_edge([{"hostname": GATE, "mode": "serve"}], gate_hostnames=frozenset({GATE}))
        build_opener.return_value.open.side_effect = lambda *_a, **_k: _Response(200)
        with self.assertRaisesRegex(RuntimeError, "HTTP 200 without a session"):
            hosting_cmd._verify_edge([{"hostname": GATE, "mode": "serve"}], gate_hostnames=frozenset({GATE}))

    def test_a_404_without_this_applys_route_tag_fails(self, build_opener, _sleep):
        """A stale route, or a cache, answers 404 too, but without the new tag."""
        for answer, tag in ((_http_error(404), None), (_http_error(404, tag="f" * 32), "stale")):
            with self.subTest(tag=tag):
                build_opener.return_value.open.side_effect = lambda *_a, _e=answer, **_k: (_ for _ in ()).throw(_e)
                with self.assertRaisesRegex(RuntimeError, "without this apply's route tag"):
                    hosting_cmd._verify_edge([{"hostname": PRIMARY, "mode": "serve"}],
                                             private_hostnames=frozenset({PRIMARY}), route_tag=TAG)
        build_opener.return_value.open.side_effect = [_http_error(404, tag=TAG)]
        hosting_cmd._verify_edge([{"hostname": PRIMARY, "mode": "serve"}],
                                 private_hostnames=frozenset({PRIMARY}), route_tag=TAG)
        build_opener.return_value.open.side_effect = [_http_error(404, gate=True, tag=TAG)]
        hosting_cmd._verify_edge([{"hostname": GATE, "mode": "serve"}],
                                 gate_hostnames=frozenset({GATE}), route_tag=TAG)

    def test_every_try_asks_a_new_url(self, build_opener, _sleep):
        build_opener.return_value.open.side_effect = [_http_error(404), _http_error(404, tag=TAG)]
        hosting_cmd._verify_edge([{"hostname": PRIMARY, "mode": "serve"}],
                                 private_hostnames=frozenset({PRIMARY}), route_tag=TAG)
        urls = [call.args[0].full_url for call in build_opener.return_value.open.call_args_list]
        self.assertEqual(len(set(urls)), 2)
        self.assertEqual({_target(url) for url in urls}, {f"https://{PRIMARY}/"})
        self.assertEqual(build_opener.return_value.open.call_args.args[0].get_header("Cache-control"),
                         "no-cache")

    def test_a_gate_behind_basic_auth(self, build_opener, _sleep):
        """Only a public target can have Basic Auth (share.private refuses it)."""
        build_opener.return_value.open.side_effect = [_http_error(401, tag=TAG)]
        hosting_cmd._verify_edge([{"hostname": GATE, "mode": "serve"}], gate_hostnames=frozenset({GATE}),
                                 route_tag=TAG, basic_auth_enabled=True)
        build_opener.return_value.open.reset_mock()
        build_opener.return_value.open.side_effect = [_http_error(404, gate=True, tag=TAG)]
        hosting_cmd._verify_edge([{"hostname": GATE, "mode": "serve"}], gate_hostnames=frozenset({GATE}),
                                 route_tag=TAG, basic_auth_enabled=True,
                                 basic_auth_credentials=("owner", "pw"))
        sent = build_opener.return_value.open.call_args.args[0].get_header("Authorization")
        self.assertEqual(sent, "Basic b3duZXI6cHc=")

    def test_a_gate_on_a_public_target_is_checked_and_the_primary_stays_public(self, build_opener, _sleep):
        answers = {f"https://{GATE}/__sandbox/gate-check": _http_error(404, gate=True)}

        def answer(request, timeout=None):
            found = answers.get(request.full_url.partition("?")[0])
            if found is not None:
                raise found
            return _Response(200)
        build_opener.return_value.open.side_effect = answer
        hosting_cmd._verify_edge([{"hostname": PRIMARY, "mode": "serve"}, {"hostname": GATE, "mode": "serve"}],
                                 gate_hostnames=frozenset({GATE}))


class PrivacyArgumentsTests(unittest.TestCase):
    def test_private_and_public_manifests(self):
        private = _HostingOwnerFixture(self, _private_manifest()).validated
        self.assertEqual(hosting_cmd._privacy_verify_kwargs(private),
                         {"gate_hostnames": frozenset({GATE}),
                          "private_hostnames": frozenset({PRIMARY, ALIAS})})
        public = _HostingOwnerFixture(self, _private_manifest(private=False)).validated
        self.assertEqual(hosting_cmd._privacy_verify_kwargs(public), {"gate_hostnames": frozenset({GATE})})
        plain = _HostingOwnerFixture(self).validated
        self.assertEqual(hosting_cmd._privacy_verify_kwargs(plain), {})


class RenderedPrivateRouteTests(unittest.TestCase):
    def setUp(self):
        self.validated = _HostingOwnerFixture(self, _private_manifest()).validated
        self.port = 18123

    def renders(self, validated):
        return {"caddy": hosting.caddyfile(validated, self.port, "/c", "/k"),
                "nginx": nginx.render_host_conf(validated, self.port, name="host-example-site-production",
                                                cert_path="/c", key_path="/k")}

    def test_a_private_route_is_marked_and_tagged_on_both_front_doors(self):
        for door, text in self.renders({**self.validated, "route_tag": TAG}).items():
            with self.subTest(door=door):
                self.assertTrue(hosting.is_marked_private_route(text))
                self.assertIn(f'X-Sandbox-Route "{TAG}"', text)
                self.assertFalse(_serves_app(text, self.port))

    def test_nginx_tags_every_level_that_carries_the_robots_header(self):
        text = self.renders({**self.validated, "route_tag": TAG})["nginx"]
        self.assertEqual(text.count("add_header X-Robots-Tag"), text.count("add_header X-Sandbox-Route"))

    def test_a_changed_or_public_route_is_not_marked(self):
        for door, text in self.renders(self.validated).items():
            with self.subTest(door=door):
                self.assertTrue(hosting.is_marked_private_route(text))
                tampered = text.replace("404", "200", 1)
                self.assertFalse(hosting.is_marked_private_route(tampered))
        public = _HostingOwnerFixture(self, _private_manifest(private=False)).validated
        for door, text in self.renders(public).items():
            with self.subTest(door=door, public=True):
                self.assertFalse(hosting.is_marked_private_route(text))
        for value in (None, "", "# sandbox-private-route v1 " + "0" * 64 + "\n"):
            self.assertFalse(hosting.is_marked_private_route(value))

    def test_a_route_tag_must_be_32_hex(self):
        for bad in ("abc", "G" * 32, TAG + "\n", 7):
            with self.subTest(tag=bad), self.assertRaises(hosting.HostingError):
                hosting.caddyfile({**self.validated, "route_tag": bad}, self.port, "/c", "/k")

    def test_share_private_refuses_cloudflare_proxying(self):
        with self.assertRaisesRegex(hosting.HostingError, "share.private needs cloudflare.proxied: false"):
            _HostingOwnerFixture(self, _private_manifest(proxied=True))


class FrontDoorClosureCommandTests(unittest.TestCase):
    """The closure step's remote commands: a strict read and a forced reload."""

    # sha256 of the commands at c6c28ed, before this fix: unforced callers keep them.
    BASELINE = {
        "caddy": "3ac2a2c5d3acc6615a097610758869be375aa8feabdc1fb05b51fe305c95bb89",
        "nginx_apply": "90450dba78f249d77ecfdb02e9a7a35333e1b1c7ba6d111ba3922ef2d6e413ac",
        "nginx_remove": "0c64c77ace485c92a5ba745073ff8a0704f4cc6c0f9de64ead713e25f9dc48b1",
    }

    @staticmethod
    def digest(command):
        return hashlib.sha256(command.encode()).hexdigest()

    def test_unforced_commands_are_byte_identical_to_before(self):
        self.assertEqual(self.digest(hosting_cmd._caddy_transaction_command(
            "/etc/caddy/conf.d/x.caddy", "/tmp/t", "d" * 64, operation="apply", remove=False)),
            self.BASELINE["caddy"])
        self.assertEqual(self.digest(nginx.transaction_command(
            "/etc/nginx/conf.d/sandbox-x.conf", "/s", "d" * 64, operation="apply", remove=False,
            probe_host="a.com", marker="m")), self.BASELINE["nginx_apply"])
        self.assertEqual(self.digest(nginx.transaction_command(
            "/etc/nginx/conf.d/sandbox-x.conf", None, "absent", operation="rollback", remove=True)),
            self.BASELINE["nginx_remove"])

    def test_forced_commands_skip_the_noop_and_are_valid_shell(self):
        caddy = hosting_cmd._caddy_transaction_command(
            "/etc/caddy/conf.d/x.caddy", "/tmp/t", "d" * 64, operation="apply", remove=False, force=True)
        self.assertIn('[ "$force_reload" != 1 ]', caddy)
        self.assertTrue(caddy.endswith(" 1"))
        forced = nginx.transaction_command("/etc/nginx/conf.d/sandbox-x.conf", "/s", "d" * 64,
                                           operation="apply", remove=False, probe_host="a.com",
                                           marker="m", force=True)
        self.assertIn('[ "$force" != 1 ]', nginx.transaction_script(force=True))
        self.assertNotIn("$force", nginx.transaction_script())
        self.assertTrue(forced.endswith(" 1"))
        for command in (caddy, forced):
            result = subprocess.run(["sh", "-n", "-c", command], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_strict_reads_tell_absence_from_failure(self):
        cases = ((0, "route\n", "route\n"), (43, "", None), (255, "", "raise"), (44, "", "raise"), (1, "", "raise"))
        for code, stdout, expected in cases:
            result = subprocess.CompletedProcess([], code, stdout, "")
            with self.subTest(door="caddy", code=code), patch.object(hosting_cmd.remote, "ssh_run", return_value=result):
                if expected == "raise":
                    with self.assertRaisesRegex(hosting.HostingError, "route state unknown"):
                        hosting_cmd._read_remote_strict({}, "/etc/caddy/conf.d/x.caddy")
                else:
                    self.assertEqual(hosting_cmd._read_remote_strict({}, "/etc/caddy/conf.d/x.caddy"), expected)
            with self.subTest(door="nginx", code=code), patch.object(nginx, "_ssh", return_value=result):
                if expected == "raise":
                    with self.assertRaises(nginx.FrontDoorError):
                        nginx.read_route_strict({}, "x")
                else:
                    self.assertEqual(nginx.read_route_strict({}, "x"), expected)

    def test_nginx_reconcile_proves_the_route_by_its_marker(self):
        validated = _HostingOwnerFixture(self, _private_manifest()).validated
        deny = nginx.render_host_conf({**validated, "route_deny": True}, 18123, name="x",
                                      cert_path="/c", key_path="/k")
        commands = []

        def fake(_entry, command, timeout=60, input_data=None):
            commands.append(command)
            return subprocess.CompletedProcess([], 0, "", "")
        with patch.object(nginx, "_ssh", side_effect=fake):
            nginx.reconcile_route({}, "x", deny)
        self.assertTrue(commands[-1].endswith(" " + nginx.route_marker(deny) + " 1"))
        with patch.object(nginx, "_ssh", side_effect=fake), self.assertRaises(nginx.FrontDoorError):
            nginx.reconcile_route({}, "x", "server { listen 80; }\n")


class HostApplyLeaseTests(unittest.TestCase):
    """One remote lease per target across a whole operation, so controllers cannot interleave."""

    def run_script(self, path, token, owner, action):
        command = target_lease.command(path, token, owner, action)
        return subprocess.run(["sh", "-c", command], capture_output=True, text=True, check=False).returncode

    @unittest.skipUnless(shutil.which("flock"), "flock(1) is Linux util-linux; remotes have it")
    def test_the_lease_script(self):
        with tempfile.TemporaryDirectory() as home:
            path = target_lease.lease_path(home, "example-site", "production")
            mine, other = "1" * 32, "2" * 32
            self.assertEqual(self.run_script(path, "a" * 32, mine, "acquire"), 0)
            self.assertEqual(self.run_script(path, "b" * 32, other, "acquire"), 45)
            # This controller's own lease (a crashed apply) is taken over.
            self.assertEqual(self.run_script(path, "c" * 32, mine, "acquire"), 0)
            # A release with a stale token leaves the current lease in place.
            self.assertEqual(self.run_script(path, "a" * 32, mine, "release"), 0)
            self.assertEqual(self.run_script(path, "b" * 32, other, "acquire"), 45)
            self.assertEqual(self.run_script(path, "c" * 32, mine, "release"), 0)
            self.assertEqual(self.run_script(path, "b" * 32, other, "acquire"), 0)
            with open(path) as handle:
                token, owner, _since = handle.read().split()
            self.assertEqual((token, owner), ("b" * 32, other))
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
            # It never expires: an old lease of another controller still refuses.
            with open(path, "w") as handle:
                handle.write(f"{'b' * 32} {other} 1\n")
            self.assertEqual(self.run_script(path, "d" * 32, mine, "acquire"), 45)

    def test_every_apply_and_edge_continuation_holds_the_lease(self):
        entry = {"ssh": "fixture@example.test"}
        validated = {"project": "example-site", "environment": "production"}
        for outer, inner, args in (
                ("_apply_host", "_apply_host_unleased", (validated, entry, "myvps", {})),
                ("_continue_host_edge_only", "_continue_host_edge_only_unleased",
                 (validated, entry, "myvps", None, {}))):
            for fails in (False, True):
                with self.subTest(outer=outer, fails=fails), \
                        patch.object(hosting_cmd, "_acquire_host_apply_lease", return_value={"t": 1}) as take, \
                        patch.object(hosting_cmd, "_release_host_apply_lease") as release, \
                        patch.object(hosting_cmd, inner,
                                     side_effect=RuntimeError("x") if fails else None, return_value={}) as run:
                    if fails:
                        with self.assertRaises(RuntimeError):
                            getattr(hosting_cmd, outer)(*args)
                    else:
                        getattr(hosting_cmd, outer)(*args)
                    take.assert_called_once_with(entry, validated)
                    run.assert_called_once()
                    release.assert_called_once_with(entry, {"t": 1})

    def test_a_held_lease_refuses_before_anything_runs(self):
        result = subprocess.CompletedProcess([], 45, "1900000000\n", "")
        with tempfile.TemporaryDirectory() as runtime, \
                patch.object(hosting_cmd, "RUNTIME_DIR", Path(runtime)), \
                patch.object(hosting_cmd.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
                patch.object(hosting_cmd.remote, "ssh_run", return_value=result) as ssh, \
                patch.object(hosting_cmd, "_apply_host_unleased") as run:
            with self.assertRaisesRegex(hosting.HostingError, "host_apply_lease_held"):
                hosting_cmd._apply_host({"project": "example-site", "environment": "production"},
                                        {"ssh": "fixture@example.test"}, "myvps", {})
            run.assert_not_called()
            command = ssh.call_args.args[1]
            self.assertIn("/srv/sandbox/.sandbox/host-apply-example-site-production.lease", command)
            owner = (Path(runtime) / "hosting" / "controller-id").read_text().strip()
            self.assertIn(owner, command)
            self.assertEqual(hosting_cmd._controller_lease_owner(), owner)

    def test_edge_recovery_takes_the_lease_inside_its_authority_guard(self):
        """Inside recovery's target serialization and before any effect is recorded."""
        from contextlib import nullcontext
        entry = {"provisioned": True, "ssh": "fixture@example.test"}
        operation = {"evidence": {"host_identity": "H", "machine_identity": "M",
                                  "edge_intent": {"i": 1}, "edge_intent_digest": "D"}}
        validated = {"project": "example-site", "environment": "production"}
        with patch.object(hosting_cmd.remote, "registered_remote_lock", return_value=nullcontext()), \
                patch.object(hosting_cmd.remote, "registration_lock_name", return_value="x"), \
                patch.object(hosting_cmd.remote, "get_remote", return_value=entry), \
                patch.object(hosting_cmd.remote, "resolve_sandbox_home", return_value="/srv"), \
                patch.object(hosting_cmd, "_registered_host_identity", return_value="H"), \
                patch.object(hosting_cmd, "_authenticated_machine_identity", return_value="M"), \
                patch.object(hosting_cmd, "validate_edge_intent", return_value={"i": 1}), \
                patch.object(hosting_cmd, "_desired_edge_intent", return_value={"i": 1}), \
                patch.object(hosting_cmd, "canonical_digest", return_value="D"):
            with patch.object(hosting_cmd, "_acquire_host_apply_lease",
                              side_effect=hosting.HostingError("host_apply_lease_held: x")), \
                    patch.object(hosting_cmd, "_release_host_apply_lease") as release:
                with self.assertRaises(hosting_cmd.RecoveryAuthorityError):
                    with hosting_cmd._registered_recovery_authority(validated, "myvps", operation, {}, lease=True):
                        self.fail("no edge effect under another controller's lease")
                release.assert_not_called()
            with patch.object(hosting_cmd, "_acquire_host_apply_lease", return_value={"t": 1}) as take, \
                    patch.object(hosting_cmd, "_release_host_apply_lease") as release:
                authority = {}
                with hosting_cmd._registered_recovery_authority(validated, "myvps", operation, authority, lease=True):
                    take.assert_called_once_with(entry, validated)
                    release.assert_not_called()
                    self.assertIs(authority["entry"], entry)
                release.assert_called_once_with(entry, {"t": 1})
            with patch.object(hosting_cmd, "_acquire_host_apply_lease") as take:
                with hosting_cmd._registered_recovery_authority(validated, "myvps", operation, {}):
                    pass
                take.assert_not_called()

    def test_destroy_holds_the_same_lease(self):
        from sandbox.hosting import destroy
        held = subprocess.CompletedProcess([], 45, "", "")
        with tempfile.TemporaryDirectory() as runtime, \
                patch("sandbox.core._paths.RUNTIME_DIR", Path(runtime)), \
                patch.object(destroy.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
                patch.object(destroy.remote, "ssh_run", return_value=held) as ssh:
            with self.assertRaises(destroy.DestroyError) as caught:
                with destroy._target_lease({"ssh": "fixture@example.test"}, "example-site", "production"):
                    self.fail("the body must not run under another controller's lease")
            self.assertEqual(caught.exception.code, "host_apply_lease_held")
            self.assertIn("/srv/sandbox/.sandbox/host-apply-example-site-production.lease",
                          ssh.call_args.args[1])
        entered = []

        @__import__("contextlib").contextmanager
        def lease(*_a):
            entered.append("lease")
            raise destroy.DestroyError("held", "host_apply_lease_held")
            yield  # pragma: no cover
        with tempfile.TemporaryDirectory() as runtime, \
                patch("sandbox.hosting.recovery.repository.RUNTIME_DIR", Path(runtime)), \
                patch.object(destroy, "_target_lease", lease), \
                patch.object(destroy.remote, "ssh_run", side_effect=AssertionError("no SSH before the lease")):
            with self.assertRaises(destroy.DestroyError):
                destroy.execute({"ssh": "fixture@example.test"}, "myvps", "example-site", "production")
        self.assertEqual(entered, ["lease"])


class DenyRouteTests(unittest.TestCase):
    def test_a_deny_route_sends_nothing_anywhere(self):
        validated = {**_HostingOwnerFixture(self, _private_manifest()).validated, "route_deny": True}
        for door, text in (("caddy", hosting.caddyfile(validated, 18123)),
                           ("nginx", nginx.render_host_conf(validated, 18123, name="x",
                                                            cert_path="/c", key_path="/k"))):
            with self.subTest(door=door):
                self.assertNotIn("reverse_proxy", text)
                self.assertNotIn("proxy_pass", text)
                self.assertTrue(hosting.is_marked_private_route(text))
                for host in (PRIMARY, ALIAS, GATE):
                    self.assertIn(host, text)

    def test_the_nginx_deny_route_keeps_https_with_any_existing_certificate(self):
        validated = _HostingOwnerFixture(self, _private_manifest()).validated
        edge = hosting_cmd._NginxHostEdge({"ssh": "fixture@example.test"})
        edge.features = {}
        runtime = {"loopback_port": 18123, "certificate_hostnames": [PRIMARY, ALIAS, GATE]}
        current = "server {\n    listen 443 ssl;\n    server_name site.example.test;\n}\n"
        public = ("/etc/letsencrypt/live/sandbox-x/fullchain.pem", "/etc/letsencrypt/live/sandbox-x/privkey.pem")
        with patch.object(nginx, "existing_certificate", return_value=public):
            text = edge.deny_route(validated, runtime, ROUTE, current)
        self.assertIn(f"ssl_certificate {public[0]};", text)
        self.assertNotIn("proxy_pass", text)
        with patch.object(nginx, "existing_certificate", return_value=None):
            for https in (current, "server {\n    listen 0.0.0.0:443 ssl;\n}\n",
                          "server {\n    listen 192.0.2.10:443 ssl;\n}\n",
                          "server {\n    listen 8443 ssl;\n    ssl_certificate /opt/c.pem;\n}\n",
                          "server {\n    listen [::]:443 quic;\n}\n"):
                with self.subTest(https=https), \
                        self.assertRaisesRegex(hosting.HostingError, "private_closure_https_unavailable"):
                    edge.deny_route(validated, runtime, ROUTE, https)
            # No route yet (first apply) or a port-80-only route: nothing on HTTPS to keep.
            for previous in (None, "server {\n    listen 80;\n}\n"):
                self.assertNotIn("listen 443", edge.deny_route(validated, runtime, ROUTE, previous))

    def test_a_private_route_keeps_https_while_its_certificate_grows(self):
        """A new gate name the certificate lacks never turns the route HTTP only."""
        validated = _HostingOwnerFixture(self, _private_manifest()).validated
        edge = hosting_cmd._NginxHostEdge({"ssh": "fixture@example.test"})
        edge.features = {}
        runtime = {"loopback_port": 18123, "certificate_hostnames": [PRIMARY, ALIAS, GATE]}
        old = nginx.public_paths("host-example-site-production")
        with patch.object(nginx, "public_certificate_present", return_value=False), \
                patch.object(nginx, "existing_certificate", return_value=old):
            cert_path, key_path, certificate = edge.certificate(False, validated, runtime, {}, None, "/srv", ROUTE)
        self.assertEqual((cert_path, key_path), old)
        self.assertTrue(certificate["pending"])
        self.assertTrue(hosting_cmd._route_serves_https(
            edge.render(validated, 18123, cert_path, key_path, {})))
        issued = {"id": None, "hostnames": sorted([PRIMARY, ALIAS, GATE]), "issuer": "public"}
        with patch.object(nginx, "ensure_public_certificate", return_value=(*old, issued)) as issue, \
                patch.object(edge, "configure") as configure:
            self.assertEqual(edge.after_dns(validated, 18123, {}, cert_path, certificate, ROUTE), issued)
        issue.assert_called_once()
        configure.assert_called_once()
        public = _HostingOwnerFixture(self, _private_manifest(private=False)).validated
        with patch.object(nginx, "public_certificate_present", return_value=False), \
                patch.object(nginx, "existing_certificate", side_effect=AssertionError("public path unchanged")):
            self.assertEqual(edge.certificate(False, public, runtime, {}, None, "/srv", ROUTE), (None, None, None))

    def test_https_detection(self):
        for text, expected in ((None, False), ("server {\n    listen 80;\n}\n", False),
                               ("server {\n    listen 443 ssl;\n}\n", True),
                               ("server {\n    listen [::]:443 ssl http2;\n}\n", True),
                               ("site.example.test {\n    respond 404\n}\n", True)):
            with self.subTest(text=text):
                self.assertEqual(hosting_cmd._route_serves_https(text), expected)

    def test_the_certificate_check_never_reads_a_failure_as_none(self):
        for code, stdout, expected in ((0, "public\n", "public"), (0, "origin\n", "origin"), (0, "none\n", None),
                                       (255, "", "raise"), (0, "", "raise")):
            with self.subTest(code=code, stdout=stdout), \
                    patch.object(nginx, "_ssh", return_value=subprocess.CompletedProcess([], code, stdout, "")):
                if expected == "raise":
                    with self.assertRaises(nginx.FrontDoorError):
                        nginx.existing_certificate({}, "x")
                elif expected is None:
                    self.assertIsNone(nginx.existing_certificate({}, "x"))
                else:
                    paths = nginx.existing_certificate({}, "x")
                    self.assertEqual(paths, nginx.public_paths("x") if expected == "public" else nginx.origin_paths("x"))

    def test_only_a_private_target_renders_a_deny_route(self):
        validated = {**_HostingOwnerFixture(self, _private_manifest(private=False)).validated, "route_deny": True}
        with self.assertRaises(hosting.HostingError):
            hosting.caddyfile(validated, 18123)
        with self.assertRaises(ValueError):
            nginx.render_host_conf(validated, 18123, name="x", cert_path="/c", key_path="/k")


class FailedPrivateApplyTests(unittest.TestCase):
    """A private apply that fails never leaves the public name serving the application."""

    def fixture(self, previous, *, private=True):
        fixture = _HostingOwnerFixture(self, _private_manifest(private))
        self.port = fixture.runtime["loopback_port"]
        self.live = {"route": self.public_route() if previous == "public" else previous}

        def read(_entry, path, *_a, **_k):
            return self.live["route"] if path.endswith(f"/{ROUTE}.caddy") else None

        def install(_entry, _name, content, *_a, **_k):
            self.live["route"] = content
            return {"state": "changed", "digest": "x"}
        fixture.patch.object(hosting_cmd, "_read_remote_optional", side_effect=read)
        fixture.read_strict = fixture.patch.object(hosting_cmd, "_read_remote_strict", side_effect=read)
        fixture.reconcile = fixture.patch.object(hosting_cmd, "_reconcile_host_caddy", side_effect=install)
        fixture.configure_edge.side_effect = install
        fixture.restore_edge.side_effect = install
        fixture.manager = MagicMock()
        for name, mock in (("push", fixture.push), ("update", fixture.update), ("compose", fixture.compose),
                           ("configure", fixture.configure_edge), ("restore", fixture.restore_edge),
                           ("reconcile", fixture.reconcile)):
            fixture.manager.attach_mock(mock, name)
        return fixture

    def public_route(self):
        return f"{PRIMARY}, {ALIAS} {{\n    reverse_proxy 127.0.0.1:{self.port}\n}}\n"

    def calls(self, fixture):
        return [name for name, _args, _kwargs in fixture.manager.mock_calls if "." not in name]

    def run_failing(self, fixture):
        with self.assertRaises(Exception):
            fixture.apply()

    def assert_denies(self, text):
        self.assertTrue(hosting.is_marked_private_route(text))
        self.assertNotIn("reverse_proxy", text)

    def assert_never_public(self, fixture):
        """Once the checkout changes, no installed route sends a public name to the app."""
        changed = False
        live = None
        for name, args, _kwargs in fixture.manager.mock_calls:
            if name in ("push", "update", "compose"):
                changed = True
            elif name in ("configure", "restore", "reconcile"):
                live = args[2]
            if changed:
                self.assertIsNotNone(live)
                self.assertFalse(_serves_app(live, self.port), (name, live))
        self.assertTrue(changed)

    def test_a_deny_route_goes_up_before_the_checkout_changes(self):
        fixture = self.fixture("public")
        fixture.edge.side_effect = RuntimeError(f"edge verification failed for {PRIMARY}: HTTP 404")
        self.run_failing(fixture)
        # The private route this apply installed stays after the failure.
        self.assertEqual(self.calls(fixture), ["reconcile", "push", "update", "compose", "configure"])
        self.assert_denies(fixture.reconcile.call_args.args[2])
        self.assert_never_public(fixture)
        live = self.live["route"]
        self.assertTrue(hosting.is_marked_private_route(live))
        self.assertIn(f"reverse_proxy 127.0.0.1:{self.port + 3000}", live)

    def test_a_runtime_failure_before_the_edge_leaves_the_deny_route(self):
        fixture = self.fixture("public")
        fixture.health.side_effect = RuntimeError("remote healthcheck did not return 200-399 within 60 seconds")
        self.run_failing(fixture)
        fixture.configure_edge.assert_not_called()
        self.assertEqual(self.calls(fixture), ["reconcile", "push", "update", "compose", "restore"])
        self.assert_denies(self.live["route"])
        self.assert_never_public(fixture)

    def test_an_absent_route_still_gets_the_deny_route(self):
        """No route on disk is not proof none is loaded: the deny route owns the names."""
        fixture = self.fixture(None)
        fixture.apply()
        self.assertEqual(self.calls(fixture)[:3], ["reconcile", "push", "update"])
        self.assert_denies(fixture.reconcile.call_args.args[2])

    def test_no_deployment_change_when_the_deny_route_is_not_confirmed(self):
        for failure in ("unreadable", "reload_failed", "unchanged"):
            with self.subTest(failure=failure):
                fixture = self.fixture("public")
                if failure == "unreadable":
                    fixture.read_strict.side_effect = hosting.HostingError("route state unknown: exit 255")
                elif failure == "reload_failed":
                    fixture.reconcile.side_effect = hosting.HostingError("caddy reload failed")
                else:
                    fixture.reconcile.side_effect = lambda *_a, **_k: {"state": "reconciled"}
                with self.assertRaisesRegex(hosting.HostingError, "private_route_closure_failed"):
                    fixture.apply()
                fixture.push.assert_not_called()
                fixture.update.assert_not_called()
                fixture.compose.assert_not_called()
                fixture.configure_edge.assert_not_called()

    def test_a_proxied_record_is_refused_before_any_effect(self):
        fixture = self.fixture("public")
        fixture.client.records.return_value = [
            {"id": "r1", "type": "A", "name": PRIMARY, "content": "192.0.2.1", "proxied": True}]
        with self.assertRaisesRegex(hosting.HostingError, "private_proxied_record"):
            fixture.apply()
        fixture.reconcile.assert_not_called()
        fixture.push.assert_not_called()
        fixture.update.assert_not_called()
        fixture.compose.assert_not_called()
        fixture.client.upsert_address.assert_not_called()

    def test_a_marked_private_route_is_restored_after_a_failure(self):
        fixture = self.fixture(None)
        previous = hosting.caddyfile({**fixture.validated, "route_tag": "e" * 32},
                                     self.port, "/c", "/k")
        self.live["route"] = previous
        fixture.health.side_effect = RuntimeError("remote healthcheck did not return 200-399 within 60 seconds")
        self.run_failing(fixture)
        self.assertEqual(self.calls(fixture), ["reconcile", "push", "update", "compose", "restore"])
        self.assert_denies(fixture.reconcile.call_args.args[2])
        fixture.restore_edge.assert_called_once_with(fixture.entry, ROUTE, previous, log_path=APPLY_LOG)
        self.assert_never_public(fixture)

    def test_an_unmarked_route_that_looks_private_is_not_put_back(self):
        """Only a route Sandbox rendered private and nobody changed counts as private."""
        fixture = self.fixture(None)
        self.live["route"] = (f"{PRIMARY} {{\n    respond 404\n}}\n"
                              f"{GATE} {{\n    reverse_proxy localhost:{self.port}\n}}\n")
        fixture.health.side_effect = RuntimeError("remote healthcheck did not return 200-399 within 60 seconds")
        self.run_failing(fixture)
        self.assert_denies(self.live["route"])

    def test_a_public_manifest_still_restores_its_previous_route(self):
        fixture = self.fixture("public", private=False)
        previous = self.live["route"]
        fixture.health.side_effect = RuntimeError("remote healthcheck did not return 200-399 within 60 seconds")
        self.run_failing(fixture)
        self.assertEqual(self.calls(fixture), ["push", "update", "compose", "restore"])
        fixture.reconcile.assert_not_called()
        fixture.restore_edge.assert_called_once_with(fixture.entry, ROUTE, previous, log_path=APPLY_LOG)

    def test_a_good_private_apply_verifies_with_its_privacy_and_route_tag(self):
        fixture = self.fixture(None)
        fixture.apply()
        kwargs = fixture.edge.call_args.kwargs
        self.assertEqual(kwargs["gate_hostnames"], frozenset({GATE}))
        self.assertEqual(kwargs["private_hostnames"], frozenset({PRIMARY, ALIAS}))
        self.assertRegex(kwargs["route_tag"], r"\A[0-9a-f]{32}\Z")
        self.assertIn(f'X-Sandbox-Route "{kwargs["route_tag"]}"', self.live["route"])
        self.assertTrue(hosting.is_marked_private_route(self.live["route"]))
        fixture.restore_edge.assert_not_called()


if __name__ == "__main__":
    unittest.main()
