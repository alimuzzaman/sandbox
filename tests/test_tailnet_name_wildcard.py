"""Tailnet names under ``tailnet_names.wildcard_parents``: one DNS-only ``*.<parent>`` record."""
from __future__ import annotations

import io
import json
import unittest
import urllib.parse

from sandbox.hosting.tailnet_names import cloudflare_child as child
from sandbox.hosting.tailnet_names import models as m
from sandbox.hosting.tailnet_names import service
from tests.test_host_tailnet_name import TAILNET_IP, FakeDns, ServiceTestCase, config

ZONE_ID = "1" * 32
PARENT = "tn.example.test"
NAME = f"mail-a.{PARENT}"


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Api:
    def __init__(self, records=()):
        self.records = [dict(row) for row in records]
        self.writes = []

    def __call__(self, request, timeout=None):
        url = urllib.parse.urlsplit(request.full_url)
        query = urllib.parse.parse_qs(url.query)
        if url.path == "/client/v4/zones":
            result = [{"id": ZONE_ID, "name": "example.test"}]
        elif request.get_method() == "GET":
            if "name.endswith" in query:
                suffix = query["name.endswith"][0]
                result = [r for r in self.records if r["name"].endswith(suffix)]
            else:
                result = [r for r in self.records if r["name"] == query["name"][0]]
        else:
            body = json.loads(request.data)
            self.writes.append(body)
            result = dict(body, id="9" * 32)
            self.records.append(result)
        return _Response(json.dumps({"success": True, "result": result}).encode())


def _row(name, content=TAILNET_IP, kind="A", proxied=False, rid="2" * 32):
    return {"id": rid, "name": name, "type": kind, "content": content, "proxied": proxied}


def _run(api, **request):
    body = {"op": "ensure_wildcard", "zone": "example.test", "name": NAME, "ip": TAILNET_IP,
            "parent": PARENT, **request}
    return child.run(body, "tok", opener=api)


class ChildTests(unittest.TestCase):
    def test_absent_wildcard_is_created_dns_only_and_covers_the_name(self):
        api = Api()
        result, status = _run(api)
        self.assertEqual((result["result"], status), ("covered", 0))
        self.assertEqual(api.writes, [{"type": "A", "name": f"*.{PARENT}", "content": TAILNET_IP,
                                       "ttl": 1, "proxied": False,
                                       "comment": "sandbox tailnet-name wildcard"}])

    def test_existing_identical_wildcard_is_adopted(self):
        api = Api([_row(f"*.{PARENT}")])
        self.assertEqual(_run(api)[0]["result"], "covered")
        self.assertEqual(api.writes, [])

    def test_an_own_exact_record_is_kept(self):
        api = Api([_row(NAME)])
        self.assertEqual(_run(api)[0]["result"], "exists")
        self.assertEqual(api.writes, [])

    def test_refusals_write_nothing(self):
        cases = {
            "other_record": [_row(NAME, kind="TXT", content="v=1")],
            "names_below": [_row("_acme-challenge." + NAME, kind="TXT", content="x")],
            "wildcard_other_record": [_row(f"*.{PARENT}", content="100.64.0.99")],
            "multiple_wildcard_records": [_row(f"*.{PARENT}"), _row(f"*.{PARENT}", rid="3" * 32)],
        }
        for code, rows in cases.items():
            with self.subTest(code=code):
                api = Api(rows)
                result, status = _run(api)
                self.assertEqual((result["result"], result.get("code"), status),
                                 ("conflict", code, 3))
                self.assertEqual(api.writes, [])

    def test_a_proxied_wildcard_is_refused(self):
        api = Api([_row(f"*.{PARENT}", proxied=True)])
        self.assertEqual(_run(api)[0]["code"], "wildcard_other_record")

    def test_a_name_not_directly_under_the_parent_is_invalid(self):
        for request in ({"name": f"a.b.{PARENT}"}, {"parent": "example.test"},
                        {"parent": "tn.other.test"}):
            with self.subTest(request=request):
                self.assertEqual(_run(Api(), **request)[0]["code"], "request_invalid")


class ConfigTests(unittest.TestCase):
    def test_parents_must_sit_below_a_listed_zone(self):
        self.assertEqual(config(wildcard_parents=[PARENT]).wildcard_parents, (PARENT,))
        self.assertEqual(config().wildcard_parents, ())
        for value in (["example.test"], ["tn.other.test"], "tn.example.test", ["*.x.example.test"]):
            with self.subTest(value=value):
                with self.assertRaises(m.TailnetNameError):
                    config(wildcard_parents=value)

    def test_wildcard_parent_matches_one_label_only(self):
        cfg = config(wildcard_parents=[PARENT])
        self.assertEqual(m.wildcard_parent(cfg, NAME), PARENT)
        self.assertIsNone(m.wildcard_parent(cfg, f"a.b.{PARENT}"))
        self.assertIsNone(m.wildcard_parent(cfg, "app.example.test"))
        self.assertIsNone(m.wildcard_parent(None, NAME))
        self.assertEqual(m.DnsResult.parse({"result": "covered"}).result, "covered")


class WildcardDns(FakeDns):
    def __call__(self, cfg, request):
        if request["op"] == "ensure_wildcard":
            self.requests.append(dict(request))
            return m.DnsResult("covered", "9" * 32, request["ip"], False)
        return super().__call__(cfg, request)


class ServiceTests(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.dns = WildcardDns()
        self.deps = service.Deps(**{**self.deps.__dict__, "dns": self.dns})
        self.ctx = service.Context(config(wildcard_parents=[PARENT]), self.ctx.target)
        self.name = m.TailnetName(NAME, "example.test")

    def test_a_covered_name_records_no_dns_record_and_removal_leaves_the_wildcard(self):
        result = self.add()
        self.assertTrue(result.ok, result.envelope())
        self.assertEqual(self.dns.requests[0]["op"], "ensure_wildcard")
        self.assertEqual(self.dns.requests[0]["parent"], PARENT)
        record = self.store.load()[NAME]
        self.assertEqual((record.dns_record_id, record.dns_adopted), (None, False))
        self.assertIn(("dns", "skipped"), self.steps(result))
        removed = service.remove(self.ctx, self.deps, self.name)
        self.assertTrue(removed.ok, removed.envelope())
        ops = [row["op"] for row in self.dns.requests]
        self.assertNotIn("delete", ops)
        self.assertNotIn(NAME, self.store.load())

    def test_names_outside_the_parents_keep_their_own_record(self):
        self.name = m.TailnetName("app.example.test", "example.test")
        result = self.add()
        self.assertTrue(result.ok, result.envelope())
        self.assertEqual(self.dns.requests[0]["op"], "ensure")
        self.assertIsNotNone(self.store.load()["app.example.test"].dns_record_id)


if __name__ == "__main__":
    unittest.main()
