"""The remote range program runs for real against a temp remote home (spec 063).

A local ``sh -c`` stand-in replaces ``ssh_run``; fake ``docker`` and ``ip``
binaries on its PATH supply the inventory.
"""
import json
import multiprocessing
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.remote_network import store as range_store  # noqa: E402
from sandbox.remote_network.ranges import RangeError  # noqa: E402
from sandbox.remote_runtime.protocol import ControlProtocol  # noqa: E402

HOLDER = "h-" + "1" * 16
NEW = ControlProtocol(range_store.RANGES_PROTOCOL, 1)
OLD = ControlProtocol(1, 1)


class _LocalRemote:
    def __init__(self, home: Path, bin_dir: Path):
        self.home, self.bin_dir, self.fail = home, bin_dir, False

    def __call__(self, _entry, command, timeout=30):
        if self.fail:
            raise OSError("ssh: connect to host 192.0.2.1 token=secret")
        env = {"SANDBOX_HOME": str(self.home), "HOME": str(self.home),
               "PATH": f"{self.bin_dir}:{os.path.dirname(sys.executable)}:/usr/bin:/bin"}
        return subprocess.run(["sh", "-c", command], env=env, capture_output=True,
                              text=True, timeout=timeout)


def _write_tool(path: Path, body: str):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _allocate_worker(home, bin_dir, owner, queue):
    store = range_store.RangeStore({"name": "fixture"}, _LocalRemote(Path(home), Path(bin_dir)),
                                   installed_protocol=NEW)
    try:
        result = store.allocate(owner_kind="workspace", owner_id=owner, workspace_id=owner,
                                networks=["default"])
        queue.put(len(result["granted"]))
    except Exception as exc:  # noqa: BLE001
        queue.put(repr(exc))


class RangeProgramTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home = root / "remote-home"
        self.home.mkdir()
        self.bin = root / "bin"
        self.bin.mkdir()
        self.set_inventory(networks=["172.17.0.0/16"], routes=["10.0.0.0/24"])
        self.remote = _LocalRemote(self.home, self.bin)
        self.store = range_store.RangeStore({"name": "fixture"}, self.remote, installed_protocol=NEW)
        self.state_dir = self.home / "runtime" / "network-ranges"

    def tearDown(self):
        self._tmp.cleanup()

    def set_inventory(self, networks=(), routes=(), docker_ok=True, ip_ok=True):
        inspect = json.dumps([{"IPAM": {"Config": [{"Subnet": n}]}} for n in networks])
        _write_tool(self.bin / "docker", (
            'case "$2" in ls) echo net1;; inspect) cat <<\'EOF\'\n' + inspect + '\nEOF\n;; esac\n'
            if docker_ok else "exit 1\n"))
        route_json = json.dumps([{"dst": "default"}] + [{"dst": r} for r in routes])
        _write_tool(self.bin / "ip", ("cat <<'EOF'\n" + route_json + "\nEOF\n") if ip_ok else "exit 2\n")

    def assign(self, cidr="10.200.0.0/20", prefix=26):
        return self.store.assign(cidr, prefix, confirm=True, holder=HOLDER)

    def test_inventory_complete_and_partial(self):
        inv = self.store.inventory()
        self.assertTrue(inv.complete)
        self.assertEqual([str(n) for n in inv.networks], ["172.17.0.0/16"])
        self.set_inventory(docker_ok=False)
        self.assertFalse(self.store.inventory().complete)
        self.set_inventory(ip_ok=False)
        self.assertFalse(self.store.inventory().complete)

    def test_read_only_ops_create_nothing(self):
        self.assertEqual(self.store.list()["ranges"], [])
        self.store.inventory()
        proposal = self.store.propose()
        self.assertEqual(proposal["proposed"], "10.200.0.0/20")
        self.assertIn("--confirm", proposal["assign_command"])
        planned = self.store.assign("10.200.0.0/20", 26)
        self.assertEqual(planned["status"], "planned")
        self.assertFalse(self.state_dir.exists())

    def test_assign_records_with_private_modes_and_is_idempotent(self):
        self.assertEqual(self.assign()["status"], "assigned")
        self.assertEqual(stat.S_IMODE(self.state_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.state_dir / "state.json").stat().st_mode), 0o600)
        self.assertEqual(self.assign()["status"], "unchanged")
        with self.assertRaises(RangeError) as ctx:
            self.assign(prefix=27)
        self.assertEqual(ctx.exception.code, "range_conflict")
        listed = self.store.list()
        self.assertEqual(len(listed["ranges"]), 1)
        self.assertEqual(listed["ranges"][0]["assigned_by"], HOLDER)
        self.assertEqual(listed["ranges"][0]["capacity"], 64)

    def test_assign_overlap_and_partial_refuse_without_recording(self):
        self.set_inventory(networks=["10.200.1.0/24"])
        with self.assertRaises(RangeError) as ctx:
            self.assign()
        self.assertEqual((ctx.exception.code, ctx.exception.data.get("class")),
                         ("range_overlap", "docker_network"))
        self.set_inventory(docker_ok=False)
        with self.assertRaises(RangeError) as ctx:
            self.assign()
        self.assertEqual(ctx.exception.code, "range_inventory_unknown")
        self.assertEqual(self.store.list()["ranges"], [])

    def test_old_runtime_is_a_typed_limitation(self):
        for protocol in (OLD, None):
            store = range_store.RangeStore({"name": "fixture"}, self.remote, installed_protocol=protocol)
            with self.subTest(protocol=protocol):
                with self.assertRaises(RangeError) as ctx:
                    store.assign("10.200.0.0/20", 26, confirm=True, holder=HOLDER)
                self.assertEqual(ctx.exception.code, "range_runtime_unsupported")
                self.assertIn("remote service migrate fixture --confirm", ctx.exception.data["remedy"])
                with self.assertRaises(RangeError):
                    store.allocate(owner_kind="job", owner_id="j1", workspace_id="w1", networks=["n"])
        self.assertFalse(self.state_dir.exists())

    def test_allocate_is_all_or_nothing_and_idempotent_per_workspace_network(self):
        self.assign("10.201.0.0/24", 25)  # capacity 2
        first = self.store.allocate(owner_kind="workspace", owner_id="w1", workspace_id="w1",
                                    networks=["default"], pool_capacity=0)
        self.assertEqual(len(first["granted"]), 1)
        again = self.store.allocate(owner_kind="job", owner_id="j1", workspace_id="w1",
                                    networks=["default"])
        self.assertEqual(again["granted"], first["granted"])
        short = self.store.allocate(owner_kind="workspace", owner_id="w2", workspace_id="w2",
                                    networks=["a", "b"])
        self.assertEqual(short["granted"], [])
        self.assertTrue(short["exhausted"])
        self.assertEqual([row["owner_id"] for row in short["table"]], ["w1"])
        self.assertNotIn("10.201", json.dumps(short["table"]))
        listed = self.store.list()
        self.assertEqual(len(listed["allocations"]), 1)
        self.assertEqual(listed["capacity_proof"]["range_capacity"], 2)
        self.assertEqual(listed["capacity_proof"]["usable"], 1)

    def test_allocate_without_range_grants_nothing(self):
        result = self.store.allocate(owner_kind="workspace", owner_id="w1", workspace_id="w1",
                                     networks=["default"])
        self.assertEqual((result["granted"], result["no_range"], result["exhausted"]), ([], True, False))

    def test_allocate_skips_subnets_a_foreign_network_occupies(self):
        self.assign("10.201.0.0/24", 25)
        self.set_inventory(networks=["10.201.0.0/25"])
        granted = self.store.allocate(owner_kind="workspace", owner_id="w1", workspace_id="w1",
                                      networks=["default"])["granted"]
        self.assertEqual(granted[0]["subnet"], "10.201.0.128/25")

    def test_release_owner_by_workspace_and_owner(self):
        self.assign()
        self.store.allocate(owner_kind="workspace", owner_id="w1", workspace_id="w1", networks=["a"])
        self.store.allocate(owner_kind="job", owner_id="j1", workspace_id="w1", networks=["b"])
        self.store.allocate(owner_kind="workspace", owner_id="w2", workspace_id="w2", networks=["a"])
        self.assertEqual(self.store.release_owner(owner_id="j1"), 1)
        self.assertEqual(self.store.release_owner(workspace_id="w1"), 1)
        self.assertEqual(self.store.release_owner(workspace_id="w1"), 0)
        self.assertEqual([a["workspace_id"] for a in self.store.list()["allocations"]], ["w2"])
        with self.assertRaises(RangeError) as ctx:
            self.store.release_owner(owner_id="j1", workspace_id="w1")
        self.assertEqual(ctx.exception.code, "range_request_invalid")

    def test_invalid_requests_refuse(self):
        self.assign()
        for kwargs in (
            {"owner_kind": "other", "owner_id": "w", "workspace_id": "w", "networks": ["a"]},
            {"owner_kind": "workspace", "owner_id": "x", "workspace_id": "w", "networks": ["a"]},
            {"owner_kind": "job", "owner_id": "j", "workspace_id": "w", "networks": []},
            {"owner_kind": "job", "owner_id": "j", "workspace_id": "w", "networks": ["a", "a"]},
            {"owner_kind": "job", "owner_id": "../x", "workspace_id": "w", "networks": ["a"]},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(RangeError) as ctx:
                    self.store.allocate(**kwargs)
                self.assertEqual(ctx.exception.code, "range_request_invalid")

    def test_corrupt_state_refuses_and_is_left_alone(self):
        self.assign()
        state = self.state_dir / "state.json"
        state.write_text("{not json")
        with self.assertRaises(RangeError) as ctx:
            self.store.allocate(owner_kind="workspace", owner_id="w", workspace_id="w", networks=["a"])
        self.assertEqual(ctx.exception.code, "range_state_invalid")
        self.assertEqual(state.read_text(), "{not json")

    def test_unreachable_store_never_leaks_transport_detail(self):
        self.remote.fail = True
        with self.assertRaises(RangeError) as ctx:
            self.store.list()
        self.assertEqual(ctx.exception.code, "range_store_unavailable")
        self.assertNotIn("secret", str(ctx.exception))
        self.assertNotIn("192.0.2.1", str(ctx.exception))

    def test_concurrent_last_subnet_yields_one_grant(self):
        self.assign("10.201.0.0/24", 24)  # capacity 1
        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        workers = [ctx.Process(target=_allocate_worker,
                               args=(str(self.home), str(self.bin), f"w{i}", queue))
                   for i in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(60)
        results = sorted(queue.get(timeout=5) for _ in workers)
        self.assertEqual(results, [0, 0, 0, 1])
        self.assertEqual(len(self.store.list()["allocations"]), 1)


if __name__ == "__main__":
    unittest.main()
