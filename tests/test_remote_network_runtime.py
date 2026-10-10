"""Runtime-side range allocation around Compose up/down (spec 063 T010, T013, T015)."""
from __future__ import annotations

import contextlib
import io
import json
import stat
import threading
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

    def test_exhaustion_is_described_without_subnets(self):
        error = RangeError(range_runtime.EXHAUSTED, "no free subnet", allocation_table=[
            {"owner_id": "instance:a", "workspace_id": "site-a"},
            {"owner_id": "instance:b", "workspace_id": "site-a"},
            {"owner_id": "instance:c", "workspace_id": "$(id)"}])
        text = range_runtime.describe(error)
        self.assertEqual(text.splitlines(), [
            "docker_network_subnet_exhausted: no free subnet",
            "  held by workspace site-a: ./sb workspace release site-a"
            " && ./sb workspace reap --confirm"])

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

    def test_instances_are_attributed_to_their_workspace_and_released_alone(self):
        from sandbox.resources.network_capacity import evaluate_network_capacity
        self.store.assign("10.200.0.0/24", 25, confirm=True, holder=HOLDER)  # capacity 2
        runtime = range_runtime.RangeRuntime(
            self.home, store=self.store, overrides=self.overrides,
            compose_config=lambda instance: self.configs[instance],
            workspace_of=lambda instance: "site-main")
        self.configs["a"] = config("sandbox-a", "default")
        self.configs["a-qa"] = config("sandbox-a-qa", "default")
        runtime.prepare("a")
        runtime.prepare("a-qa")
        table = self.store.stats()["table"]
        self.assertEqual({(r["owner_kind"], r["owner_id"], r["workspace_id"]) for r in table},
                         {("instance", "instance:a", "site-main"),
                          ("instance", "instance:a-qa", "site-main")})
        refused = evaluate_network_capacity(
            {"ok": False, "status": "unavailable", "code": "docker_address_pools_unavailable"},
            remote_name="vps", range_evidence=self.store.stats())
        self.assertEqual(refused["release_commands"], ["./sb workspace release site-main --remote vps && ./sb workspace reap --remote vps --confirm"])
        self.assertEqual(runtime.release("a-qa"), 1)
        self.assertEqual([r["owner_id"] for r in self.store.stats()["table"]], ["instance:a"])

    def _sweep(self, live, **kw):
        from unittest.mock import patch
        kw.setdefault("remove_network", lambda _n: True)
        kw.setdefault("grace_seconds", 0)
        with patch.object(range_runtime, "_LocalRunner", return_value=self.runner):
            return range_runtime.release_removed_instances(
                live if callable(live) else (lambda: set(live)), self.home, **kw)

    def _home_runtime(self):
        return range_runtime.RangeRuntime(
            self.home, store=self.store, overrides=range_runtime._overrides_dir(self.home),
            compose_config=lambda instance: self.configs[instance],
            workspace_of=lambda instance: "site-" + instance)

    def test_reap_frees_instances_that_are_no_longer_registered(self):
        self.store.assign("10.200.0.0/24", 25, confirm=True, holder=HOLDER)
        runtime = self._home_runtime()
        self.configs["a"] = config("sandbox-a", "default")
        self.configs["b"] = config("sandbox-b", "default")
        runtime.prepare("a")
        runtime.prepare("b")
        self.assertEqual(self._sweep({"b"}), {"released": 1, "retained": []})
        self.assertEqual([r["owner_id"] for r in self.store.stats()["table"]], ["instance:b"])
        self.assertFalse(runtime.override_path("a").exists())

    def test_reap_program_sweeps_ranges_on_every_reconcile(self):
        from sandbox.resources.remote import _REMOTE_PROGRAM
        self.assertIn("release_removed_instances(", _REMOTE_PROGRAM)
        self.assertIn("return _sweep_ranges(result)", _REMOTE_PROGRAM.split(
            "def reconcile_after_removal")[1].split("names = ")[1].split("try:")[0])

    def test_reconcile_reports_retained_owners_as_partial(self):
        import ast
        from unittest.mock import patch
        from sandbox.resources.remote import _REMOTE_PROGRAM
        tree = ast.parse(_REMOTE_PROGRAM.replace("__REQUEST__", "'{}'"))
        sweep = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == "_sweep_ranges")
        namespace = {"RUNTIME": self.home / "runtime", "HOME": self.home}
        exec(compile(ast.Module([sweep], []), "remote", "exec"), namespace)
        range_runtime.state_file(self.home).parent.mkdir(parents=True, exist_ok=True)
        range_runtime.state_file(self.home).touch()

        class Registry:
            def __init__(self, _path):
                pass

            def read_only_all(self):
                return {"/d/site-b": {"instance": "b"}}
        seen = []
        sweep_ranges = namespace["_sweep_ranges"]
        # No registry file: no liveness evidence, so nothing is released.
        with patch.object(range_runtime, "release_removed_instances",
                          side_effect=lambda live, home: live()):
            self.assertEqual(sweep_ranges({"status": "complete"}),
                             {"status": "partial", "reason": "range_release_unavailable"})
        (self.home / "runtime" / "registry.json").write_text("{}")
        with patch("sandbox.project_registry.JsonRegistryRepository", Registry), \
                patch.object(range_runtime, "release_removed_instances",
                             side_effect=lambda live, home: seen.append(live())
                             or {"released": 1, "retained": ["a"]}):
            result = namespace["_sweep_ranges"]({"status": "complete"})
        self.assertEqual(seen, [{"b"}])
        self.assertEqual(result, {"status": "partial", "reason": "range_networks_in_use",
                                  "range_allocations_released": 1,
                                  "range_allocations_retained": 1})

    def test_reap_rechecks_owner_and_registry_after_acquiring_lock(self):
        self.store.assign("10.200.0.0/24", 26, confirm=True, holder=HOLDER)
        runtime = self._home_runtime()
        self.configs["a"] = config("sandbox-a", "default")
        runtime.prepare("a")
        calls = []

        def live():
            calls.append(1)
            # Unregistered when the page is read, registered by the time the
            # sweep holds a's lock (an ensure finished in between).
            return set() if len(calls) == 1 else {"a"}
        self.assertEqual(self._sweep(live), {"released": 0, "retained": []})
        self.assertEqual([r["owner_id"] for r in self.store.stats()["table"]], ["instance:a"])

    def test_reap_liveness_failure_under_lock_retains_owner_and_unlocks(self):
        self.store.assign("10.200.0.0/24", 26, confirm=True, holder=HOLDER)
        runtime = self._home_runtime()
        self.configs["a"] = config("sandbox-a", "default")
        runtime.prepare("a")
        calls = []

        def live():
            calls.append(1)
            if len(calls) > 1:
                raise OSError("registry unreadable")
            return set()
        with self.assertRaises(OSError):
            self._sweep(live)
        self.assertEqual(len(self.store.stats()["table"]), 1)
        entered = threading.Event()
        worker = threading.Thread(target=lambda: self._enter_exclusive(runtime, entered))
        worker.start()
        self.assertTrue(entered.wait(5))  # the lock was released
        worker.join(5)

    @staticmethod
    def _enter_exclusive(runtime, entered):
        with runtime.lifecycle("a", exclusive=True):
            entered.set()

    def test_reap_removes_the_owners_current_networks(self):
        self.store.assign("10.200.0.0/24", 26, confirm=True, holder=HOLDER)
        runtime = self._home_runtime()
        self.configs["a"] = config("sandbox-a", "default")
        runtime.prepare("a")
        removed = []
        real = self.store.instance_owners

        def grow(after=None):
            page = real(after)
            if not removed and not getattr(grow, "done", False):
                grow.done = True  # a second network appears after the page read
                self.configs["a"] = config("sandbox-a", "default", "backend")
                runtime.prepare("a")
            return page
        from unittest.mock import patch
        with patch.object(range_store.RangeStore, "instance_owners",
                          side_effect=lambda after=None: grow(after)):
            result = self._sweep(set(), remove_network=lambda n: removed.append(n) or True)
        self.assertEqual(result, {"released": 2, "retained": []})
        self.assertEqual(removed, ["sandbox-a_backend", "sandbox-a_default"])

    def test_reap_refuses_without_liveness_evidence(self):
        self.store.assign("10.200.0.0/24", 26, confirm=True, holder=HOLDER)
        self.configs["a"] = config("sandbox-a", "default")
        self._home_runtime().prepare("a")

        def missing():
            raise FileNotFoundError("registry.json")
        with self.assertRaises(FileNotFoundError):
            self._sweep(missing)
        self.assertEqual(len(self.store.stats()["table"]), 1)

    def test_reconcile_removes_labelled_records_of_removed_roots(self):
        import ast
        from sandbox.project_registry import JsonRegistryRepository
        from sandbox.resources.remote import _REMOTE_PROGRAM
        tree = ast.parse(_REMOTE_PROGRAM.replace("__REQUEST__", "'{}'"))
        wanted = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name in {"reconcile_after_removal", "_sweep_ranges"}]
        runtime_dir = self.home / "runtime"
        runtime_dir.mkdir(exist_ok=True)
        namespace = {"RUNTIME": runtime_dir, "HOME": self.home, "Path": Path,
                     "LEASE_NAME": __import__("re").compile(r"[A-Za-z0-9_.-]+"),
                     "LEASE_DIR": runtime_dir / "leases", "SB": "sb",
                     "run": lambda argv, timeout: (0, "", "")}
        exec(compile(ast.Module(wanted, []), "remote", "exec"), namespace)
        registry = JsonRegistryRepository(runtime_dir / "registry.json")
        gone, kept = self.home / "deploy" / "site", self.home / "deploy" / "other"
        kept.mkdir(parents=True)
        registry.put(str(gone), "default", instance="site")
        registry.put(str(gone), "qa", instance="site-qa")
        registry.put(str(kept), "default", instance="other")
        result = namespace["reconcile_after_removal"]({str(gone)}, {})
        self.assertEqual(result["registry_removed"], 2)
        self.assertEqual({r["instance"] for r in registry.read_only_all().values()}, {"other"})

        # An older v2 writer stored a bare-root key with no label.
        legacy = (self.home / "deploy" / "legacy").resolve()
        data = json.loads((runtime_dir / "registry.json").read_text())
        data["instances"][str(legacy)] = {"root": str(legacy), "instance": "legacy"}
        (runtime_dir / "registry.json").write_text(json.dumps(data))
        result = namespace["reconcile_after_removal"]({str(legacy)}, {})
        self.assertEqual(result["registry_removed"], 1)
        self.assertEqual({r["instance"] for r in registry.read_only_all().values()}, {"other"})

    def test_instance_owner_cursor_is_validated(self):
        self.store.assign("10.200.0.0/24", 26, confirm=True, holder=HOLDER)
        for cursor in ("$(id)", "../x", ""):
            with self.subTest(cursor=cursor), self.assertRaises(RangeError) as caught:
                self.store.instance_owners(cursor)
            self.assertEqual(caught.exception.code, "range_request_invalid")

    def test_reap_removes_networks_first_and_retries_survivors(self):
        self.store.assign("10.200.0.0/24", 25, confirm=True, holder=HOLDER)
        runtime = self._home_runtime()
        self.configs["a"] = config("sandbox-a", "default")
        self.configs["b"] = config("sandbox-b", "default")
        runtime.prepare("a")
        runtime.prepare("b")
        removed = []

        def remove(network):
            removed.append(network)
            return network != "sandbox-b_default"  # b's network is still in use
        self.assertEqual(self._sweep(set(), remove_network=remove),
                         {"released": 1, "retained": ["b"]})
        self.assertEqual(removed, ["sandbox-a_default", "sandbox-b_default"])
        self.assertEqual([r["owner_id"] for r in self.store.stats()["table"]], ["instance:b"])
        # The next reap retries the retained owner once its network is free.
        self.assertEqual(self._sweep(set()), {"released": 1, "retained": []})
        self.assertEqual(self.store.stats()["table"], [])

    def test_reap_pages_owners_so_none_is_released_unseen(self):
        from unittest.mock import patch
        from sandbox.remote_network import program
        self.store.assign("10.200.0.0/24", 26, confirm=True, holder=HOLDER)
        runtime = self._home_runtime()
        for name in ("a", "b", "c"):
            self.configs[name] = config(f"sandbox-{name}", "default")
            runtime.prepare(name)
        removed = []
        with patch.object(program, "MAX_LISTED", 1):
            result = self._sweep({"a"}, remove_network=lambda n: removed.append(n) or True)
        self.assertEqual(result, {"released": 2, "retained": []})
        self.assertEqual(removed, ["sandbox-b_default", "sandbox-c_default"])

    def test_reap_skips_recent_and_in_flight_owners(self):
        self.store.assign("10.200.0.0/24", 25, confirm=True, holder=HOLDER)
        runtime = self._home_runtime()
        self.configs["a"] = config("sandbox-a", "default")
        runtime.prepare("a")
        self.assertEqual(self._sweep(set(), grace_seconds=600), {"released": 0, "retained": []})
        with runtime.lifecycle("a"):
            self.assertEqual(self._sweep(set()), {"released": 0, "retained": []})
        self.assertEqual(self._sweep(set()), {"released": 1, "retained": []})

    def test_volume_down_lock_excludes_a_concurrent_up(self):
        entered = threading.Event()
        with self.runtime.lifecycle("a", exclusive=True):
            worker = threading.Thread(target=lambda: self._enter_shared(entered))
            worker.start()
            self.assertFalse(entered.wait(0.3))
        self.assertTrue(entered.wait(5))
        worker.join(5)

    def _enter_shared(self, entered):
        with self.runtime.lifecycle("a"):
            entered.set()

    def test_prepare_leaves_no_temporary_files(self):
        self.store.assign("10.200.0.0/24", 26, confirm=True, holder=HOLDER)
        self.configs["a"] = config("sandbox-a", "default")
        self.runtime.prepare("a")
        self.runtime.prepare("a")
        self.assertEqual(sorted(p.name for p in self.overrides.iterdir()), ["a.yml"])

    def test_reap_sweep_is_inert_without_ranges(self):
        self.runner.fail = True
        self.assertEqual(range_runtime.release_removed_instances(set, self.home),
                         {"released": 0, "retained": []})

    def test_instance_names_that_cannot_be_owner_ids_are_refused(self):
        self.store.assign("10.200.0.0/24", 25, confirm=True, holder=HOLDER)
        with self.assertRaises(RangeError):
            self.runtime.prepare("../escape")


