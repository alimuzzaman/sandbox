"""Typed workspace ownership projection for local Docker resources."""

from __future__ import annotations

import json
import ast
import tempfile
import unittest
from pathlib import Path

from sandbox.resources.adapters import LocalResourceAdapter
from sandbox.resources.remote import _program
from sandbox.services.process import ProcessResult
from tests.resource_fixtures import NOW


OBSERVED_AT = NOW.isoformat()


class Runner:
    def __init__(self):
        self.calls = []
        self.network_connected = True

    def run(self, argv, *, cwd=None, env=None, timeout=None):
        command = tuple(argv)
        self.calls.append(command)
        responses = {
            ("docker", "ps", "-aq"): "",
            ("docker", "volume", "ls", "-q"): "",
            ("docker", "network", "ls", "-q"): "workspace-net\n",
            ("docker", "network", "inspect"): json.dumps([{
                "Id": "workspace-net", "Name": "sandbox-unit_default",
                "Labels": {"com.docker.compose.project": "sandbox-unit"},
                "Containers": {"container": {}} if self.network_connected else {},
            }]),
            ("docker", "image", "ls", "-q"): "",
            ("docker", "buildx", "du"): "",
        }
        output = next((value for prefix, value in responses.items()
                       if command[:len(prefix)] == prefix), "")
        return ProcessResult(command, 0, output, "")


