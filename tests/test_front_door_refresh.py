"""Spec 084: the front-door-only refresh of an existing hosted target.

Real manifests, renderers, recovery repository and locks; only the remote (route file,
reload, certificate checks, verification) is replaced, in memory.
"""
from __future__ import annotations

import copy
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp" / "wp-server"))

import sandbox.commands.hosting as hosting_cmd  # noqa: E402
import sandbox.core._hosting as hosting  # noqa: E402
from sandbox.hosting.front_door import nginx  # noqa: E402
from sandbox.hosting.front_door import refresh  # noqa: E402
from tests.test_hosting import _HostingOwnerFixture, _manifest, _public_acme_manifest  # noqa: E402
from tests.test_share_private_rollout import GATE, TAG, _private_manifest  # noqa: E402

ROUTE = "sandbox-host-example-site-production"
PORT = 18123
PASSWORD = "fixture-basic-auth-password"


def _with_basic_auth(manifest: str) -> str:
    return manifest.replace(
        "    healthcheck:\n",
        "    basic_auth:\n      username: reviewer\n      password_secret: FIXTURE_BASIC_AUTH\n"
        "    healthcheck:\n", 1)


class _Refresh:
    """A delivered target on a Caddy (default) or nginx front door, remote in memory."""

    def __init__(self, test, manifest=None, *, front_door="caddy", previous=None, record=None):
        self.test = test
        self.fx = _HostingOwnerFixture(test, manifest or _public_acme_manifest())
        self.validated = self.fx.validated
        self.entry = self.fx.entry
        if front_door != "caddy":
            self.entry["front_door"] = front_door
        self.key = self.fx.key
        self.state = {"version": 1, "hosts": {self.key: record if record is not None else self.delivered()}}
        self.saves = []
        patcher = self.fx.patch
        patcher.object(hosting_cmd.hosting, "load_host_state", side_effect=lambda: copy.deepcopy(self.state))
        patcher.object(hosting_cmd.hosting, "save_host_state", side_effect=self._save)
        secrets = {"FIXTURE_BASIC_AUTH": PASSWORD} if self.validated.get("basic_auth") else {}
        patcher.object(hosting_cmd, "_secret_status", return_value=(secrets, []))
        patcher.object(hosting_cmd, "_remote_basic_auth_hash", return_value="$2a$14$" + "h" * 53)
        patcher.object(hosting_cmd, "_controller_lease_owner", return_value="controller-fixture")
        self.ssh = patcher.object(hosting_cmd.remote, "ssh_run",
                                  return_value=types.SimpleNamespace(returncode=0, stdout="", stderr=""))
        self.live = {"route": previous if previous is not None else self.old_route()}
        self.read_error = None
        if front_door == "caddy":
            patcher.object(hosting_cmd, "_read_remote_strict", side_effect=self._read)
            self.configure = self.fx.configure_edge
            self.configure.side_effect = lambda entry, name, content, previous=None, **kw: self._set(content)
            # The refresh restores through the forced re-install (spec 081 closure path).
            self.restore = patcher.object(
                hosting_cmd, "_reconcile_host_caddy",
                side_effect=lambda entry, name, keep, **kw: self._set(keep))
        else:
            patcher.object(nginx, "preflight", return_value={})
            patcher.object(nginx, "features", return_value={})
            patcher.object(nginx, "read_route_strict", side_effect=lambda entry, name: self._read(entry, name))
            self.configure = patcher.object(
                nginx, "apply_route", side_effect=lambda entry, name, content, **kw: self._set(content))
            self.restore = patcher.object(
                nginx, "reconcile_route", side_effect=lambda entry, name, keep, **kw: self._set(keep))
            self.public_cert = patcher.object(nginx, "public_certificate_present", return_value=True)
            self.origin_cert = patcher.object(nginx, "origin_certificate_present", return_value=True)
        self.verify = self.fx.edge
        self.compose = self.fx.compose
        self.push = self.fx.push
        self.update = self.fx.update

    def _save(self, state):
        self.state = copy.deepcopy(state)
        self.saves.append(copy.deepcopy(state))

    def _read(self, entry, path):
        if self.read_error is not None:
            raise self.read_error
        return self.live["route"]

    def _set(self, content):
        self.live["route"] = content
        return {"state": "changed"}

    def delivered(self, **extra):
        plan = hosting.desired_plan(self.validated, self.fx.entry["origin_ipv4"])
        records = [{"zone_id": "zone-1", "previous": None, "created_id": f"record-{index}",
                    "applied": {"name": item["hostname"], "type": "A", "content": item["address"],
                                "proxied": item["proxied"], "ttl": 1, "comment": None}}
                   for index, item in enumerate(plan["records"])]
        record = {"loopback_port": PORT, "caddy_name": ROUTE, "records": records,
                  "certificate": None, "edge": {"state": "pending"},
                  "runtime": {"state": "unverified"}, "staged_revision": "a" * 40,
                  "hosting_operation": {"request_id": "refused-attempt", "digest": "sha256:" + "e" * 64}}
        record.update(extra)
        return record

    def render(self, validated, door="caddy"):
        if door == "caddy":
            basic = "$2a$14$" + "h" * 53 if validated.get("basic_auth") else None
            return hosting.caddyfile(validated, PORT, None, None, basic)
        return nginx.render_host_conf(validated, PORT, name=ROUTE.removeprefix("sandbox-"),
                                      cert_path=nginx.public_paths(ROUTE.removeprefix("sandbox-"))[0],
                                      key_path=nginx.public_paths(ROUTE.removeprefix("sandbox-"))[1],
                                      basic_auth_password=PASSWORD if validated.get("basic_auth") else None)

    def old_route(self):
        """A route rendered before spec 081's default: no noindex header."""
        door = self.entry.get("front_door") or "caddy"
        return self.render({**self.validated, "indexing": "public"}, door)

    def run(self, confirm=True):
        return hosting_cmd._refresh_host_front_door(
            self.validated, self.entry, self.fx.remote_name, confirm=confirm)

    def refused(self, code, confirm=True):
        with self.test.assertRaises(refresh.FrontDoorRefreshRefused) as caught:
            self.run(confirm=confirm)
        self.test.assertEqual(caught.exception.code, code, caught.exception.message)
        return caught.exception

    def audit(self):
        path = refresh.audit_path(self.fx.home / "runtime")
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def assert_no_delivery_effect(self):
        self.compose.assert_not_called()
        self.push.assert_not_called()
        self.update.assert_not_called()
        self.fx.client.upsert_address.assert_not_called()
        self.fx.client.restore_record.assert_not_called()
        self.fx.client.create_origin_certificate.assert_not_called()
        self.fx.client.ssl_mode.assert_not_called()