class _FakeRanges:
    def __init__(self, refusal=None):
        self.events, self.refusal = [], refusal

    @contextlib.contextmanager
    def lifecycle(self, instance, *, exclusive=False):
        self.events.append(("lock", instance, exclusive))
        try:
            yield 77
        finally:
            self.events.append(("unlock", instance))

    def prepare(self, instance):
        self.events.append(("prepare", instance))
        if self.refusal is not None:
            raise self.refusal

    def compose_args(self, instance):
        return ["-f", "/o/" + instance + ".yml"]

    def release(self, instance):
        self.events.append(("release", instance))
        return 1


class ComposeHookTests(unittest.TestCase):
    def _compose(self, fake, *calls, result=None):
        from unittest.mock import patch
        from sandbox.core import _docker
        argv, results = [], []

        def run(cmd, **kw):
            argv.append(cmd)
            return result
        with patch.object(_docker, "run", side_effect=run), \
                patch("sandbox.remote_network.runtime.default_runtime", return_value=fake):
            for args, kw in calls:
                results.append(_docker.compose(*args, instance="x", **kw))
        return argv, results

    def test_compose_passes_the_override_last_and_frees_on_volume_down(self):
        fake = _FakeRanges()
        argv, _ = self._compose(fake, *[(args, {}) for args in (
            ("up", "-d"), ("create",), ("run", "--rm", "wpcli", "wp"), ("ps",), ("kill",),
            ("stop",), ("down",), ("down", "-v"), ("down", "--volumes"))])
        # A killed or stopped stack keeps its subnets attributed (FR-006).
        self.assertEqual([e for e in fake.events if e[0] not in ("lock", "unlock")],
                         [("prepare", "x")] * 3 + [("release", "x")] * 2)
        # Only a volume-removing down holds the lifecycle lock exclusively.
        self.assertEqual([e[2] for e in fake.events if e[0] == "lock"],
                         [False] * 3 + [True] * 2)
        for cmd in argv:
            files = [cmd[i + 1] for i, item in enumerate(cmd) if item == "-f"]
            self.assertEqual(files[-1], "/o/x.yml")

    def test_failed_volume_down_keeps_allocations(self):
        import subprocess
        fake = _FakeRanges()
        self._compose(fake, (("down", "-v"), {"check": False}),
                      result=subprocess.CompletedProcess([], 1, "", "boom"))
        self.assertNotIn(("release", "x"), fake.events)

    def test_refusal_without_check_is_a_failed_result_not_an_exit(self):
        # Apply calls compose(check=False) and rolls back on a non-zero result.
        fake = _FakeRanges(RangeError(range_runtime.EXHAUSTED, "no free subnet", allocation_table=[
            {"owner_id": "instance:a", "workspace_id": "site-a"}]))
        argv, results = self._compose(fake, (("up", "-d"), {"check": False}))
        self.assertEqual(argv, [])
        self.assertEqual(results[0].returncode, 1)
        self.assertTrue(results[0].stderr.startswith("docker_network_subnet_exhausted: "))

    def test_refusal_with_check_exits(self):
        fake = _FakeRanges(RangeError(range_runtime.EXHAUSTED, "no free subnet"))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self._compose(fake, (("up", "-d"), {}))

    def test_unreadable_compose_config_refuses_typed(self):
        import subprocess
        fake = _FakeRanges(subprocess.CalledProcessError(1, ["docker"]))
        _, results = self._compose(fake, (("up", "-d"), {"check": False}))
        self.assertTrue(results[0].stderr.startswith("range_prepare_failed: "))

    def test_failing_compose_config_never_exits_past_the_callers_rollback(self):
        import subprocess
        from unittest.mock import patch
        from sandbox.core import _docker, _ui
        failed = subprocess.CompletedProcess([], 1, "", "bad compose file")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            range_runtime.state_file(home).parent.mkdir(parents=True)
            range_runtime.state_file(home).write_text("{}")
            ranges = range_runtime.RangeRuntime(home, store=None, overrides=home / "o",
                                                compose_config=_docker._compose_config)
            with patch.object(_ui.subprocess, "run", return_value=failed):
                # `run(check=True)` would sys.exit() here, past apply's rollback.
                refusal = _docker._prepare_ranges(ranges, "x")
        self.assertTrue(refusal.startswith("range_prepare_failed: "))

    def test_collision_on_a_granted_subnet_is_typed(self):
        import subprocess
        fake = _FakeRanges()
        _, results = self._compose(fake, (("up", "-d"), {"check": False, "capture": True}),
                                   result=subprocess.CompletedProcess(
                                       [], 1, "", "Error response from daemon: Pool overlaps with "
                                       "other one on this address space"))
        self.assertTrue(results[0].stderr.startswith("range_network_collision: "))
        self.assertNotIn("Pool overlaps", results[0].stderr)

    def test_builtin_up_reports_range_refusals_by_code(self):
        from sandbox.commands.lifecycle import _range_refusal
        self.assertEqual(_range_refusal("range_network_collision: a granted subnet collides")[0],
                         "range_network_collision")
        self.assertEqual(_range_refusal("docker_network_subnet_exhausted: none\n  held by")[0],
                         "docker_network_subnet_exhausted")
        # Without ranges compose() never rewrites Docker's text, so raw overlap is not typed.
        self.assertIsNone(_range_refusal("Pool overlaps with other one on this address space"))

    def test_outside_callers_hold_the_lock_through_their_command(self):
        from unittest.mock import patch
        from sandbox.core import _docker
        fake = _FakeRanges()
        with patch("sandbox.remote_network.runtime.default_runtime", return_value=fake):
            with _docker.range_compose_session("x", ["run", "--rm", "wpcli"]) as (args, fds):
                self.assertEqual((args, fds), (["-f", "/o/x.yml"], (77,)))
                # Still held while the caller runs its command.
                self.assertEqual([e[0] for e in fake.events], ["lock", "prepare"])
            self.assertEqual(fake.events[-1], ("unlock", "x"))
            with _docker.range_compose_session("x", ["exec", "wp"]) as (args, fds):
                self.assertEqual((args, fds), (["-f", "/o/x.yml"], ()))
        self.assertEqual([e[0] for e in fake.events], ["lock", "prepare", "unlock"])
        fake = _FakeRanges(RangeError(range_runtime.EXHAUSTED, "no free subnet"))
        with patch("sandbox.remote_network.runtime.default_runtime", return_value=fake), \
                self.assertRaises(RuntimeError) as caught:
            with _docker.range_compose_session("x", ["run", "--rm", "wpcli"]):
                self.fail("a refused allocation must not run the command")
        self.assertTrue(str(caught.exception).startswith("docker_network_subnet_exhausted: "))
        self.assertEqual(fake.events[-1], ("unlock", "x"))
        with patch("sandbox.remote_network.runtime.default_runtime", return_value=None):
            with _docker.range_compose_session("x", ["run"]) as session:
                self.assertEqual(session, ([], ()))

    def test_inherited_lifecycle_lock_outlives_the_parent_context(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            ranges = range_runtime.RangeRuntime(home, store=None, overrides=home / "o",
                                                compose_config=dict)
            lock = home / "o" / "x.lock"
            with ranges.lifecycle("x") as descriptor:
                child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                                         stdin=subprocess.PIPE, pass_fds=(descriptor,))
            try:
                # The parent has closed its copy; the detached child still holds it.
                with range_runtime._try_exclusive(lock) as idle:
                    self.assertFalse(idle)
            finally:
                child.communicate(b"", timeout=30)
            with range_runtime._try_exclusive(lock) as idle:
                self.assertTrue(idle)

    def test_detached_job_launch_passes_the_lock_to_its_supervisor(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from sandbox.commands import jobs
        held = []

        @contextlib.contextmanager
        def session(instance, args):
            held.append(True)
            yield ["-f", "/o/unit.yml"], (77,)
            held.append(False)

        def popen(argv, **kwargs):
            # Launched while the range lock is held, and handed to the child.
            self.assertEqual(held, [True])
            self.assertEqual(kwargs["pass_fds"], (77,))
            files = [argv[i + 1] for i, item in enumerate(argv) if item == "-f"]
            self.assertEqual(files[-1], "/o/unit.yml")
            return SimpleNamespace(pid=5252)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(jobs, "wp_dir", return_value=root), \
                    patch.object(jobs, "_is_herd_instance", return_value=False), \
                    patch.object(jobs, "project_name", return_value="sandbox-unit"), \
                    patch.object(jobs, "compose_file", return_value=root / "unit.yml"), \
                    patch.object(jobs, "range_compose_session", session), \
                    patch.object(jobs.subprocess, "Popen", side_effect=popen) as launched:
                jobs.launch_job("unit", ["option", "get", "siteurl"])
        self.assertEqual(launched.call_count, 1)
        self.assertEqual(held, [True, False])

    def test_detached_supervisor_abandoned_before_handle_releases_range_lock(self):
        import subprocess
        from types import SimpleNamespace
        from unittest.mock import patch
        from sandbox.commands import jobs
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(jobs, "wp_dir", return_value=root), \
                    patch.object(jobs, "_is_herd_instance", return_value=False), \
                    patch.object(jobs, "project_name", return_value="sandbox-unit"), \
                    patch.object(jobs, "compose_file", return_value=root / "unit.yml"), \
                    patch.object(jobs, "_BOOTSTRAP_TICKS", 5), \
                    patch("sandbox.remote_network.runtime.default_runtime", return_value=None), \
                    patch.object(jobs.subprocess, "Popen",
                                 return_value=SimpleNamespace(pid=1)) as launched:
                jobs.launch_job("unit", ["option", "get", "siteurl"])
            script = launched.call_args.args[0][2]
            ranges = range_runtime.RangeRuntime(root, store=None, overrides=root / "o",
                                                compose_config=dict)
            status, handle = root / "job.status", root / "job.pid"
            marker = root / "compose-ran"
            # The launcher "dies" here: it never publishes the handle.
            with ranges.lifecycle("unit") as descriptor:
                supervisor = subprocess.Popen(
                    ["sh", "-c", script, "c", "c", str(status), str(handle),
                     str(root / "job.tmp"), "touch", str(marker)], pass_fds=(descriptor,))
            self.assertEqual(supervisor.wait(timeout=30), 125)
            self.assertEqual(status.read_text(), "125")
            self.assertFalse(marker.exists())
            with range_runtime._try_exclusive(root / "o" / "unit.lock") as idle:
                self.assertTrue(idle)

    def test_introspect_runs_under_the_lock(self):
        import subprocess
        from types import SimpleNamespace
        from unittest.mock import patch
        from sandbox.commands import debug
        held = []

        @contextlib.contextmanager
        def session(instance, args):
            held.append(True)
            yield ["-f", "/o/x.yml"], (77,)
            held.append(False)

        def run(argv, **kwargs):
            self.assertEqual(held, [True])
            files = [argv[i + 1] for i, item in enumerate(argv) if item == "-f"]
            self.assertEqual(files[-1], "/o/x.yml")
            return subprocess.CompletedProcess(argv, 0, '{"count": 1}', "")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(debug, "range_compose_session", session), \
                    patch.object(debug, "RUNTIME_DIR", root / "runtime"), \
                    patch.object(debug, "ROOT", root), \
                    patch.object(debug, "project_name", return_value="sandbox-x"), \
                    patch.object(debug, "compose_file", return_value=root / "x.yml"), \
                    patch.object(debug.subprocess, "run", side_effect=run), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                debug.cmd_introspect({}, SimpleNamespace(target="blocks", resolved_instance="x"))
        self.assertEqual(held, [True, False])

    def test_mcp_compose_runs_inside_the_range_session(self):
        # The MCP server needs its own runtime to import; check its shape instead.
        import ast
        source = (Path(__file__).resolve().parent.parent / "mcp" / "wp-server" / "app.py").read_text()
        function = next(node for node in ast.walk(ast.parse(source))
                        if isinstance(node, ast.FunctionDef) and node.name == "_compose")
        sessions = [node for node in ast.walk(function) if isinstance(node, ast.With)
                    and any("range_compose_session" in ast.unparse(stmt) for stmt in node.body)]
        self.assertEqual(len(sessions), 1)
        runs = [node for stmt in sessions[0].body for node in ast.walk(stmt)
                if isinstance(node, ast.Call) and ast.unparse(node.func) == "subprocess.run"]
        self.assertEqual(len(runs), 1)


if __name__ == "__main__":
    unittest.main()
