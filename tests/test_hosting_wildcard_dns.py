"""`cloudflare.dns: wildcard`: DNS-only ACME targets share one `*.<zone>` record and certificate."""
from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import sandbox.core._hosting as hosting
from sandbox.commands import hosting as hosting_cmd
from sandbox.hosting.front_door import FrontDoorError, exact_read, wildcard_cert
from sandbox.hosting.zone_coverage import targets
from tests.test_host_share import _environment, _validate

ZONE = "dev.example.test"
ORIGIN = "203.0.113.10"
ENTRY = {"origin_ipv4": ORIGIN, "front_door": "nginx"}
CSR = "-----BEGIN CERTIFICATE REQUEST-----\nMIIB\n-----END CERTIFICATE REQUEST-----\n"
CHAIN = "-----BEGIN CERTIFICATE-----\nMIIC\n-----END CERTIFICATE-----\n"


def _wildcard(environment=None, **cloudflare):
    environment = copy.deepcopy(environment or _environment())
    environment["cloudflare"] = {"proxied": False, "tls": "acme", "dns": "wildcard", **cloudflare}
    return environment


def _result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _hex_read(text: str) -> str:
    import hashlib
    data = text.encode()
    return hashlib.sha256(data).hexdigest() + "\n" + data.hex() + "\n"


class ValidationTests(unittest.TestCase):
    def refused(self, environment, needle):
        with self.assertRaises(hosting.HostingError) as raised:
            _validate(environment)
        self.assertIn(needle, str(raised.exception))

    def test_wildcard_plans_no_records_and_keeps_routes_and_certificate_names(self):
        validated = _validate(_wildcard())
        self.assertEqual(hosting.dns_mode(validated), "wildcard")
        self.assertEqual(hosting.wildcard_zone(validated), ZONE)
        plan = hosting.desired_plan(validated, ORIGIN, "2001:db8::10")
        self.assertEqual(plan["records"], [])
        self.assertEqual(set(plan["certificate_hostnames"]),
                         {route["hostname"] for route in validated["routes"]})

    def test_other_modes_are_unchanged(self):
        validated = _validate(_environment())
        self.assertEqual(hosting.dns_mode(validated), "auto")
        self.assertIsNone(hosting.wildcard_zone(validated))
        self.assertTrue(hosting.desired_plan(validated, ORIGIN)["records"])

    def test_proxied_targets_are_refused(self):
        environment = _wildcard()
        environment["cloudflare"] = {"proxied": True, "tls": "origin-ca", "ssl_mode": "strict",
                                     "dns": "wildcard"}
        self.refused(environment, "requires DNS-only ACME")

    def test_external_dns_cannot_be_combined(self):
        self.refused(_wildcard(external_dns=["mockups.dev.example.test"]),
                     "cannot be combined with cloudflare.external_dns")

    def test_names_under_two_zones_are_refused(self):
        environment = _wildcard()
        environment["host"]["aliases"].append({"hostname": "deep.s7k2.dev.example.test",
                                               "mode": "serve"})
        self.refused(environment, "under one zone")

    def test_unknown_mode_is_refused(self):
        self.refused(_wildcard(dns="star"), "cloudflare.dns must be auto, exact or wildcard")

    def test_coverage_decision_is_exact_without_any_remote_call(self):
        validated = _validate(_wildcard())
        store = MagicMock()
        self.assertEqual(targets.decide(validated, ENTRY, None, store_client=store), {"mode": "exact"})
        store.assert_not_called()
        self.assertEqual(store.method_calls, [])


