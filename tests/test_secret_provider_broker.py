"""Synthetic checks for the killable provider/credential helper boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

from sandbox.secrets.providers.base import ProviderError
from sandbox.secrets.providers.broker import BoundedProviderBroker
from sandbox.secrets.providers.worker import _status_budget
from tests.subprocess_support import synthetic_environment


DESCRIPTOR = {
    "alias": "fixture-provider", "type": "infisical",
    "endpoint": "https://fixture.invalid", "projectId": "fixture-project",
    "environment": "development", "path": "/fixture", "key": "FIXTURE_KEY",
    "clientId": "fixture-client", "mcpModes": ["metadata"], "timeoutSeconds": 1,
}


class FakeProcess:
    pid = 91_337

    def __init__(self, output):
        self.output = output
        self.args = ["synthetic-worker"]
        self.inputs = []
        self.returncode = 0

    def communicate(self, *, input=None, timeout=None):
        self.inputs.append((input, timeout))
        return self.output, b""

    def poll(self):
        return self.returncode


class ProviderBrokerTests(unittest.TestCase):
    def setUp(self):
        self.started = time.monotonic()
        self.addCleanup(self._duration)

    def _duration(self):
        self.assertLess(time.monotonic() - self.started, 5.0)

    @staticmethod
    def _assert_gone(pid):
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.02)
        raise AssertionError(f"provider helper {pid} survived its deadline")

    def test_one_parent_deadline_kills_a_blocked_worker_group(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "pid"
            command = [
                sys.executable, "-c",
                "import os,pathlib,time;pathlib.Path(__import__('sys').argv[1]).write_text(str(os.getpid()));time.sleep(30)",
                str(marker),
            ]
            broker = BoundedProviderBroker()
            with mock.patch.object(broker, "_command", return_value=command), \
                 mock.patch.object(broker, "_environment", return_value=dict(synthetic_environment())):
                with self.assertRaises(ProviderError) as raised:
                    broker._call("status", DESCRIPTOR)
            self.assertEqual(raised.exception.code, "operation_timeout")
            self.assertTrue(marker.exists())
            self._assert_gone(int(marker.read_text()))
            self.assertLess(time.monotonic() - self.started, 2.5)

    def test_protocol_is_length_framed_and_request_contains_only_descriptor(self):
        value = b"synthetic-provider-value"
        process = FakeProcess(b"V" + len(value).to_bytes(4, "big") + value)
        factory = mock.Mock(return_value=process)
        broker = BoundedProviderBroker(process_factory=factory)
        with mock.patch("sandbox.secrets.providers.broker.terminate_process_group", return_value=True):
            self.assertEqual(broker.resolve_exact(DESCRIPTOR, None), value)
        request = json.loads(process.inputs[0][0].decode("ascii"))
        self.assertEqual(request["action"], "resolve")
        self.assertEqual(request["descriptor"]["endpoint"], DESCRIPTOR["endpoint"])
        self.assertNotIn("credential", repr(request).lower())
        kwargs = factory.call_args.kwargs
        self.assertTrue(kwargs["start_new_session"])
        self.assertTrue(kwargs["close_fds"])

    def test_source_status_frame_is_value_free_and_projected_by_caller(self):
        status = {
            "state": "available", "endpoint_reachable": True, "error_code": None,
            "credential_state": "present", "credential_backend": "linux_secret_service",
        }
        process = FakeProcess(b"S" + json.dumps(status, separators=(",", ":")).encode("ascii"))
        broker = BoundedProviderBroker(process_factory=mock.Mock(return_value=process))
        with mock.patch("sandbox.secrets.providers.broker.terminate_process_group", return_value=True):
            self.assertEqual(broker.source_status(DESCRIPTOR), status)

    def test_source_status_timeout_degrades_to_value_free_status(self):
        broker = BoundedProviderBroker()
        with mock.patch.object(broker, "_call", side_effect=ProviderError("operation_timeout")):
            status = broker.source_status(DESCRIPTOR)
        self.assertEqual(status["state"], "timed_out")
        self.assertFalse(status["endpoint_reachable"])
        self.assertEqual(status["credential_state"], "unknown")
        self.assertEqual(status["error_code"], "operation_timeout")
        self.assertLess(_status_budget(DESCRIPTOR), DESCRIPTOR["timeoutSeconds"])

    def test_real_isolated_worker_command_returns_stable_error_frame(self):
        broker = BoundedProviderBroker()
        with mock.patch.object(
                broker, "_environment", return_value=dict(synthetic_environment())):
            with self.assertRaises(ProviderError) as raised:
                broker._call("unsupported", DESCRIPTOR)
        self.assertEqual(raised.exception.code, "provider_unsupported")

    def test_worker_environment_keeps_session_routing_but_drops_proxies(self):
        synthetic = {
            "PATH": "/synthetic/bin", "HOME": "/synthetic/home", "LANG": "C.UTF-8",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/synthetic/bus",
            "XDG_RUNTIME_DIR": "/synthetic/runtime", "HTTPS_PROXY": "http://proxy.invalid",
            "TOKEN": "synthetic-parent-value",
        }
        with mock.patch.dict(os.environ, synthetic, clear=True):
            environment = BoundedProviderBroker._environment()
        self.assertEqual(environment["DBUS_SESSION_BUS_ADDRESS"], synthetic["DBUS_SESSION_BUS_ADDRESS"])
        self.assertEqual(environment["XDG_RUNTIME_DIR"], synthetic["XDG_RUNTIME_DIR"])
        self.assertNotIn("HTTPS_PROXY", environment)
        self.assertNotIn("TOKEN", environment)


if __name__ == "__main__":
    unittest.main()
