"""Development range math and overlap rules (spec 063 FR-002, FR-003)."""
import ipaddress
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.remote_network import ranges  # noqa: E402
from sandbox.remote_network.ranges import RangeError  # noqa: E402

N = ipaddress.IPv4Network


def complete(networks=(), routes=()):
    return ranges.inventory_from_payload(
        {"status": "complete", "networks": list(networks), "routes": list(routes)}
    )


class ParseRangeTests(unittest.TestCase):
    def test_valid_range_and_capacity(self):
        r = ranges.parse_range("10.200.0.0/20")
        self.assertEqual(r.subnet_prefix, 26)
        self.assertEqual(r.capacity, 64)
        self.assertRegex(r.range_id, r"^r-[0-9a-f]{12}$")
        self.assertEqual(r.range_id, ranges.parse_range("10.200.0.0/20", 26).range_id)

    def test_capacity_one_when_prefixes_equal(self):
        self.assertEqual(ranges.parse_range("10.201.0.0/24", 24).capacity, 1)

    def test_rejections(self):
        for cidr, prefix in (
            ("10.200.0.1/20", 26),      # host bits set
            ("10.0.0.0/8", 26),         # too wide
            ("10.200.0.0/25", 26),      # too narrow
            ("10.200.0.0", 26),         # no prefix
            ("fd00::/64", 26),          # IPv6
            ("nonsense", 26),
            (None, 26),
            ("10.200.0.0/20", 23),      # subnet prefix out of bounds
            ("10.200.0.0/20", 30),
            ("10.200.0.0/20", True),
            ("10.200.0.0/24", 25),      # fine bounds-wise
        ):
            if (cidr, prefix) == ("10.200.0.0/24", 25):
                self.assertEqual(ranges.parse_range(cidr, prefix).capacity, 2)
                continue
            with self.subTest(cidr=cidr, prefix=prefix):
                with self.assertRaises(RangeError) as ctx:
                    ranges.parse_range(cidr, prefix)
                self.assertEqual(ctx.exception.code, "range_invalid")


class OverlapTests(unittest.TestCase):
    def test_each_class(self):
        cases = (
            (complete(networks=["10.200.0.0/24"]), "docker_network"),
            (complete(routes=["10.200.8.0/21"]), "host_route"),
        )
        r = ranges.parse_range("10.200.0.0/20")
        for inventory, cls in cases:
            with self.subTest(cls=cls):
                with self.assertRaises(RangeError) as ctx:
                    ranges.check_assignment(r, inventory)
                self.assertEqual(ctx.exception.code, "range_overlap")
                self.assertEqual(ctx.exception.data["class"], cls)
        for cidr, cls in (("172.20.0.0/16", "docker_default_pool"),
                          ("192.168.16.0/20", "docker_default_pool"),
                          ("100.100.0.0/16", "cgnat")):
            with self.subTest(cidr=cidr):
                with self.assertRaises(RangeError) as ctx:
                    ranges.check_assignment(ranges.parse_range(cidr), complete())
                self.assertEqual(ctx.exception.data["class"], cls)

    def test_default_route_and_ipv6_are_not_conflicts(self):
        inv = complete(networks=["fd00::/64", "garbage"], routes=["0.0.0.0/0", "default"])
        self.assertFalse(ranges.check_assignment(ranges.parse_range("10.200.0.0/20"), inv))

    def test_partial_inventory_refuses_unknown(self):
        for payload in ({"status": "partial"}, {}, None, "x"):
            with self.subTest(payload=payload):
                inv = ranges.inventory_from_payload(payload)
                with self.assertRaises(RangeError) as ctx:
                    ranges.check_assignment(ranges.parse_range("10.200.0.0/20"), inv)
                self.assertEqual(ctx.exception.code, "range_inventory_unknown")

    def test_reassign_same_is_noop_and_different_prefix_conflicts(self):
        existing = ranges.parse_range("10.200.0.0/20", 26)
        # The range's own networks are visible in the inventory; a no-op
        # re-assignment must not be refused as an overlap.
        inv = complete(networks=["10.200.0.0/26"])
        self.assertTrue(ranges.check_assignment(ranges.parse_range("10.200.0.0/20", 26), inv, [existing]))
        with self.assertRaises(RangeError) as ctx:
            ranges.check_assignment(ranges.parse_range("10.200.0.0/20", 27), inv, [existing])
        self.assertEqual(ctx.exception.code, "range_conflict")
        with self.assertRaises(RangeError) as ctx:
            ranges.check_assignment(ranges.parse_range("10.200.0.0/21", 26), complete(), [existing])
        self.assertEqual(ctx.exception.code, "range_conflict")


class ProposalTests(unittest.TestCase):
    def test_first_free_block_inside_proposal_space(self):
        r = ranges.propose(complete())
        self.assertEqual(r.cidr, N("10.200.0.0/20"))
        self.assertEqual(r.subnet_prefix, 26)
        r = ranges.propose(complete(networks=["10.200.3.0/24"], routes=["10.200.16.0/20"]))
        self.assertEqual(r.cidr, N("10.200.32.0/20"))
        self.assertTrue(r.cidr.subnet_of(ranges.PROPOSAL_SPACE))

    def test_skips_assigned_ranges(self):
        r = ranges.propose(complete(), [ranges.parse_range("10.200.0.0/20")])
        self.assertEqual(r.cidr, N("10.200.16.0/20"))

    def test_partial_inventory_never_proposes(self):
        with self.assertRaises(RangeError) as ctx:
            ranges.propose(ranges.inventory_from_payload({"status": "partial"}))
        self.assertEqual(ctx.exception.code, "range_inventory_unknown")

    def test_exhausted_space(self):
        with self.assertRaises(RangeError) as ctx:
            ranges.propose(complete(routes=["10.200.0.0/14"]))
        self.assertEqual(ctx.exception.code, "range_proposal_unavailable")


class FreeSubnetTests(unittest.TestCase):
    def test_free_subnets_skip_used(self):
        r = ranges.parse_range("10.201.0.0/24", 26)
        free = [str(s) for _r, s in ranges.free_subnets([r], ["10.201.0.0/26", "10.201.0.128/26"])]
        self.assertEqual(free, ["10.201.0.64/26", "10.201.0.192/26"])


if __name__ == "__main__":
    unittest.main()
