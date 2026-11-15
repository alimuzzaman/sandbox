"""`cloudflare.external_dns`: hostnames whose DNS is managed outside Sandbox."""
from __future__ import annotations

import contextlib
import copy
import io
import unittest
from unittest.mock import MagicMock, patch

import sandbox.core._hosting as hosting
from sandbox.commands import hosting as hosting_cmd
from sandbox.hosting import destroy, stack_template
from sandbox.hosting.stack_template import StackRef, StackTemplateError
from sandbox.hosting.zone_coverage import targets
from tests.test_host_share import PRIMARY, _environment, _share, _validate
from tests.test_host_stack import COMMIT, TEMPLATE, _yaml
from tests.test_hosting import _HostingOwnerFixture, _public_acme_manifest

ALIAS = "www.example.test"
ORIGIN = "203.0.113.10"
# The fixture's primary hostname, the IDNA form of আমারসোনার.বাংলা.
FIXTURE_PRIMARY = hosting.normalize_hostname("আমারসোনার.বাংলা", wildcard=False)
FOREIGN_CNAME = {"id": "foreign-cname", "type": "CNAME", "name": ALIAS,
                 "content": "edge.cdn.example.net", "proxied": False, "ttl": 1}


def _external(environment, names):
    environment = copy.deepcopy(environment)
    environment["cloudflare"]["external_dns"] = names
    return environment


def _external_manifest(names=(ALIAS,)):
    listed = "".join(f"        - {name}\n" for name in names)
    return _public_acme_manifest().replace(
        "      tls: acme\n", "      tls: acme\n      external_dns:\n" + listed)


class ExternalDnsValidationTests(unittest.TestCase):
    def refused(self, environment, needle):
        with self.assertRaises(hosting.HostingError) as raised:
            _validate(environment)
        self.assertIn(needle, str(raised.exception))

    def test_absent_field_stays_absent(self):
        validated = _validate(_environment())
        self.assertNotIn("external_dns", validated["cloudflare"])
        plan = hosting.desired_plan(validated, ORIGIN)
        self.assertNotIn("external_dns", plan)
        self.assertEqual({row["hostname"] for row in plan["records"]},
                         {route["hostname"] for route in validated["routes"]})

    def test_declared_names_are_normalized_and_left_out_of_the_plan(self):
        validated = _validate(_external(_environment(), [PRIMARY.upper() + "."]))
        self.assertEqual(validated["cloudflare"]["external_dns"], [PRIMARY])
        plan = hosting.desired_plan(validated, ORIGIN, "2001:db8::10")
        self.assertEqual(plan["external_dns"], [PRIMARY])
        self.assertNotIn(PRIMARY, {row["hostname"] for row in plan["records"]})
        self.assertEqual(len(plan["records"]), 2 * (len(validated["routes"]) - 1))
        # The route, certificate and health are unchanged for the external name.
        self.assertIn(PRIMARY, {route["hostname"] for route in plan["routes"]})
        self.assertIn(PRIMARY, plan["certificate_hostnames"])

    def test_empty_list_is_accepted_and_manages_every_name(self):
        validated = _validate(_external(_environment(), []))
        self.assertEqual(validated["cloudflare"]["external_dns"], [])
        self.assertNotIn("external_dns", hosting.desired_plan(validated, ORIGIN))

    def test_names_not_among_the_routes_are_refused(self):
        self.refused(_external(_environment(), ["other.dev.example.test"]),
                     "cloudflare.external_dns lists other.dev.example.test, which is not a declared host route")

    def test_wildcards_duplicates_and_non_lists_are_refused(self):
        self.refused(_external(_environment(), ["*.dev.example.test"]), "no wildcard")
        self.refused(_external(_environment(), [PRIMARY, PRIMARY.upper()]), f"lists {PRIMARY} twice")
        for value in (PRIMARY, None, [PRIMARY, 3], {"name": PRIMARY}):
            with self.subTest(value=value):
                self.refused(_external(_environment(), value),
                             "cloudflare.external_dns must be a list of declared hostnames")

    def test_proxied_hosting_is_refused(self):
        environment = _external(_environment(), [PRIMARY])
        environment["cloudflare"] = {"proxied": True, "tls": "origin-ca", "ssl_mode": "strict",
                                     "external_dns": [PRIMARY]}
        self.refused(environment, "cloudflare.external_dns requires DNS-only hosting")

    def test_private_share_is_refused(self):
        self.refused(_external(_environment(_share()), [PRIMARY]),
                     "share.private cannot be combined with cloudflare.external_dns")

    def test_edge_intent_carries_the_external_names(self):
        validated = _validate(_external(_environment(), [PRIMARY]))
        intent = hosting_cmd._desired_edge_intent(validated, {"origin_ipv4": ORIGIN})
        self.assertEqual(intent["external_dns"], [PRIMARY])
        self.assertNotIn(PRIMARY, {row["hostname"] for row in intent["records"]})
        plain = hosting_cmd._desired_edge_intent(_validate(_environment()), {"origin_ipv4": ORIGIN})
        self.assertNotIn("external_dns", plain)

    def test_plan_text_names_the_unmanaged_hostnames(self):
        validated = _validate(_external(_environment(), [PRIMARY]))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            hosting_cmd._emit(hosting.desired_plan(validated, ORIGIN), False)
        self.assertIn(f"DNS managed outside Sandbox (never written): {PRIMARY}", output.getvalue())


