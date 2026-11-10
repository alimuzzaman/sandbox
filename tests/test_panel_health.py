"""Bounded, cached health checks for the panel (spec 076)."""
from __future__ import annotations

import http.client
import unittest

from sandbox.hosting import panel_health as ph


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Queue:
    """A submit function that holds jobs until the test runs them."""

    def __init__(self):
        self.jobs = []

    def __call__(self, job):
        self.jobs.append(job)

    def run(self):
        jobs, self.jobs = self.jobs, []
        for job in jobs:
            job()


class HealthCacheTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.queue = Queue()
        self.calls = []

        def probe(host, path, timeout):
            self.calls.append((host, path, timeout))
            return {"status": "up", "http_status": 200, "checked_at": 1}
        self.cache = ph.HealthCache(ttl=60, timeout=4, max_hosts=3, probe_fn=probe,
                                    clock=self.clock, submit=self.queue)

    def test_snapshot_never_waits(self):
        first = self.cache.snapshot([("hub-demo.example.test", "/health")])
        self.assertEqual(first["hub-demo.example.test"]["status"], "checking")
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.queue.jobs), 1)

    def test_one_probe_per_host_per_ttl(self):
        targets = [("hub-demo.example.test", "/health"), ("hub-demo.example.test", "/other")]
        self.cache.snapshot(targets)
        self.cache.snapshot(targets)  # in flight: not queued again
        self.assertEqual(len(self.queue.jobs), 1)
        self.queue.run()
        self.assertEqual(self.calls, [("hub-demo.example.test", "/health", 4)])
        self.assertEqual(self.cache.snapshot(targets)["hub-demo.example.test"]["status"], "up")
        self.assertEqual(self.queue.jobs, [])
        self.clock.now += 61
        stale = self.cache.snapshot(targets)
        self.assertEqual(stale["hub-demo.example.test"]["status"], "up")  # stale value shown meanwhile
        self.assertEqual(len(self.queue.jobs), 1)

    def test_cap_and_invalid_hosts(self):
        targets = [("*.demo.example.test", "/"), ("203.0.113.5", "/"), ("hub.example.test:8443", "/"),
                   ("user@hub.example.test", "/"), ("localhost", "/"),
                   ("a.example.test", "/"), ("b.example.test", "/"), ("c.example.test", "/"),
                   ("d.example.test", "/")]
        result = self.cache.snapshot(targets)
        for host in ("*.demo.example.test", "203.0.113.5", "hub.example.test:8443",
                     "user@hub.example.test", "localhost"):
            self.assertEqual(result[host]["status"], "not_checked", host)
        self.assertEqual(result["c.example.test"]["status"], "checking")
        self.assertEqual(result["d.example.test"]["status"], "not_checked")
        self.assertEqual(len(self.queue.jobs), 3)

    def test_bad_path_falls_back_to_root(self):
        self.cache.snapshot([("hub.example.test", "/x\r\nHost: evil")])
        self.queue.run()
        self.assertEqual(self.calls[0][1], "/")

    def test_probe_exception_is_unreachable(self):
        cache = ph.HealthCache(probe_fn=lambda *a: (_ for _ in ()).throw(RuntimeError("x")),
                               clock=self.clock, submit=self.queue)
        cache.snapshot([("hub.example.test", "/")])
        self.queue.run()
        self.assertEqual(cache.snapshot([("hub.example.test", "/")])["hub.example.test"]["status"],
                         "unreachable")


class ProbeTest(unittest.TestCase):
    def connection(self, *, status=200, error=None):
        record = {}

        class Response:
            def __init__(self):
                self.status = status

        class Connection:
            def __init__(self, host, port, timeout, context):
                record.update(host=host, port=port, timeout=timeout, context=context)

            def request(self, method, path, headers):
                record.update(method=method, path=path, headers=headers)
                if error:
                    raise error

            def getresponse(self):
                return Response()

            def close(self):
                record["closed"] = True
        return Connection, record

    def test_classification(self):
        for status, expected in ((200, "up"), (302, "redirect"), (401, "up_login"), (403, "up_login"),
                                 (404, "client_error"), (502, "server_error")):
            connection, _ = self.connection(status=status)
            result = ph.probe("hub.example.test", "/", 4, wall=lambda: 7, connection_class=connection)
            self.assertEqual(result, {"status": expected, "http_status": status, "checked_at": 7})

    def test_request_shape_has_no_credentials(self):
        connection, record = self.connection()
        ph.probe("hub.example.test", "/health", 4, connection_class=connection)
        self.assertEqual((record["host"], record["port"], record["timeout"]), ("hub.example.test", 443, 4))
        self.assertEqual(record["method"], "GET")
        self.assertEqual(set(record["headers"]), {"User-Agent", "Accept", "Connection"})
        self.assertTrue(record["closed"])
        self.assertTrue(record["context"].check_hostname)

    def test_errors_are_unreachable(self):
        for error in (TimeoutError(), ConnectionRefusedError(), http.client.BadStatusLine("x")):
            connection, record = self.connection(error=error)
            result = ph.probe("hub.example.test", "/", 4, wall=lambda: 7, connection_class=connection)
            self.assertEqual(result["status"], "unreachable")
            self.assertTrue(record["closed"])


if __name__ == "__main__":
    unittest.main()
