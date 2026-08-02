from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sandbox.services.process import ProcessResult


class RecordingProcess:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        return ProcessResult(tuple(argv), self.returncode, self.stdout, "failure" if self.returncode else "")


class TestResolvedAdapter(unittest.TestCase):
    def test_apply_uses_fixed_helper_and_preserves_resolv_conf_symlink(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        links = ["/run/systemd/resolve/stub-resolv.conf"]
        process = RecordingProcess()
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ResolvedAdapter(
                process=process,
                network_root=Path(tmp), readlink=lambda _path: links[0],
            )
            applied = adapter.apply({
                "suffix": "test", "address": "127.0.0.54", "port": 5300,
                "owner_digest": "b" * 64,
            })
        self.assertTrue(applied["ok"])
        self.assertEqual(links, ["/run/systemd/resolve/stub-resolv.conf"])
        self.assertEqual(process.calls[0][0][:2], ("sudo", "-n"))
        self.assertEqual(process.calls[0][0][2:7], (
            "/usr/local/libexec/sandbox-resolver-helper", "resolved-apply",
            "b" * 64, "test", "127.0.0.54",
        ))
        self.assertNotIn(str(Path(tmp)), process.calls[0][0])

    def test_helper_failure_returns_no_false_success(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = ResolvedAdapter(
                process=RecordingProcess(returncode=1),
                network_root=Path(tmp), readlink=lambda _path: "/run/systemd/resolve/stub-resolv.conf",
            )
            result = adapter.apply({
                "suffix": "test", "address": "127.0.0.54", "port": 5300,
                "owner_digest": "b" * 64,
            })
        self.assertFalse(result["ok"])
        self.assertFalse(result["mutated"])

    def test_rollback_uses_expected_fragment_digest(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        process = RecordingProcess()
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ResolvedAdapter(
                process=process, network_root=Path(tmp),
                readlink=lambda _path: "/run/systemd/resolve/stub-resolv.conf",
            )
            result = adapter.rollback({
                "suffix": "test", "address": "127.0.0.54", "port": 5300,
                "owner_digest": "b" * 64, "fragment_digest": "a" * 64,
            })
        self.assertTrue(result["ok"])
        self.assertEqual(process.calls[0][0][-5:], (
            "b" * 64, "test", "127.0.0.54", "5300", "a" * 64,
        ))

    def test_preapply_revoke_is_receipt_only_and_distinct_from_rollback(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        process = RecordingProcess()
        plan = {"suffix": "test", "address": "127.0.0.54", "port": 5300,
                "owner_digest": "b" * 64}
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ResolvedAdapter(process=process, network_root=Path(tmp))
            adapter.revoke_authorization(plan)
        self.assertEqual(process.calls[0][0][3:5], (
            "revoke-authorization", "resolved",
        ))
        self.assertNotIn("resolved-remove", process.calls[0][0])

    def test_nonfixed_mutation_helper_is_rejected(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "fixed"):
            ResolvedAdapter(
                process=RecordingProcess(), helper="/tmp/repository-helper",
                network_root=Path(tmp),
            )

    def test_helper_install_requires_interactive_consent_and_verifies_fixed_copy(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        process = RecordingProcess(returncode=1)
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ResolvedAdapter(
                process=process, repository_helper="/repo/tools/resolver-helper.sh",
                network_root=Path(tmp),
            )
            pending = adapter.ensure_helper(interactive=False)
        self.assertFalse(pending["ok"])
        self.assertEqual(len(process.calls), 1)
        self.assertEqual(process.calls[0][0][:3], (
            "sudo", "-n", "/usr/local/libexec/sandbox-resolver-helper",
        ))


if __name__ == "__main__":
    unittest.main()
