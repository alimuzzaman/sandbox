import io
import json
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from sandbox.commands import hosting
from sandbox.sync.models import (
    SourceGeneration,
    SynchronizationRelationship,
    failure_envelope,
    success_envelope,
)


FORBIDDEN = (
    "/private/work/project", "token=fixture-secret", "fixture-secret",
    "user@ssh.internal", "['ssh', '-i', '/private/key']", "raw exception text",
)


def args(*, watch=False, json_mode=True):
    return types.SimpleNamespace(
        request_id="request_fixture", include=None, watch=watch,
        watch_seconds=1, interval=0.1, debounce=0.1, json=json_mode,
    )


def accepted(status="accepted"):
    relationship = SynchronizationRelationship(
        relationship_id="relationship_fixture", project_identity="project_fixture",
        remote_name="remote-fixture", workspace_id="workspace_fixture",
        mode="live", lifecycle="active", updated_at="2026-08-28T00:00:00Z",
    )
    generation = SourceGeneration(
        generation_id="generation_fixture", relationship_id="relationship_fixture",
        sequence=1, manifest_digest="a" * 64, file_count=1, byte_count=4,
        lifecycle="accepted", request_id="request_fixture",
        created_at="2026-08-28T00:00:00Z",
        accepted_at="2026-08-28T00:00:01Z",
    )
    return success_envelope(relationship, generation, status=status)


def failed(code):
    return failure_envelope(
        code=code,
        status="refused" if code == "credential_detected" else (
            "unknown" if code == "transport_unknown" else "failed"
        ),
        relationship_id="relationship_fixture", remote_name="remote-fixture",
        request_id="request_fixture", pending_generation="generation_fixture",
        retryable=False,
    )


class FakeService:
    def __init__(self, result=None, *, once_error=None, stop_error=None):
        self.result = result or accepted()
        self.once_error = once_error
        self.stop_error = stop_error
        self.once_calls = 0
        self.stop_calls = 0

    def start(self, *_args, **_kwargs):
        return None

    def once(self, *_args, **_kwargs):
        self.once_calls += 1
        if self.once_error is not None:
            raise self.once_error
        return self.result

    def stop(self, *_args, **_kwargs):
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error
        return accepted(status="stopped")


class HostSyncHardeningTests(unittest.TestCase):
    def _run_watch(self, service, result_args=None):
        output = io.StringIO()
        with patch.object(
            hosting, "_host_sync_service",
            return_value=(service, "workspace_fixture", "/private/work/project"),
        ), patch.object(
            hosting, "_host_sync_watch_signature", return_value="signature"
        ), patch.object(
            hosting.time, "monotonic", side_effect=[0.0, 0.0, 0.0, 0.2, 0.2, 2.0]
        ), patch.object(hosting.time, "sleep"), redirect_stdout(output):
            try:
                hosting._cmd_host_sync(
                    {"source_root": "/private/work/project"}, {"provisioned": True},
                    "remote-fixture", result_args or args(watch=True),
                )
            finally:
                self.watch_output = output.getvalue()
        return output.getvalue()

    def test_setup_exceptions_are_fixed_and_redacted_in_json_and_human_modes(self):
        diagnostic = RuntimeError(" ".join(FORBIDDEN))
        for json_mode in (True, False):
            output = io.StringIO()
            with self.subTest(json=json_mode), patch.object(
                hosting, "_host_sync_service", side_effect=diagnostic,
            ), redirect_stdout(output), self.assertRaises(SystemExit) as raised:
                hosting._cmd_host_sync(
                    {"source_root": FORBIDDEN[0]}, {"provisioned": True},
                    FORBIDDEN[3], args(json_mode=json_mode),
                )
            self.assertEqual(raised.exception.code, 1)
            serialized = output.getvalue()
            for forbidden in FORBIDDEN:
                self.assertNotIn(forbidden, serialized)
            if json_mode:
                payload = json.loads(serialized)
                self.assertEqual(payload["code"], "remote_unavailable")

    def test_watch_latches_refused_failed_and_unknown_without_duplicate_or_stop_success(self):
        for code in ("credential_detected", "remote_unavailable", "transport_unknown"):
            service = FakeService(failed(code))
            with self.subTest(code=code), self.assertRaises(SystemExit) as raised:
                self._run_watch(service)
            output = self.watch_output
            self.assertEqual(raised.exception.code, 1)
            self.assertEqual(service.once_calls, 1)
            self.assertEqual(service.stop_calls, 1)
            lines = output.splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["code"], code)
            self.assertNotIn('"status":"stopped"', output)

    def test_keyboard_interrupt_stops_cleanly_without_failure(self):
        service = FakeService(once_error=KeyboardInterrupt())
        output = self._run_watch(service)
        self.assertEqual(service.once_calls, 1)
        self.assertEqual(service.stop_calls, 1)
        self.assertEqual(json.loads(output)["status"], "stopped")

    def test_stop_exception_replaces_success_and_exits_nonzero_without_diagnostics(self):
        service = FakeService(
            accepted(), stop_error=RuntimeError(" ".join(FORBIDDEN)),
        )
        with self.assertRaises(SystemExit) as raised:
            self._run_watch(service)
        output = self.watch_output
        self.assertEqual(raised.exception.code, 1)
        payload = json.loads(output.splitlines()[-1])
        self.assertEqual(payload["code"], "remote_unavailable")
        for forbidden in FORBIDDEN:
            self.assertNotIn(forbidden, output)

    def test_watch_exception_is_redacted_latched_and_cleaned_up(self):
        service = FakeService(once_error=RuntimeError(" ".join(FORBIDDEN)))
        with self.assertRaises(SystemExit) as raised:
            self._run_watch(service)
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(service.once_calls, 1)
        self.assertEqual(service.stop_calls, 1)
        payload = json.loads(self.watch_output)
        self.assertEqual(payload["code"], "remote_unavailable")
        for forbidden in FORBIDDEN:
            self.assertNotIn(forbidden, self.watch_output)

    def test_malformed_service_result_cannot_expand_public_output(self):
        output = io.StringIO()
        service = FakeService({"ok": True, "exception": " ".join(FORBIDDEN)})
        with patch.object(
            hosting, "_host_sync_service",
            return_value=(service, "workspace_fixture", "/private/work/project"),
        ), redirect_stdout(output), self.assertRaises(SystemExit):
            hosting._cmd_host_sync(
                {"source_root": "/private/work/project"}, {"provisioned": True},
                "remote-fixture", args(),
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["code"], "remote_unavailable")
        for forbidden in FORBIDDEN:
            self.assertNotIn(forbidden, output.getvalue())


if __name__ == "__main__":
    unittest.main()