class RefreshPublicTargetTests(unittest.TestCase):
    def test_the_plan_reads_only_and_reports_a_legacy_binding(self):
        target = _Refresh(self)
        before = copy.deepcopy(target.state)
        result = target.run(confirm=False)
        self.assertEqual(result["action"], "plan")
        self.assertTrue(result["legacy_binding"])
        self.assertTrue(result["would_reload"])
        target.configure.assert_not_called()
        target.verify.assert_not_called()
        self.assertEqual(target.state, before)
        self.assertEqual(target.audit(), [])
        target.fx.lease.assert_not_called()

    def test_a_refresh_renders_the_noindex_header_and_touches_nothing_else(self):
        target = _Refresh(self)
        self.assertNotIn("X-Robots-Tag", target.live["route"])
        result = target.run()
        self.assertEqual(result["action"], "refreshed")
        self.assertIn("X-Robots-Tag", target.live["route"])
        self.assertIn(f"127.0.0.1:{PORT}", target.live["route"])
        target.configure.assert_called_once()
        target.verify.assert_called_once()
        target.fx.lease.assert_called_once()
        target.assert_no_delivery_effect()
        record = target.state["hosts"][target.key]
        self.assertEqual(record["front_door_refresh"]["state"], "refreshed")
        self.assertEqual(record["front_door_declaration"], refresh.declaration_digest(target.validated))
        # The delivery fence and the recorded attempt stay exactly as they were.
        self.assertEqual(record["hosting_operation"]["request_id"], "refused-attempt")
        self.assertEqual(record["edge"], {"state": "pending"})
        self.assertEqual(record["staged_revision"], "a" * 40)
        lines = target.audit()
        self.assertEqual([line["op"] for line in lines], ["start", "end"])
        self.assertEqual(lines[1]["outcome"], "refreshed")
        self.assertEqual(lines[0]["route_after"], result["route_after"])

    def test_a_second_refresh_is_bound_by_the_recorded_declaration(self):
        target = _Refresh(self)
        target.run()
        result = target.run()
        self.assertEqual(result["action"], "unchanged")
        self.assertFalse(result["legacy_binding"])
        target.configure.assert_called_once()
        self.assertEqual(target.verify.call_count, 2)

    def test_an_identical_route_is_verified_but_not_reloaded(self):
        target = _Refresh(self)
        target.live["route"] = target.render(target.validated)
        self.assertEqual(target.run()["action"], "unchanged")
        target.configure.assert_not_called()
        target.verify.assert_called_once()

    def test_a_failed_verification_puts_the_previous_route_back(self):
        target = _Refresh(self)
        previous = target.live["route"]
        target.verify.side_effect = RuntimeError("primary answered 502")
        error = target.refused(refresh.FAILED)
        self.assertEqual(error.restore, "restored")
        self.assertEqual(target.live["route"], previous)
        self.assertIn("502", error.message)
        record = target.state["hosts"][target.key]
        self.assertEqual(record["front_door_refresh"]["state"], "failed")
        self.assertNotIn("front_door_declaration", record)
        self.assertEqual(target.audit()[-1]["restore"], "restored")
        target.assert_no_delivery_effect()

    def test_a_failed_restore_is_reported(self):
        target = _Refresh(self)
        target.verify.side_effect = RuntimeError("primary answered 502")
        target.restore.side_effect = RuntimeError("reload refused")
        error = target.refused(refresh.FAILED)
        self.assertEqual(error.restore, "restore_failed")
        self.assertIn("restore failed", error.message)

    def test_an_audit_failure_never_hides_a_failed_restore(self):
        target = _Refresh(self)
        target.verify.side_effect = RuntimeError("primary answered 502")
        target.restore.side_effect = RuntimeError("reload refused")
        real_write = refresh.AuditLog.write

        def failing_end(audit, **fields):
            if fields.get("op") == "end":
                raise OSError(28, "No space left on device")
            return real_write(audit, **fields)

        with patch.object(refresh.AuditLog, "write", failing_end):
            error = target.refused(refresh.FAILED)
        self.assertEqual(error.restore, "restore_failed")
        self.assertIn("not audited", error.message)
        self.assertEqual(target.state["hosts"][target.key]["front_door_refresh"]["restore"],
                         "restore_failed")

    def test_an_outcome_that_cannot_be_recorded_rolls_the_route_back(self):
        target = _Refresh(self)
        previous = target.live["route"]
        real_record = hosting_cmd._record_front_door_refresh

        def failing_success(key, fields, **kwargs):
            if fields["state"] == "refreshed":
                raise OSError(5, "Input/output error")
            return real_record(key, fields, **kwargs)

        with patch.object(hosting_cmd, "_record_front_door_refresh", side_effect=failing_success):
            error = target.refused(refresh.FAILED)
        self.assertEqual(error.restore, "restored")
        self.assertEqual(target.live["route"], previous)
        self.assertEqual(target.audit()[-1]["outcome"], "failed")

    def test_an_unsupported_front_door_refuses_before_lock_lease_or_audit(self):
        target = _Refresh(self)
        target.entry["front_door"] = "unknown-door"
        with self.assertRaises(refresh.FrontDoorRefreshRefused):
            target.run()
        target.fx.lease.assert_not_called()
        self.assertEqual(target.audit(), [])

    def test_a_restore_that_does_not_read_back_is_reported(self):
        target = _Refresh(self)
        target.verify.side_effect = RuntimeError("primary answered 502")
        target.restore.side_effect = lambda entry, name, keep, **kw: target._set(keep + "# drift\n")
        error = target.refused(refresh.FAILED)
        self.assertEqual(error.restore, "restore_failed")

    def test_an_audit_log_close_error_never_escapes(self):
        import os
        audit = refresh.AuditLog(refresh.audit_path(_Refresh(self).fx.home / "runtime"))
        audit.write(op="start", outcome="started")
        os.close(audit._fd)  # the descriptor is gone: close() now gets EBADF
        audit.close()
        self.assertIsNone(audit._fd)

    def _short_audit_write(self, op):
        import os
        real_write, real_os_write = refresh.AuditLog.write, os.write

        def short(fd, data):
            if data == b"\n":
                return real_os_write(fd, data)
            return real_os_write(fd, data[:len(data) // 2])

        def write(audit, **fields):
            if fields.get("op") == op and fields.get("outcome") != "failed":
                with patch.object(refresh.os, "write", short):
                    return real_write(audit, **fields)
            return real_write(audit, **fields)

        return patch.object(refresh.AuditLog, "write", write)

    def test_a_short_audit_write_is_a_failure(self):
        import os
        audit = refresh.AuditLog(refresh.audit_path(_Refresh(self).fx.home / "runtime"))
        self.addCleanup(audit.close)
        real_os_write = os.write
        with patch.object(refresh.os, "write", lambda fd, data: real_os_write(fd, data[:5])):
            with self.assertRaises(OSError):
                audit.write(op="start", outcome="started")

    def test_a_short_start_line_refuses_before_any_reload(self):
        target = _Refresh(self)
        with self._short_audit_write("start"), self.assertRaises(refresh.FrontDoorRefreshRefused):
            target.run()
        target.configure.assert_not_called()

    def test_a_short_success_line_rolls_the_route_back(self):
        target = _Refresh(self)
        previous = target.live["route"]
        with self._short_audit_write("end"):
            error = target.refused(refresh.FAILED)
        self.assertEqual(error.restore, "restored")
        self.assertEqual(target.live["route"], previous)
        # The partial line stands alone; the failure record after it is still readable.
        lines = refresh.audit_path(target.fx.home / "runtime").read_text().splitlines()
        self.assertEqual(len(lines), 3)
        with self.assertRaises(ValueError):
            json.loads(lines[1])
        failed = json.loads(lines[2])
        self.assertEqual((failed["outcome"], failed["restore"]), ("failed", "restored"))

    def test_a_re_run_after_an_interrupted_refresh_converges(self):
        target = _Refresh(self)
        target.state["hosts"][target.key]["front_door_refresh"] = {"state": "started", "at": 1}
        self.assertEqual(target.run()["action"], "refreshed")
        self.assertEqual(target.state["hosts"][target.key]["front_door_refresh"]["state"], "refreshed")

    def test_basic_auth_is_kept_and_verified_with_its_credentials(self):
        target = _Refresh(self, _with_basic_auth(_public_acme_manifest()))
        target.run()
        self.assertTrue(refresh.has_basic_auth(target.live["route"]))
        kwargs = target.verify.call_args.kwargs
        self.assertEqual(kwargs["basic_auth_credentials"], ("reviewer", PASSWORD))
        self.assertNotIn(PASSWORD, json.dumps(target.audit()))
        self.assertNotIn(PASSWORD, json.dumps(target.state))

    def test_a_secret_never_reaches_a_failure_message(self):
        target = _Refresh(self, _with_basic_auth(_public_acme_manifest()))
        target.verify.side_effect = RuntimeError(f"probe sent {PASSWORD}")
        error = target.refused(refresh.FAILED)
        self.assertNotIn(PASSWORD, error.message)


class RefreshRefusalTests(unittest.TestCase):
    def assert_refused_without_change(self, target, code, *, part=None):
        route = target.live["route"]
        error = target.refused(code)
        if part is not None:
            self.assertEqual(error.part, part)
        target.configure.assert_not_called()
        self.assertEqual(target.live["route"], route)
        target.assert_no_delivery_effect()
        self.assertTrue(all(line["op"] == "end" and line["outcome"] == "refused"
                            for line in target.audit()))
        return error

    def test_another_owner_fences_the_target(self):
        for field, value in (("active_operation", {"action": "destroy"}),
                             ("recovery_uncertainty", {"state": "unknown"}),
                             ("image_activation", {"active": {"id": "x"}})):
            with self.subTest(field=field):
                target = _Refresh(self)
                target.state["hosts"][target.key][field] = value
                self.assert_refused_without_change(target, refresh.FENCED)

    def test_a_held_lease_refuses(self):
        target = _Refresh(self)
        target.fx.lease.side_effect = hosting.HostingError("host_apply_lease_held: held by another controller")
        self.assert_refused_without_change(target, "host_apply_lease_held")

    def test_no_successful_delivery_or_no_route_refuses(self):
        for change in ({"loopback_port": None}, {"caddy_name": "sandbox-host-other"}):
            with self.subTest(change=change):
                target = _Refresh(self)
                target.state["hosts"][target.key].update(change)
                self.assert_refused_without_change(target, refresh.NO_ROUTE)
        target = _Refresh(self)
        target.live["route"] = None
        self.assert_refused_without_change(target, refresh.NO_ROUTE)
        target = _Refresh(self)
        target.read_error = hosting.HostingError("route state unknown: exit 255")
        self.assert_refused_without_change(target, refresh.ROUTE_UNREADABLE)

    def test_a_changed_declaration_refuses_with_its_part(self):
        cases = {
            "front_door": {"front_door": "nginx"},
            "records": {"records": []},
            "declaration": {"front_door_declaration": "sha256:" + "0" * 64},
            "dns_coverage": {"dns_coverage": {"mode": "shared", "zone": "example.test"}},
            "certificate": {"certificate": {"hostnames": ["site.example.test"], "id": None}},
        }
        for part, change in cases.items():
            with self.subTest(part=part):
                target = _Refresh(self)
                target.state["hosts"][target.key].update(change)
                self.assert_refused_without_change(target, refresh.DECLARATION_CHANGED, part=part)

    def test_a_record_whose_proxy_setting_differs_refuses(self):
        target = _Refresh(self)
        target.state["hosts"][target.key]["records"][0]["applied"]["proxied"] = True
        self.assert_refused_without_change(target, refresh.DECLARATION_CHANGED, part="records")

    def test_a_pending_certificate_refuses(self):
        target = _Refresh(self)
        target.state["hosts"][target.key]["certificate"] = {"hostnames": [], "pending": True}
        self.assert_refused_without_change(target, refresh.CERTIFICATE_MISSING)

    def test_a_missing_origin_certificate_refuses_and_nothing_is_issued(self):
        target = _Refresh(self, _manifest())
        target.ssh.return_value = types.SimpleNamespace(returncode=1, stdout="", stderr="")
        self.assert_refused_without_change(target, refresh.CERTIFICATE_MISSING)

    def test_a_missing_basic_auth_secret_refuses(self):
        target = _Refresh(self, _with_basic_auth(_public_acme_manifest()))
        with patch.object(hosting_cmd, "_secret_status", return_value=({}, ["FIXTURE_BASIC_AUTH"])):
            self.assert_refused_without_change(target, refresh.SECRETS_MISSING)

    def test_a_route_serving_other_names_refuses(self):
        target = _Refresh(self)
        target.live["route"] = target.live["route"].replace(
            "www.example.test", "www.example.test, extra.example.test", 1)
        self.assert_refused_without_change(target, refresh.NAMES_CHANGED)

    def test_a_route_proxying_to_another_port_refuses(self):
        target = _Refresh(self)
        target.live["route"] = target.live["route"].replace(f"127.0.0.1:{PORT}", "127.0.0.1:18999")
        self.assert_refused_without_change(target, refresh.UPSTREAM_CHANGED)

    def test_dropping_basic_auth_refuses(self):
        protected = _Refresh(self, _with_basic_auth(_public_acme_manifest()))
        target = _Refresh(self, previous=protected.live["route"])
        self.assert_refused_without_change(target, refresh.AUTH_WEAKENED)

    def test_losing_https_refuses(self):
        with self.assertRaises(refresh.FrontDoorRefreshRefused) as caught:
            refresh.check_no_weakening("a", "a", private=False, marked_private=lambda _t: False,
                                       https_before=True, https_after=False)
        self.assertEqual(caught.exception.code, refresh.HTTPS_LOST)


class RefreshPrivateTargetTests(unittest.TestCase):
    def private(self, **kwargs):
        target = _Refresh(self, _private_manifest(), **kwargs)
        return target

    def test_a_private_target_is_refreshed_private_with_a_fresh_tag(self):
        old = _Refresh(self, _private_manifest())
        previous = old.render({**old.validated, "indexing": "public", "route_tag": TAG})
        self.assertTrue(hosting.is_marked_private_route(previous))
        target = self.private(previous=previous)
        self.assertEqual(target.run()["action"], "refreshed")
        route = target.live["route"]
        self.assertTrue(hosting.is_marked_private_route(route))
        kwargs = target.verify.call_args.kwargs
        self.assertIn(GATE, kwargs["gate_hostnames"])
        self.assertIn("site.example.test", kwargs["private_hostnames"])
        self.assertNotEqual(kwargs["route_tag"], TAG)
        self.assertIn(f'X-Sandbox-Route "{kwargs["route_tag"]}"', route)

    def test_an_unchanged_private_route_keeps_and_verifies_its_tag(self):
        old = _Refresh(self, _private_manifest())
        previous = old.render({**old.validated, "route_tag": TAG})
        target = self.private(previous=previous)
        self.assertEqual(target.run()["action"], "unchanged")
        target.configure.assert_not_called()
        self.assertEqual(target.verify.call_args.kwargs["route_tag"], TAG)

    def test_a_private_target_needs_a_marked_private_route(self):
        public = _Refresh(self, _private_manifest(private=False))
        target = self.private(previous=public.render(public.validated))
        target.refused(refresh.PRIVACY_UNPROVEN)
        target.configure.assert_not_called()

    def test_a_refresh_never_publishes_a_private_target(self):
        private = _Refresh(self, _private_manifest())
        target = _Refresh(self, _private_manifest(private=False),
                          previous=private.render({**private.validated, "route_tag": TAG}))
        target.refused(refresh.WOULD_PUBLISH)
        target.configure.assert_not_called()

    def test_a_proxied_name_refuses_a_private_refresh(self):
        old = _Refresh(self, _private_manifest())
        target = self.private(previous=old.render({**old.validated, "route_tag": TAG}))
        target.fx.client.records.return_value = [{"type": "A", "proxied": True}]
        target.refused("private_proxied_record")
        target.configure.assert_not_called()

    def test_a_failed_private_refresh_restores_the_marked_route(self):
        old = _Refresh(self, _private_manifest())
        previous = old.render({**old.validated, "indexing": "public", "route_tag": TAG})
        target = self.private(previous=previous)
        target.verify.side_effect = RuntimeError("gate answered 502")
        self.assertEqual(target.refused(refresh.FAILED).restore, "restored")
        self.assertEqual(target.live["route"], previous)


class RefreshNginxTests(unittest.TestCase):
    def test_an_nginx_target_is_refreshed_with_its_existing_public_certificate(self):
        target = _Refresh(self, front_door="nginx", record=None)
        target.state["hosts"][target.key]["front_door"] = "nginx"
        target.state["hosts"][target.key]["certificate"] = {
            "hostnames": sorted(route["hostname"] for route in target.validated["routes"]),
            "id": None, "issuer": "public"}
        self.assertEqual(target.run()["action"], "refreshed")
        self.assertIn("X-Robots-Tag", target.live["route"])
        target.configure.assert_called_once()
        target.assert_no_delivery_effect()

    def test_an_nginx_route_that_could_not_be_proven_back_refuses(self):
        target = _Refresh(self, front_door="nginx")
        target.state["hosts"][target.key]["front_door"] = "nginx"
        marker = nginx.route_marker(target.live["route"])
        self.assertTrue(marker)
        target.live["route"] = target.live["route"].replace(marker, "unmarked")
        target.refused(refresh.ROUTE_UNPROVABLE)
        target.configure.assert_not_called()

    def test_an_nginx_route_without_a_covering_certificate_refuses(self):
        target = _Refresh(self, front_door="nginx")
        target.state["hosts"][target.key]["front_door"] = "nginx"
        target.public_cert.return_value = False
        target.refused(refresh.CERTIFICATE_MISSING)
        target.configure.assert_not_called()


class DeclarationRecordedTests(unittest.TestCase):
    def test_a_successful_apply_records_the_front_door_declaration(self):
        fixture = _HostingOwnerFixture(self)
        fixture.apply()
        self.assertEqual(fixture.state["hosts"][fixture.key]["front_door_declaration"],
                         refresh.declaration_digest(fixture.validated))

    def test_the_declaration_ignores_the_basic_auth_password_and_follows_privacy(self):
        public = _HostingOwnerFixture(self, _private_manifest(private=False)).validated
        private = _HostingOwnerFixture(self, _private_manifest()).validated
        self.assertNotEqual(refresh.declaration_digest(public), refresh.declaration_digest(private))
        with_password = {**public, "basic_auth": {"username": "u", "password_secret": "S", "password": "x"}}
        without = {**public, "basic_auth": {"username": "u", "password_secret": "S"}}
        self.assertEqual(refresh.declaration_digest(with_password), refresh.declaration_digest(without))


class RefreshEntryPointTests(unittest.TestCase):
    def test_the_mcp_tool_answers_with_a_code_and_never_raises(self):
        import tools.share as share_tools
        result = share_tools.host_front_door_refresh("", project_dir=".")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], refresh.UNAVAILABLE)
        with patch.object(hosting_cmd, "_refresh_host_front_door", side_effect=OSError("boom")), \
                patch.object(hosting_cmd.hosting, "validate_manifest", return_value={}), \
                patch.object(hosting_cmd.remote, "get_remote", return_value={"ssh": "x"}):
            result = share_tools.host_front_door_refresh("dev", project_dir=".")
        self.assertEqual(result, {"ok": False, "code": refresh.UNAVAILABLE, "error": "boom"})

    def test_the_mcp_manifest_names_the_tool(self):
        from tools import manifest
        self.assertIn("host_front_door_refresh", manifest.BUILTIN_TOOL_NAMES["share"])

    def test_the_cli_accepts_the_action(self):
        import sandbox.cli as cli
        source = Path(cli.__file__).read_text()
        self.assertIn('"refresh-front-door"', source)


class RouteFeatureTests(unittest.TestCase):
    def test_served_names_read_both_front_doors(self):
        caddy = "a.example.test, b.example.test {\n    respond 404\n}\nhttp://c.example.test:80 {\n}\n"
        self.assertEqual(refresh.served_names(caddy),
                         {"a.example.test", "b.example.test", "c.example.test"})
        conf = "server {\n    listen 443 ssl;\n    server_name a.example.test b.example.test;\n}\n"
        self.assertEqual(refresh.served_names(conf), {"a.example.test", "b.example.test"})

    def test_route_tag_of_reads_both_front_doors(self):
        self.assertEqual(refresh.route_tag_of(f'        X-Sandbox-Route "{TAG}"\n'), TAG)
        self.assertEqual(refresh.route_tag_of(f'    add_header X-Sandbox-Route "{TAG}" always;\n'), TAG)
        self.assertIsNone(refresh.route_tag_of("respond 404"))


if __name__ == "__main__":
    unittest.main()
