from pathlib import Path
import tempfile
import unittest


class Preflight:
    def __init__(self, ok): self.ok = ok
    def inspect(self):
        return {"ok": self.ok, "state": "ready" if self.ok else "blocked",
                "mutated": False, "reason": {"code": "ready" if self.ok else
                                             "isolation_prerequisite_missing",
                                             "missing": [] if self.ok else ["nftables"]}}


class TestManagedNativeAdapter(unittest.TestCase):
    def adapter(self, *, preflight=True, evidence=None):
        from sandbox.runtimes.managed.adapter import ManagedNativeAdapter
        from sandbox.runtimes.managed.repository import NativeRepository
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        return ManagedNativeAdapter(
            preflight=Preflight(preflight),
            repository=NativeRepository(Path(temporary.name) / "state.json"),
            evidence_id=evidence,
        )

    def test_missing_effective_gate_blocks_without_mutation(self):
        from sandbox.runtimes.base import OperationRequest
        result = self.adapter(preflight=False).invoke(
            OperationRequest("/tmp/project", "ensure"))
        self.assertFalse(result.ok); self.assertFalse(result.data["mutated"])
        self.assertEqual(result.data["reason"]["code"], "isolation_prerequisite_missing")

    def test_code_complete_but_unproven_runtime_remains_blocked(self):
        from sandbox.runtimes.base import OperationRequest
        result = self.adapter(preflight=True).invoke(
            OperationRequest("/tmp/project", "ensure"))
        self.assertFalse(result.ok)
        self.assertEqual(result.data["reason"]["code"], "managed_runtime_unproven")


if __name__ == "__main__": unittest.main()
