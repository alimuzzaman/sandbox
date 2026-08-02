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
                process=object(), staging_root=Path(tmp),
            )
            plan = adapter.plan("test", "127.0.0.54", 5300)
        self.assertEqual(plan["destination"], "/etc/resolver/test")
        self.assertNotIn("/etc/resolv.conf", str(plan))

    def test_apply_uses_fixed_helper_without_a_user_mutable_candidate(self):
        from sandbox.network.adapters.macos import MacosResolverAdapter

        with tempfile.TemporaryDirectory() as tmp:
            process = Process()
            adapter = MacosResolverAdapter(
                process=process, staging_root=Path(tmp),
            )
            plan = adapter.plan("test", "127.0.0.54", 5300)
            plan["owner_digest"] = "b" * 64
            result = adapter.apply(plan)
        self.assertTrue(result["ok"])
        self.assertEqual(process.calls[0][0], (
            "sudo", "-n", "/usr/local/libexec/sandbox-resolver-helper",
            "macos-apply", "b" * 64, "test", "127.0.0.54", "5300",
            result["applied"]["content_digest"],
        ))

    def test_apply_failure_is_reported_without_claiming_mutation(self):
        from sandbox.network.adapters.macos import MacosResolverAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = MacosResolverAdapter(
                process=Process(65), staging_root=Path(tmp),
            )
            plan = adapter.plan("test", "127.0.0.54", 5300)
            plan["owner_digest"] = "b" * 64
            result = adapter.apply(plan)
        self.assertFalse(result["ok"])
        self.assertFalse(result["mutated"])

    def test_preapply_revoke_cannot_remove_a_resolver_fragment(self):
        from sandbox.network.adapters.macos import MacosResolverAdapter

        with tempfile.TemporaryDirectory() as tmp:
            process = Process()
            adapter = MacosResolverAdapter(process=process, staging_root=Path(tmp))
            plan = adapter.plan("test", "127.0.0.54", 5300)
            plan["owner_digest"] = "b" * 64
            adapter.revoke_authorization(plan)
        self.assertEqual(process.calls[0][0][3:5], (
            "revoke-authorization", "macos",
        ))
        self.assertNotIn("macos-remove", process.calls[0][0])


if __name__ == "__main__":
    unittest.main()
