from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sandbox.services.process import ProcessResult


class RecordingProcess:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        return ProcessResult(tuple(argv), self.returncode, "", "failure" if self.returncode else "")


class TestResolvedAdapter(unittest.TestCase):
    def test_apply_uses_fixed_helper_and_preserves_resolv_conf_symlink(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        links = ["/run/systemd/resolve/stub-resolv.conf"]
        process = RecordingProcess()
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ResolvedAdapter(
                process=process, helper="/fixed/resolver-helper",
                network_root=Path(tmp), readlink=lambda _path: links[0],
            )
            applied = adapter.apply("test", "127.0.0.54", 5300)
        self.assertTrue(applied["ok"])
        self.assertEqual(links, ["/run/systemd/resolve/stub-resolv.conf"])
        self.assertEqual(process.calls[0][0][:2], ("sudo", "-n"))
        self.assertIn("resolved-apply", process.calls[0][0])

    def test_helper_failure_returns_no_false_success(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = ResolvedAdapter(
                process=RecordingProcess(returncode=1), helper="/fixed/helper",
                network_root=Path(tmp), readlink=lambda _path: "/run/systemd/resolve/stub-resolv.conf",
            )
            result = adapter.apply("test", "127.0.0.54", 5300)
        self.assertFalse(result["ok"])
        self.assertFalse(result["mutated"])

    def test_rollback_uses_expected_fragment_digest(self):
        from sandbox.network.adapters.resolved import ResolvedAdapter

        process = RecordingProcess()
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ResolvedAdapter(
                process=process, helper="/fixed/helper", network_root=Path(tmp),
                readlink=lambda _path: "/run/systemd/resolve/stub-resolv.conf",
            )
            result = adapter.rollback({"suffix": "test", "fragment_digest": "a" * 64})
        self.assertTrue(result["ok"])
        self.assertEqual(process.calls[0][0][-2:], ("test", "a" * 64))


if __name__ == "__main__":
    unittest.main()
