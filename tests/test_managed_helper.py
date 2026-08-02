from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest


class TestManagedHelper(unittest.TestCase):
    def test_execution_stages_argv_and_helper_argv_contains_only_digests(self):
        from sandbox.runtimes.managed.helper import ManagedMachineExecutor
        calls = []
        process = SimpleNamespace(run=lambda argv, timeout: calls.append((argv, timeout)) or
                                  SimpleNamespace(returncode=0, stdout="ok", stderr=""))
        with tempfile.TemporaryDirectory() as temp:
            executor = ManagedMachineExecutor(process=process, helper="/fixed/helper",
                                              staging_root=temp)
            secret_like = "project-private-argument"
            result = executor("sb-0123456789ab", ("/usr/bin/bwrap", "--", secret_like),
                              context={"environment": {}, "credential_refs": ()}, timeout=30,
                              expected_policy_digest="a" * 64)
            self.assertEqual(result.stdout, "ok")
            self.assertNotIn(secret_like, calls[0][0])
            self.assertEqual(calls[0][0][:6], ("sudo", "-n", "/fixed/helper", "execute",
                                               "sb-0123456789ab", "a" * 64))
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_observer_rejects_unbounded_or_mismatched_json(self):
        from sandbox.runtimes.managed.helper import ManagedIsolationObserver
        process = SimpleNamespace(run=lambda *_args, **_kwargs:
                                  SimpleNamespace(returncode=0, stdout='{"machine_id":"other"}'))
        with self.assertRaises(RuntimeError):
            ManagedIsolationObserver(process=process, helper="/h")("sb-0123456789ab")


if __name__ == "__main__": unittest.main()
