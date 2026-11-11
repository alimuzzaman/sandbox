"""Spec 081 rollout fixes: delivery verification follows `share.private`, and a failed
apply of a private manifest never leaves the public name serving the application.

The 2026-11-11 mockups rollout failed both ways: verification expected the private
primary to answer like a public site, and the rollback put the previous public route in
front of the newly built, unlocked runtime.
"""
import datetime as dt
import io
import re
import subprocess
import sys
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox.commands.hosting as hosting_cmd  # noqa: E402
import sandbox.core._hosting as hosting  # noqa: E402
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

    def test_the_caddy_transaction_can_force_a_reload(self):
        plain = hosting_cmd._caddy_transaction_command("/etc/caddy/conf.d/x.caddy", None, "absent",
                                                       operation="apply", remove=True)
        forced = hosting_cmd._caddy_transaction_command("/etc/caddy/conf.d/x.caddy", None, "absent",
                                                        operation="apply", remove=True, force=True)
        self.assertEqual(forced, plain + " 1")
        self.assertIn('[ "$force_reload" != 1 ]', forced)
        for command in (plain, forced):
            result = subprocess.run(["sh", "-n", "-c", command], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_nginx_transaction_can_force_a_reload_and_prove_removal(self):
        plain = nginx.transaction_command("/etc/nginx/conf.d/sandbox-x.conf", None, "absent",
                                          operation="apply", remove=True)
        forced = nginx.transaction_command("/etc/nginx/conf.d/sandbox-x.conf", None, "absent",
                                           operation="apply", remove=True, force=True,
                                           probe_host="a.example.test", gone_marker="c" * 24)
        self.assertTrue(forced.endswith(" 1 " + "c" * 24))
        self.assertNotIn(" 1 -", plain[-6:])
        script = nginx.transaction_script()
        self.assertIn('[ "$force" != 1 ]', script)
        self.assertIn("route_still_loaded", script)
        self.assertEqual(nginx._EXIT_REASONS[74], "route_still_loaded")

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

    def test_nginx_reconcile_removes_with_the_old_marker_or_installs_the_kept_route(self):
        validated = _HostingOwnerFixture(self, _private_manifest()).validated
        kept = nginx.render_host_conf(validated, 18123, name="x", cert_path="/c", key_path="/k")
        public = nginx.render_host_conf(_HostingOwnerFixture(self, _private_manifest(private=False)).validated,
                                        18123, name="x", cert_path="/c", key_path="/k")
        commands = []

        def fake(_entry, command, timeout=60, input_data=None):
            commands.append(command)
            return subprocess.CompletedProcess([], 0, "", "")
        with patch.object(nginx, "_ssh", side_effect=fake):
            nginx.reconcile_route({}, "x", None, public)
            self.assertTrue(commands[-1].endswith(" 1 " + nginx.route_marker(public)))
            nginx.reconcile_route({}, "x", kept, kept)
            self.assertTrue(commands[-1].endswith(" " + nginx.route_marker(kept) + " 1 -"))


class FailedPrivateApplyTests(unittest.TestCase):
    """A private apply that fails never leaves the public name serving the application."""

    def fixture(self, previous, *, private=True):
        fixture = _HostingOwnerFixture(self, _private_manifest(private))
        self.port = fixture.runtime["loopback_port"]
        self.live = {"route": self.public_route() if previous == "public" else previous}

        def read(_entry, path, *_a, **_k):
            return self.live["route"] if path.endswith(f"/{ROUTE}.caddy") else None

        def configure(_entry, _name, content, *_a, **_k):
            self.live["route"] = content
            return {"state": "changed", "digest": "x"}

        def restore(_entry, _name, previous_route, **_k):
            self.live["route"] = previous_route
            return {"state": "rollback_complete", "digest": "x"}

        def reconcile(_entry, _name, keep, **_k):
            self.live["route"] = keep
            return {"state": "reconciled", "digest": "x"}
        fixture.patch.object(hosting_cmd, "_read_remote_optional", side_effect=read)
        fixture.read_strict = fixture.patch.object(hosting_cmd, "_read_remote_strict", side_effect=read)
        fixture.reconcile = fixture.patch.object(hosting_cmd, "_reconcile_host_caddy", side_effect=reconcile)
        fixture.configure_edge.side_effect = configure
        fixture.restore_edge.side_effect = restore
        fixture.manager = MagicMock()
        fixture.manager.attach_mock(fixture.compose, "compose")
        fixture.manager.attach_mock(fixture.configure_edge, "configure")
        fixture.manager.attach_mock(fixture.restore_edge, "restore")
        fixture.manager.attach_mock(fixture.reconcile, "reconcile")
        return fixture

    def public_route(self):
        return f"{PRIMARY}, {ALIAS} {{\n    reverse_proxy 127.0.0.1:{self.port}\n}}\n"

    def calls(self, fixture):
        return [name for name, _args, _kwargs in fixture.manager.mock_calls if "." not in name]

    def run_failing(self, fixture):
        with self.assertRaises(Exception):
            fixture.apply()

    def assert_never_public(self, fixture):
        """Once compose has run, no installed route sends a public name to the app."""
        composed = False
        live = None
        for name, args, _kwargs in fixture.manager.mock_calls:
            if name == "compose":
                composed = True
            elif name in ("configure", "restore", "reconcile"):
                live = args[2]
            if composed:
                self.assertFalse(_serves_app(live, self.port), (name, live))
        self.assertTrue(composed)

    def test_a_public_route_comes_down_before_the_runtime_changes(self):
        fixture = self.fixture(None)
        self.live["route"] = self.public_route()
        fixture.edge.side_effect = RuntimeError(f"edge verification failed for {PRIMARY}: HTTP 404")
        self.run_failing(fixture)
        # The private route this apply installed stays after the failure.
        self.assertEqual(self.calls(fixture), ["reconcile", "compose", "configure"])
        self.assertIsNone(fixture.reconcile.call_args.args[2])
        self.assert_never_public(fixture)
        live = self.live["route"]
        self.assertTrue(hosting.is_marked_private_route(live))
        self.assertIn(f"reverse_proxy 127.0.0.1:{self.port + 3000}", live)

    def test_a_runtime_failure_before_the_edge_leaves_no_route(self):
        fixture = self.fixture("public")
        fixture.health.side_effect = RuntimeError("remote healthcheck did not return 200-399 within 60 seconds")
        self.run_failing(fixture)
        fixture.configure_edge.assert_not_called()
        self.assertEqual(self.calls(fixture)[:2], ["reconcile", "compose"])
        self.assertTrue(all(call.args[2] is None for call in fixture.restore_edge.call_args_list))
        self.assertIsNone(self.live["route"])
        self.assert_never_public(fixture)

    def test_an_absent_route_is_still_reloaded_before_the_runtime_changes(self):
        """A fragment a failed reload left loaded is unloaded although the file is gone."""
        fixture = self.fixture(None)
        fixture.apply()
        self.assertEqual(self.calls(fixture)[:2], ["reconcile", "compose"])
        self.assertIsNone(fixture.reconcile.call_args.args[2])

    def test_no_runtime_change_when_the_route_cannot_be_closed(self):
        for failure in ("unreadable", "reload_failed", "unchanged"):
            with self.subTest(failure=failure):
                fixture = self.fixture("public")
                if failure == "unreadable":
                    fixture.read_strict.side_effect = hosting.HostingError("route state unknown: exit 255")
                elif failure == "reload_failed":
                    def failed_reload(*_a, **_k):
                        self.live["route"] = None  # removed on disk, reload failed
                        raise hosting.HostingError("caddy reload failed")
                    fixture.reconcile.side_effect = failed_reload
                else:
                    fixture.reconcile.side_effect = lambda *_a, **_k: {"state": "reconciled"}
                with self.assertRaisesRegex(hosting.HostingError, "private_route_closure_failed"):
                    fixture.apply()
                fixture.compose.assert_not_called()
                fixture.configure_edge.assert_not_called()

    def test_a_proxied_record_is_refused_before_any_effect(self):
        fixture = self.fixture("public")
        fixture.client.records.return_value = [
            {"id": "r1", "type": "A", "name": PRIMARY, "content": "192.0.2.1", "proxied": True}]
        with self.assertRaisesRegex(hosting.HostingError, "private_proxied_record"):
            fixture.apply()
        fixture.reconcile.assert_not_called()
        fixture.compose.assert_not_called()
        fixture.client.upsert_address.assert_not_called()

    def test_a_marked_private_route_is_kept_and_restored(self):
        fixture = self.fixture(None)
        previous = hosting.caddyfile({**fixture.validated, "route_tag": "e" * 32},
                                     self.port, "/c", "/k")
        self.live["route"] = previous
        fixture.health.side_effect = RuntimeError("remote healthcheck did not return 200-399 within 60 seconds")
        self.run_failing(fixture)
        self.assertEqual(self.calls(fixture), ["reconcile", "compose", "restore"])
        self.assertEqual(fixture.reconcile.call_args.args[2], previous)
        fixture.restore_edge.assert_called_once_with(fixture.entry, ROUTE, previous, log_path=APPLY_LOG)
        self.assert_never_public(fixture)

    def test_an_unmarked_route_that_looks_private_is_removed(self):
        """Only a route Sandbox rendered private and nobody changed counts as private."""
        fixture = self.fixture(None)
        self.live["route"] = (f"{PRIMARY} {{\n    respond 404\n}}\n"
                              f"{GATE} {{\n    reverse_proxy localhost:{self.port}\n}}\n")
        fixture.health.side_effect = RuntimeError("remote healthcheck did not return 200-399 within 60 seconds")
        self.run_failing(fixture)
        self.assertEqual(self.calls(fixture)[:2], ["reconcile", "compose"])
        self.assertIsNone(self.live["route"])

    def test_a_public_manifest_still_restores_its_previous_route(self):
        fixture = self.fixture(None, private=False)
        previous = self.public_route()
        self.live["route"] = previous
        fixture.health.side_effect = RuntimeError("remote healthcheck did not return 200-399 within 60 seconds")
        self.run_failing(fixture)
        self.assertEqual(self.calls(fixture), ["compose", "restore"])
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