class ExternalDnsCoverageTests(unittest.TestCase):
    def test_all_external_target_makes_no_coverage_decision(self):
        validated = _validate(_external(_environment(), [
            route["hostname"] for route in _validate(_environment())["routes"]]))
        store = MagicMock()
        decision = targets.decide(validated, {"name": "r1", "front_door": "nginx"}, None,
                                  store_client=store, cf_client=MagicMock())
        self.assertEqual(decision, {"mode": "exact"})
        store.list.assert_not_called()

    def test_partly_external_target_still_reads_the_store(self):
        validated = _validate(_external(_environment(), [PRIMARY]))
        store = MagicMock()
        store.list.return_value = {"zones": []}
        decision = targets.decide(validated, {"name": "r1", "front_door": "nginx"}, None,
                                  store_client=store, cf_client=MagicMock())
        self.assertEqual(decision, {"mode": "exact"})
        store.list.assert_called_once()


class ExternalDnsApplyTests(unittest.TestCase):
    def fixture(self, manifest):
        fixture = _HostingOwnerFixture(self, manifest)
        fixture.client.upsert_address.side_effect = lambda _zone, hostname, address, proxied=False: {
            "id": f"record-{hostname}", "name": hostname, "type": "A", "content": address,
            "proxied": proxied, "ttl": 60, "comment": "managed by Sandbox hosting"}
        return fixture

    def test_apply_leaves_a_foreign_cname_on_an_external_host(self):
        fixture = self.fixture(_external_manifest())
        self.assertEqual({row["hostname"] for row in fixture.runtime["records"]}, {FIXTURE_PRIMARY})
        fixture.client.records.side_effect = lambda _zone, hostname: (
            [dict(FOREIGN_CNAME)] if hostname == ALIAS else [])
        # A record an earlier apply wrote for the alias, since deleted by its new owner.
        fixture.state["hosts"][fixture.key] = {"records": [{
            "zone_id": "zone-1", "previous": None, "created_id": "gone-a",
            "applied": {"name": ALIAS, "type": "A", "content": ORIGIN, "proxied": False,
                        "ttl": 60, "comment": "managed by Sandbox hosting"}}]}
        fixture.client.record.return_value = None
        fixture.apply()
        for call in fixture.client.records.call_args_list:
            self.assertNotEqual(call.args[1], ALIAS)
        self.assertEqual([call.args[1] for call in fixture.client.upsert_address.call_args_list],
                         [FIXTURE_PRIMARY])
        fixture.client.update_record.assert_not_called()
        fixture.client.delete_record.assert_not_called()
        fixture.client.restore_record.assert_not_called()
        saved = fixture.repository.load()["hosts"][fixture.key]
        self.assertEqual(saved["dns_external"], [ALIAS])
        self.assertEqual([row["applied"]["name"] for row in saved["records"]], [FIXTURE_PRIMARY])
        self.assertIn(ALIAS, saved["hostnames"])

    def test_other_hosts_keep_the_conflicting_cname_refusal(self):
        fixture = self.fixture(_external_manifest())
        foreign = dict(FOREIGN_CNAME, name=FIXTURE_PRIMARY)
        fixture.client.records.side_effect = lambda _zone, hostname: (
            [foreign] if hostname == FIXTURE_PRIMARY else [])
        with self.assertRaisesRegex(Exception, f"declared hostname {FIXTURE_PRIMARY} has a conflicting CNAME"):
            fixture.apply()
        fixture.client.upsert_address.assert_not_called()

    def test_without_the_field_the_cname_is_still_refused(self):
        fixture = self.fixture(_public_acme_manifest())
        fixture.client.records.side_effect = lambda _zone, hostname: (
            [dict(FOREIGN_CNAME)] if hostname == ALIAS else [])
        with self.assertRaisesRegex(Exception, f"declared hostname {ALIAS} has a conflicting CNAME"):
            fixture.apply()

    def test_removing_the_field_clears_the_recorded_names(self):
        fixture = self.fixture(_public_acme_manifest())
        fixture.state["hosts"][fixture.key] = {"dns_external": [ALIAS]}
        fixture.apply()
        self.assertNotIn("dns_external", fixture.repository.load()["hosts"][fixture.key])


