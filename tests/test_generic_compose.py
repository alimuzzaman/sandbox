import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.runtimes.base import RuntimeDependencies, OperationRequest
from sandbox.services.process import BoundedProcessRunner
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

    def test_exec_honors_a_finite_durable_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
            adapter, process, _, _ = self.make_adapter(root)
            adapter.invoke(OperationRequest(str(root), "exec", arguments={
                "argv": ["pnpm", "test:fast"], "timeout": 1200,
            }))
            self.assertEqual(process.calls[-1][2], 1200.0)
            with self.assertRaisesRegex(ValueError, "execution timeout"):
                adapter.invoke(OperationRequest(str(root), "exec", arguments={
                    "argv": ["pnpm", "test:fast"], "timeout": 0,
                }))

    def test_exec_failure_returns_separate_bounded_compose_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
            adapter, process, _, registry = self.make_adapter(root)

            def fail(argv, *, cwd=None, env=None, timeout=None):
                if "config" in argv:
                    return ProcessResult(tuple(argv), 0, "web\n", "")
                return ProcessResult(tuple(argv), 23,
                                     "stdout: beginning\n" + "x" * 5000 + "\nstdout: ending\n",
                                     "stderr: beginning\n" + "y" * 5000 + "\nstderr: ending\n")

            process.run = fail
            result = adapter.invoke(OperationRequest(
                str(root), "exec", arguments={"argv": ["pnpm", "test:fast"]}))
            self.assertFalse(result.ok)
            self.assertEqual(result.data["exit_code"], 23)
            self.assertEqual(result.data["stdout"],
                             "stdout: beginning\n" + "x" * 5000 + "\nstdout: ending\n")
            self.assertEqual(result.data["stderr"],
                             "stderr: beginning\n" + "y" * 5000 + "\nstderr: ending\n")
            self.assertNotIn("stderr: beginning", result.data["stdout"])
            self.assertNotIn("stdout: beginning", result.data["stderr"])
            self.assertIsNone(registry.registry_get(str(root)))

    def test_exec_failure_bounds_custom_stream_and_retains_both_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
            adapter, process, _, registry = self.make_adapter(root)

            def fail(argv, *, cwd=None, env=None, timeout=None):
                if "config" in argv:
                    return ProcessResult(tuple(argv), 0, "web\n", "")
                return ProcessResult(tuple(argv), 17,
                                     "stdout-head\n" + "s" * 1_100_000 + "\nstdout-tail\n",
                                     "stderr-head\n" + "e" * 1_100_000 + "\nstderr-tail\n")

            process.run = fail
            result = adapter.invoke(OperationRequest(
                str(root), "exec", arguments={"argv": ["pnpm", "test:fast"]}))
            for stream, head, tail in (
                    ("stdout", "stdout-head\n", "\nstdout-tail\n"),
                    ("stderr", "stderr-head\n", "\nstderr-tail\n")):
                value = result.data[stream]
                self.assertLessEqual(len(value), 1_048_576)
                self.assertTrue(value.startswith(head))
                self.assertTrue(value.endswith(tail))
                self.assertIn("output truncated", value)
            self.assertEqual(result.data["exit_code"], 17)
            self.assertIsNone(registry.registry_get(str(root)))

    def test_exec_failure_with_real_runner_retains_edges_redacts_and_separates_streams(self):
        secret = "compose-failure-secret-sentinel"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
            docker = root / "docker"
            docker.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "if 'config' in sys.argv:\n"
                "    print('web')\n"
                "    raise SystemExit(0)\n"
                f"sys.stdout.write('stdout-head\\n' + 's' * 1100000 + '\\nstdout-tail {secret}\\n')\n"
                f"sys.stderr.write('stderr-head\\n' + 'e' * 1100000 + '\\nstderr-tail {secret}\\n')\n"
                "raise SystemExit(23)\n"
            )
            docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
            descriptor = {
                "root": str(root), "kind": "compose", "compose_file": str(root / "compose.yaml"),
                "service": "web", "internal_port": 80, "health_path": "/healthz",
                "display_name": root.name, "label": "default", "framework": "node",
            }
            registry = _Registry(root, descriptor)
            deps = RuntimeDependencies(
                process=BoundedProcessRunner(secret_values=(secret,)), http=_Http(), ports=_Ports(),
                paths=object(), proxy=object(), registry=registry,
            )
            adapter = ComposeAdapter(deps, registry)
            with patch.dict(os.environ, {
                    "PATH": f"{root}{os.pathsep}{os.environ.get('PATH', '')}",
            }, clear=False):
                result = adapter.invoke(OperationRequest(
                    str(root), "exec", arguments={"argv": ["pnpm", "test:fast"]}))
            self.assertFalse(result.ok)
            self.assertEqual(result.data["exit_code"], 23)
            for stream, head, tail in (
                    ("stdout", "stdout-head\n", "\nstdout-tail [REDACTED]\n"),
                    ("stderr", "stderr-head\n", "\nstderr-tail [REDACTED]\n")):
                value = result.data[stream]
                self.assertLessEqual(len(value.encode()), 1_048_576)
                self.assertTrue(value.startswith(head))
                self.assertTrue(value.endswith(tail))
                self.assertIn("output truncated", value)
                self.assertNotIn(secret, value)
            self.assertNotIn("stderr-head", result.data["stdout"])
            self.assertNotIn("stdout-head", result.data["stderr"])
            self.assertIsNone(registry.registry_get(str(root)))

    def test_overlay_enforces_instance_resource_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
            adapter, process, _, _ = self.make_adapter(root)
            adapter.invoke(OperationRequest(str(root), "ensure"))
            overlay = next(Path(part) for call, _, _ in process.calls for part in call
                           if part.endswith("sandbox.override.yaml"))
            content = overlay.read_text()
            self.assertIn('cpus: "2"', content)
            self.assertIn('mem_limit: "4096m"', content)
            self.assertIn("pids_limit: 512", content)

    def test_ensure_can_recreate_a_dependency_bootstrapping_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
            adapter, process, _, registry = self.make_adapter(root)
            registry.descriptor.update({"startup_timeout_seconds": 300, "recreate_on_ensure": True})
            adapter.invoke(OperationRequest(str(root), "ensure"))
            up_call = next(call for call, _, _ in process.calls if "up" in call)
            self.assertIn("--force-recreate", up_call)

    def test_health_timeout_includes_bounded_service_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
            adapter, process, http, _ = self.make_adapter(root)
            adapter.timeout = 0.01
            http.probe = lambda *_args, **_kwargs: False

            def process_with_logs(argv, *, cwd=None, env=None, timeout=None):
                process.calls.append((tuple(argv), cwd, timeout))
                if "config" in argv:
                    return ProcessResult(tuple(argv), 0, "web\n", "")
                if "logs" in argv:
                    return ProcessResult(tuple(argv), 0, "pnpm install failed\n", "")
                return ProcessResult(tuple(argv), 0, "started\n", "")

            process.run = process_with_logs
            with self.assertRaisesRegex(RuntimeError, "(?s)health check.*pnpm install failed"):
                adapter.invoke(OperationRequest(str(root), "ensure"))
            self.assertTrue(any("logs" in call for call, _, _ in process.calls))

    def test_descriptor_validation_rejects_command_ambiguous_fields(self):
        invalid = (
            {"service": "web\nbad"},
            {"internal_port": True},
            {"health_path": "healthz"},
            {"health_path": "/health\x00z"},
            {"http_port": 70000},
        )
        for changes in invalid:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
                adapter, _, _, registry = self.make_adapter(root)
                registry.descriptor.update(changes)
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    adapter.invoke(OperationRequest(str(root), "status"))

    def test_constructor_rejects_unbounded_or_non_positive_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for timeout in (0, -1, float("nan"), True):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    ComposeAdapter(self.make_adapter(root)[0].dependencies, object(), timeout=timeout)


if __name__ == "__main__":
    unittest.main()
