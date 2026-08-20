import json
import unittest
from types import SimpleNamespace


class Process:
    def __init__(self): self.calls = []
    def run(self, argv, **kwargs): self.calls.append((argv, kwargs)); return type("Result", (), {"returncode": 0, "stdout": "Valet 4.7.0", "stderr": ""})()


class ProbeRunner:
    def __init__(self, document=None):
        self.document = document or {
            "schema_version": 1, "php_version": "8.3.4", "sapi": "cli",
            "extensions": {"gd": {"enabled": True, "version": "2.3.0"}},
        }
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), dict(kwargs)))
        return SimpleNamespace(returncode=0, stdout=json.dumps(self.document), stderr="")


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

    def test_declared_status_uses_all_four_bounded_read_only_planes(self):
        from sandbox.runtimes.base import OperationRequest
        from sandbox.runtimes.incumbent.valet import ValetAdapter
        runners = {plane: ProbeRunner() for plane in ("web", "cli", "exec", "phpunit")}
        adapter = ValetAdapter(process=Process(), executable="valet", platform="darwin",
                               php_version=lambda: "8.3", plane_runners=runners)
        request = OperationRequest("/project", "status",
                                   arguments={"phpExtensions": {"extensions": {"gd": True}}})
        result = adapter.invoke(request)
        self.assertTrue(result.ok); self.assertFalse(result.data["mutated"])
        self.assertEqual(set(result.data["php_extensions"]["observed"]),
                         {"web", "cli", "exec", "phpunit"})
        self.assertEqual(result.data["php_extensions"]["provenance"], {"state": "unavailable"})