def _row(name, record_id, previous=None):
    return {"zone_id": "zone-1", "previous": previous, "created_id": record_id,
            "applied": {"name": name, "type": "A", "content": ORIGIN, "proxied": False,
                        "ttl": 60, "comment": "managed by Sandbox hosting"}}


class ExternalDnsTeardownTests(unittest.TestCase):
    PROBE = {"containers": 0, "volumes": 0, "networks": 0, "images": 0,
             "runtime_kb": 0, "source_kb": 0, "route": 0, "certs": 0}
    ENTRY = {"name": "r1", "host": "h", "user": "u", "front_door": "nginx"}

    def state(self, **record):
        return {"hosts": {"r1/site/sandbox": {
            "compose_project": "sandbox-host-site-sandbox", "front_door": "nginx", **record}}}

    def plan(self, state):
        with patch.object(destroy.remote, "resolve_sandbox_home", return_value="/home/u/sandbox"):
            return destroy.plan(state, self.ENTRY, "r1", "site", "sandbox", probe=self.PROBE)

    def test_destroy_never_touches_an_external_name(self):
        state = self.state(dns_external=[ALIAS.upper() + "."],
                           records=[_row("app.example.test", "own-a"),
                                    _row(ALIAS, "old-a", previous={"id": "old-a", "type": "A"})])
        planned = self.plan(state)
        self.assertEqual([row["record_id"] for row in planned["dns_records"]], ["own-a"])
        self.assertEqual(planned["dns_external"], [ALIAS])
        rendered = destroy.render(planned, confirmed=False)
        self.assertIn(f"DNS for {ALIAS} is managed outside Sandbox; left untouched", rendered)
        client = MagicMock()
        client.record.side_effect = lambda _zone, record_id: dict(
            _row("app.example.test", record_id)["applied"], id=record_id)
        found = {"_records": destroy._target_dns_records(state["hosts"]["r1/site/sandbox"])}
        results = destroy._remove_cloudflare(found, client)
        self.assertEqual(results, [{"zone_id": "zone-1", "state": "removed"}])
        client.restore_record.assert_called_once_with("zone-1", None, "own-a")
        for call in client.record.call_args_list:
            self.assertNotEqual(call.args[1], "old-a")

    def test_a_managed_record_already_gone_does_not_fail_destroy(self):
        state = self.state(records=[_row("app.example.test", "gone-a")])
        client = MagicMock()
        client.record.return_value = None
        found = {"_records": destroy._target_dns_records(state["hosts"]["r1/site/sandbox"])}
        self.assertEqual(destroy._remove_cloudflare(found, client),
                         [{"zone_id": "zone-1", "state": "already_absent", "record_id": "gone-a"}])
        client.restore_record.assert_not_called()

    def test_without_external_names_every_row_is_planned(self):
        planned = self.plan(self.state(records=[_row("app.example.test", "own-a"), _row(ALIAS, "old-a")]))
        self.assertEqual([row["record_id"] for row in planned["dns_records"]], ["own-a", "old-a"])
        self.assertNotIn("dns_external", planned)


class ExternalDnsStackTemplateTests(unittest.TestCase):
    def test_template_entries_take_placeholders(self):
        document = _yaml(TEMPLATE)
        env = document["environments"]["sandbox"]
        env["cloudflare"] = {"proxied": False, "tls": "acme",
                             "external_dns": ["admin-{slug}.example.test"]}
        template = stack_template.parse_template(document)
        rendered = stack_template.render(template, slug="demo", ref=StackRef("dev", COMMIT, "dev"),
                                         ports={"mailpit": 18142}, source_root="/fixture",
                                         template_path="/template.yml")
        self.assertEqual(rendered["environments"]["sandbox"]["cloudflare"]["external_dns"],
                         ["admin-demo.example.test"])

    def test_other_cloudflare_fields_still_refuse_placeholders(self):
        document = _yaml(TEMPLATE)
        document["environments"]["sandbox"]["cloudflare"]["tls"] = "{slug}"
        with self.assertRaisesRegex(StackTemplateError, "cloudflare.tls"):
            stack_template.parse_template(document)


if __name__ == "__main__":
    unittest.main()
