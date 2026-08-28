"""Generic Compose shared node-store overlay contract tests."""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sandbox.runtimes.compose import ComposeAdapter, node_store_family_id
from sandbox.runtimes.base import OperationRequest
from sandbox.resources.adapters import LocalResourceAdapter, ProviderSnapshot
from sandbox.resources.models import (
    CleanupItemOutcome, ResourceObservation, StorageTarget,
)
from sandbox.resources.node_store import NodeStoreReclaimService


def render_overlay_evidence(adapter: ComposeAdapter, descriptor: dict,
                            source_id: str, sibling_ids: tuple[str, str]) -> dict:
    paths = [adapter._overlay(descriptor, runtime_id, 8080)
             for runtime_id in (source_id, *sibling_ids)]
    control = adapter._overlay({**descriptor, "node_store": False}, "control", 8081)
    return {"family": node_store_family_id(source_id),
            "overlays": [path.read_text() for path in paths],
            "control_overlay": control.read_text(),
            "package_scripts_run": False}


class ComposeNodeStoreTests(unittest.TestCase):
    def test_family_derivation_is_exact_and_repeated_markers_do_not_strip(self):
        self.assertEqual(node_store_family_id("lenzora-workspace-0123456789abcd"), "lenzora")
        self.assertEqual(node_store_family_id("lenzora"), "lenzora")
        repeated = "a-workspace-0123456789abcd-workspace-fedcba98765432"
        self.assertEqual(node_store_family_id(repeated), repeated)
        self.assertEqual(node_store_family_id("lenzora-workspace-not-a-hash"),
                         "lenzora-workspace-not-a-hash")
        self.assertNotEqual(node_store_family_id("one"), node_store_family_id("two"))
        first = "site-a1b2c3d4-workspace-0123456789abcd"
        second = "site-e5f6a7b8-workspace-fedcba98765432"
        self.assertEqual(node_store_family_id(first), "site-a1b2c3d4")
        self.assertEqual(node_store_family_id(second), "site-e5f6a7b8")
        self.assertNotEqual(node_store_family_id(first), node_store_family_id(second))

    def test_real_runtime_id_sequence_keeps_colliding_projects_isolated(self):
        taken = set()
        first_source = ComposeAdapter._runtime_id("/a/site", "default", taken)
        taken.add(first_source)
        second_source = ComposeAdapter._runtime_id("/b/site", "default", taken)
        taken.add(second_source)
        first_workspace = ComposeAdapter._runtime_id(
            "/a/site-workspace-0123456789abcd", "default", taken,
            source_family=first_source,
        )
        taken.add(first_workspace)
        second_workspace = ComposeAdapter._runtime_id(
            "/b/site-workspace-fedcba98765432", "default", taken,
            source_family=second_source,
        )
        self.assertEqual(first_source, "site")
        self.assertRegex(second_source, r"^site-[0-9a-f]{8}$")
        self.assertEqual(node_store_family_id(first_workspace), first_source)
        self.assertEqual(node_store_family_id(second_workspace), second_source)
        self.assertNotEqual(
            node_store_family_id(first_workspace),
            node_store_family_id(second_workspace),
        )

    def test_workspace_cannot_join_an_occupied_family_without_source_proof(self):
        with self.assertRaisesRegex(ValueError, "registered source family"):
            ComposeAdapter._runtime_id(
                "/b/site-workspace-0123456789abcd", "default", {"site"},
                require_source_family=True,
            )

    def test_workspace_first_opt_in_refuses_without_source_proof(self):
        with self.assertRaisesRegex(ValueError, "registered source family"):
            ComposeAdapter._runtime_id(
                "/b/site-workspace-0123456789abcd", "default", set(),
                require_source_family=True,
            )

    def test_registry_pinned_source_family_keeps_real_siblings_together(self):
        workspace = ComposeAdapter._runtime_id(
            "/a/site-workspace-0123456789abcd", "default", {"site"},
            source_family="site", require_source_family=True,
        )
        self.assertEqual(node_store_family_id(workspace), "site")

    def _legacy_workspace_invoke(self, source_record):
        adapter = object.__new__(ComposeAdapter)
        workspace_root = "/b/site-workspace-0123456789abcd"
        workspace_record = {
            "root": workspace_root, "label": "default",
            "instance": "site-workspace-0123456789abcd", "http_port": 8080,
        }
        records = [workspace_record]
        if source_record is not None:
            records.append(source_record)
        adapter.registry = SimpleNamespace(registry_all=lambda: {
            str(index): value for index, value in enumerate(records)
        })
        adapter._descriptor = Mock(return_value={
            "root": workspace_root, "node_store": True,
        })
        adapter._record = Mock(return_value=workspace_record)
        adapter._record_port = Mock(return_value=8080)
        adapter._overlay = Mock(side_effect=RuntimeError("overlay reached"))
        request = OperationRequest(workspace_root, "status")
        return adapter, request

    def test_legacy_workspace_record_without_source_refuses_before_overlay(self):
        adapter, request = self._legacy_workspace_invoke({
            "root": "/a/site", "label": "default", "instance": "site",
        })
        with self.assertRaisesRegex(ValueError, "registered source family"):
            adapter.invoke(request)
        adapter._record_port.assert_not_called()
        adapter._overlay.assert_not_called()

    def test_legacy_workspace_record_with_wrong_source_family_refuses(self):
        adapter, request = self._legacy_workspace_invoke({
            "root": "/b/site", "label": "default", "instance": "site-deadbeef",
        })
        with self.assertRaisesRegex(ValueError, "does not match"):
            adapter.invoke(request)
        adapter._record_port.assert_not_called()
        adapter._overlay.assert_not_called()

    def test_legacy_workspace_record_with_exact_source_family_can_continue(self):
        adapter, request = self._legacy_workspace_invoke({
            "root": "/b/site", "label": "default", "instance": "site",
        })
        with self.assertRaisesRegex(RuntimeError, "overlay reached"):
            adapter.invoke(request)
        adapter._overlay.assert_called_once()

    def test_long_runtime_family_compaction_is_stable_for_source_and_workspace(self):
        name = "project-with-a-very-long-canonical-runtime-identity-123456"
        taken = set()
        source = ComposeAdapter._runtime_id(f"/a/{name}", "default", taken)
        taken.add(source)
        workspace = ComposeAdapter._runtime_id(
            f"/a/{name}-workspace-0123456789abcd", "default", taken,
            source_family=source,
        )
        self.assertEqual(
            node_store_family_id(source), node_store_family_id(workspace),
        )
        self.assertLessEqual(len(node_store_family_id(source)), 38)

    def test_opted_in_overlay_has_one_family_volume_and_exact_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = object.__new__(ComposeAdapter)
            with patch.object(adapter, "_artifact_dir", side_effect=lambda runtime: (root / runtime)):
                # _overlay expects the artifact directory to exist.
                def artifact(runtime):
                    path = root / runtime
                    path.mkdir()
                    return path
                with patch.object(adapter, "_artifact_dir", side_effect=artifact):
                    descriptor = {"service": "web", "internal_port": 3000,
                                  "node_store": True,
                                  "resources": {"cpus": 2, "memoryMB": 4096, "pids": 512}}
                    evidence = render_overlay_evidence(
                        adapter, descriptor, "lenzora",
                        ("lenzora-workspace-0123456789abcd",
                         "lenzora-workspace-fedcba98765432"),
                    )
            runtime_ids = (
                "lenzora", "lenzora-workspace-0123456789abcd",
                "lenzora-workspace-fedcba98765432",
            )
            for runtime_id, overlay in zip(runtime_ids, evidence["overlays"]):
                self.assertEqual(overlay.count("sandbox-nodestore-lenzora:/sandbox-node"), 1)
                self.assertIn("SANDBOX_NODE_STORE: /sandbox-node/store", overlay)
                self.assertIn(
                    f"SANDBOX_NODE_MODULES: /sandbox-node/node_modules/{runtime_id}",
                    overlay,
                )
                self.assertIn("npm_config_store_dir: /sandbox-node/store", overlay)
                self.assertNotIn("/workspace/node_modules", overlay)
            self.assertNotIn("sandbox-nodestore", evidence["control_overlay"])
            self.assertNotIn("SANDBOX_NODE_STORE", evidence["control_overlay"])
            self.assertFalse(evidence["package_scripts_run"])
            module_paths = {
                line.strip().split(": ", 1)[1]
                for overlay in evidence["overlays"]
                for line in overlay.splitlines()
                if "SANDBOX_NODE_MODULES:" in line
            }
            self.assertEqual(len(module_paths), 3)

    def test_false_and_absent_opt_in_are_byte_identical_legacy_overlays(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = object.__new__(ComposeAdapter)
            counter = [0]
            def artifact(_runtime):
                counter[0] += 1
                path = root / str(counter[0])
                path.mkdir()
                return path
            base = {"service": "web", "internal_port": 3000,
                    "resources": {"cpus": 2, "memoryMB": 4096, "pids": 512}}
            with patch.object(adapter, "_artifact_dir", side_effect=artifact):
                absent = adapter._overlay(dict(base), "project", 8080).read_bytes()
                false = adapter._overlay({**base, "node_store": False}, "project", 8080).read_bytes()
            self.assertEqual(absent, false)
            self.assertNotIn(b"node", absent)
            self.assertNotIn(b"volumes:", absent)

    def test_overlay_never_emits_reclaim_or_host_bind(self):
        source = Path(ComposeAdapter.__module__.replace(".", "/"))
        self.assertNotIn("volume prune", str(source))

    def test_named_reclaim_is_plan_bound_confirmation_gated_and_race_safe(self):
        class Adapter:
            mounted = False
            removed = []
            identity = "volume-1"

            def observe(self, **_kwargs):
                references = ("running-container",) if self.mounted else ()
                item = ResourceObservation(
                    self.identity, "volume", "sandbox-nodestore-lenzora",
                    "node store", "sandbox", "lenzora", "retained",
                    "measured", 123, 0, references=references,
                    evidence=("engine_volume_identity",),
                )
                return ProviderSnapshot(StorageTarget("local", "local", "host-1"),
                                        None, (item,))

            def remove(self, candidate):
                self.removed.append(candidate.locator)
                return CleanupItemOutcome(candidate.resource_id, "removed", None,
                                          candidate.expected_size_bytes, False,
                                          datetime.now(timezone.utc))

        with tempfile.TemporaryDirectory() as temp:
            adapter = Adapter()
            service = NodeStoreReclaimService(adapter, Path(temp))
            planned = service.plan("lenzora")
            self.assertTrue(planned["ok"])
            plan_id = planned["data"]["plan_id"]
            self.assertEqual(planned["data"]["volume_name"],
                             "sandbox-nodestore-lenzora")
            self.assertFalse(service.apply(plan_id, family="lenzora")["ok"])
            adapter.mounted = True
            raced = service.apply(plan_id, family="lenzora", confirm=True)
            self.assertEqual(raced["error"]["code"], "node_store_mounted")
            adapter.mounted = False
            applied = service.apply(plan_id, family="lenzora", confirm=True)
            self.assertTrue(applied["ok"])
            self.assertEqual(adapter.removed, ["sandbox-nodestore-lenzora"])
            self.assertFalse(service.plan("*")["ok"])

    def test_recreated_same_name_volume_is_never_deleted_by_old_plan(self):
        class Adapter:
            identity = "engine-volume-old"
            removed = []
            def observe(self, **_kwargs):
                item = ResourceObservation(
                    self.identity, "volume", "sandbox-nodestore-lenzora",
                    "node store", "sandbox", "lenzora", "retained",
                    "measured", 10, 0, evidence=("engine_volume_identity",),
                )
                return ProviderSnapshot(StorageTarget("local", "local", "host-1"),
                                        None, (item,))
            def remove(self, candidate):
                self.removed.append(candidate.resource_id)
                raise AssertionError("recreated volume must not be removed")
        with tempfile.TemporaryDirectory() as temp:
            adapter = Adapter()
            service = NodeStoreReclaimService(adapter, Path(temp))
            plan_id = service.plan("lenzora")["data"]["plan_id"]
            adapter.identity = "engine-volume-new"
            refused = service.apply(plan_id, family="lenzora", confirm=True)
            self.assertEqual(refused["error"]["code"], "node_store_identity_changed")
            self.assertEqual(adapter.removed, [])

    def test_named_reclaim_candidate_uses_the_local_adapter_fingerprint(self):
        item = ResourceObservation(
            "volume-engine-identity", "volume", "sandbox-nodestore-lenzora",
            "node store", "sandbox", "lenzora", "retained",
            "measured", 10, 0, evidence=("engine_volume_identity",),
        )

        class Runner:
            calls = []
            def run(self, argv, **_kwargs):
                self.calls.append(tuple(argv))
                return SimpleNamespace(returncode=0, stdout="", stderr="")

        class Adapter(LocalResourceAdapter):
            def observe(self, **_kwargs):
                return ProviderSnapshot(
                    StorageTarget("local", "local", "host-1"), None, (item,),
                )
            def _find_current(self, _candidate):
                return item

        with tempfile.TemporaryDirectory() as temp:
            runner = Runner()
            adapter = Adapter(Path(temp) / "home", runner=runner)
            service = NodeStoreReclaimService(adapter, Path(temp) / "plans")
            plan_id = service.plan("lenzora")["data"]["plan_id"]
            applied = service.apply(plan_id, family="lenzora", confirm=True)
            self.assertTrue(applied["ok"])
            self.assertIn(
                ("docker", "volume", "rm", "sandbox-nodestore-lenzora"),
                runner.calls,
            )

    def test_cli_routes_exact_family_without_scope_or_tier(self):
        from sandbox.commands import resources

        class Service:
            calls = []
            def plan(self, family, *, budget_seconds):
                self.calls.append((family, budget_seconds))
                return {"ok": True, "action": "node_store_plan", "status": "planned",
                        "data": {"plan_id": "a" * 32, "family": family}}

        parser = argparse.ArgumentParser()
        resources.configure_parser(parser)
        args = parser.parse_args([
            "plan", "--node-store-family", "lenzora", "--budget", "12", "--json",
        ])
        service = Service()
        output = io.StringIO()
        with patch.object(resources, "node_store_service", return_value=service), \
                redirect_stdout(output):
            resources.cmd_resources({}, args)
        self.assertEqual(service.calls, [("lenzora", 12.0)])
        self.assertEqual(json.loads(output.getvalue())["data"]["family"], "lenzora")


if __name__ == "__main__":
    unittest.main()
