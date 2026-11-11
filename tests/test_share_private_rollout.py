"""Spec 081 rollout fixes: delivery verification follows `share.private`, and a failed
apply of a private manifest never leaves the public name serving the application.

The 2026-11-11 mockups rollout failed both ways: verification expected the private
primary to answer like a public site, and the rollback put the previous public route in
front of the newly built, unlocked runtime.
"""
import datetime as dt
import io
import re
import sys
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox.commands.hosting as hosting_cmd  # noqa: E402
from tests.test_hosting import _HostingOwnerFixture, _public_acme_manifest  # noqa: E402

PRIMARY = "site.example.test"
ALIAS = "www.example.test"
GATE = "gate.example.test"
APPLY_LOG = "/srv/sandbox/runtime/hosts/example-site/production/apply.log"
ROUTE = "sandbox-host-example-site-production"


def _private_manifest(private=True):
    enabled_until = (dt.date.today() + dt.timedelta(days=10)).isoformat()
    manifest = _public_acme_manifest(
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


def _http_error(code, gate=False):
    headers = Message()
    if gate:
        headers["X-Sandbox-Gate"] = "1"
    return urllib.error.HTTPError("https://x/", code, "status", headers, io.BytesIO())


@patch("sandbox.commands.hosting.time.sleep")
@patch("sandbox.commands.hosting.urllib.request.build_opener")
class PrivateVerificationTests(unittest.TestCase):
    def verify(self, build_opener, answers, route, **kwargs):
        build_opener.return_value.open.side_effect = answers
        hosting_cmd._verify_edge([route], **kwargs)
        return [call.args[0].full_url for call in build_opener.return_value.open.call_args_list]

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
        build_opener.return_value.open.side_effect = lambda *_a, **_k: (_ for _ in ()).throw(_http_error(404))
        with self.assertRaisesRegex(RuntimeError, "HTTP 404, but not from the gate"):
            hosting_cmd._verify_edge([{"hostname": GATE, "mode": "serve"}], gate_hostnames=frozenset({GATE}))
        build_opener.return_value.open.side_effect = lambda *_a, **_k: _Response(200)
        with self.assertRaisesRegex(RuntimeError, "HTTP 200 without a session"):
            hosting_cmd._verify_edge([{"hostname": GATE, "mode": "serve"}], gate_hostnames=frozenset({GATE}))

    def test_a_gate_on_a_public_target_is_checked_and_the_primary_stays_public(self, build_opener, _sleep):
        answers = {f"https://{GATE}/__sandbox/gate-check": _http_error(404, gate=True)}

        def answer(request, timeout=None):
            found = answers.get(request.full_url)
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


class FailedPrivateApplyTests(unittest.TestCase):
    """A private apply that fails never leaves the public name serving the application."""

    def fixture(self, previous, *, private=True):
        fixture = _HostingOwnerFixture(self, _private_manifest(private))
        fixture.patch.object(hosting_cmd, "_read_remote_optional", return_value=previous)
        self.port = fixture.runtime["loopback_port"]
        return fixture

    def public_route(self):
        return f"{PRIMARY}, {ALIAS} {{\n    reverse_proxy 127.0.0.1:{self.port}\n}}\n"

    def live_route(self, fixture, previous):
        """What the front door serves after the apply, from the order of edge calls."""
        live = previous
        for name, args, _kwargs in fixture.manager.mock_calls:
            if name == "configure":
                live = args[2]
            elif name == "restore":
                live = args[2]
        return live

    def run_failing(self, fixture):
        fixture.manager = MagicMock()
        fixture.manager.attach_mock(fixture.configure_edge, "configure")
        fixture.manager.attach_mock(fixture.restore_edge, "restore")
        with self.assertRaises(Exception):
            fixture.apply()

    def test_verification_fails_after_the_private_route_is_live(self):
        fixture = self.fixture(None)
        previous = self.public_route()
        fixture.patch.object(hosting_cmd, "_read_remote_optional", return_value=previous)
        fixture.edge.side_effect = RuntimeError(f"edge verification failed for {PRIMARY}: HTTP 404")
        self.run_failing(fixture)
        fixture.configure_edge.assert_called_once()
        fixture.restore_edge.assert_not_called()
        live = self.live_route(fixture, previous)
        self.assertFalse(_serves_app(live, self.port))
        self.assertIn(f"reverse_proxy 127.0.0.1:{self.port + 3000}", live)

    def test_a_runtime_failure_before_the_edge_removes_a_public_route(self):
        fixture = self.fixture(None)
        previous = self.public_route()
        fixture.patch.object(hosting_cmd, "_read_remote_optional", return_value=previous)
        fixture.health.side_effect = RuntimeError("remote healthcheck did not return 200-399 within 60 seconds")
        self.run_failing(fixture)
        fixture.configure_edge.assert_not_called()
        fixture.restore_edge.assert_called_once_with(fixture.entry, ROUTE, None, log_path=APPLY_LOG)
        self.assertFalse(_serves_app(self.live_route(fixture, previous), self.port))

    def test_an_already_private_route_is_restored(self):
        previous = f"{PRIMARY} {{\n    respond 404\n}}\n{GATE} {{\n    reverse_proxy 127.0.0.1:21000\n}}\n"
        fixture = self.fixture(previous)
        fixture.health.side_effect = RuntimeError("remote healthcheck did not return 200-399 within 60 seconds")
        self.run_failing(fixture)
        fixture.restore_edge.assert_called_once_with(fixture.entry, ROUTE, previous, log_path=APPLY_LOG)
        self.assertFalse(_serves_app(self.live_route(fixture, previous), self.port))

    def test_a_public_manifest_still_restores_its_previous_route(self):
        fixture = self.fixture(None, private=False)
        previous = self.public_route()
        fixture.patch.object(hosting_cmd, "_read_remote_optional", return_value=previous)
        fixture.health.side_effect = RuntimeError("remote healthcheck did not return 200-399 within 60 seconds")
        self.run_failing(fixture)
        fixture.restore_edge.assert_called_once_with(fixture.entry, ROUTE, previous, log_path=APPLY_LOG)

    def test_a_good_private_apply_verifies_with_its_privacy(self):
        fixture = self.fixture(None)
        fixture.apply()
        kwargs = fixture.edge.call_args.kwargs
        self.assertEqual(kwargs["gate_hostnames"], frozenset({GATE}))
        self.assertEqual(kwargs["private_hostnames"], frozenset({PRIMARY, ALIAS}))
        fixture.restore_edge.assert_not_called()


if __name__ == "__main__":
    unittest.main()
