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


class _Store:
    """Stand-in RangeStore recording calls (the real one runs in
    tests/test_remote_network_program.py)."""

    instances = []
    fail = None

    def __init__(self, entry, ssh_run=None, *, installed_protocol=None):
        self.entry, self.installed_protocol = entry, installed_protocol
        self.name = entry.get("name")
        self.calls = []
        self.error = None
        _Store.instances.append(self)

    def _maybe_fail(self):
        if _Store.fail is not None:
            raise _Store.fail

    def propose(self):
        self._maybe_fail()
        self.calls.append("propose")
        return {"proposed": "10.200.0.0/20", "subnet_prefix": 26, "capacity": 64,
                "assign_command": "./sb remote network-range assign vps --cidr 10.200.0.0/20 "
                                  "--subnet-prefix 26 --confirm"}

    def assign(self, cidr, subnet_prefix, *, confirm=False):
        self._maybe_fail()
        self.calls.append(("assign", cidr, subnet_prefix, confirm))
        return {"status": "assigned" if confirm else "planned", "range_id": "r-" + "a" * 12,
                "capacity": 64}

    def list(self):
        self._maybe_fail()
        self.calls.append("list")
        return {"ranges": [{"range_id": "r-" + "a" * 12, "cidr": "10.200.0.0/20",
                            "subnet_prefix": 26, "capacity": 64, "assigned_at": 1,
                            "assigned_by": "h-" + "1" * 16}],
                "allocations": [{"allocation_id": "a-" + "b" * 16, "subnet": "10.200.0.0/26",
                                 "owner_kind": "workspace", "owner_id": "w1",
                                 "workspace_id": "w1", "network": "default", "allocated_at": 1,
                                 "range_id": "r-" + "a" * 12}],
                "capacity_proof": None, "truncated": False}


class NetworkRangeCommandTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        _Store.instances, _Store.fail = [], None
        self.patch = patch
        self.put = []
        patches = [
            patch("sandbox.remote_network.store.RangeStore", _Store),
            patch("sandbox.core._remote.get_remote", return_value={"ssh": "target", "_remote_name": "vps"}),
            patch("sandbox.core._remote.put_remote",
                  side_effect=lambda name, **kw: self.put.append((name, kw))),
            patch("sandbox.core._remote.remote_mcp_service_status", return_value={
                "control_protocol": {"installed": {"spoken": 2, "oldest_served": 1}}}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _run(self, operation, **extra):
        import json
        import types
        from sandbox.commands import remote as remote_cmd
        args = types.SimpleNamespace(name=operation, ssh_url="vps", cidr=None, subnet_prefix=None,
                                     confirm=False, json=True)
        for key, value in extra.items():
            setattr(args, key, value)
        with self.patch("builtins.print") as printed:
            try:
                remote_cmd._cmd_network_range(args, as_json=True)
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, json.loads(printed.call_args.args[0])

    def test_propose_is_read_only_and_its_command_parses(self):
        from tests.test_remote_runtime_refusal import parse_with_cli
        code, payload = self._run("propose")
        self.assertEqual((code, payload["status"]), (0, "proposed"))
        parsed = parse_with_cli(payload["data"]["assign_command"])
        self.assertEqual((parsed.action, parsed.name, parsed.ssh_url, parsed.cidr,
                          parsed.subnet_prefix, parsed.confirm),
                         ("network-range", "assign", "vps", "10.200.0.0/20", 26, True))
        self.assertEqual(self.put, [])

    def test_assign_without_confirm_is_planned_and_records_nothing(self):
        code, payload = self._run("assign", cidr="10.200.0.0/20")
        self.assertEqual((code, payload["status"]), (0, "planned"))
        self.assertEqual(_Store.instances[-1].calls, [("assign", "10.200.0.0/20", 26, False)])
        self.assertEqual(self.put, [])

    def test_confirmed_assign_passes_installed_protocol_and_echoes_ranges(self):
        code, payload = self._run("assign", cidr="10.200.0.0/20", subnet_prefix=27, confirm=True)
        self.assertEqual((code, payload["status"]), (0, "assigned"))
        store = _Store.instances[-1]
        self.assertEqual(store.installed_protocol.spoken, 2)
        self.assertEqual(store.calls[0], ("assign", "10.200.0.0/20", 27, True))
        name, fields = self.put[-1]
        self.assertEqual(name, "vps")
        self.assertEqual(fields["network_ranges"], [{"range_id": "r-" + "a" * 12,
                                                     "cidr": "10.200.0.0/20", "subnet_prefix": 26}])

    def test_assign_requires_cidr(self):
        code, payload = self._run("assign")
        self.assertEqual((code, payload["error"]["code"]), (1, "range_invalid"))

    def test_typed_refusal_is_nonzero_and_carries_data(self):
        _Store.fail = RangeError("range_overlap", "overlaps", **{"class": "host_route"})
        code, payload = self._run("assign", cidr="10.200.0.0/20", confirm=True)
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "refused")
        self.assertEqual(payload["error"]["code"], "range_overlap")
        self.assertEqual(payload["error"]["data"], {"class": "host_route"})
        _Store.fail = RangeError("range_inventory_unknown", "partial")
        code, payload = self._run("propose")
        self.assertEqual((code, payload["status"]), (1, "unknown"))

    def test_list_is_the_only_output_with_subnets(self):
        import json
        _code, listed = self._run("list")
        self.assertIn("10.200.0.0/26", json.dumps(listed))
        for operation, extra in (("propose", {}), ("assign", {"cidr": "10.200.0.0/20"})):
            _code, payload = self._run(operation, **extra)
            self.assertNotIn("10.200.0.0/26", json.dumps(payload))

    def test_invalid_cidr_refusal_omits_sensitive_input(self):
        """Sol R5-2: a malformed --cidr is never echoed back."""
        import io
        import json
        import types
        from sandbox.commands import remote as remote_cmd
        canaries = ("ghp_" + "A" * 36, "/home/operator/.ssh/id_ed25519")
        parsing = self.patch.object(_Store, "assign", lambda self, cidr, subnet_prefix,
                                    confirm=False: ranges.parse_range(cidr, subnet_prefix))
        parsing.start()
        self.addCleanup(parsing.stop)
        for canary in canaries:
            with self.subTest(canary=canary[:6]):
                with self.assertRaises(RangeError) as ctx:
                    ranges.parse_range(canary)
                self.assertNotIn(canary, str(ctx.exception))
                code, payload = self._run("assign", cidr=canary, confirm=True)
                self.assertEqual((code, payload["error"]["code"]), (1, "range_invalid"))
                self.assertNotIn(canary, json.dumps(payload))
                err = io.StringIO()
                with self.patch("sys.stderr", err), self.assertRaises(SystemExit):
                    remote_cmd._cmd_network_range(types.SimpleNamespace(
                        name="assign", ssh_url="vps", cidr=canary, subnet_prefix=None,
                        confirm=False), as_json=False)
                self.assertNotIn(canary, err.getvalue())

    def test_unknown_operation_or_remote_dies(self):
        code, _payload = None, None
        with self.patch("sandbox.core._remote.get_remote", return_value=None), \
                self.patch("sys.stderr"), self.assertRaises(SystemExit):
            from sandbox.commands import remote as remote_cmd
            import types
            remote_cmd._cmd_network_range(types.SimpleNamespace(
                name="list", ssh_url="nope", cidr=None, subnet_prefix=None, confirm=False),
                as_json=False)
        with self.patch("sys.stderr"), self.assertRaises(SystemExit):
            from sandbox.commands import remote as remote_cmd
            import types
            remote_cmd._cmd_network_range(types.SimpleNamespace(
                name="bogus", ssh_url="vps", cidr=None, subnet_prefix=None, confirm=False),
                as_json=False)

    def test_provision_hint_proposes_without_assigning_and_never_fails(self):
        from sandbox.commands import remote as remote_cmd
        empty = {"ranges": [], "allocations": [], "capacity_proof": None, "truncated": False}
        with self.patch.object(_Store, "list", return_value=empty):
            hint = remote_cmd._network_range_hint({"ssh": "t", "name": "vps"})
        self.assertEqual(hint["state"], "proposed")
        self.assertIn("--confirm", hint["assign_command"])
        self.assertNotIn(("assign",), [c[:1] for c in _Store.instances[-1].calls if isinstance(c, tuple)])
        _Store.fail = RangeError("range_store_unavailable", "down")
        self.assertEqual(remote_cmd._network_range_hint({"ssh": "t", "name": "vps"}),
                         {"state": "unknown", "reason": "range_store_unavailable"})

    def test_provision_hint_is_absent_when_a_range_exists(self):
        from sandbox.commands import remote as remote_cmd
        with self.patch.object(_Store, "propose", side_effect=AssertionError("proposed")):
            # A range is listed, so no proposal is made.
            self.assertEqual(remote_cmd._network_range_hint({"ssh": "t", "name": "vps"}),
                             {"state": "assigned", "ranges": 1})


if __name__ == "__main__":
    unittest.main()
