"""Spec 021 T060 live-state and stale-observation contract tests."""

import tempfile
import unittest
from pathlib import Path

from sandbox.application.runtime_service import RuntimeService
from sandbox.runtimes.base import (
    AdapterRegistry,
    OperationRequest,
    OperationResult,
    RuntimeDependencies,
)
from sandbox.runtimes.compose import ComposeAdapter
from sandbox.services.process import ProcessResult


class _MutableProcess:
    def __init__(self):
        self.status = '[{"Service":"web","State":"running"}]\n'
        self.returncode = 0

    def run(self, argv, *, cwd=None, env=None, timeout=None):
        if "ps" in argv:
            return ProcessResult(tuple(argv), self.returncode, self.status, "")
        if "config" in argv:
            return ProcessResult(tuple(argv), 0, "web\n", "")
        return ProcessResult(tuple(argv), 0, "", "")


class _AlwaysHealthyHttp:
    def probe(self, url, *, timeout=5):
        return True


class _Ports:
    def allocate(self, preferred=None):
        return preferred or 49152


class _Registry:
    def __init__(self, root, descriptor):
        self.root = str(root)
        self.descriptor = descriptor
        self.records = {}

    def load_project_config(self, root, label="default"):
        return self.descriptor

    def registry_all(self):
        return {f"{self.root}::{label}": value
                for label, value in self.records.items()}

    def registry_find_instance(self, instance):
        return next((value for value in self.records.values()
                     if value.get("instance") == instance), None)

    def registry_get(self, root, label="default"):
        return self.records.get(label)

    def registry_put(self, root, label="default", **fields):
        self.records[label] = dict(fields)
        return self.records[label]

    def registry_remove(self, root, label="default"):
        self.records.pop(label, None)
        return True


def _compose_service(root, process):
    descriptor = {
        "root": str(root),
        "kind": "compose",
        "compose_file": str(root / "compose.yaml"),
        "service": "web",
        "internal_port": 80,
        "health_path": "/healthz",
        "framework": "fixture",
    }
    registry = _Registry(root, descriptor)
    adapter = ComposeAdapter(
        RuntimeDependencies(
            process=process,
            http=_AlwaysHealthyHttp(),
            ports=_Ports(),
            paths=object(),
            proxy=object(),
            registry=registry,
        ),
        registry,
    )
    adapters = AdapterRegistry()
    adapters.register("compose", adapter, kinds=("compose",), owner="tests")
    service = RuntimeService(
        resolve_descriptor=lambda _root, label="default": descriptor,
        adapters=adapters,
    )
    return service


class _SnapshotAdapter:
    capabilities = frozenset({"status"})

    def invoke(self, request):
        return OperationResult(
            True,
            request.operation,
            request.project_root,
            "wordpress",
            {
                "status": "ready",
                "plugins": [{"name": "fixture", "status": "active"}],
                "observation": {"source": "registry", "freshness": "snapshot"},
            },
        )


class _LivePluginAdapter:
    capabilities = frozenset({"status"})

    def __init__(self):
        self.plugin_status = "active"

    def invoke(self, request):
        return OperationResult(
            True,
            request.operation,
            request.project_root,
            "wordpress",
            {
                "status": "ready",
                "plugins": [{"name": "fixture", "status": self.plugin_status}],
                "observation": {"source": "wp-cli", "freshness": "live"},
            },
        )


class TestStateFreshness(unittest.TestCase):
    def test_next_compose_session_reflects_mutated_runtime_and_new_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
            process = _MutableProcess()
            service = _compose_service(root, process)

            first = service.invoke(OperationRequest(str(root), "status"))
            process.status = '[{"Service":"web","State":"exited"}]\n'
            second = service.invoke(OperationRequest(str(root), "status"))

            self.assertEqual(first.data["status"], "ready")
            self.assertEqual(second.data["status"], "ready")
            self.assertIn('"running"', first.data["compose"])
            self.assertIn('"exited"', second.data["compose"])
            self.assertTrue(first.data["state_current"])
            self.assertTrue(second.data["state_current"])
            self.assertFalse(first.data["observation"]["stale"])
            self.assertNotEqual(
                first.data["observation"]["observation_generation"],
                second.data["observation"]["observation_generation"],
            )

    def test_registry_snapshot_cannot_claim_current_state(self):
        adapters = AdapterRegistry()
        adapters.register("wordpress", _SnapshotAdapter(),
                          kinds=("wordpress",), owner="tests")
        service = RuntimeService(
            resolve_descriptor=lambda _root, label="default": {"kind": "wordpress"},
            adapters=adapters,
        )

        result = service.invoke(OperationRequest("/tmp/project", "status"))

        self.assertTrue(result.ok)
        self.assertFalse(result.data["state_current"])
        self.assertTrue(result.data["observation"]["stale"])
        self.assertEqual(result.data["observation"]["freshness"], "snapshot")
        self.assertEqual(result.data["plugins"][0]["status"], "active")

    def test_live_plugin_mutation_between_observations_changes_generation(self):
        adapter = _LivePluginAdapter()
        adapters = AdapterRegistry()
        adapters.register("wordpress", adapter, kinds=("wordpress",), owner="tests")
        service = RuntimeService(
            resolve_descriptor=lambda _root, label="default": {"kind": "wordpress"},
            adapters=adapters,
        )

        first = service.invoke(OperationRequest("/tmp/project", "status"))
        adapter.plugin_status = "inactive"
        second = service.invoke(OperationRequest("/tmp/project", "status"))

        self.assertTrue(first.data["state_current"])
        self.assertTrue(second.data["state_current"])
        self.assertEqual(second.data["plugins"][0]["status"], "inactive")
        self.assertNotEqual(
            first.data["observation"]["observation_generation"],
            second.data["observation"]["observation_generation"],
        )


if __name__ == "__main__":
    unittest.main()
