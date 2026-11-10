"""The broker-run Cloudflare child for tailnet names (spec 078).

The child is imported as a module and its HTTP opener is replaced, so no
request leaves the machine. Placeholders only.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from sandbox.hosting.tailnet_names import cloudflare_child as child  # noqa: E402

TOKEN = "tntoken-0123456789abcdef-SECRET"
ZONE_ID = "1" * 32
RID = "2" * 32
FQDN = "app.example.test"
IP = "100.64.0.10"


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeApi:
    def __init__(self, records=None, zones=None):
        self.records = records if records is not None else []
        self.zones = zones if zones is not None else [{"id": ZONE_ID, "name": "example.test"}]
        self.requests = []

    def __call__(self, request, timeout=None):
        url = urllib.parse.urlsplit(request.full_url)
        body = json.loads(request.data) if request.data else None
        self.requests.append({"method": request.get_method(), "url": request.full_url,
                              "headers": dict(request.header_items()), "body": body})
        assert url.scheme == "https" and url.netloc == "api.cloudflare.com"
        path, query = url.path, urllib.parse.parse_qs(url.query)
        if path == "/client/v4/zones":
            result = [z for z in self.zones if z["name"] == query["name"][0]]
        elif path == f"/client/v4/zones/{ZONE_ID}/dns_records" and request.get_method() == "GET":
            result = [r for r in self.records if r["name"] == query["name"][0]
                      and r["type"] == query["type"][0]]
        elif path == f"/client/v4/zones/{ZONE_ID}/dns_records" and request.get_method() == "POST":
            result = dict(body, id=RID)
            self.records.append(result)
        elif path.startswith(f"/client/v4/zones/{ZONE_ID}/dns_records/"):
            rid = path.rsplit("/", 1)[1]
            row = next((r for r in self.records if r["id"] == rid), None)
            if row is None:
                raise urllib.error.HTTPError(request.full_url, 404, "nf", {}, io.BytesIO(b"{}"))
            if request.get_method() == "DELETE":
                self.records.remove(row)
                result = {"id": rid}
            else:
                result = row
        else:
            raise AssertionError(path)
        return _Response(json.dumps({"success": True, "result": result}).encode())

    def writes(self):
        return [r for r in self.requests if r["method"] != "GET"]


def record(content=IP, proxied=False, rid=RID, name=FQDN):
    return {"id": rid, "type": "A", "name": name, "content": content, "proxied": proxied}


class ChildTests(unittest.TestCase):
    def run_child(self, api, request):
        return child.run(request, TOKEN, opener=api)

    def test_ensure_creates_dns_only_record_when_absent(self):
        api = FakeApi()
        result, code = self.run_child(api, {"op": "ensure", "zone": "example.test",
                                            "name": FQDN, "ip": IP})
        self.assertEqual((result["result"], result["record_id"], code), ("created", RID, 0))
        post = api.writes()[0]
        self.assertEqual(post["body"], {"type": "A", "name": FQDN, "content": IP, "ttl": 1,
                                        "proxied": False, "comment": "sandbox tailnet-name"})
        for req in api.requests:
            self.assertEqual(req["headers"].get("Authorization"), "Bearer " + TOKEN)
            self.assertNotIn(TOKEN, req["url"])
            self.assertNotIn(TOKEN, json.dumps(req["body"]))

    def test_identical_record_is_exists_without_write(self):
        api = FakeApi([record()])
        result, code = self.run_child(api, {"op": "ensure", "zone": "example.test",
                                            "name": FQDN, "ip": IP})
        self.assertEqual((result["result"], code), ("exists", 0))
        self.assertEqual(api.writes(), [])

    def test_conflicts_never_write(self):
        for rows in ([record(content="100.64.0.99")], [record(proxied=True)],
                     [record(), record(rid="3" * 32, content="100.64.0.11")]):
            with self.subTest(rows=rows):
                api = FakeApi([dict(r) for r in rows])
                result, code = self.run_child(api, {"op": "ensure", "zone": "example.test",
                                                    "name": FQDN, "ip": IP})
                self.assertEqual((result["result"], code), ("conflict", 3))
                self.assertEqual(api.writes(), [])

    def test_zone_must_be_visible_exactly_once(self):
        api = FakeApi(zones=[])
        result, code = self.run_child(api, {"op": "inspect", "zone": "example.test",
                                            "name": FQDN, "ip": IP})
        self.assertEqual((result["result"], result["code"], code), ("error", "zone_not_visible", 1))

    def test_delete_only_the_matching_recorded_record(self):
        api = FakeApi([record()])
        request = {"op": "delete", "zone": "example.test", "name": FQDN, "ip": IP, "record_id": RID}
        result, code = self.run_child(api, request)
        self.assertEqual((result["result"], code), ("deleted", 0))
        self.assertEqual(api.records, [])
        result, code = self.run_child(api, request)
        self.assertEqual((result["result"], code), ("absent", 0))
        for row in (record(content="100.64.0.99"), record(proxied=True),
                    record(name="other.example.test")):
            with self.subTest(row=row):
                api = FakeApi([dict(row)])
                result, code = self.run_child(api, request)
                self.assertEqual((result["result"], code), ("conflict", 3))
                self.assertEqual(api.writes(), [])

    def test_request_validation(self):
        api = FakeApi()
        for bad in ({"op": "ensure", "zone": "example.test", "name": "x.other.test", "ip": IP},
                    {"op": "ensure", "zone": "example.test", "name": FQDN, "ip": "203.0.113.1"},
                    {"op": "delete", "zone": "example.test", "name": FQDN, "ip": IP,
                     "record_id": "../../x"},
                    {"op": "purge", "zone": "example.test", "name": FQDN, "ip": IP}):
            with self.subTest(bad=bad):
                result, code = self.run_child(api, bad)
                self.assertEqual((result["result"], result["code"], code),
                                 ("error", "request_invalid", 1))
        self.assertEqual(api.requests, [])

    def test_http_errors_never_echo_bodies(self):
        def opener(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 403, "forbidden", {},
                                         io.BytesIO(b'{"errors":[{"message":"' + TOKEN.encode() + b'"}]}'))
        result, code = child.run({"op": "inspect", "zone": "example.test", "name": FQDN,
                                  "ip": IP}, TOKEN, opener=opener)
        self.assertEqual((result["result"], result["code"], code), ("error", "http_403", 1))
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_main_reads_token_from_environment_only(self):
        out = io.StringIO()
        code = child.main(["child", json.dumps({"op": "inspect", "zone": "example.test",
                                                "name": FQDN, "ip": IP})],
                          environ={}, stdout=out)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.getvalue())["code"], "token_missing")

    def test_child_is_stdlib_only(self):
        source = Path(child.__file__).read_text()
        self.assertNotIn("import sandbox", source)
        self.assertNotIn("from sandbox", source)
        self.assertIn('API_BASE = "https://api.cloudflare.com/client/v4"', source)
        self.assertNotIn("environ.get(\"CLOUDFLARE_API", source)


if __name__ == "__main__":
    unittest.main()
