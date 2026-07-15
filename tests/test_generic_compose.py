import tempfile
import unittest
from pathlib import Path

from sandbox.runtimes.base import RuntimeDependencies, OperationRequest
from sandbox.services.process import ProcessResult
from sandbox.runtimes.compose import ComposeAdapter


class _Process:
    def __init__(self):
        self.calls = []

    def run(self, argv, *, cwd=None, env=None, timeout=None):
        self.calls.append((tuple(argv), cwd, timeout))
        if "config" in argv:
            return ProcessResult(tuple(argv), 0, "web\n", "")
        return ProcessResult(tuple(argv), 0, "started\n", "")


class _Http:
    def __init__(self):
        self.urls = []

    def probe(self, url, *, timeout=5):
        self.urls.append((url, timeout))
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
        return {f"{self.root}::{label}": value for label, value in self.records.items()}

    def registry_find_instance(self, instance):
        return next((value for value in self.records.values() if value.get("instance") == instance), None)

    def registry_get(self, root, label="default"):
        return self.records.get(label)

    def registry_put(self, root, label="default", **fields):
        self.records[label] = dict(fields)
        return self.records[label]

    def registry_remove(self, root, label="default"):
        self.records.pop(label, None)
        return True


class TestGenericComposeAdapter(unittest.TestCase):
    def make_adapter(self, root):
        descriptor = {
            "root": str(root), "kind": "compose", "compose_file": str(root / "compose.yaml"),
            "service": "web", "internal_port": 80, "health_path": "/healthz",
            "display_name": root.name, "label": "default", "framework": "laravel",
        }
        process, http, ports = _Process(), _Http(), _Ports()
        registry = _Registry(root, descriptor)
        deps = RuntimeDependencies(process=process, http=http, ports=ports, paths=object(), proxy=object(), registry=registry)
        return ComposeAdapter(deps, registry, timeout=2), process, http, registry

    def test_frameworks_share_one_compose_adapter_and_ensure_is_idempotent(self):
        with tempfile.TemporaryDirectory(suffix=".site") as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
            adapter, process, http, registry = self.make_adapter(root)

            first = adapter.invoke(OperationRequest(str(root), "ensure"))
            second = adapter.invoke(OperationRequest(str(root), "status"))

            self.assertTrue(first.ok)
            self.assertEqual(first.project_kind, "compose")
            self.assertEqual(first.data["framework"], "laravel")
            self.assertEqual(second.data["instance"], first.data["instance"])
            self.assertEqual(first.data["http_port"], 49152)
            self.assertTrue(any("--file" in call[0] and "sandbox.override.yaml" in " ".join(call[0]) for call in process.calls))
            self.assertEqual(http.urls, [("http://127.0.0.1:49152/healthz", 2)])

    def test_exec_requires_argument_list_and_never_accepts_shell_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
            adapter, _, _, _ = self.make_adapter(root)
            with self.assertRaisesRegex(ValueError, "argv list"):
                adapter.invoke(OperationRequest(str(root), "exec", arguments={"argv": "sh -c id"}))


if __name__ == "__main__":
    unittest.main()
