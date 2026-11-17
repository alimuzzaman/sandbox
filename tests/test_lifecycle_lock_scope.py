"""The host-wide instance lifecycle lock is short, bounded and announced."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]


def _finishes(target, seconds=3.0):
    """Run target in a daemon thread; return True when it ends within seconds."""
    done = threading.Event()
    errors = []

    def run():
        try:
            target()
        except BaseException as exc:  # surfaced by the caller
            errors.append(exc)
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    finished = done.wait(seconds)
    if finished and errors:
        raise errors[0]
    return finished


class LifecycleLockScopeTests(unittest.TestCase):
    def setUp(self):
        import sandbox.core._paths as paths
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name) / "runtime"
        patcher = mock.patch.object(paths, "RUNTIME_DIR", self.runtime)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _registry_write(self):
        from sandbox.project_registry.lifecycle import lifecycle_writer
        with lifecycle_writer():
            pass

    def test_slow_adapter_up_does_not_block_another_projects_registry_write(self):
        from sandbox.application.runtime_service import RuntimeService
        from sandbox.runtimes.base import AdapterRegistry, OperationRequest, OperationResult

        entered, release = threading.Event(), threading.Event()

        class SlowUp:
            capabilities = frozenset({"ensure"})

            def invoke(self, request):
                entered.set()          # stands in for image pull / compose up / readiness
                release.wait(10)
                return OperationResult(True, request.operation, request.project_root, "test", {})

        adapters = AdapterRegistry()
        adapters.register("test", SlowUp(), kinds=("test",), owner="tests")
        service = RuntimeService(
            resolve_descriptor=lambda root, label=None: {"kind": "test", "root": root},
            adapters=adapters)
        project_a = str(Path(self.tmp.name) / "project-a")
        worker = threading.Thread(
            target=lambda: service.invoke(OperationRequest(project_a, "ensure")), daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertTrue(_finishes(self._registry_write),
                            "registry write waited for another project's Compose up")
        finally:
            release.set()
            worker.join(10)

    def test_project_lock_holder_does_not_hold_host_lock(self):
        import sandbox_core as sc
        held, release = threading.Event(), threading.Event()

        def wordpress_ensure_like():
            with sc.project_lock(str(Path(self.tmp.name) / "project-a")):
                held.set()
                release.wait(10)

        worker = threading.Thread(target=wordpress_ensure_like, daemon=True)
        with mock.patch.dict(os.environ, {"SANDBOX_RUNTIME": str(self.runtime)}):
            worker.start()
            try:
                self.assertTrue(held.wait(5))
                self.assertTrue(_finishes(self._registry_write))
            finally:
                release.set()
                worker.join(10)

    def test_instance_list_does_not_take_the_lifecycle_lock(self):
        import sandbox.commands.instances_cmd as instances_cmd
        from sandbox.project_registry.lifecycle import lifecycle_writer

        listed = []
        held, release = threading.Event(), threading.Event()

        def holder():
            with lifecycle_writer():
                held.set()
                release.wait(10)

        worker = threading.Thread(target=holder, daemon=True)
        worker.start()
        try:
            self.assertTrue(held.wait(5))
            with mock.patch.object(instances_cmd, "cmd_instances",
                                   lambda _cfg, _args: listed.append(True)):
                args = SimpleNamespace(action="list", name=None, remote=None, local=False)
                self.assertTrue(_finishes(lambda: instances_cmd.cmd_instance({}, args)),
                                "`sb instance list` waited on the lifecycle lock")
            self.assertEqual(listed, [True])
        finally:
            release.set()
            worker.join(10)

    def test_blocked_wait_prints_one_line_and_gives_up_at_the_bound(self):
        from sandbox.project_registry import lifecycle

        lock = self.runtime / "locks" / "instance-lifecycle.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import fcntl,sys\n"
             "h=open(sys.argv[1],'a'); fcntl.flock(h, fcntl.LOCK_EX)\n"
             "print('locked', flush=True); sys.stdin.read()\n", str(lock)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.wait, 10)
        self.addCleanup(holder.stdin.close)
        self.assertEqual(holder.stdout.readline().strip(), "locked")

        stderr = io.StringIO()
        outcome = []

        def attempt():
            try:
                with lifecycle.lifecycle_writer():
                    outcome.append("acquired")
            except TimeoutError as exc:
                outcome.append(exc)

        with mock.patch.dict(os.environ, {"SANDBOX_LIFECYCLE_LOCK_TIMEOUT": "0.3"}), \
                contextlib.redirect_stderr(stderr):
            self.assertTrue(_finishes(attempt), "wait for the busy lock was unbounded")
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], TimeoutError)
        self.assertEqual(stderr.getvalue().count("waiting for another instance operation"), 1)

    def test_project_writer_is_reentrant_and_serializes_other_threads(self):
        from sandbox.project_registry.lifecycle import project_writer
        root = str(Path(self.tmp.name) / "project-a")
        held, release = threading.Event(), threading.Event()

        def nested_holder():
            with project_writer(root), project_writer(root):
                held.set()
                release.wait(10)

        worker = threading.Thread(target=nested_holder, daemon=True)
        worker.start()
        try:
            self.assertTrue(held.wait(5), "nested project_writer deadlocked")

            def same_project():
                with project_writer(root):
                    pass
            self.assertFalse(_finishes(same_project, 0.5))
        finally:
            release.set()
            worker.join(10)


if __name__ == "__main__":
    unittest.main()
