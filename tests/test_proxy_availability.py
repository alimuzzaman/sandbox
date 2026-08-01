from __future__ import annotations

import unittest


class Observer:
    def __init__(self, endpoints):
        self.endpoints = tuple(endpoints)

    def snapshot(self):
        return self.endpoints


class TestProxyAvailability(unittest.TestCase):
    def test_docker_binary_alone_does_not_claim_availability(self):
        from sandbox.core._domains import proxy_availability
        from sandbox.ingress.models import ListenerEndpoint
        result = proxy_availability(
            observer=Observer((ListenerEndpoint("0.0.0.0", 80),)),
            docker_path="/usr/bin/docker", running=False,
        )
        self.assertFalse(result["available"])
        self.assertEqual(result["reason_code"], "listener_conflict")
        self.assertNotIn("is Docker running", result["message"])

    def test_other_exact_loopback_does_not_conflict_with_dedicated_address(self):
        from sandbox.core._domains import proxy_availability
        from sandbox.ingress.models import ListenerEndpoint
        result = proxy_availability(
            observer=Observer((ListenerEndpoint("127.0.0.1", 80),)),
            docker_path="/usr/bin/docker", running=False,
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["reason_code"], "endpoints_free")

    def test_owned_running_proxy_is_available_and_missing_binary_is_distinct(self):
        from sandbox.core._domains import proxy_availability
        self.assertEqual(proxy_availability(
            observer=Observer(()), docker_path="/usr/bin/docker", running=True,
        )["reason_code"], "sandbox_proxy_owned")
        self.assertEqual(proxy_availability(
            observer=Observer(()), docker_path="", running=False,
        )["reason_code"], "docker_binary_unavailable")


if __name__ == "__main__":
    unittest.main()
