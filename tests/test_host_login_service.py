"""Declaring, rendering, and issuing links for the Sandbox login service."""
import base64
import contextlib
import copy
import datetime as dt
import hashlib
import io
import json
import re
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
        return result

    def _record(self):
        payload = re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \| base64 -d", self.commands[0]).group(1)
        return json.loads(base64.b64decode(payload))

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
        decoded = base64.b64decode(re.search(
            r"printf %s '?([A-Za-z0-9+/=]+)'? \| base64 -d", self.commands[0]).group(1)).decode()
        self.assertNotIn(token, decoded)

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


if __name__ == "__main__":
    unittest.main()
