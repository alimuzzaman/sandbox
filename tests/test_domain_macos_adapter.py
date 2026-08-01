from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest


class Process:
    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def run(self, argv, *, timeout):
        self.calls.append((tuple(argv), timeout))
        return subprocess.CompletedProcess(argv, self.returncode, "", "failed")


class TestMacosDomainAdapter(unittest.TestCase):
    def test_plan_uses_only_exact_owned_resolver_fragment(self):
        from sandbox.network.adapters.macos import MacosResolverAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = MacosResolverAdapter(
                helper="/fixed/helper", process=object(), staging_root=Path(tmp),
            )
            plan = adapter.plan("test", "127.0.0.54", 5300)
        self.assertEqual(plan["destination"], "/etc/resolver/test")
        self.assertNotIn("/etc/resolv.conf", str(plan))

    def test_apply_stages_owned_candidate_and_uses_fixed_helper_contract(self):
        from sandbox.network.adapters.macos import MacosResolverAdapter

        with tempfile.TemporaryDirectory() as tmp:
            process = Process()
            adapter = MacosResolverAdapter(
                helper="/fixed/helper", process=process, staging_root=Path(tmp),
            )
            plan = adapter.plan("test", "127.0.0.54", 5300)
            result = adapter.apply(plan)
            candidate = Path(plan["candidate"])
            self.assertEqual(candidate.read_text(), plan["content"])
            self.assertEqual(candidate.stat().st_mode & 0o777, 0o600)
        self.assertTrue(result["ok"])
        self.assertEqual(process.calls[0][0][3:5], ("macos-apply", str(Path(tmp).resolve())))

    def test_apply_failure_is_reported_without_claiming_mutation(self):
        from sandbox.network.adapters.macos import MacosResolverAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = MacosResolverAdapter(
                helper="/fixed/helper", process=Process(65), staging_root=Path(tmp),
            )
            result = adapter.apply(adapter.plan("test", "127.0.0.54", 5300))
        self.assertFalse(result["ok"])
        self.assertFalse(result["mutated"])


if __name__ == "__main__":
    unittest.main()