class RecordTests(unittest.TestCase):
    def check(self, rows, entry=ENTRY):
        client = MagicMock()
        client.records.return_value = rows
        return client, wildcard_cert.check_record(client, "z1", ZONE, entry)

    def refused(self, rows, entry=ENTRY):
        with self.assertRaises(FrontDoorError) as raised:
            self.check(rows, entry)
        self.assertEqual(raised.exception.code, wildcard_cert.WILDCARD_DNS_CONFLICT)

    def test_absent_record_is_reported_missing_and_nothing_is_written(self):
        client, missing = self.check([])
        self.assertEqual(missing, [ORIGIN])
        client.records.assert_called_once_with("z1", f"*.{ZONE}")
        client.create_address.assert_not_called()

    def test_one_identical_record_is_adopted_whatever_its_ttl_and_comment(self):
        row = {"id": "old", "type": "A", "content": ORIGIN, "proxied": False, "ttl": 1,
               "comment": None}
        self.assertEqual(self.check([row])[1], [])

    def test_conflicting_rows_are_refused(self):
        good = {"id": "g", "type": "A", "content": ORIGIN, "proxied": False}
        for rows in ([{"id": "p", "type": "A", "content": ORIGIN, "proxied": True}],
                     [{"id": "o", "type": "A", "content": "198.51.100.1", "proxied": False}],
                     [{"id": "c", "type": "CNAME", "content": "edge.example.net"}],
                     [good, {"id": "x", "type": "A", "content": "198.51.100.1", "proxied": False}],
                     [good, {"id": "v6", "type": "AAAA", "content": "2001:db8::9", "proxied": False}]):
            with self.subTest(rows=rows):
                self.refused(rows)

    def test_ipv6_remote_needs_both_families(self):
        entry = {**ENTRY, "origin_ipv6": "2001:db8::10"}
        good = {"id": "g", "type": "A", "content": ORIGIN, "proxied": False}
        self.assertEqual(self.check([good], entry)[1], ["2001:db8::10"])

    def test_created_rows_are_dns_only(self):
        client = MagicMock()
        client.create_address.return_value = {"id": "new", "type": "A", "content": ORIGIN,
                                              "proxied": False}
        created = wildcard_cert.create_missing_records(client, "z1", ZONE, [ORIGIN])
        self.assertEqual(created[0]["id"], "new")
        client.create_address.assert_called_once_with(
            "z1", f"*.{ZONE}", ORIGIN, proxied=False, comment="sandbox wildcard hosting")


