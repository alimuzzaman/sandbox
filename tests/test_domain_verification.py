from __future__ import annotations

import unittest

from sandbox.services.process import ProcessResult


class TestDomainVerification(unittest.TestCase):
    def test_fresh_answer_must_match_ingress_and_http_fallback_must_respond(self):
        from sandbox.network.verification import DomainVerifier

        process = type("Process", (), {"run": lambda self, argv, **kwargs: ProcessResult(
            tuple(argv), 0, "demo.test: 127.0.0.77 -- link: lo\n", "",
        )})()
        http = type("Http", (), {"probe": lambda self, url, timeout=5: url.endswith(":8123")})()
        verifier = DomainVerifier(process=process, http=http, platform="linux")
        self.assertTrue(verifier.verify(
            "demo.test", ("127.0.0.77",), "http://localhost:8123",
        ))
        self.assertFalse(verifier.verify(
            "demo.test", ("127.0.0.88",), "http://localhost:8123",
        ))

    def test_query_failure_never_falls_back_to_cached_socket_result(self):
        from sandbox.network.verification import DomainVerifier

        process = type("Process", (), {"run": lambda self, argv, **kwargs: ProcessResult(
            tuple(argv), 1, "", "not found",
        )})()
        http = type("Http", (), {"probe": lambda self, url, timeout=5: True})()
        self.assertFalse(DomainVerifier(process=process, http=http, platform="linux").verify(
            "demo.test", ("127.0.0.77",), "http://localhost:8123",
        ))


if __name__ == "__main__":
    unittest.main()
