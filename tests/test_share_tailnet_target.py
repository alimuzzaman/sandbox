"""Behavior contract for the untrusted tailnet URL selector boundary."""
from __future__ import annotations

import dataclasses
import unittest

from sandbox.hosting.share import ShareRefusal
from sandbox.hosting.share.tailnet_target import (
    AddressBinding,
    NodeSnapshot,
    ReceiverSnapshot,
    URLSelection,
    bind_node,
    parse_url,
)


class ParseURLTests(unittest.TestCase):
    def test_accepts_http_https_magicdns_and_explicit_port(self):
        http = parse_url("http://demo.tailnet.example:8080/app", scope="/app/")
        https = parse_url("https://münchen.tailnet.example:443/", scope="/")

        self.assertIsInstance(http, URLSelection)
        self.assertEqual((http.scheme, http.host, http.port, http.path),
                         ("http", "demo.tailnet.example", 8080, "/app"))
        self.assertEqual(http.scope, "/app/")
        self.assertEqual(https.host, "xn--mnchen-3ya.tailnet.example")
        self.assertEqual(https.port, 443)

    def test_accepts_tailnet_ip_selector_but_it_is_not_authority(self):
        selected = parse_url("http://100.101.102.103:8080/")

        self.assertEqual(selected.host, "100.101.102.103")
        self.assertFalse(hasattr(selected, "ready"))

    def test_accepts_bracketed_ipv6_tailnet_address(self):
        selected = parse_url("https://[fd7a:115c:a1e0::12]:443/")

        self.assertEqual(selected.host, "fd7a:115c:a1e0::12")
        self.assertEqual(selected.port, 443)

    def test_query_is_private_and_never_appears_in_repr_or_refusal(self):
        selected = parse_url("https://web.tailnet.example:443/?secret=private-value")

        self.assertEqual(selected.query, "secret=private-value")
        self.assertNotIn("private-value", repr(selected))
        with self.assertRaises(ShareRefusal) as caught:
            parse_url("https://user:private-value@web.tailnet.example/")
        self.assertNotIn("private-value", str(caught.exception))
        self.assertNotIn("private-value", repr(caught.exception))

    def test_selection_and_snapshots_are_immutable(self):
        selected = parse_url("https://web.tailnet.example:443/")
        self.assertTrue(dataclasses.is_dataclass(selected))
        with self.assertRaises((AttributeError, dataclasses.FrozenInstanceError)):
            selected.host = "elsewhere.example"


class RefusalTests(unittest.TestCase):
    def test_rejects_unsupported_or_ambiguous_url_forms(self):
        invalid = (
            "ftp://web.tailnet.example:21/", "//web.tailnet.example/",
            "https://user@web.tailnet.example/", "https://web.tailnet.example/#fragment",
            "https://web.tailnet.example:0/", "https://web.tailnet.example:65536/",
            "https://web.tailnet.example:nope/", "https://web.tailnet.example\\@evil/",
            "https://127.0.0.1/", "https://192.168.1.8/", "https://8.8.8.8/",
            "https://169.254.169.254/", "https://[::1]/", "https://[fe80::1]/",
            "https://[2001:4860:4860::8888]/", "https://100.64.0.1.evil/",
            "https://2130706433/", "https://0177.0.0.1/", "https://0x7f.0.0.1/",
            "https://web.tailnet.example/a/../b", "https://web.tailnet.example/%2e%2e/x",
            "https://web.tailnet.example/%2fadmin", "https://web.tailnet.example/%252e%252e/x",
            "https://web.tailnet.example/a\\b", "https://web.tailnet.example/\nsecret",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ShareRefusal):
                    parse_url(value)

    def test_rejects_overlong_utf8_input(self):
        with self.assertRaises(ShareRefusal):
            parse_url("https://web.tailnet.example:443/" + "a" * 4100)


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.receiver = ReceiverSnapshot(receiver_id="receiver-1", observed_at=123.0)

    def test_binds_dns_and_all_addresses_to_same_stable_node(self):
        selected = parse_url("https://web.tailnet.example:443/")
        node = NodeSnapshot("node-1", "web.tailnet.example", ("100.101.102.103",))

        binding = bind_node(selected, self.receiver, node, ("100.101.102.103",))

        self.assertIsInstance(binding, AddressBinding)
        self.assertEqual(binding.node_id, "node-1")
        self.assertEqual(binding.addresses, ("100.101.102.103",))

    def test_binds_literal_only_when_observed_on_same_node(self):
        selected = parse_url("http://100.101.102.103:8080/")
        node = NodeSnapshot("node-1", "web.tailnet.example", ("100.101.102.103",))

        self.assertEqual(bind_node(selected, self.receiver, node, ("100.101.102.103",)).node_id,
                         "node-1")

    def test_refuses_wrong_dns_mixed_or_replaced_node_addresses(self):
        selected = parse_url("https://web.tailnet.example:443/")
        cases = (
            (NodeSnapshot("node-1", "other.tailnet.example", ("100.101.102.103",)),
             ("100.101.102.103",)),
            (NodeSnapshot("node-1", "web.tailnet.example", ("100.101.102.103",)),
             ("100.101.102.103", "8.8.8.8")),
            (NodeSnapshot("node-2", "web.tailnet.example", ("100.101.102.104",)),
             ("100.101.102.103",)),
            (NodeSnapshot("node-1", "web.tailnet.example", ("100.101.102.103",)),
             ("100.101.102.103", "100.101.102.104", "100.101.102.105",
              "100.101.102.106", "100.101.102.107")),
        )
        for node, answers in cases:
            with self.subTest(node=node, answers=answers):
                with self.assertRaises(ShareRefusal):
                    bind_node(selected, self.receiver, node, answers)


if __name__ == "__main__":
    unittest.main()
