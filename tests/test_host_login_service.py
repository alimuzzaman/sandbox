"""Declaring, rendering, and issuing links for the Sandbox login service."""
import base64
import contextlib
import copy
import datetime as dt
import hashlib
import io
import json
import re
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox.commands.hosting as hosting_cmd  # noqa: E402
import sandbox.core._hosting as hosting  # noqa: E402
from sandbox.hosting import login_service as login  # noqa: E402
from tests.subprocess_support import run_test_process  # noqa: E402

IMAGE = "python:3.13-alpine@sha256:" + "a" * 64


def _future(days=10):
    return (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=days)).isoformat()


def _login_block():
    return {
        "login_service": {
            "image": IMAGE,
            "enabled_until": _future(),
            "session_ttl_seconds": 3600,
            "allowed_host_suffixes": ["example.test"],
            "capture": {
                "service": "capture",
                "adapter": "mailpit_v1",
                "api_url": "http://capture:8025/mail",
                "username": "capture",
                "credential_secret": "CAPTURE_PASSWORD",
            },
        },
        "entries": {
            "app": {
                "mode": "magic_link",
                "requires_email": True,
                "hostname": "gate.example.test",
                "path": "/__sandbox/login/app",
                "ttl_seconds": 900,
                "magic_link": {
                    "request_url": "http://app:8080/login/request",
                    "request_origin": "https://app.example.test",
                    "link_origin": "https://app.example.test",
                    "link_path": "/login/verify",
                    "link_pattern": r"https://app\.example\.test/login/verify\?[^\s\"<>]+",
                    "required_params": {"return": "https://app.example.test"},
                    "poll_timeout_seconds": 10,
                },
            },
            "inbox": {
                "mode": "inbox_gate",
                "requires_email": False,
                "hostname": "gate.example.test",
                "path": "/__sandbox/login/inbox",
                "ttl_seconds": 600,
                "landing_path": "/mail/",
            },
        },
    }


def _environment(autologin=None):
    return {
        "compose": {"files": ["compose.yml"], "service": "web", "container_port": 8080,
                    "background_services": ["capture", "app"]},
        "healthcheck": {"path": "/", "statuses": [200]},
        "deploy": {"allowed_branches": ["main"], "require_clean": True},
        "host": {"primary": "app.example.test",
                 "aliases": [{"hostname": "gate.example.test", "mode": "serve"}]},
        "cloudflare": {"proxied": False, "tls": "acme"},
        "secrets": {"required": {"CAPTURE_PASSWORD": "SOURCE_CAPTURE_PASSWORD"}},
        **({"autologin": autologin} if autologin is not None else {}),
    }


def _validate(environment):
    document = {"version": 1, "project": "example-site",
                "environments": {"production": environment}}
    with tempfile.TemporaryDirectory() as directory:
        # YAML is a superset of JSON, so the fixture stays a plain mapping.
        Path(directory, "sandbox.hosting.yml").write_text(json.dumps(document))
        Path(directory, "compose.yml").write_text("services: {}\n")
        return hosting.validate_manifest(directory)


def _valid():
    return _validate(_environment(_login_block()))


