import json
import unittest
from types import SimpleNamespace


class Process:
    def __init__(self, code=0, output="Herd 1.8.2"): self.code = code; self.output = output; self.calls = []
    def run(self, argv, **kwargs):
        self.calls.append((argv, kwargs)); return type("Result", (), {"returncode": self.code, "stdout": self.output, "stderr": ""})()


class ProbeRunner:
    def __init__(self, document=None, *, code=0, stderr=""):
        self.document = document or {
            "schema_version": 1, "php_version": "8.3.4", "sapi": "cli",
            "extensions": {"gd": {"enabled": True, "version": "2.3.0"}},
        }
        self.code = code; self.stderr = stderr; self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), dict(kwargs)))
        return SimpleNamespace(returncode=self.code,
                               stdout=json.dumps(self.document) if self.code == 0 else "",
                               stderr=self.stderr)


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

    def test_omitted_php_extensions_never_resolves_or_probes(self):
        from sandbox.runtimes.incumbent.herd import HerdAdapter
        runner = ProbeRunner()
        adapter = HerdAdapter(process=Process(), executable="herd", platform="linux",
                              php_version=lambda: "8.3", plane_runners={
                                  plane: runner for plane in ("web", "cli", "exec", "phpunit")})
        result = adapter.invoke(self.request("status"))
        self.assertTrue(result.ok)
        self.assertNotIn("php_extensions", result.data)
        self.assertEqual(runner.calls, [])

    def test_ready_status_is_canonical_and_secret_free(self):
        from sandbox.runtimes.incumbent.herd import HerdAdapter
        runners = {plane: ProbeRunner() for plane in ("web", "cli", "exec", "phpunit")}
        adapter = HerdAdapter(process=Process(), executable="herd", platform="linux",
                              php_version=lambda: "8.3", plane_runners=runners)
        request = type(self.request("status"))("/project", "status",
                                                arguments={"phpExtensions": {"extensions": {"gd": True}}})
        result = adapter.invoke(request)
        report = result.data["php_extensions"]
        self.assertTrue(result.ok); self.assertFalse(result.data["mutated"])
        self.assertEqual(set(report), {"ok", "exit_code", "desired", "provenance",
                                       "observed", "readiness", "staleness", "drift", "issues"})
        self.assertEqual(report["provenance"], {"state": "unavailable"})
        self.assertEqual(tuple(report["observed"]), ("web", "cli", "exec", "phpunit"))
        self.assertEqual(report["readiness"], {"state": "ready"})
        self.assertEqual(report["staleness"], {"state": "fresh", "reason": "all_four_planes_observed"})
        self.assertEqual(report["drift"], {"state": "ready"})

    def test_all_failure_classes_and_plane_states_are_closed(self):
        from sandbox.runtimes.incumbent.herd import HerdAdapter
        planes = ("web", "cli", "exec", "phpunit")

        def invoke(requirement, documents):
            runners = {plane: ProbeRunner(documents.get(plane, documents.get("default")))
                       for plane in planes}
            adapter = HerdAdapter(process=Process(), executable="herd", platform="linux",
                                  php_version=lambda: "8.3", plane_runners=runners)
            return adapter.invoke(self.request("status", None) if requirement is None else
                                  type(self.request("status"))("/project", "status",
                                  arguments={"phpExtensions": requirement})).data["php_extensions"]

        good = {"schema_version": 1, "php_version": "8.3.4", "sapi": "cli",
                "extensions": {"gd": {"enabled": True, "version": "2.3.0"},
                               "tokenizer": {"enabled": True, "version": None}}}
        missing = {**good, "extensions": {"tokenizer": {"enabled": True, "version": None}}}
        mismatch = {**good, "extensions": {"gd": {"enabled": True, "version": "1.0"}}}
        unobservable = {**good, "extensions": {"gd": {"enabled": True, "version": None}}}
        drift = {**good, "extensions": {"gd": {"enabled": False, "version": None}}}
        missing_runtime = {**good, "extensions": {}}
        self.assertIn("missing", {row["code"] for row in invoke(
            {"extensions": {"tokenizer": True}}, {"default": missing_runtime})["issues"]})
        self.assertIn("version_mismatch", {row["code"] for row in invoke(
            {"extensions": {"gd": "2.3.0"}}, {"default": mismatch})["issues"]})
        self.assertIn("version_unobservable", {row["code"] for row in invoke(
            {"extensions": {"gd": "2.3.0"}}, {"default": unobservable})["issues"]})
        self.assertIn("unsupported_provisioning", {row["code"] for row in invoke(
            {"extensions": {"gd": True}}, {"default": missing})["issues"]})
        self.assertIn("unsupported_disable", {row["code"] for row in invoke(
            {"extensions": {"gd": False}}, {"default": good})["issues"]})
        report = invoke({"extensions": {"gd": True}}, {"default": good, "phpunit": drift})
        self.assertIn("plane_drift", {row["code"] for row in report["issues"]})
        self.assertEqual(report["drift"], {"state": "drift"})

    def test_unavailable_error_and_secret_path_observations_fail_closed(self):
        from sandbox.runtimes.incumbent.herd import HerdAdapter
        missing_runner = HerdAdapter(process=Process(), executable="herd", platform="linux",
                                     php_version=lambda: "8.3", php_extensions={"extensions": {"gd": True}})
        unavailable = missing_runner.invoke(self.request("status")).data["php_extensions"]
        self.assertEqual(unavailable["readiness"], {"state": "unavailable"})
        error_runners = {plane: ProbeRunner(code=1, stderr="/private/password=secret")
                         for plane in ("web", "cli", "exec", "phpunit")}
        errored = HerdAdapter(process=Process(), executable="herd", platform="linux",
                              php_version=lambda: "8.3", php_extensions={"extensions": {"gd": True}},
                              plane_runners=error_runners).invoke(self.request("status")).data["php_extensions"]
        self.assertEqual({row["state"] for row in errored["observed"].values()}, {"error"})
        self.assertNotIn("private", json.dumps(errored).lower())
