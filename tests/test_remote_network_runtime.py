"""Runtime-side range allocation around Compose up/down (spec 063 T010, T013, T015)."""
from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sandbox.remote_network import runtime as range_runtime  # noqa: E402
from sandbox.remote_network import store as range_store  # noqa: E402
from sandbox.remote_network.ranges import RangeError  # noqa: E402
from tests.test_remote_network_program import HOLDER, NEW, _LocalRemote, _write_tool  # noqa: E402


def config(project, *networks, external=()):
    nets = {name: {"name": f"{project}_{name}"} for name in networks}
    nets.update({name: {"name": name, "external": True} for name in external})
    return {"name": project, "networks": nets}


class RangeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        inspect = json.dumps([{"IPAM": {"Config": [{"Subnet": "172.17.0.0/16"}]}}])
        _write_tool(bin_dir / "docker", 'case "$2" in ls) echo net1;; inspect) cat <<\'EOF\'\n'
                    + inspect + '\nEOF\n;; esac\n')
        _write_tool(bin_dir / "ip", "cat <<'EOF'\n" + json.dumps([{"dst": "default"}]) + "\nEOF\n")
        self.runner = _LocalRemote(self.home, bin_dir)
        self.store = range_store.RangeStore({"name": "local"}, self.runner, installed_protocol=NEW)
        self.configs = {}
        self.overrides = root / "overrides"
        self.runtime = range_runtime.RangeRuntime(
            self.home, store=self.store, overrides=self.overrides,
            compose_config=lambda instance: self.configs[instance])

    def tearDown(self):
        self._tmp.cleanup()

    def test_without_ranges_nothing_runs_and_no_override_is_passed(self):
        calls = []
        self.runner.fail = True  # any range-program run would raise
        runtime = range_runtime.RangeRuntime(
            self.home, store=self.store, overrides=self.overrides,
            compose_config=lambda instance: calls.append(instance))
        self.assertIsNone(runtime.prepare("a"))
        self.assertEqual(runtime.release("a"), 0)
        self.assertEqual(runtime.compose_args("a"), [])
        self.assertEqual(calls, [])

    def test_prepare_writes_one_subnet_per_created_network(self):
        self.store.assign("10.200.0.0/24", 26, confirm=True, holder=HOLDER)
        self.configs["a"] = config("sandbox-a", "default", "backend", external=("edge",))
        evidence = self.runtime.prepare("a")
        path = self.overrides / "a.yml"
        self.assertEqual(self.runtime.compose_args("a"), ["-f", str(path)])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        text = path.read_text()
        self.assertEqual(text.count("- subnet: 10.200.0."), 2)
        self.assertEqual(evidence["covered"], ["backend", "default"])
        self.assertEqual(evidence["outside_range"], ["edge"])
        self.assertEqual(len(evidence["granted"]), 2)
        self.assertNotIn("10.200", json.dumps(evidence))
        self.assertEqual(self.runtime.prepare("a")["granted"], evidence["granted"])
        self.assertEqual(path.read_text(), text)

    def test_two_stacks_in_one_home_get_distinct_subnets(self):
        self.store.assign("10.200.0.0/24", 26, confirm=True, holder=HOLDER)
        self.configs["a"] = config("sandbox-a", "default")
        self.configs["b"] = config("sandbox-b", "default")
        self.runtime.prepare("a")
        self.runtime.prepare("b")
        self.assertNotEqual((self.overrides / "a.yml").read_text(),
                            (self.overrides / "b.yml").read_text())

    def test_exhaustion_refuses_before_any_override_with_the_table(self):
        self.store.assign("10.200.0.0/24", 25, confirm=True, holder=HOLDER)  # capacity 2
        self.configs["a"] = config("sandbox-a", "default", "backend")
        self.runtime.prepare("a")
        self.configs["b"] = config("sandbox-b", "default")
        with self.assertRaises(RangeError) as caught:
            self.runtime.prepare("b")
        self.assertEqual(caught.exception.code, "docker_network_subnet_exhausted")
        self.assertEqual([row["owner_id"] for row in caught.exception.data["allocation_table"]],
                         ["instance:a", "instance:a"])
        self.assertNotIn("10.200", json.dumps(caught.exception.data))
        self.assertFalse((self.overrides / "b.yml").exists())

    def test_release_frees_the_instance_and_removes_its_override(self):
        self.store.assign("10.200.0.0/24", 25, confirm=True, holder=HOLDER)
        self.configs["a"] = config("sandbox-a", "default", "backend")
        self.runtime.prepare("a")
        self.assertEqual(self.runtime.release("a"), 2)
        self.assertFalse((self.overrides / "a.yml").exists())
        self.assertEqual(self.runtime.compose_args("a"), [])
        self.configs["b"] = config("sandbox-b", "default", "backend")
        self.assertEqual(len(self.runtime.prepare("b")["granted"]), 2)

    def test_a_stack_creating_no_network_needs_no_override(self):
        self.store.assign("10.200.0.0/24", 25, confirm=True, holder=HOLDER)
        self.configs["a"] = config("sandbox-a", external=("edge",))
        evidence = self.runtime.prepare("a")
        self.assertEqual((evidence["covered"], evidence["outside_range"]), ([], ["edge"]))
        self.assertEqual(self.runtime.compose_args("a"), [])

    def test_instance_names_that_cannot_be_owner_ids_are_refused(self):
        self.store.assign("10.200.0.0/24", 25, confirm=True, holder=HOLDER)
        with self.assertRaises(RangeError):
            self.runtime.prepare("../escape")


class ComposeHookTests(unittest.TestCase):
    def test_compose_passes_the_override_last_and_frees_on_volume_down(self):
        from unittest.mock import patch
        from sandbox.core import _docker

        class Fake:
            def __init__(self):
                self.events = []

            def prepare(self, instance):
                self.events.append(("prepare", instance))

            def compose_args(self, instance):
                return ["-f", "/o/" + instance + ".yml"]

            def release(self, instance):
                self.events.append(("release", instance))
                return 1

        fake, argv = Fake(), []
        with patch.object(_docker, "run", side_effect=lambda cmd, **kw: argv.append(cmd)), \
                patch("sandbox.remote_network.runtime.default_runtime", return_value=fake):
            _docker.compose("up", "-d", instance="x")
            _docker.compose("ps", instance="x")
            _docker.compose("down", instance="x")
            _docker.compose("down", "-v", instance="x")
        self.assertEqual(fake.events, [("prepare", "x"), ("release", "x")])
        for cmd in argv:
            files = [cmd[i + 1] for i, item in enumerate(cmd) if item == "-f"]
            self.assertEqual(files[-1], "/o/x.yml")


if __name__ == "__main__":
    unittest.main()