class DeclarationTests(unittest.TestCase):
    def assertRefused(self, mutate, code):
        environment = _environment(_login_block())
        mutate(environment)
        with self.assertRaisesRegex(hosting.HostingError, code):
            _validate(environment)

    def _magic(self, environment):
        return environment["autologin"]["entries"]["app"]["magic_link"]

    def test_origins_must_be_on_an_allowed_host(self):
        for field in ("request_origin", "link_origin"):
            for value in ("https://other.invalid", "https://app.example.test.evil.invalid"):
                with self.subTest(field=field, value=value):
                    self.assertRefused(lambda e: self._magic(e).__setitem__(field, value), field)

    def test_origins_must_be_canonical(self):
        for value in ("https://user@app.example.test", "https://user:pw@app.example.test",
                      "https://APP.example.test", "https://app.example.test:",
                      "https://app.example.test/", "http://app.example.test",
                      "https://app.example.test:99999"):
            with self.subTest(value=value):
                self.assertRefused(lambda e: self._magic(e).__setitem__("link_origin", value), "link_origin")

    def test_origin_with_a_port_on_an_allowed_host_is_accepted(self):
        environment = _environment(_login_block())
        self._magic(environment)["link_origin"] = "https://app.example.test:8443"
        self.assertEqual(
            _validate(environment)["login_service"]["entries"]["app"]["magic_link"]["link_origin"],
            "https://app.example.test:8443")

    def test_return_destination_must_be_on_an_allowed_host(self):
        for value in ("https://other.invalid", "https://user@app.example.test/",
                      "http://app.example.test", "//other.invalid", "other.invalid",
                      "https://other.invalid\\@app.example.test"):
            with self.subTest(value=value):
                self.assertRefused(
                    lambda e: self._magic(e)["required_params"].__setitem__("return", value), "return")

    def test_return_destination_on_an_allowed_host_or_local_path_is_accepted(self):
        for value in ("https://app.example.test/account", "/account"):
            with self.subTest(value=value):
                environment = _environment(_login_block())
                self._magic(environment)["required_params"]["return"] = value
                _validate(environment)

    def test_other_required_params_are_not_treated_as_destinations(self):
        environment = _environment(_login_block())
        self._magic(environment)["required_params"]["purpose"] = "login"
        _validate(environment)

    def test_paths_must_be_single_rooted_and_printable(self):
        for value in ("//other.invalid", "/a\\b", "/a\x07b", "/a\x7fb"):
            with self.subTest(value=value):
                self.assertRefused(
                    lambda e: e["autologin"]["entries"]["inbox"].__setitem__("landing_path", value),
                    "landing_path")

    def test_declared_service_is_normalized_and_runs_with_the_stack(self):
        validated = _valid()
        self.assertEqual(sorted(validated["login_service"]["entries"]), ["app", "inbox"])
        self.assertIn(login.SERVICE_NAME, validated["compose"]["background_services"])
        self.assertIsNone(validated["autologin"])

    def test_no_declaration_means_no_container(self):
        validated = _validate(_environment())
        self.assertIsNone(validated.get("login_service"))
        self.assertNotIn(login.SERVICE_NAME, validated["compose"].get("background_services", []))
        self.assertNotIn(login.SERVICE_NAME, hosting.compose_override(validated, 18000))

    def test_wordpress_form_is_unchanged(self):
        environment = _environment({
            "user": "admin", "service": "wordpress", "request_path": "/wp-login.php",
            "container_path": "/var/www/html/wp-content/mu-plugins/99-autologin.php",
            "ttl_seconds": 600,
        })
        validated = _validate(environment)
        self.assertEqual(validated["autologin"], {
            "user": "admin", "service": "wordpress", "request_path": "/wp-login.php",
            "container_path": "/var/www/html/wp-content/mu-plugins/99-autologin.php",
            "ttl_seconds": 600,
        })
        self.assertIsNone(validated["login_service"])

    def test_refuses_mixed_forms(self):
        self.assertRefused(lambda e: e["autologin"].update(user="admin"), "login_service_invalid")

    def test_refuses_entries_without_the_service(self):
        self.assertRefused(lambda e: e["autologin"].pop("login_service"), "login_service_invalid")

    def test_expiry_is_mandatory(self):
        self.assertRefused(lambda e: e["autologin"]["login_service"].pop("enabled_until"),
                           "enabled_until")

    def test_expiry_must_be_a_date(self):
        self.assertRefused(lambda e: e["autologin"]["login_service"].update(enabled_until="soon"),
                           "enabled_until")

    def test_session_lifetime_is_never_defaulted(self):
        self.assertRefused(lambda e: e["autologin"]["login_service"].pop("session_ttl_seconds"),
                           "session_ttl_seconds")

    def test_session_lifetime_accepts_two_days(self):
        environment = _environment(_login_block())
        environment["autologin"]["login_service"]["session_ttl_seconds"] = 172800
        self.assertEqual(_validate(environment)["login_service"]["session_ttl_seconds"], 172800)

    def test_session_lifetime_keeps_its_bounds(self):
        for value in (299, 172801):
            with self.subTest(value=value):
                self.assertRefused(
                    lambda e: e["autologin"]["login_service"].update(session_ttl_seconds=value),
                    "session_ttl_seconds")

    def test_image_must_be_pinned_by_digest(self):
        self.assertRefused(lambda e: e["autologin"]["login_service"].update(image="python:3.13"),
                           "image")

    def test_host_suffixes_are_mandatory(self):
        self.assertRefused(lambda e: e["autologin"]["login_service"].pop("allowed_host_suffixes"),
                           "allowed_host_suffixes")

    def test_every_target_hostname_must_match_a_suffix(self):
        self.assertRefused(
            lambda e: e["host"]["aliases"].append({"hostname": "other.example.org", "mode": "serve"}),
            "allowed_host_suffixes")

    def test_suffix_matches_whole_labels_only(self):
        environment = _environment(_login_block())
        environment["autologin"]["login_service"]["allowed_host_suffixes"] = ["ample.test"]
        with self.assertRaisesRegex(hosting.HostingError, "allowed_host_suffixes"):
            _validate(environment)

    def test_capture_must_be_a_running_service_of_this_project(self):
        self.assertRefused(
            lambda e: e["compose"].update(background_services=["app"]), "capture")

    def test_capture_url_must_name_the_capture_service(self):
        self.assertRefused(
            lambda e: e["autologin"]["login_service"]["capture"].update(
                api_url="http://mail.example.test:8025"), "capture")

    def test_capture_must_not_be_the_routed_service(self):
        def mutate(environment):
            environment["compose"]["service"] = "capture"
            environment["compose"]["background_services"] = ["app"]
        self.assertRefused(mutate, "capture")

    def test_capture_credential_must_be_a_secret(self):
        self.assertRefused(
            lambda e: e["autologin"]["login_service"]["capture"].update(
                credential_secret="NOT_DECLARED"), "credential_secret")

    @staticmethod
    def _without_login(environment):
        environment["autologin"]["entries"].pop("inbox")
        capture = environment["autologin"]["login_service"]["capture"]
        capture.pop("username")
        capture.pop("credential_secret")

    def test_capture_without_a_login_is_accepted(self):
        environment = _environment(_login_block())
        self._without_login(environment)
        capture = _validate(environment)["login_service"]["capture"]
        self.assertIsNone(capture["username"])
        self.assertIsNone(capture["credential_secret"])

    def test_capture_login_needs_both_fields(self):
        for field in ("username", "credential_secret"):
            with self.subTest(field=field):
                def mutate(environment):
                    environment["autologin"]["entries"].pop("inbox")
                    environment["autologin"]["login_service"]["capture"].pop(field)
                self.assertRefused(mutate, "username and credential_secret")

    def test_inbox_gate_requires_a_capture_login(self):
        # While a link is live the gate passes visitors to the capture service's
        # own login. With no login there, that would be open access.
        def mutate(environment):
            capture = environment["autologin"]["login_service"]["capture"]
            capture.pop("username")
            capture.pop("credential_secret")
        self.assertRefused(mutate, "inbox_gate")

    def test_refuses_a_reserved_service_name(self):
        self.assertRefused(
            lambda e: e["compose"]["background_services"].append(login.SERVICE_NAME), "reserved")

    def test_entry_hostname_must_be_served(self):
        self.assertRefused(
            lambda e: e["autologin"]["entries"]["app"].update(hostname="missing.example.test"),
            "hostname")

    def test_entry_hostname_must_not_be_the_application(self):
        self.assertRefused(
            lambda e: e["autologin"]["entries"]["inbox"].update(hostname="app.example.test"),
            "hostname")

    def test_entry_path_must_be_dedicated(self):
        self.assertRefused(lambda e: e["autologin"]["entries"]["app"].update(path="/"), "path")

    def test_entry_paths_must_be_unique_per_host(self):
        self.assertRefused(
            lambda e: e["autologin"]["entries"]["app"].update(path="/__sandbox/login/inbox"),
            "path")

    def test_entry_lifetime_uses_the_existing_bounds(self):
        for value in (59, 3601, True):
            with self.subTest(value=value):
                self.assertRefused(
                    lambda e: e["autologin"]["entries"]["app"].update(ttl_seconds=value),
                    "ttl_seconds")

    def test_entry_must_declare_whether_it_needs_an_email(self):
        self.assertRefused(lambda e: e["autologin"]["entries"]["inbox"].pop("requires_email"),
                           "requires_email")

    def test_magic_link_needs_an_email(self):
        self.assertRefused(lambda e: e["autologin"]["entries"]["app"].update(requires_email=False),
                           "requires_email")

    def test_unknown_entry_fields_are_refused(self):
        self.assertRefused(lambda e: e["autologin"]["entries"]["app"].update(user="admin"),
                           "unknown")

    def test_entry_names_are_constrained(self):
        def mutate(environment):
            entries = environment["autologin"]["entries"]
            entries["../bad"] = entries.pop("app")
        self.assertRefused(mutate, "entry name")

    def test_magic_link_settings_are_required_and_bounded(self):
        for field in ("request_url", "request_origin", "link_origin", "link_path", "link_pattern"):
            with self.subTest(field=field):
                self.assertRefused(
                    lambda e: e["autologin"]["entries"]["app"]["magic_link"].pop(field), field)
        self.assertRefused(
            lambda e: e["autologin"]["entries"]["app"]["magic_link"].update(poll_timeout_seconds=99),
            "poll_timeout_seconds")
        self.assertRefused(
            lambda e: e["autologin"]["entries"]["app"]["magic_link"].update(link_pattern="("),
            "link_pattern")

    def test_denied_emails_must_be_addresses(self):
        self.assertRefused(
            lambda e: e["autologin"]["entries"]["app"]["magic_link"].update(denied_emails=["staff"]),
            "denied_emails")

    def test_denied_emails_are_normalized(self):
        environment = _environment(_login_block())
        environment["autologin"]["entries"]["app"]["magic_link"]["denied_emails"] = ["Staff@Example.test"]
        entry = _validate(environment)["login_service"]["entries"]["app"]
        self.assertEqual(entry["magic_link"]["denied_emails"], ["staff@example.test"])

    def test_magic_link_must_not_call_a_public_host(self):
        self.assertRefused(
            lambda e: e["autologin"]["entries"]["app"]["magic_link"].update(
                request_url="https://app.example.test/login/request"), "request_url")


