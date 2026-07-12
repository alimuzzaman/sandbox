import unittest


class TestServiceContracts(unittest.TestCase):
    def test_recorders_start_empty_and_capture_only_explicit_calls(self):
        from tests.fakes.sandbox_services import ServiceRecorders

        fakes = ServiceRecorders()
        self.assertEqual(fakes.calls, [])
        result = fakes.process.run(["printf", "ok"], cwd="/tmp")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(fakes.calls[0][0], "process.run")

    def test_service_protocols_are_explicit(self):
        from sandbox.services import HttpProbe, PathPolicy, PortAllocator, ProcessRunner, ProxyManager

        expected = {
            ProcessRunner: "run",
            HttpProbe: "probe",
            PortAllocator: "allocate",
            PathPolicy: "require_allowed",
            ProxyManager: "plan",
        }
        for contract, method in expected.items():
            self.assertTrue(hasattr(contract, method), f"{contract.__name__}.{method}")


if __name__ == "__main__":
    unittest.main()
