import unittest


class Process:
    def __init__(self): self.calls = []
    def run(self, argv, **kwargs): self.calls.append((argv, kwargs)); return type("Result", (), {"returncode": 0, "stdout": "Valet 4.7.0", "stderr": ""})()


class TestIncumbentValet(unittest.TestCase):
    def test_macos_only_user_database_and_no_route_ownership(self):
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.incumbent.valet import ValetAdapter
        process = Process(); adapter = ValetAdapter(process=process, executable="/usr/local/bin/valet", platform="darwin", php_version=lambda: "8.3")
        request = OperationRequest("/project", "ensure", arguments={"database": {"host": "localhost", "name": "demo", "user": "demo"}})
        result = adapter.invoke(request)
        self.assertTrue(result.ok); self.assertFalse(result.data["runtime"]["route_mutations"])
        self.assertEqual(process.calls, [(("/usr/local/bin/valet", "--version"), {"timeout": 10})])
        blocked = ValetAdapter(process=Process(), executable="valet", platform="linux").invoke(request)
        self.assertEqual(blocked.data["reason"]["code"], "unsupported_platform")