class CertificateTests(unittest.TestCase):
    def test_current_certificate_is_reused_without_issuing(self):
        ssh = MagicMock(return_value=_result(stdout=f"X509v3 SAN:\n    DNS:*.{ZONE}\n"))
        issue = MagicMock()
        result = wildcard_cert.ensure_certificate(ENTRY, ZONE, issue=issue, ssh=ssh)
        self.assertEqual(result["state"], "current")
        self.assertEqual((result["cert_path"], result["key_path"]), wildcard_cert.paths(ZONE))
        issue.assert_not_called()
        self.assertEqual(ssh.call_count, 1)
        self.assertIn("pkey -pubout", ssh.call_args.args[1])

    def test_missing_certificate_is_issued_from_a_remote_csr_and_installed(self):
        current = _result(stdout=f"DNS:*.{ZONE}")
        ssh = MagicMock(side_effect=[_result(returncode=1), _result(stdout=_hex_read(CSR)),
                                     _result(), current])
        issue = MagicMock(return_value=CHAIN)
        result = wildcard_cert.ensure_certificate(ENTRY, ZONE, issue=issue, ssh=ssh)
        self.assertEqual(result["state"], "issued")
        self.assertEqual(issue.call_args.args[:2], (ZONE, CSR))
        csr_command = ssh.call_args_list[1].args[1]
        self.assertIn("subjectAltName=DNS:*.", csr_command)
        self.assertIn("umask 077", csr_command)
        self.assertIn("ln ", csr_command)  # the key is created without clobbering
        install = ssh.call_args_list[2]
        self.assertEqual(install.kwargs["input_data"], CHAIN)
        for needle in ("flock", "timeout -k 5 60 nginx -t", "pkey -pubout", "rm -f"):
            self.assertIn(needle, install.args[1])

    def test_a_chain_for_another_key_defers_to_the_one_installed_meanwhile(self):
        current = _result(stdout=f"DNS:*.{ZONE}")
        ssh = MagicMock(side_effect=[_result(returncode=1), _result(stdout=_hex_read(CSR)),
                                     _result(returncode=76), current])
        result = wildcard_cert.ensure_certificate(ENTRY, ZONE, issue=MagicMock(return_value=CHAIN),
                                                  ssh=ssh)
        self.assertEqual(result["state"], "current")

    def test_a_certificate_installed_while_waiting_is_used_instead_of_issuing(self):
        current = _result(stdout=f"DNS:*.{ZONE}")
        ssh = MagicMock(side_effect=[_result(returncode=1), _result(stdout=_hex_read(CSR)), current])
        issue = MagicMock(side_effect=lambda zone, csr, still_needed: None if not still_needed() else CHAIN)
        result = wildcard_cert.ensure_certificate(ENTRY, ZONE, issue=issue, ssh=ssh)
        self.assertEqual(result["state"], "current")
        self.assertEqual(ssh.call_count, 3)  # no install

    def test_busy_broker_rechecks_the_remote_before_each_retry(self):
        from sandbox.hosting.tailnet_names import models
        config = SimpleNamespace(zones=(ZONE,))
        deps = MagicMock()
        deps.issue.side_effect = models.TailnetNameError(models.BUSY, "busy")
        checks = iter([True, False])
        with patch.object(models.TailnetNameConfig, "load", return_value=config), \
             patch("sandbox.commands.host_tailnet_name.default_deps", return_value=deps):
            issue = wildcard_cert.default_issuer(sleep=lambda _s: None)
            self.assertIsNone(issue(ZONE, CSR, lambda: next(checks)))
        self.assertEqual(deps.issue.call_count, 2)

    def test_issue_failure_installs_nothing(self):
        ssh = MagicMock(side_effect=[_result(returncode=1), _result(stdout=_hex_read(CSR))])
        issue = MagicMock(side_effect=RuntimeError("acme said no"))
        with self.assertRaises(FrontDoorError) as raised:
            wildcard_cert.ensure_certificate(ENTRY, ZONE, issue=issue, ssh=ssh)
        self.assertEqual(raised.exception.code, wildcard_cert.WILDCARD_CERTIFICATE_FAILED)
        self.assertEqual(ssh.call_count, 2)

    def test_refresh_check_accepts_a_certificate_inside_the_renewal_window(self):
        ssh = MagicMock(return_value=_result(stdout=f"DNS:*.{ZONE}"))
        wildcard_cert.certificate_current(ENTRY, ZONE, min_days=0, ssh=ssh)
        self.assertIn("-checkend 0 ", ssh.call_args.args[1])

    def test_lego_names_wildcards_with_an_underscore(self):
        self.assertEqual(wildcard_cert.lego_name(ZONE), "_." + ZONE)

    def test_shared_paths_never_collide_with_a_target_directory(self):
        from sandbox.hosting import destroy
        cert, _ = wildcard_cert.paths(ZONE)
        self.assertTrue(wildcard_cert.is_shared_certificate_path(cert))
        for path in destroy._cert_dirs("sandbox-host-wildcard-dev", "nginx"):
            self.assertFalse(wildcard_cert.is_shared_certificate_path(path))

    def test_remove_route_refuses_the_shared_directory(self):
        from sandbox.hosting.front_door import nginx
        with patch.object(nginx, "_ssh") as ssh:
            with self.assertRaises(FrontDoorError):
                nginx.remove_route_and_certificates(ENTRY, f"wildcard-{ZONE}")
        ssh.assert_not_called()

    def test_record_is_written_only_after_the_certificate(self):
        client = MagicMock()
        client.records.return_value = []
        order = []
        client.create_address.side_effect = lambda *a, **k: order.append("dns") or {"id": "n"}
        with patch.object(wildcard_cert, "ensure_certificate",
                          side_effect=lambda *a, **k: order.append("cert") or {}):
            wildcard_cert.ensure(ENTRY, ZONE, "z1", client)
        self.assertEqual(order, ["cert", "dns"])

    def test_a_row_written_during_issuance_is_not_written_again(self):
        client = MagicMock()
        row = {"id": "g", "type": "A", "content": ORIGIN, "proxied": False}
        client.records.side_effect = [[], [row]]
        with patch.object(wildcard_cert, "ensure_certificate", return_value={}):
            result = wildcard_cert.ensure(ENTRY, ZONE, "z1", client)
        client.create_address.assert_not_called()
        self.assertEqual(result["created_records"], [])

    def test_failed_issuance_writes_no_record(self):
        client = MagicMock()
        client.records.return_value = []
        with patch.object(wildcard_cert, "ensure_certificate",
                          side_effect=FrontDoorError("wildcard_certificate_failed", "no")):
            with self.assertRaises(FrontDoorError):
                wildcard_cert.ensure(ENTRY, ZONE, "z1", client)
        client.create_address.assert_not_called()

    def test_issuer_refuses_a_zone_outside_the_allowlist(self):
        from sandbox.hosting.tailnet_names import models
        config = SimpleNamespace(zones=("other.test",))
        with patch.object(models.TailnetNameConfig, "load", return_value=config):
            with self.assertRaises(FrontDoorError) as raised:
                wildcard_cert.issuer_preflight(ZONE)
        self.assertEqual(raised.exception.code, wildcard_cert.WILDCARD_ISSUER_UNAVAILABLE)