class ApplicabilityTests(unittest.TestCase):
    def setUp(self):
        self.declaration = _valid()["login_service"]
        self.now = dt.datetime.now(dt.timezone.utc).timestamp()

    def test_future_date_within_the_maximum_applies(self):
        login.assert_applicable(self.declaration, now=self.now)

    def test_a_past_date_is_refused(self):
        self.declaration["enabled_until"] = "2020-01-01"
        with self.assertRaises(login.LoginServiceError) as raised:
            login.assert_applicable(self.declaration, now=self.now)
        self.assertEqual(raised.exception.code, "login_service_expired")

    def test_a_date_past_the_maximum_is_refused(self):
        self.declaration["enabled_until"] = _future(login.MAX_ENABLED_DAYS + 2)
        with self.assertRaises(login.LoginServiceError) as raised:
            login.assert_applicable(self.declaration, now=self.now)
        self.assertEqual(raised.exception.code, "login_service_expiry_too_far")

    def test_the_last_day_counts_through_its_end(self):
        today = dt.datetime.fromtimestamp(self.now, dt.timezone.utc).date()
        self.declaration["enabled_until"] = today.isoformat()
        login.assert_applicable(self.declaration, now=self.now)

    def test_status_shows_each_entry_and_its_expiry(self):
        view = login.status_view(self.declaration, now=self.now)
        self.assertEqual(view["state"], "enabled")
        self.assertEqual(view["enabled_until"], self.declaration["enabled_until"])
        self.assertEqual({row["name"] for row in view["entries"]}, {"app", "inbox"})
        self.assertNotIn("capture", json.dumps(view))

    def test_status_shows_a_left_on_service_as_expired(self):
        self.declaration["enabled_until"] = "2020-01-01"
        self.assertEqual(login.status_view(self.declaration, now=self.now)["state"], "expired")

    def test_run_compose_refuses_an_expired_declaration_before_any_write(self):
        validated = _valid()
        validated["login_service"]["enabled_until"] = "2020-01-01"
        with patch.object(hosting_cmd, "_write_remote_text") as write, \
             patch.object(hosting_cmd, "_remote_checked") as checked:
            with self.assertRaisesRegex(hosting.HostingError, "login_service_expired"):
                hosting_cmd._run_compose(
                    {}, validated, "/src", "/run", {"compose_override": "", "environment": ""})
        write.assert_not_called()
        checked.assert_not_called()


class OverrideTests(unittest.TestCase):
    def setUp(self):
        self.validated = _valid()
        self.override = hosting.compose_override(self.validated, 18000)

    def test_only_the_routed_service_publishes_a_port(self):
        self.assertEqual(self.override.count("ports:"), 1)
        self.assertIn("127.0.0.1:18000:8080", self.override)

    def test_service_is_rendered_hardened_and_unpublished(self):
        for expected in (login.SERVICE_NAME + ":", "read_only: true", "cap_drop", "no-new-privileges",
                         "/run/sandbox-login", "expose", "healthcheck", IMAGE):
            self.assertIn(expected, self.override)

    def test_credential_is_passed_by_reference_only(self):
        self.assertIn("SANDBOX_LOGIN_CAPTURE_CREDENTIAL", self.override)
        self.assertIn("${CAPTURE_PASSWORD}", self.override)
        config = self._embedded_config()
        self.assertNotIn("CAPTURE_PASSWORD", json.dumps(config))

    def test_runtime_receives_the_expiry_and_entries(self):
        config = self._embedded_config()
        self.assertGreater(config["enabled_until_epoch"], 0)
        self.assertEqual(sorted(config["entries"]), ["app", "inbox"])
        self.assertEqual(config["session_ttl_seconds"], 3600)

    def test_capture_without_a_login_renders_no_credential(self):
        environment = _environment(_login_block())
        DeclarationTests._without_login(environment)
        override = hosting.compose_override(_validate(environment), 18000)
        self.assertNotIn("SANDBOX_LOGIN_CAPTURE_CREDENTIAL", override)
        self.assertNotIn("CAPTURE_PASSWORD", override)

    def test_denied_addresses_stay_out_of_the_container(self):
        magic = self.validated["login_service"]["entries"]["app"]["magic_link"]
        magic["denied_emails"] = ["staff@example.test"]
        override = hosting.compose_override(self.validated, 18000)
        match = re.search(r'SANDBOX_LOGIN_CONFIG: "([A-Za-z0-9+/=]+)"', override)
        self.assertNotIn("staff@example.test", base64.b64decode(match.group(1)).decode())

    def _embedded_config(self):
        match = re.search(r'SANDBOX_LOGIN_CONFIG: "([A-Za-z0-9+/=]+)"', self.override)
        self.assertIsNotNone(match)
        return json.loads(base64.b64decode(match.group(1)))


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.validated = _valid()

    def _run(self, output):
        with patch.object(hosting_cmd, "_remote_checked", return_value=output) as checked:
            hosting_cmd._preflight_login_service({}, "docker compose", self.validated)
        return checked

    def test_clean_topology_passes_with_read_only_probe(self):
        checked = self._run(json.dumps(
            {"capture_present": True, "capture_ports": 0, "login_ports": 0}))
        self.assertIn("config --format json", checked.call_args.args[1])

    def test_published_capture_port_is_refused(self):
        with self.assertRaisesRegex(hosting.HostingError, "capture_port_published"):
            self._run(json.dumps({"capture_present": True, "capture_ports": 1, "login_ports": 0}))

    def test_published_login_port_is_refused(self):
        with self.assertRaisesRegex(hosting.HostingError, "login_port_published"):
            self._run(json.dumps({"capture_present": True, "capture_ports": 0, "login_ports": 1}))

    def test_absent_capture_is_refused(self):
        with self.assertRaisesRegex(hosting.HostingError, "capture_not_in_project"):
            self._run(json.dumps({"capture_present": False, "capture_ports": 0, "login_ports": 0}))

    def test_unreadable_probe_fails_closed(self):
        with self.assertRaisesRegex(hosting.HostingError, "login_service_preflight_failed"):
            self._run("not json")

    def test_probe_returns_counts_never_compose_values(self):
        command = login.preflight_command("docker compose", "capture")
        self.assertNotIn("print(document", command)
        self.assertIn("capture_ports", command)


