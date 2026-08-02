from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest


class TestDomainRepository(unittest.TestCase):
    def _binding(self):
        from sandbox.network.models import ResolutionBinding
        return ResolutionBinding.create(
            kind="exact", name="demo.test", target="127.0.0.77",
            adapter_id="hosts", owners=("/tmp/project::default",),
            desired={"line": "127.0.0.77 demo.test"},
        ).with_applied({"line": "127.0.0.77 demo.test"})

    def test_put_is_atomic_and_readable_from_another_repository(self):
        from sandbox.network.repository import DomainRepository

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resolver-state.json"
            first = DomainRepository(path)
            second = DomainRepository(path)
            binding = self._binding()
            first.put_binding(binding)
            self.assertEqual(second.binding(binding.binding_id), binding)
            self.assertFalse((path.parent / (path.name + ".tmp")).exists())

    def test_compare_before_remove_preserves_drift(self):
        from sandbox.network.repository import DomainRepository

        with tempfile.TemporaryDirectory() as tmp:
            repository = DomainRepository(Path(tmp) / "resolver-state.json")
            binding = self._binding()
            repository.put_binding(binding)
            result = repository.remove_binding_if_unchanged(binding.binding_id, "f" * 64)
            self.assertEqual(result, "drifted")
            self.assertIsNotNone(repository.binding(binding.binding_id))
            self.assertEqual(repository.snapshot()["recovery"][binding.binding_id]["status"], "drifted")

    def test_v0_state_migrates_under_lock(self):
        from sandbox.network.repository import DomainRepository

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resolver-state.json"
            path.write_text(json.dumps({"bindings": {}, "consents": {}}))
            repository = DomainRepository(path)
            self.assertEqual(repository.snapshot()["version"], 1)
            self.assertEqual(json.loads(path.read_text())["version"], 1)

    def test_recovery_survives_owner_binding_removal(self):
        from sandbox.network.models import CleanupRecovery
        from sandbox.network.repository import DomainRepository

        with tempfile.TemporaryDirectory() as tmp:
            repository = DomainRepository(Path(tmp) / "resolver-state.json")
            recovery = CleanupRecovery(
                "residual", "resolved", "a" * 64, None,
                "resolver_unavailable", None, "unavailable",
            )
            repository.put_recovery(recovery)
            self.assertEqual(repository.snapshot()["recovery"]["residual"]["reason_code"],
                             "resolver_unavailable")

    def test_operation_lock_refuses_symlink_substitution(self):
        from sandbox.network.repository import DomainRepository

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resolver-state.json"
            repository = DomainRepository(path)
            target = Path(tmp) / "foreign.lock"
            target.write_text("")
            repository.operation_lock_path.symlink_to(target)
            with self.assertRaises(OSError):
                with repository.operation():
                    self.fail("unsafe operation lock must not be entered")


if __name__ == "__main__":
    unittest.main()
