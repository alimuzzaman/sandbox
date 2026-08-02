from __future__ import annotations

import unittest

from sandbox.services.process import ProcessResult


class TestDomainVerification(unittest.TestCase):
    def test_fresh_answer_must_match_ingress_and_http_fallback_must_respond(self):
        from sandbox.network.verification import DomainVerifier

        process = type("Process", (), {"run": lambda self, argv, **kwargs: ProcessResult(
            tuple(argv), 0, "demo.test: 127.0.0.77 -- link: lo\n", "",
        )})()
        calls = []
        http = type("Http", (), {"probe_route": lambda self, address, port, host, timeout=5:
                    calls.append((address, port, host)) or port == 8123})()
        verifier = DomainVerifier(process=process, http=http, platform="linux")
        self.assertTrue(verifier.verify(
            "demo.test", ("127.0.0.77",), "http://localhost:8123",
        ))
        self.assertFalse(verifier.verify(
            "demo.test", ("127.0.0.88",), "http://localhost:8123",
        ))
        self.assertEqual(calls, [("127.0.0.1", 8123, "demo.test")])

    def test_verifier_never_ambient_probes_public_or_metadata_fallbacks(self):
        from sandbox.network.verification import DomainVerifier

        process = type("Process", (), {"run": lambda self, argv, **kwargs: ProcessResult(
            tuple(argv), 0, "demo.test: 127.0.0.77 -- link: lo\n", "",
        )})()
        calls = []
        http = type("Http", (), {"probe_route": lambda self, *args, **kwargs:
                    calls.append(args) or True})()
        verifier = DomainVerifier(process=process, http=http, platform="linux")
        self.assertFalse(verifier.verify(
            "demo.test", ("127.0.0.77",), "http://169.254.169.254/latest/meta-data",
        ))
        self.assertFalse(verifier.verify(
            "demo.test", ("127.0.0.77",), "https://example.com/",
        ))
        self.assertEqual(calls, [])

    def test_query_failure_never_falls_back_to_cached_socket_result(self):
        from sandbox.network.verification import DomainVerifier

        process = type("Process", (), {"run": lambda self, argv, **kwargs: ProcessResult(
            tuple(argv), 1, "", "not found",
        )})()
        http = type("Http", (), {"probe_route": lambda self, *args, **kwargs: True})()
        self.assertFalse(DomainVerifier(process=process, http=http, platform="linux").verify(
            "demo.test", ("127.0.0.77",), "http://localhost:8123",
        ))

    def test_mixed_accepted_and_foreign_answers_fail_verification(self):
        from sandbox.network.verification import DomainVerifier

        process = type("Process", (), {"run": lambda self, argv, **kwargs: ProcessResult(
            tuple(argv), 0,
            "demo.test: 127.0.0.77\ndemo.test: 203.0.113.9\n", "",
        )})()
        calls = []
        http = type("Http", (), {"probe_route": lambda self, *args, **kwargs:
                    calls.append(args) or True})()
        verifier = DomainVerifier(process=process, http=http, platform="linux")
        self.assertFalse(verifier.verify(
            "demo.test", ("127.0.0.77",), "http://localhost:8123",
        ))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()


class TestReadyResultReportsFreshAnswers(unittest.TestCase):
    """Spec A compares `actual_answers` with the addresses it offered before it
    activates a route. Reporting the pre-apply snapshot made a healthy adoption
    look like an address mismatch."""

    def test_apply_reobserves_after_verification(self):
        import inspect

        from sandbox.application import domain_service

        source = inspect.getsource(domain_service.DomainService._apply) \
            if hasattr(domain_service.DomainService, "_apply") \
            else inspect.getsource(domain_service.DomainService)
        ready_block = source.split('reason_code="ready"')[0]
        self.assertIn("verified = self.observer(", ready_block)
        self.assertIn("observation=verified or current", ready_block)