class PreflightProbeTests(unittest.TestCase):
    """Runs the real remote probe against a fake `docker compose config`."""

    def _probe(self, capture=None, login_service=None):
        document = {"services": {"capture": capture or {},
                                 login.SERVICE_NAME: login_service or {}}}
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory, "config.json")
            config.write_text(json.dumps(document))
            fake = Path(directory, "compose.sh")
            fake.write_text(f"#!/bin/sh\nexec /bin/cat {config}\n")
            command = shlex.split(login.preflight_command(f"/bin/sh {fake}", "capture"))
            done = run_test_process([sys.executable, *command[1:]], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    @staticmethod
    def _port(**fields):
        return {"mode": "ingress", "target": 8025, "published": "18025", "protocol": "tcp", **fields}

    def assertCaptureRefused(self, capture):
        with self.assertRaises(login.LoginServiceError) as raised:
            login.check_preflight(self._probe(capture))
        self.assertEqual(raised.exception.code, "capture_port_published")

    def test_no_ports_pass(self):
        login.check_preflight(self._probe())

    def test_loopback_only_capture_ports_pass(self):
        for host_ip in ("127.0.0.1", "::1"):
            with self.subTest(host_ip=host_ip):
                login.check_preflight(self._probe({"ports": [self._port(host_ip=host_ip)]}))

    def test_other_bindings_are_refused(self):
        for fields in ({"host_ip": "0.0.0.0"}, {"host_ip": "::"}, {"host_ip": ""}, {},
                       {"host_ip": "127.0.0.2"}, {"host_ip": "100.64.0.1"}):
            with self.subTest(fields=fields):
                self.assertCaptureRefused({"ports": [self._port(**fields)]})

    def test_one_public_mapping_among_loopback_is_refused(self):
        self.assertCaptureRefused({"ports": [self._port(host_ip="127.0.0.1"), self._port()]})

    def test_unrecognized_port_shape_is_refused(self):
        self.assertCaptureRefused({"ports": ["127.0.0.1:18025:8025"]})

    def test_host_network_is_refused(self):
        self.assertCaptureRefused({"network_mode": "host"})

    def test_login_service_may_not_publish_even_on_loopback(self):
        with self.assertRaises(login.LoginServiceError) as raised:
            login.check_preflight(self._probe(login_service={"ports": [self._port(host_ip="127.0.0.1")]}))
        self.assertEqual(raised.exception.code, "login_port_published")


class EmailResolutionTests(unittest.TestCase):
    needs = {"requires_email": True}
    none = {"requires_email": False}

    def test_validates_addresses(self):
        for value in ("person@example.test", "first.last+tag@sub.example.test"):
            self.assertEqual(login.validate_email(value), value)
        for value in ("", "person", "@example.test", "person@localhost", "a b@example.test",
                      "person@example.test\nX: y", "x" * 250 + "@example.test"):
            with self.subTest(value=value), self.assertRaises(login.LoginServiceError) as raised:
                login.validate_email(value)
            self.assertEqual(raised.exception.code, "invalid_email")

    def _resolve(self, config, *, requested=None, replace=False, saved=None, stdin=None):
        return login.resolve_email(
            config, requested=requested, replace_default=replace, saved=saved,
            stdin=stdin or io.StringIO(""), stderr=io.StringIO())

    def test_no_terminal_and_no_saved_email_refuses(self):
        with self.assertRaises(login.LoginServiceError) as raised:
            self._resolve(self.needs)
        self.assertEqual(raised.exception.code, "email_required")

    def test_saved_default_is_reused(self):
        chosen = self._resolve(self.needs, saved="kept@example.test")
        self.assertEqual((chosen.address, chosen.source), ("kept@example.test", "saved_default"))

    def test_terminal_prompt_when_nothing_is_saved(self):
        terminal = io.StringIO("asked@example.test\n")
        terminal.isatty = lambda: True
        chosen = self._resolve(self.needs, stdin=terminal)
        self.assertEqual((chosen.address, chosen.source), ("asked@example.test", "prompt"))

    def test_prompt_does_not_run_when_a_default_exists(self):
        terminal = io.StringIO("ignored@example.test\n")
        terminal.isatty = lambda: True
        chosen = self._resolve(self.needs, saved="kept@example.test", stdin=terminal)
        self.assertEqual(chosen.address, "kept@example.test")

    def test_explicit_address_wins_for_this_link(self):
        chosen = self._resolve(self.needs, requested="other@example.test", saved="kept@example.test")
        self.assertEqual((chosen.address, chosen.source), ("other@example.test", "argument"))
        self.assertFalse(chosen.replace_default)

    def test_replace_requires_an_explicit_address(self):
        with self.assertRaises(login.LoginServiceError) as raised:
            self._resolve(self.needs, replace=True, saved="kept@example.test")
        self.assertEqual(raised.exception.code, "email_required")

    def test_denied_address_is_refused_from_every_source(self):
        denied = {"requires_email": True,
                  "magic_link": {"denied_emails": ["staff@example.test"]}}
        terminal = io.StringIO("staff@example.test\n")
        terminal.isatty = lambda: True
        for options in ({"requested": "Staff@Example.test"}, {"saved": "staff@example.test"},
                        {"stdin": terminal}):
            with self.subTest(options=sorted(options)), \
                 self.assertRaises(login.LoginServiceError) as raised:
                self._resolve(denied, **options)
            self.assertEqual(raised.exception.code, "email_denied")

    def test_other_addresses_pass_the_denied_list(self):
        denied = {"requires_email": True,
                  "magic_link": {"denied_emails": ["staff@example.test"]}}
        self.assertEqual(self._resolve(denied, requested="person@example.test").address,
                         "person@example.test")

    def test_entry_without_email_refuses_email_flags(self):
        self.assertIsNone(self._resolve(self.none))
        with self.assertRaises(login.LoginServiceError) as raised:
            self._resolve(self.none, requested="other@example.test")
        self.assertEqual(raised.exception.code, "email_not_applicable")


class RemoteInputTests(unittest.TestCase):
    def test_input_cannot_be_combined_with_a_streamed_command(self):
        for options in ({"log_path": "/srv/log"}, {"progress": print}):
            with self.subTest(options=sorted(options)), \
                    patch.object(hosting_cmd.remote, "ssh_stream") as stream, \
                    self.assertRaises(ValueError):
                hosting_cmd._remote_checked({}, "cat", input_data="record", **options)
            stream.assert_not_called()


class LinkIssueTests(unittest.TestCase):
    def setUp(self):
        self.validated = _valid()
        self.key = "remote/example-site/production"
        self.state = {"version": 1, "hosts": {self.key: {"provisioned": True}}}
        self.saves = []

    def _email(self, address, source="argument", replace=False):
        return login.ResolvedEmail(address=address, source=source, replace_default=replace)

    def _issue(self, name="app", email=None, ttl=None, checked_effect=None):
        with patch.object(hosting_cmd.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
             patch.object(hosting_cmd, "_compose_prefix", return_value="docker compose"), \
             patch.object(hosting_cmd, "_remote_checked",
                          side_effect=checked_effect) as checked:
            result = hosting_cmd._issue_host_login_link(
                self.validated, {}, "remote", self.state, name, ttl, email,
                save_state=lambda state: self.saves.append(copy.deepcopy(state)))
        self.commands = [call.args[1] for call in checked.call_args_list]
        self.inputs = [call.kwargs.get("input_data") for call in checked.call_args_list]
        return result

    def _record(self):
        return json.loads(self.inputs[0])

    def test_record_and_email_stay_out_of_the_remote_command(self):
        self._issue(email=self._email("person@example.test"))
        self.assertIn("person@example.test", self.inputs[0])
        encoded = base64.b64encode(self.inputs[0].encode()).decode()
        for command in self.commands:
            self.assertNotIn("person@example.test", command)
            self.assertNotIn(encoded, command)

    def test_link_is_on_the_entry_host_with_the_token_in_the_fragment(self):
        result = self._issue(email=self._email("person@example.test"))
        self.assertRegex(
            result["url"],
            r"^https://gate\.example\.test/__sandbox/login/app#sandbox_autologin=[A-Za-z0-9_-]{40,}$")

    def test_raw_token_never_leaves_the_returned_link(self):
        result = self._issue(email=self._email("person@example.test"))
        token = result["url"].rsplit("=", 1)[1]
        blob = " ".join(self.commands) + json.dumps(self.saves) + json.dumps(self.state)
        self.assertNotIn(token, blob)
        self.assertNotIn(token, self.inputs[0])

    def test_record_carries_hash_expiry_and_email(self):
        result = self._issue(email=self._email("person@example.test"), ttl=300)
        token = result["url"].rsplit("=", 1)[1]
        record = self._record()
        self.assertEqual(record["schema"], "sandbox.login-token.v1")
        self.assertEqual(record["entry"], "app")
        self.assertEqual(record["token_sha256"], hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(record["expires_at"] - record["issued_at"], 300)
        self.assertEqual(record["email"], "person@example.test")

    def test_record_for_an_entry_without_email_has_no_email(self):
        self._issue(name="inbox")
        self.assertNotIn("email", self._record())

    def test_delivery_is_atomic_into_the_login_service(self):
        self._issue(name="inbox")
        self.assertIn(f"exec -T {login.SERVICE_NAME}", self.commands[0])
        self.assertIn("mv -f", self.commands[0])
        self.assertIn("umask 077", self.commands[0])

    def test_first_address_becomes_the_target_default(self):
        result = self._issue(email=self._email("person@example.test"))
        self.assertTrue(result["email_saved"])
        self.assertEqual(self.state["hosts"][self.key][login.STATE_KEY], "person@example.test")
        self.assertEqual(len(self.saves), 1)

    def test_explicit_address_does_not_replace_the_default(self):
        self.state["hosts"][self.key][login.STATE_KEY] = "kept@example.test"
        result = self._issue(email=self._email("other@example.test"))
        self.assertFalse(result["email_saved"])
        self.assertEqual(result["email"], "other@example.test")
        self.assertEqual(self.state["hosts"][self.key][login.STATE_KEY], "kept@example.test")
        self.assertEqual(self.saves, [])

    def test_explicit_flag_replaces_the_default(self):
        self.state["hosts"][self.key][login.STATE_KEY] = "kept@example.test"
        result = self._issue(email=self._email("other@example.test", replace=True))
        self.assertTrue(result["email_saved"])
        self.assertEqual(self.state["hosts"][self.key][login.STATE_KEY], "other@example.test")

    def test_saved_default_is_not_rewritten(self):
        self.state["hosts"][self.key][login.STATE_KEY] = "kept@example.test"
        self._issue(email=self._email("kept@example.test", source="saved_default"))
        self.assertEqual(self.saves, [])

    def test_failed_delivery_saves_nothing_and_returns_no_link(self):
        def fail(*_args, **_kwargs):
            raise RuntimeError("exec failed")
        with self.assertRaises(RuntimeError):
            self._issue(email=self._email("person@example.test"), checked_effect=fail)
        self.assertEqual(self.saves, [])
        self.assertNotIn(login.STATE_KEY, self.state["hosts"][self.key])

    def test_lifetime_bounds_are_enforced_at_issue(self):
        for ttl in (59, 3601):
            with self.subTest(ttl=ttl), self.assertRaisesRegex(hosting.HostingError, "ttl-seconds"):
                self._issue(name="inbox", ttl=ttl)

    def test_undeployed_target_is_refused(self):
        self.state["hosts"].clear()
        with self.assertRaisesRegex(hosting.HostingError, "not deployed"):
            self._issue(name="inbox")

    def test_saved_default_survives_a_state_round_trip(self):
        self._issue(email=self._email("person@example.test"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "hosts.json")
            hosting.save_host_state(self.state, path)
            loaded = hosting.load_host_state(path)
        self.assertEqual(loaded["hosts"][self.key][login.STATE_KEY], "person@example.test")


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.validated = _valid()
        self.key = "remote/example-site/production"
        self.state = {"version": 1, "hosts": {self.key: {}}}

    def _run(self, *, service="app", email=None, save_email=False, as_json=True, stdin=None):
        args = SimpleNamespace(service=service, email=email, save_email=save_email, json=as_json,
                               remote="remote", ttl_seconds=None)
        out, err = io.StringIO(), io.StringIO()
        issued = {"url": "https://gate.example.test/x#sandbox_autologin=t", "expires_at": 1,
                  "one_time": True, "service": service, "hostname": "gate.example.test",
                  "email": email, "email_source": "argument", "email_saved": True}
        with patch.object(hosting_cmd.hosting, "load_host_state", return_value=self.state), \
             patch.object(hosting_cmd, "_with_host_writer_lock", return_value=issued) as locked, \
             patch.object(hosting_cmd.sys, "stdin", stdin or io.StringIO("")), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                hosting_cmd._cmd_host_login_link(self.validated, {"provisioned": True}, args)
                code = 0
            except SystemExit as stopped:
                code = stopped.code
        self.locked = locked
        return code, out.getvalue(), err.getvalue()

    def test_email_required_is_a_named_error_before_any_lock(self):
        code, out, _ = self._run()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["code"], "email_required")
        self.locked.assert_not_called()

    def test_email_required_for_humans_names_the_code(self):
        code, _, err = self._run(as_json=False)
        self.assertEqual(code, 1)
        self.assertIn("email_required", err)

    def test_the_service_flag_is_required(self):
        code, out, _ = self._run(service=None)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["code"], "login_entry_required")

    def test_unknown_entry_is_refused_by_name(self):
        code, out, _ = self._run(service="missing")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["code"], "login_entry_unknown")

    def test_expired_declaration_refuses_to_issue(self):
        self.validated["login_service"]["enabled_until"] = "2020-01-01"
        code, out, _ = self._run(email="person@example.test")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["code"], "login_service_expired")
        self.locked.assert_not_called()

    def test_invalid_address_does_not_echo_the_value(self):
        code, out, _ = self._run(email="not an address")
        self.assertEqual(json.loads(out)["code"], "invalid_email")
        self.assertNotIn("not an address", out)
        self.assertEqual(code, 1)

    def test_success_reports_the_email_in_json(self):
        code, out, _ = self._run(email="person@example.test")
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(payload["email"], "person@example.test")
        self.assertTrue(payload["ok"])

    def test_human_output_names_the_email_and_ends_with_the_link(self):
        code, out, _ = self._run(email="person@example.test", as_json=False)
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertIn("person@example.test", lines[0])
        self.assertTrue(lines[-1].startswith("https://"))

    def test_manifest_without_a_login_service_refuses_the_flag(self):
        self.validated["login_service"] = None
        code, out, _ = self._run(service="app", email="person@example.test")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["code"], "login_service_not_declared")


