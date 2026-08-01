import subprocess
import unittest

from tests.test_isolation_verification import healthy, policy


class Verifier:
    def __init__(self, ok): self.ok = ok; self.calls = 0
    def verify(self, target):
        self.calls += 1
        return {"ok": self.ok, "state": "ready" if self.ok else "blocked",
                "mutated": False, "reason": {"code": "ready" if self.ok else "drift"}}


class Bubblewrap:
    def argv(self, **kwargs): return ("bwrap", "--", *kwargs["command"])


class Machine:
    def __init__(self): self.calls = []
    def __call__(self, machine_id, argv, **kwargs):
        self.calls.append((machine_id, argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")


class TestIsolationExecutionPaths(unittest.TestCase):
    def test_every_project_code_path_uses_same_policy_digest_gateway(self):
        from sandbox.isolation.launcher import ENTRY_PATHS, IsolationLauncher
        machine = Machine(); verifier = Verifier(True)
        launcher = IsolationLauncher(verifier=verifier, bubblewrap=Bubblewrap(),
                                     machine_exec=machine)
        target = policy()
        for path in sorted(ENTRY_PATHS):
            result = launcher.launch(target, entry_path=path, command=("php", "probe.php"),
                                     environment={"PATH": "/usr/bin", "AWS_TOKEN": "leak"})
            self.assertTrue(result["ok"])
        self.assertEqual(verifier.calls, len(ENTRY_PATHS))
        self.assertTrue(all(call[2]["expected_policy_digest"] == target.digest
                            for call in machine.calls))
        self.assertTrue(all("AWS_TOKEN" not in call[2]["context"]["environment"]
                            for call in machine.calls))

    def test_failed_verification_never_executes_and_has_no_host_fallback(self):
        from sandbox.isolation.launcher import IsolationLauncher
        machine = Machine(); launcher = IsolationLauncher(
            verifier=Verifier(False), bubblewrap=Bubblewrap(), machine_exec=machine,
        )
        result = launcher.launch(policy(), entry_path="wordpress_cli", command=("wp", "core"))
        self.assertFalse(result["ok"]); self.assertEqual(result["state"], "blocked")
        self.assertEqual(machine.calls, [])


if __name__ == "__main__": unittest.main()
