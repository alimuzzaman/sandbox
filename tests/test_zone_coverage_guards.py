import tempfile
import unittest
from unittest import mock

from sandbox.hosting.zone_coverage import program
from tests.zone_coverage_test_support import store_transport, validate_store_command


class StoreGuardTests(unittest.TestCase):
    def test_canonical_command_runs_fixed_program(self):
        with tempfile.TemporaryDirectory() as home:
            result = store_transport(home)({}, program.remote_command({"action": "list"}))
            self.assertEqual(result.returncode, 0)
            self.assertTrue(result.stdout or result.stderr)

    def test_rejects_before_launch(self):
        command = program.remote_command({"action": "invalid"})
        for bad in [command + " ", command + "; touch /tmp/bad", "python3 -c pass", "'unterminated", "echo zone-coverage"]:
            with self.subTest(command=bad[:30]), mock.patch("tests.zone_coverage_test_support.run_test_process") as run:
                with self.assertRaises(AssertionError):
                    store_transport("/unused")({}, bad)
                run.assert_not_called()

class TransportGuardTests(unittest.TestCase):
    def test_store_positive_and_forbidden_processes(self):
        from tests.zone_coverage_test_support import GuardedIO
        import subprocess
        with tempfile.TemporaryDirectory() as home, GuardedIO() as guard:
            with self.assertRaises(AssertionError):
                guard.allow_command(["sudo", "true"])
            result = store_transport(home, guard)({}, program.remote_command({"action": "list"}))
            self.assertEqual(result.returncode, 0)
            for argv, kw in [(['python3', '-c', 'pass'], {}), (['sudo', 'true'], {}),
                             ('true', {'shell': True})]:
                with self.subTest(argv=argv), self.assertRaises(AssertionError):
                    subprocess.run(argv, **kw)

    def test_network_and_transport_denied_and_owned_loopback_allowed(self):
        from tests.zone_coverage_test_support import GuardedIO
        from sandbox.core import _remote, _cloudflare
        import socket
        import urllib.request
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            address = listener.getsockname()
            with GuardedIO() as guard:
                guard.allow_loopback(*address)
                with self.assertRaises(AssertionError):
                    _remote.ssh_run({}, 'true')
                with self.assertRaises(AssertionError):
                    _remote.ssh_stream({}, 'true')
                with self.assertRaises(AssertionError):
                    _remote.remote_host_memory_request({}, {})
                with self.assertRaises(AssertionError):
                    _cloudflare.Client('synthetic').zone('example.com')
                with self.assertRaises(AssertionError):
                    socket.getaddrinfo('example.com', 443)
                with socket.socket() as conn:
                    with self.assertRaises(AssertionError):
                        conn.connect(('192.0.2.1', 443))
                    conn.connect(address)
                accepted, _ = listener.accept()
                accepted.close()
                self.assertEqual(len(guard.attempts), 6)