class ApplyHelperTests(unittest.TestCase):
    def setUp(self):
        self.validated = _validate(_wildcard())
        self.client = MagicMock()
        self.client.zone.return_value = {"id": "z1", "name": ZONE}
        self.client.records.return_value = []
        self.client.records_under.return_value = []
        patcher = patch.object(wildcard_cert, "issuer_preflight")
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_helper(self, state_entry=None, entry=ENTRY):
        shared = {"certificate": {"cert_path": "/c", "key_path": "/k"}}
        with patch.object(wildcard_cert, "ensure", return_value=shared) as ensure:
            result = hosting_cmd._wildcard_certificate(entry, self.validated, state_entry, self.client)
        return result, ensure

    def refused(self, needle, **kwargs):
        with self.assertRaises(hosting.HostingError) as raised:
            self.run_helper(**kwargs)
        self.assertIn(needle, str(raised.exception))

    def test_shared_certificate_is_returned_for_every_route(self):
        (cert, key, certificate), ensure = self.run_helper()
        self.assertEqual((cert, key), ("/c", "/k"))
        self.assertEqual(certificate["issuer"], "wildcard-acme")
        self.assertEqual(certificate["zone"], ZONE)
        ensure.assert_called_once_with(ENTRY, ZONE, "z1", self.client)

    def test_a_name_with_a_foreign_address_record_is_refused(self):
        self.client.records.side_effect = lambda zone_id, name: (
            [{"id": "foreign", "type": "A", "content": ORIGIN}] if name.startswith("mockups.") else [])
        self.refused("did not create")

    def test_a_name_with_any_other_record_type_is_refused_even_if_its_a_record_is_ours(self):
        state_entry = {"records": [{"zone_id": "z1", "created_id": "mine", "previous": None}]}
        self.client.records.side_effect = lambda zone_id, name: (
            [{"id": "mine", "type": "A"}, {"id": "t", "type": "TXT"}]
            if name.startswith("mockups.") else [])
        self.refused("TXT record", state_entry=state_entry)

    def test_a_name_with_names_below_it_is_refused(self):
        self.client.records_under.side_effect = lambda zone_id, name: (
            [{"name": "x." + name}] if name.startswith("mockups.") else [])
        self.refused("names exist below")

    def test_records_this_target_created_do_not_block_the_switch(self):
        self.client.records.side_effect = lambda zone_id, name: (
            [] if name.startswith("*.") else [{"id": "mine", "type": "A"}])
        state_entry = {"records": [{"zone_id": "z1", "created_id": "mine", "previous": None}]}
        (_cert, _key, certificate), _ = self.run_helper(state_entry)
        self.assertEqual(certificate["issuer"], "wildcard-acme")

    def test_caddy_front_door_is_refused_by_the_helper_and_the_edge_preflight(self):
        caddy = {**ENTRY, "front_door": "caddy"}
        self.refused("nginx front door", entry=caddy)
        with self.assertRaises(hosting.HostingError):
            hosting_cmd._host_edge_preflight(self.validated, caddy)

    def test_a_name_outside_any_zone_is_refused(self):
        from sandbox.core import _cloudflare as cloudflare
        self.client.zone.side_effect = cloudflare.CloudflareError("no zone", "zone_not_found")
        self.refused("not a Cloudflare zone")

    def test_private_login_service_is_refused_at_validation(self):
        from tests.test_host_share import _share
        with self.assertRaises(hosting.HostingError) as raised:
            _validate(_wildcard(_environment(_share())))
        self.assertIn("private login", str(raised.exception))