class StatusTests(unittest.TestCase):
    def test_status_includes_the_declared_entries(self):
        validated = _valid()
        result = hosting_cmd._host_runtime_status(
            validated, {"provisioned": False}, "remote", {"version": 1, "hosts": {}})
        self.assertEqual(result["login_service"]["state"], "enabled")
        self.assertEqual({row["name"] for row in result["login_service"]["entries"]}, {"app", "inbox"})

    def test_status_is_silent_when_undeclared(self):
        validated = _validate(_environment())
        result = hosting_cmd._host_runtime_status(
            validated, {"provisioned": False}, "remote", {"version": 1, "hosts": {}})
        self.assertIsNone(result["login_service"])



SITE_HOST = "share.example.test"
CANARY = "C4nAryT0kenC4nAryT0kenC4nAryT0kenC4nAryT0k"  # 43 characters, like token_urlsafe(32)


def _site_entry(**fields):
    return {"mode": "site_gate", "hostname": SITE_HOST, "upstream": "http://site:80", **fields}


def _site_environment(entries=None, *, capture=False, basic_auth=None):
    block = _login_block()
    if not capture:
        block["login_service"].pop("capture")
    block["entries"] = entries if entries is not None else {"mockup": _site_entry()}
    environment = _environment(block)
    environment["compose"]["background_services"] = ["capture", "app", "site"]
    environment["host"]["aliases"].append({"hostname": SITE_HOST, "mode": "serve"})
    if basic_auth:
        environment["basic_auth"] = basic_auth
    return environment


