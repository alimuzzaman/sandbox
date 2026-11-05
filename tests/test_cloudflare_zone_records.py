from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from sandbox.core._cloudflare import Client, CloudflareError


class TestCloudflareZoneRecords(unittest.TestCase):
    def test_single_page_records(self):
        client = Client(token="fake-token")
        fake_data = {
            "success": True,
            "result": [
                {"id": "r1", "name": "a.asb.bd", "type": "A"},
                {"id": "r2", "name": "b.asb.bd", "type": "CNAME"},
            ],
            "result_info": {"page": 1, "per_page": 100, "total_pages": 1, "count": 2, "total_count": 2},
        }
        with patch.object(client, "_request", return_value=fake_data) as mock_req:
            records = client.zone_records("zone123")
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["id"], "r1")
            self.assertEqual(records[1]["id"], "r2")
            mock_req.assert_called_once_with("GET", "/zones/zone123/dns_records?page=1&per_page=100")

    def test_multi_page_pagination(self):
        client = Client(token="fake-token")

        def side_effect(method, path):
            if "page=1" in path:
                return {
                    "success": True,
                    "result": [{"id": f"r{i}", "name": f"site{i}.asb.bd"} for i in range(1, 3)],
                    "result_info": {"page": 1, "per_page": 2, "total_pages": 2},
                }
            elif "page=2" in path:
                return {
                    "success": True,
                    "result": [{"id": "r3", "name": "site3.asb.bd"}],
                    "result_info": {"page": 2, "per_page": 2, "total_pages": 2},
                }
            return {"success": True, "result": []}

        with patch.object(client, "_request", side_effect=side_effect):
            records = client.zone_records("zone123", per_page=2)
            self.assertEqual(len(records), 3)
            self.assertEqual([r["id"] for r in records], ["r1", "r2", "r3"])

    def test_page_bound_exceeded_refuses(self):
        client = Client(token="fake-token")
        fake_data = {
            "success": True,
            "result": [{"id": "r1", "name": "a.asb.bd"}],
            "result_info": {"page": 1, "per_page": 1, "total_pages": 10},
        }
        with patch.object(client, "_request", return_value=fake_data):
            with self.assertRaises(CloudflareError) as ctx:
                client.zone_records("zone123", per_page=1, max_pages=3)
            self.assertIn("exceeded page bound", str(ctx.exception))

    def test_invalid_response_not_list(self):
        client = Client(token="fake-token")
        with patch.object(client, "_request", return_value={"success": True, "result": "not-a-list"}):
            with self.assertRaises(CloudflareError):
                client.zone_records("zone123")

    def test_invalid_response_item_not_dict(self):
        client = Client(token="fake-token")
        with patch.object(client, "_request", return_value={"success": True, "result": ["not-a-dict"]}):
            with self.assertRaises(CloudflareError):
                client.zone_records("zone123")


    def test_create_address_post_only(self):
        client = Client(token="fake-token")
        with patch.object(client, "_request", return_value={"result": {"id": "rec-new"}}) as mock_req:
            res = client.create_address("z1", "*.example.com", "1.2.3.4", proxied=True, comment="sandbox-zone-coverage 1234567890abcdef")
            self.assertEqual(res["id"], "rec-new")
            mock_req.assert_called_once_with(
                "POST",
                "/zones/z1/dns_records",
                {
                    "type": "A",
                    "name": "*.example.com",
                    "content": "1.2.3.4",
                    "proxied": True,
                    "ttl": 1,
                    "comment": "sandbox-zone-coverage 1234567890abcdef",
                }
            )

    def test_create_address_ipv6_unproxied(self):
        client = Client(token="fake-token")
        with patch.object(client, "_request", return_value={"result": {"id": "rec-6"}}) as mock_req:
            res = client.create_address("z1", "*.example.com", "2001:db8::1", proxied=False, comment="sandbox-zone-coverage 1234567890abcdef")
            self.assertEqual(res["id"], "rec-6")
            mock_req.assert_called_once_with(
                "POST",
                "/zones/z1/dns_records",
                {
                    "type": "AAAA",
                    "name": "*.example.com",
                    "content": "2001:db8::1",
                    "proxied": False,
                    "ttl": 60,
                    "comment": "sandbox-zone-coverage 1234567890abcdef",
                }
            )

if __name__ == "__main__":
    unittest.main()