class RetireRecordTests(unittest.TestCase):
    KEY = "r/mockups/dev"

    def row(self, record_id, **extra):
        return {"zone_id": "z1", "created_id": record_id, "previous": None,
                "applied": {"name": f"{record_id}.{ZONE}", "type": "A", "content": ORIGIN,
                            "proxied": False}, **extra}

    def test_only_unchanged_own_records_are_deleted(self):
        mine, changed = self.row("mine"), self.row("changed")
        adopted = {**self.row("adopted"), "previous": {"id": "adopted", "content": ORIGIN}}
        shared = self.row("shared")
        state = {"hosts": {self.KEY: {"records": [mine, changed, adopted, shared]},
                           "r/other/dev": {"records": [self.row("shared")]}}}
        client = MagicMock()
        current = {"mine": mine["applied"], "changed": {**changed["applied"], "content": "198.51.100.9"}}
        client.record.side_effect = lambda zone_id, record_id: current.get(record_id)
        kept = hosting_cmd._retire_wildcard_records(state, self.KEY, client)
        client.restore_record.assert_called_once_with("z1", None, "mine")
        self.assertEqual({row["created_id"] for row in kept}, {"changed", "adopted", "shared"})

    def test_a_name_is_retired_whole_or_not_at_all_and_aaaa_goes_first(self):
        def pair(name, aaaa_changed=False):
            a = {"zone_id": "z1", "created_id": f"{name}-a", "previous": None,
                 "applied": {"name": f"{name}.{ZONE}", "type": "A", "content": ORIGIN}}
            aaaa = {"zone_id": "z1", "created_id": f"{name}-aaaa", "previous": None,
                    "applied": {"name": f"{name}.{ZONE}", "type": "AAAA", "content": "2001:db8::10"}}
            return a, aaaa
        ok_a, ok_aaaa = pair("ok")
        split_a, split_aaaa = pair("split")
        split_aaaa = {**split_aaaa, "previous": {"id": "split-aaaa"}}  # not ours to delete
        state = {"hosts": {self.KEY: {"records": [ok_a, ok_aaaa, split_a, split_aaaa]}}}
        client = MagicMock()
        rows = {row["created_id"]: row["applied"] for row in (ok_a, ok_aaaa, split_a, split_aaaa)}
        client.record.side_effect = lambda zone_id, record_id: rows.get(record_id)
        kept = hosting_cmd._retire_wildcard_records(state, self.KEY, client)
        deleted = [call.args[2] for call in client.restore_record.call_args_list]
        self.assertEqual(deleted, ["ok-aaaa", "ok-a"])
        self.assertEqual({row["created_id"] for row in kept}, {"split-a", "split-aaaa"})

    def test_a_provider_failure_keeps_every_row(self):
        from sandbox.core import _cloudflare as cloudflare
        mine = self.row("mine")
        state = {"hosts": {self.KEY: {"records": [mine]}}}
        client = MagicMock()
        client.record.side_effect = cloudflare.CloudflareError("HTTP 500")
        with patch("sys.stderr"):
            self.assertEqual(hosting_cmd._retire_wildcard_records(state, self.KEY, client), [mine])


if __name__ == "__main__":
    unittest.main()
