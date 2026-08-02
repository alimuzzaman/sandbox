import unittest


class Process:
    def __init__(self, code=0, output="Herd 1.8.2"): self.code = code; self.output = output; self.calls = []
    def run(self, argv, **kwargs):
        self.calls.append((argv, kwargs)); return type("Result", (), {"returncode": self.code, "stdout": self.output, "stderr": ""})()


class TestIncumbentHerd(unittest.TestCase):
    def request(self, operation="preflight", database=None):
        from sandbox.runtimes.base import OperationRequest
        return OperationRequest("/project", operation, arguments={} if database is None else {"database": database})

    def test_capability_version_database_backend_and_no_route_mutation(self):
        from sandbox.runtimes.incumbent.herd import HerdAdapter
        process = Process(); adapter = HerdAdapter(process=process, executable="/usr/bin/herd", platform="linux",
            php_version=lambda: "8.3", backend=lambda _request: {"document_root": "/project/wp"})
        result = adapter.invoke(self.request("ensure", {"host": "127.0.0.1", "name": "demo", "user": "demo"}))
        self.assertTrue(result.ok); self.assertEqual(result.data["version"], "1.8.2")
        self.assertEqual(result.data["runtime"]["isolation"], "trusted_shared_host")
        self.assertFalse(result.data["runtime"]["route_mutations"])
        self.assertEqual(process.calls, [(("/usr/bin/herd", "--version"), {"timeout": 10})])
        self.assertNotIn("link", repr(process.calls)); self.assertNotIn("secure", repr(process.calls))

    def test_explicit_php_requirement_must_match_observed_major_minor(self):
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.incumbent.herd import HerdAdapter
        adapter = HerdAdapter(process=Process(), executable="/usr/bin/herd", platform="darwin",
                              php_version=lambda: "PHP 8.5.8")
        request = OperationRequest("/project", "preflight", arguments={"php": "8.3"})
        self.assertEqual(adapter.invoke(request).data["reason"]["code"],
                         "php_version_mismatch")

    def test_missing_database_and_wrong_platform_are_truthfully_blocked(self):
        from sandbox.runtimes.incumbent.herd import HerdAdapter
        adapter = HerdAdapter(process=Process(), executable="/usr/bin/herd", platform="linux",
                              php_version=lambda: "8.3")
        self.assertEqual(adapter.invoke(self.request("ensure")).data["reason"]["code"], "user_database_required")
        blocked = HerdAdapter(process=Process(), executable="/usr/bin/herd", platform="windows").invoke(self.request())
        self.assertEqual(blocked.data["reason"]["code"], "unsupported_platform")