class SiteDeclarationTests(unittest.TestCase):
    def assert_refused(self, environment, needle):
        with self.assertRaises(hosting.HostingError) as raised:
            _validate(environment)
        self.assertIn(needle, str(raised.exception))

    def test_site_gate_needs_no_capture_and_takes_defaults(self):
        declaration = _validate(_site_environment())["login_service"]
        self.assertIsNone(declaration["capture"])
        self.assertEqual(declaration["entries"]["mockup"], {
            "mode": "site_gate", "requires_email": False, "hostname": SITE_HOST,
            "path": "/__sandbox/share/mockup", "ttl_seconds": 900,
            "upstream": "http://site:80", "landing_path": "/"})

    def test_a_given_capture_is_still_checked(self):
        environment = _site_environment(capture=True)
        environment["autologin"]["login_service"]["capture"]["adapter"] = "other"
        self.assert_refused(environment, "capture.adapter")

    def test_upstream_must_be_a_background_service_with_a_port_only(self):
        for upstream in ("http://web:8080", "http://sandbox-login:8080", "http://nope:80",
                         "http://site:80/sub", "http://site:80?x=1", "http://u:p@site:80",
                         "https://site:443", "http://site", "http://site:0", None):
            with self.subTest(upstream=upstream):
                self.assert_refused(_site_environment({"mockup": _site_entry(upstream=upstream)}),
                                    "upstream")

    def test_gate_hostname_must_be_an_alias(self):
        self.assert_refused(_site_environment({"mockup": _site_entry(hostname="app.example.test")}),
                            "served alias")

    def test_a_site_gate_hostname_fronts_nothing_else(self):
        entries = {"mockup": _site_entry(),
                   "inbox": {"mode": "inbox_gate", "requires_email": False, "hostname": SITE_HOST,
                             "path": "/__sandbox/login/inbox"}}
        self.assert_refused(_site_environment(entries, capture=True), "front no other entry")
        entries["other"] = _site_entry(path="/__sandbox/share/other")
        del entries["inbox"]
        self.assert_refused(_site_environment(entries), "front no other entry")

    def test_requires_email_must_stay_false(self):
        self.assert_refused(_site_environment({"mockup": _site_entry(requires_email=True)}),
                            "requires_email")

    def test_link_path_rules(self):
        self.assert_refused(_site_environment({"mockup": _site_entry(path="/")}), "dedicated path")
        self.assert_refused(_site_environment({"mockup": _site_entry(path="/x", landing_path="/x")}),
                            "must differ")

    def test_capture_modes_still_need_a_capture(self):
        entries = {"mockup": _site_entry(),
                   "inbox": {"mode": "inbox_gate", "requires_email": False,
                             "hostname": "gate.example.test", "path": "/__sandbox/login/inbox"}}
        self.assert_refused(_site_environment(entries), "capture must be a mapping")

    def test_upstream_belongs_to_site_gate_only(self):
        environment = _environment(_login_block())
        environment["autologin"]["entries"]["inbox"]["upstream"] = "http://capture:8025"
        self.assert_refused(environment, "applies only to site_gate")

    def test_basic_auth_cannot_front_a_site_gate(self):
        self.assert_refused(
            _site_environment(basic_auth={"username": "owner", "password_secret": "CAPTURE_PASSWORD"}),
            "site_gate")


