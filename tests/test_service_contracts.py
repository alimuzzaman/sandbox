import unittest


class TestServiceContracts(unittest.TestCase):
    def test_recorders_start_empty_and_capture_only_explicit_calls(self):
        from tests.fakes.sandbox_services import ServiceRecorders

        fakes = ServiceRecorders()
        self.assertEqual(fakes.calls, [])
        result = fakes.process.run(["printf", "ok"], cwd="/tmp")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(fakes.calls[0][0], "process.run")
        with fakes.ports.reserve() as reservation:
            self.assertEqual(reservation.port, 8200)
        fakes.paths.artifact_path("/tmp", "archive.tar.gz")
        plan = fakes.proxy.plan("fixture.test", 8080)
        fakes.proxy.apply(plan)
        fakes.proxy.remove("fixture.test")
        self.assertEqual(
            [call[0] for call in fakes.calls],
            ["process.run", "ports.reserve", "paths.artifact_path", "proxy.plan", "proxy.apply", "proxy.remove"],
        )

    def test_service_protocols_are_explicit(self):
        from sandbox.services import HttpProbe, PathPolicy, PortAllocator, ProcessRunner, ProxyManager
        from sandbox.application.context import runtime_neutral_dependencies
        from tests.fakes.sandbox_services import ServiceRecorders

        expected = {
            ProcessRunner: "run",
            HttpProbe: "probe",
            PortAllocator: "allocate",
            PathPolicy: "require_allowed",
            ProxyManager: "plan",
        }
        for contract, method in expected.items():
            self.assertTrue(hasattr(contract, method), f"{contract.__name__}.{method}")

        fakes = ServiceRecorders()
        dependencies = runtime_neutral_dependencies(
            registry=object(), allowed_roots=("/tmp",), process=fakes.process,
            http=fakes.http, ports=fakes.ports, paths=fakes.paths, proxy=fakes.proxy,
        )
        self.assertIs(dependencies.process, fakes.process)
        self.assertIs(dependencies.http, fakes.http)
        self.assertIs(dependencies.ports, fakes.ports)
        self.assertIs(dependencies.paths, fakes.paths)
        self.assertIs(dependencies.proxy, fakes.proxy)


if __name__ == "__main__":
    unittest.main()