class WorkspaceResourceOwnershipTests(unittest.TestCase):
    def test_resource_modules_use_application_projection_boundary(self):
        root = Path(__file__).parents[1] / "sandbox" / "resources"
        source = "\n".join(path.read_text() for path in root.glob("*.py"))
        self.assertNotIn("WorkspaceRepository", source)
        imported_modules = set()
        imported_names = set()
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_modules.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported_modules.add(node.module or "")
                    imported_names.update(alias.name for alias in node.names)
        self.assertNotIn("sqlite3", imported_modules)
        self.assertNotIn("WorkspaceRepository", imported_names)
        context = (root / "context.py").read_text()
        tree = ast.parse(context)
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertIn("sandbox.application.context", imports)

    def test_unique_binding_attributes_active_network_to_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = Runner()
            projection = {
                "records": [{
                    "workspace_id": "ws_unit", "project_identity": "project:unit",
                    "label": "default", "owner_kind": "workspace",
                    "lifecycle": "ready", "status": "ready",
                    "observed_at": OBSERVED_AT, "index_generation": 1,
                    "bindings": [{
                        "resource_type": "compose_project",
                        "resource_id": "sandbox-unit", "status": "owned",
                    }],
                }],
                "index_generation": 1,
                "counts": {"total": 1, "unresolved": 0, "conflict": 0, "incomplete": 0},
            }
            adapter = LocalResourceAdapter(
                Path(temp), runner=runner, clock=lambda: NOW, host_root=Path(temp),
                workspace_projection=lambda: projection,
            )
            network = next(item for item in adapter.observe(
                thorough=False, budget_seconds=30).resources if item.kind == "network")
            self.assertEqual((network.owner_kind, network.owner_id), ("workspace", "ws_unit"))
            self.assertEqual(network.classification, "active")
            self.assertIn("workspace_binding", network.evidence)
            self.assertFalse(any(call[:3] == ("docker", "network", "rm") for call in runner.calls))

    def test_active_workspace_reference_keeps_unconnected_network_active(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = Runner()
            # Replace the network fixture with an unconnected network: the
            # typed lease/job reference, not a label, is the activity proof.
            runner.network_connected = False
            projection = {
                "records": [{
                    "workspace_id": "ws_unit", "owner_kind": "workspace",
                    "lifecycle": "ready", "status": "ready",
                    "observed_at": OBSERVED_AT, "index_generation": 1,
                    "active_references": {"jobs": 1, "containers": 0},
                    "bindings": [{
                        "resource_type": "compose_project",
                        "resource_id": "sandbox-unit", "status": "owned",
                    }],
                }],
                "index_generation": 1, "counts": {"total": 1},
            }
            adapter = LocalResourceAdapter(
                Path(temp), runner=runner, clock=lambda: NOW, host_root=Path(temp),
                workspace_projection=lambda: projection,
            )
            network = next(item for item in adapter.observe(
                thorough=False, budget_seconds=30).resources if item.kind == "network")
            self.assertEqual(network.classification, "active")
            self.assertIn("workspace_active_reference", network.references)

    def test_destroyed_workspace_keeps_owner_identity_and_marks_network_orphaned(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = Runner()
            runner.network_connected = False
            projection = {
                "records": [{
                    "workspace_id": "ws_unit", "project_identity": "project:unit",
                    "label": "default", "owner_kind": "workspace",
                    "lifecycle": "destroyed", "status": "destroyed",
                    "observed_at": OBSERVED_AT, "index_generation": 1,
                    "active_references": {"leases": 0, "containers": 0, "jobs": 0},
                    "bindings": [{
                        "resource_type": "compose_project",
                        "resource_id": "sandbox-unit", "status": "owned",
                    }],
                }],
                "index_generation": 1,
                "counts": {"total": 1, "unresolved": 0, "conflict": 0, "incomplete": 0},
            }
            adapter = LocalResourceAdapter(
                Path(temp), runner=runner, clock=lambda: NOW, host_root=Path(temp),
                workspace_projection=lambda: projection,
            )
            network = next(item for item in adapter.observe(
                thorough=False, budget_seconds=30).resources if item.kind == "network")

            self.assertEqual((network.owner_kind, network.owner_id), ("workspace", "ws_unit"))
            self.assertEqual(network.lifecycle, "orphaned")
            self.assertEqual(dict(network.active_references), {
                "leases": 0, "containers": 0, "jobs": 0,
            })
            self.assertEqual(network.allocation_state, "allocated")
            self.assertFalse(network.cleanup_eligible)
            self.assertFalse(any(call[:3] == ("docker", "network", "rm") for call in runner.calls))

    def test_runtime_instance_binding_gives_workspace_worktree_an_opaque_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            worktree = home / "deploy-src" / "instance-a-workspace-unit"
            worktree.mkdir(parents=True)
            projection = {
                "records": [{
                    "workspace_id": "ws_unit", "owner_kind": "workspace",
                    "lifecycle": "ready", "status": "ready",
                    "observed_at": OBSERVED_AT, "index_generation": 1,
                    "bindings": [{
                        "resource_type": "runtime_instance",
                        "resource_id": worktree.name, "status": "owned",
                    }],
                }],
                "index_generation": 1, "counts": {"total": 1},
            }
            adapter = LocalResourceAdapter(
                home, runner=Runner(), clock=lambda: NOW, host_root=home,
                workspace_projection=lambda: projection,
            )
            worktree_item = next(item for item in adapter.observe(
                thorough=False, budget_seconds=30).resources if item.kind == "worktree")
            self.assertEqual((worktree_item.owner_kind, worktree_item.owner_id), ("workspace", "ws_unit"))

    def test_incomplete_or_missing_binding_remains_unknown_and_unreclaimable(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = LocalResourceAdapter(
                Path(temp), runner=Runner(), clock=lambda: NOW, host_root=Path(temp),
                workspace_projection=lambda: {
                    "records": [], "index_generation": 0,
                    "counts": {"total": 0, "unresolved": 1},
                },
            )
            network = next(item for item in adapter.observe(
                thorough=False, budget_seconds=30).resources if item.kind == "network")
            self.assertEqual(network.owner_kind, "unknown")
            self.assertEqual(network.reclaimable_bytes, 0)

    def test_projection_generation_drift_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            projection = {
                "index_generation": 2,
                "records": [{
                    "workspace_id": "ws_unit", "lifecycle": "ready",
                    "status": "ready", "complete": True,
                    "owner_kind": "workspace", "observed_at": OBSERVED_AT,
                    "index_generation": 1,
                    "bindings": [{
                        "resource_type": "compose_project",
                        "resource_id": "sandbox-unit", "status": "owned",
                    }],
                }],
                "counts": {"total": 1},
            }
            adapter = LocalResourceAdapter(
                Path(temp), runner=Runner(), clock=lambda: NOW, host_root=Path(temp),
                workspace_projection=lambda: projection,
            )
            network = next(item for item in adapter.observe(
                thorough=False, budget_seconds=30).resources if item.kind == "network")
            self.assertEqual(network.owner_kind, "unknown")
            self.assertEqual(network.reclaimable_bytes, 0)
            self.assertIn("workspace_index_incomplete", network.evidence)

    def test_missing_lifecycle_or_observation_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            projection = {
                "index_generation": 1,
                "records": [{
                    "workspace_id": "ws_unit", "owner_kind": "workspace",
                    "status": "ready", "index_generation": 1,
                    "bindings": [{
                        "resource_type": "compose_project",
                        "resource_id": "sandbox-unit", "status": "owned",
                    }],
                }],
                "counts": {"total": 1},
            }
            adapter = LocalResourceAdapter(
                Path(temp), runner=Runner(), clock=lambda: NOW, host_root=Path(temp),
                workspace_projection=lambda: projection,
            )
            network = next(item for item in adapter.observe(
                thorough=False, budget_seconds=30).resources if item.kind == "network")
            self.assertEqual((network.owner_kind, network.owner_id), ("unknown", None))
            self.assertIn("workspace_index_incomplete", network.evidence)

    def test_empty_projection_is_not_treated_as_successful_unowned_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = LocalResourceAdapter(
                Path(temp), runner=Runner(), clock=lambda: NOW, host_root=Path(temp),
                workspace_projection=lambda: {
                    "records": [], "index_generation": 0, "counts": {"total": 0},
                },
            )
            network = next(item for item in adapter.observe(
                thorough=False, budget_seconds=30).resources if item.kind == "network")
            self.assertEqual(network.owner_kind, "unknown")
            self.assertIn("workspace_index_incomplete", network.evidence)

    def test_duplicate_binding_is_unknown_even_when_one_project_label_matches(self):
        with tempfile.TemporaryDirectory() as temp:
            projection = {
                "records": [
                    {
                        "workspace_id": "ws_one", "owner_kind": "workspace",
                        "lifecycle": "ready", "status": "ready",
                        "observed_at": OBSERVED_AT, "index_generation": 1,
                        "bindings": [{"resource_type": "compose_project", "resource_id": "sandbox-unit"}],
                    },
                    {
                        "workspace_id": "ws_two", "owner_kind": "workspace",
                        "lifecycle": "ready", "status": "ready",
                        "observed_at": OBSERVED_AT, "index_generation": 1,
                        "bindings": [{"resource_type": "compose_project", "resource_id": "sandbox-unit"}],
                    },
                ],
                "index_generation": 1, "counts": {"total": 2},
            }
            adapter = LocalResourceAdapter(
                Path(temp), runner=Runner(), clock=lambda: NOW, host_root=Path(temp),
                workspace_projection=lambda: projection,
            )
            network = next(item for item in adapter.observe(
                thorough=False, budget_seconds=30).resources if item.kind == "network")
            self.assertEqual((network.owner_kind, network.owner_id), ("unknown", None))
            self.assertEqual(network.classification, "active")
            self.assertIn("workspace_alias_collision", network.evidence)
            self.assertEqual(network.reclaimable_bytes, 0)

    def test_remote_probe_uses_the_same_exact_binding_rules(self):
        source = _program({
            "action": "observe", "thorough": False, "deep": False,
            "budget_seconds": 30, "managed_host": True,
            "remote_name": "remote-a",
        }).split("\ntry:\n    output = remove()", 1)[0]
        namespace = {}
        exec(source, namespace)
        unique = {
            "records": [{
                "workspace_id": "ws_remote", "owner_kind": "workspace",
                "lifecycle": "ready", "status": "ready",
                "observed_at": OBSERVED_AT, "index_generation": 1,
                "bindings": [{"resource_type": "runtime_instance", "resource_id": "instance-a"}],
            }],
            "index_generation": 1, "counts": {"total": 1},
        }
        self.assertEqual(
            namespace["workspace_owner"](unique, "runtime_instance", "instance-a"),
            ("workspace", "ws_remote", ("workspace_binding", "runtime_instance"), True),
        )
        duplicate = {
            "records": [
                {"workspace_id": "ws_one", "owner_kind": "workspace", "lifecycle": "ready",
                 "status": "ready", "observed_at": OBSERVED_AT, "index_generation": 1,
                 "bindings": [{"resource_type": "runtime_instance", "resource_id": "instance-a"}]},
                {"workspace_id": "ws_two", "owner_kind": "workspace", "lifecycle": "ready",
                 "status": "ready", "observed_at": OBSERVED_AT, "index_generation": 1,
                 "bindings": [{"resource_type": "runtime_instance", "resource_id": "instance-a"}]},
            ],
            "index_generation": 1, "counts": {"total": 2},
        }
        owner_kind, owner_id, evidence, protected = namespace["workspace_owner"](
            duplicate, "runtime_instance", "instance-a",
        )
        self.assertEqual((owner_kind, owner_id, protected), ("unknown", None, False))
        self.assertIn("workspace_alias_collision", evidence)
        missing_lifecycle = {
            "index_generation": 1,
            "records": [{
                "workspace_id": "ws_remote", "owner_kind": "workspace",
                "status": "ready", "observed_at": OBSERVED_AT,
                "index_generation": 1,
                "bindings": [{
                    "resource_type": "runtime_instance", "resource_id": "instance-a",
                }],
            }],
            "counts": {"total": 1},
        }
        self.assertEqual(
            namespace["workspace_owner"](
                missing_lifecycle, "runtime_instance", "instance-a")[:2],
            ("unknown", None),
        )

    def test_remote_active_reference_is_activity_evidence(self):
        source = _program({
            "action": "observe", "thorough": False, "deep": False,
            "budget_seconds": 30, "managed_host": True,
            "remote_name": "remote-a",
        }).split("\ntry:\n    output = remove()", 1)[0]
        namespace = {}
        exec(source, namespace)
        owner_kind, owner_id, evidence, protected = namespace["workspace_owner"](
            {
                "records": [{
                    "workspace_id": "ws_remote", "owner_kind": "workspace",
                    "lifecycle": "ready", "status": "ready",
                    "observed_at": OBSERVED_AT, "index_generation": 1,
                    "active_references": {"jobs": 1},
                    "bindings": [{"resource_type": "compose_project", "resource_id": "sandbox-unit"}],
                }],
                "index_generation": 1, "counts": {"total": 1},
            },
            "compose_project", "sandbox-unit",
        )
        self.assertEqual((owner_kind, owner_id, protected), ("workspace", "ws_remote", True))
        self.assertIn("workspace_active_reference", evidence)


if __name__ == "__main__":
    unittest.main()