class SiteRenderTests(unittest.TestCase):
    def test_override_without_capture(self):
        validated = _validate(_site_environment())
        override = hosting.compose_override(validated, 18080)
        self.assertIn("      site:\n        condition: service_started", override)
        self.assertNotIn("capture:", override)
        self.assertNotIn("SANDBOX_LOGIN_CAPTURE_CREDENTIAL", override)
        self.assertIn("size=8m", override)
        config = login.runtime_config(validated["login_service"])
        self.assertIsNone(config["capture"])
        self.assertEqual(config["entries"]["mockup"]["upstream"], "http://site:80")

    def test_declarations_without_a_site_gate_keep_their_tmpfs(self):
        self.assertIn("size=1m", hosting.compose_override(_valid(), 18080))

    def test_status_lists_the_site_gate(self):
        view = login.status_view(_validate(_site_environment())["login_service"], now=0)
        self.assertEqual(view["entries"][0]["mode"], "site_gate")


class SitePreflightTests(unittest.TestCase):
    def test_upstream_checks(self):
        base = {"capture_present": True, "capture_ports": 0, "login_ports": 0}
        login.check_preflight(json.dumps(base))
        login.check_preflight(json.dumps({**base, "upstream_missing": 0, "upstream_ports": 0}))
        for extra, code in (({"upstream_missing": 1}, "upstream_not_in_project"),
                            ({"upstream_ports": 1}, "upstream_port_published"),
                            ({"upstream_ports": "1"}, "login_service_preflight_failed")):
            with self.subTest(code=code), self.assertRaises(login.LoginServiceError) as raised:
                login.check_preflight(json.dumps({**base, **extra}))
            self.assertEqual(raised.exception.code, code)

    def _probe(self, services):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory, "config.json")
            config.write_text(json.dumps({"services": services}))
            fake = Path(directory, "compose.sh")
            fake.write_text(f"#!/bin/sh\nexec /bin/cat {config}\n")
            command = shlex.split(login.preflight_command(f"/bin/sh {fake}", None, ["site"]))
            done = run_test_process([sys.executable, *command[1:]], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_probe_counts_upstream_exposure(self):
        public = {"mode": "ingress", "target": 80, "published": "8090", "protocol": "tcp"}
        self.assertEqual(self._probe({"site": {"ports": [public]}})["upstream_ports"], 1)
        loopback = {**public, "host_ip": "127.0.0.1"}
        observed = self._probe({"site": {"ports": [loopback]}})
        self.assertEqual((observed["upstream_ports"], observed["upstream_missing"]), (0, 0))
        self.assertTrue(observed["capture_present"])
        self.assertEqual(self._probe({"site": {"network_mode": "host"}})["upstream_ports"], 1)
        self.assertEqual(self._probe({})["upstream_missing"], 1)

    def test_apply_preflight_names_the_upstream(self):
        validated = _validate(_site_environment())
        with patch.object(hosting_cmd, "_remote_checked",
                          return_value=json.dumps({"capture_present": True, "capture_ports": 0,
                                                   "login_ports": 0, "upstream_missing": 0,
                                                   "upstream_ports": 0})) as checked:
            hosting_cmd._preflight_login_service({}, "docker compose", validated)
        self.assertIn('["site"]', checked.call_args.args[1])


LINK_ID = "l-" + "0" * 31 + "1"


class _Terminal(io.StringIO):
    """A fake controlling terminal that keeps what was written after close."""

    def close(self):
        self.final = self.getvalue()
        super().close()


class SiteCommandTests(unittest.TestCase):
    def setUp(self):
        environment = _site_environment({"mockup": _site_entry()}, capture=True)
        environment["autologin"]["entries"]["inbox"] = {
            "mode": "inbox_gate", "requires_email": False, "hostname": "gate.example.test",
            "path": "/__sandbox/login/inbox"}
        self.validated = _validate(environment)
        self.key = "remote/example-site/production"
        self.state = {"version": 1, "hosts": {self.key: {}}}
        self.replies = {}

    def _remote(self, _entry, command, timeout=180, **kwargs):
        self.commands.append(command)
        self.inputs.append(kwargs.get("input_data"))
        verb = shlex.split(command.split(" exec -T ", 1)[1])[6]
        if verb in self.replies:
            return self.replies[verb]
        if verb == "mint":
            record = json.loads(kwargs["input_data"])["link"]
            return json.dumps({"ok": True, "id": record["id"], "expires_at": record["expires_at"],
                               "uses": record["uses"]})
        if verb == "list":
            return json.dumps({"ok": True, "links": [{"id": LINK_ID, "issued_at": 1,
                                                       "expires_at": 2, "uses": 1, "used": 0}],
                               "sessions": []})
        return json.dumps({"ok": True, "revoked_links": 1, "revoked_sessions": 2})

    def _run(self, *, terminal=True, lock_error=None, **fields):
        args = SimpleNamespace(**{"service": "mockup", "email": None, "save_email": False, "json": True,
                                  "remote": "remote", "ttl_seconds": None, "uses": None,
                                  "list_links": False, "revoke": None, **fields})
        self.commands, self.inputs, self.saves = [], [], []
        self.terminal = _Terminal() if terminal else None

        def locked(_validated, _remote_name, callback):
            if lock_error is not None:
                raise lock_error
            return callback(self.state, lambda state: self.saves.append(copy.deepcopy(state)))

        out, err = io.StringIO(), io.StringIO()
        with patch.object(hosting_cmd.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
             patch.object(hosting_cmd, "_compose_prefix", return_value="docker compose"), \
             patch.object(hosting_cmd, "_remote_checked", side_effect=self._remote), \
             patch.object(hosting_cmd, "_with_host_writer_lock", side_effect=locked), \
             patch.object(hosting_cmd, "_operator_terminal", return_value=self.terminal), \
             patch.object(hosting_cmd.secrets, "token_urlsafe", return_value=CANARY), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                hosting_cmd._cmd_host_login_link(self.validated, {"provisioned": True}, args)
                code = 0
            except SystemExit as stopped:
                code = stopped.code
        return code, out.getvalue(), err.getvalue()

    def _assert_no_leak(self, out, err):
        digest = hashlib.sha256(CANARY.encode()).hexdigest()
        stored = json.dumps(self.saves) + json.dumps(self.state)
        for channel in (out, err, stored, *self.commands):
            self.assertNotIn(CANARY, channel)
            self.assertNotIn(digest, channel)
        for sent in self.inputs:
            self.assertNotIn(CANARY, sent or "")

    def test_mint_shows_the_link_on_the_terminal_only(self):
        code, out, err = self._run(uses=3)
        self.assertEqual(code, 0, out + err)
        self._assert_no_leak(out, err)
        self.assertEqual(self.terminal.final.count(CANARY), 1)
        self.assertRegex(self.terminal.final.strip(),
                         rf"^https://{re.escape(SITE_HOST)}/s/l-[0-9a-f]{{32}}#{re.escape(CANARY)}$")
        reply = json.loads(out)
        self.assertNotIn("url", reply)
        self.assertEqual((reply["ok"], reply["uses"], reply["link_shown_on"]), (True, 3, "terminal"))
        self.assertRegex(reply["link_id"], r"^l-[0-9a-f]{32}$")
        self.assertIn(reply["link_id"], self.terminal.final)
        payload = json.loads(self.inputs[0])
        self.assertIsNone(payload["stack"])
        record = payload["link"]
        self.assertEqual((record["schema"], record["scope"], record["kind"]),
                         ("sandbox.share-link.v2", "/", "site"))
        self.assertEqual(record["token_sha256"], hashlib.sha256(CANARY.encode()).hexdigest())
        self.assertEqual((record["uses"], record["expires_at"] - record["issued_at"]), (3, 900))

    def test_human_mint_output_carries_no_link(self):
        code, out, err = self._run(json=False)
        self.assertEqual(code, 0, out + err)
        self._assert_no_leak(out, err)
        self.assertIn("Share link l-", out)
        self.assertEqual(self.terminal.final.count(CANARY), 1)

    def test_no_terminal_refuses_before_minting(self):
        code, out, _ = self._run(terminal=False)
        self.assertEqual((code, json.loads(out)["code"]), (1, "terminal_required"))
        self.assertEqual(self.commands, [])

    def test_uses_bounds(self):
        for uses in (0, 21, -1, True):
            with self.subTest(uses=uses):
                code, out, _ = self._run(uses=uses)
                self.assertEqual((code, json.loads(out)["code"]), (1, "uses_out_of_range"))
                self.assertEqual(self.commands, [])

    def test_site_options_belong_to_site_gate_entries(self):
        for fields in ({"uses": 2}, {"list_links": True}, {"revoke": "all"}):
            with self.subTest(fields=fields):
                code, out, _ = self._run(service="inbox", **fields)
                self.assertEqual((code, json.loads(out)["code"]), (1, "site_gate_only"))

    def test_conflicting_options(self):
        for fields in ({"email": "person@example.test"}, {"save_email": True},
                       {"list_links": True, "revoke": "all"}, {"list_links": True, "uses": 2},
                       {"revoke": "all", "ttl_seconds": 600}):
            with self.subTest(fields=fields):
                code, out, _ = self._run(**fields)
                self.assertEqual((code, json.loads(out)["code"]), (1, "site_options_conflict"))
                self.assertEqual(self.commands, [])

    def test_list_reports_ids_and_counts(self):
        code, out, _ = self._run(list_links=True)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["links"][0]["id"], LINK_ID)
        self.assertIn("site list mockup", self.commands[0])
        self.assertEqual(self.inputs, [None])

    def test_revoke_runs_under_the_writer_lock(self):
        code, out, _ = self._run(revoke=LINK_ID)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["revoked_sessions"], 2)
        self.assertIn(f"site revoke mockup {LINK_ID}", self.commands[0])
        # A 075-length id is no longer a valid target.
        code, out, _ = self._run(revoke="l-000000000001")
        self.assertEqual((code, json.loads(out)["code"]), (1, "site_link_unknown"))

    def test_revoke_target_is_checked_before_the_remote(self):
        code, out, _ = self._run(revoke="x; rm -rf /")
        self.assertEqual((code, json.loads(out)["code"]), (1, "site_link_unknown"))
        self.assertEqual(self.commands, [])

    def test_service_refusals_pass_through(self):
        self.replies["list"] = json.dumps({"ok": False, "code": "site_entry_unknown"})
        code, out, _ = self._run(list_links=True)
        self.assertEqual((code, json.loads(out)["code"]), (1, "site_entry_unknown"))
        self.replies["mint"] = "Traceback (most recent call last)"
        code, out, _ = self._run()
        self.assertEqual((code, json.loads(out)["code"]), (1, "login_link_failed"))
        self.assertEqual(self.terminal.final, "")

    def test_a_busy_target_is_a_named_failure(self):
        code, out, err = self._run(lock_error=TimeoutError())
        self.assertEqual((code, json.loads(out)["code"]), (1, "login_link_failed"))
        self._assert_no_leak(out, err)

    def test_expired_declaration_mints_nothing(self):
        self.validated["login_service"]["enabled_until"] = "2000-01-01"
        code, out, _ = self._run()
        self.assertEqual((code, json.loads(out)["code"]), (1, "login_service_expired"))
        self.assertEqual(self.commands, [])

    def test_site_command_argv_holds_no_secret(self):
        command = login.site_command("docker compose", "mint", "mockup")
        self.assertTrue(command.startswith("docker compose exec -T sandbox-login python3 -c "))
        self.assertEqual(shlex.split(command)[-3:], ["site", "mint", "mockup"])


if __name__ == "__main__":
    unittest.main()
