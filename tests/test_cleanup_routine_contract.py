"""Control contract for the remote cleanup routine (spec 057)."""

from __future__ import annotations

import json
import unittest

from sandbox.resources.cleanup_routine import contract
from sandbox.resources.cleanup_routine.contract import RoutineError

REV = "a" * 24


def enable(**overrides):
    payload = {
        "action": "cleanup_routine_enable",
        "expected_runtime_revision": REV,
        "cadence": "daily",
        "timeout": "30min",
        "randomized_delay": "5min",
        "exclusions": ["lenzora*"],
    }
    payload.update(overrides)
    return payload


class RequestValidation(unittest.TestCase):
    def code(self, payload):
        with self.assertRaises(RoutineError) as raised:
            contract.validate_request(payload)
        return raised.exception.code

    def test_exactly_three_actions(self):
        self.assertEqual(contract.ACTIONS, frozenset({
            "cleanup_routine_enable", "cleanup_routine_disable",
            "cleanup_routine_status",
        }))
        self.assertEqual(self.code({"action": "cleanup_routine_run"}), "invalid_request")
        self.assertEqual(self.code({"action": "observe"}), "invalid_request")
        self.assertEqual(self.code("nope"), "invalid_request")

    def test_valid_requests_are_normalized(self):
        request = contract.validate_request(enable())
        self.assertEqual(request["exclusions"], ["lenzora*"])
        self.assertEqual(request["timeout"], "30min")
        self.assertEqual(
            contract.validate_request({"action": "cleanup_routine_disable"}),
            {"action": "cleanup_routine_disable"},
        )
        self.assertEqual(
            contract.validate_request({"action": "cleanup_routine_status"})["history"], 30,
        )

    def test_enable_defaults(self):
        request = contract.validate_request({
            "action": "cleanup_routine_enable", "expected_runtime_revision": REV,
        })
        self.assertEqual(request["cadence"], "daily")
        self.assertEqual(request["timeout"], "30min")
        self.assertEqual(request["randomized_delay"], "5min")
        self.assertEqual(request["exclusions"], [])

    def test_unknown_keys_are_refused(self):
        self.assertEqual(self.code(enable(command="id")), "invalid_request")
        self.assertEqual(self.code({"action": "cleanup_routine_disable", "x": 1}),
                         "invalid_request")
        self.assertEqual(self.code({"action": "cleanup_routine_status", "tier": "all"}),
                         "invalid_request")

    def test_revision_is_required_and_hex(self):
        payload = enable()
        del payload["expected_runtime_revision"]
        self.assertEqual(self.code(payload), "invalid_request")
        self.assertEqual(self.code(enable(expected_runtime_revision="../x")),
                         "invalid_request")

    def test_history_is_capped(self):
        for value in (0, 31, True, "30"):
            self.assertEqual(
                self.code({"action": "cleanup_routine_status", "history": value}),
                "invalid_request", value,
            )

    def test_cadence_text_is_capped_and_control_free(self):
        self.assertEqual(self.code(enable(cadence="")), "invalid_cadence")
        self.assertEqual(self.code(enable(cadence="x" * 129)), "invalid_cadence")
        self.assertEqual(self.code(enable(cadence="daily\nExecStart=/bin/id")),
                         "invalid_cadence")
        self.assertEqual(self.code(enable(cadence=5)), "invalid_cadence")


class ExclusionValidation(unittest.TestCase):
    def test_accepts_reap_globs(self):
        self.assertEqual(
            contract.validate_exclusions(["lenzora*", "keep-[ab]?", "x[!0-9]"]),
            ["lenzora*", "keep-[ab]?", "x[!0-9]"],
        )

    def test_rejects_unparseable_exclusions(self):
        bad = ["", "a/b", "x" * 129, "tab\there", "open[", "close]", "nul\x00",
               "del\x7f", 7, None]
        for value in bad:
            with self.assertRaises(RoutineError, msg=repr(value)) as raised:
                contract.validate_exclusions([value])
            self.assertEqual(raised.exception.code, "invalid_exclusion")

    def test_at_most_32(self):
        contract.validate_exclusions([f"p{i}*" for i in range(32)])
        with self.assertRaises(RoutineError) as raised:
            contract.validate_exclusions([f"p{i}*" for i in range(33)])
        self.assertEqual(raised.exception.code, "invalid_exclusion")

    def test_must_be_a_list(self):
        with self.assertRaises(RoutineError) as raised:
            contract.validate_exclusions("lenzora*")
        self.assertEqual(raised.exception.code, "invalid_exclusion")

    def test_duplicates_collapse_in_order(self):
        self.assertEqual(contract.validate_exclusions(["b*", "a*", "b*"]), ["b*", "a*"])


class TimeSpanValidation(unittest.TestCase):
    def test_storage_monitor_span_rules(self):
        self.assertEqual(contract.validate_spans("45min", "2min"), ("45min", "2min"))
        for timeout, delay in (("soon", "5min"), ("30min", "5 minutes"),
                               ("", "5min"), ("30min", None), (30, "5min")):
            with self.assertRaises(RoutineError, msg=(timeout, delay)) as raised:
                contract.validate_spans(timeout, delay)
            self.assertEqual(raised.exception.code, "invalid_timeout")

    def test_timeout_bounds(self):
        for timeout in ("30s", "7h", "1d"):
            with self.assertRaises(RoutineError, msg=timeout) as raised:
                contract.validate_spans(timeout, "5min")
            self.assertEqual(raised.exception.code, "invalid_timeout")
        with self.assertRaises(RoutineError):
            contract.validate_spans("30min", "2d")

    def test_span_seconds(self):
        self.assertEqual(contract.span_seconds("30min"), 1800)
        self.assertEqual(contract.span_seconds("1h 30m"), 5400)
        self.assertEqual(contract.span_seconds("90s"), 90)
        self.assertEqual(contract.span_seconds("500ms"), 0.5)

    def test_enable_maps_span_errors(self):
        with self.assertRaises(RoutineError) as raised:
            contract.validate_request(enable(timeout="forever"))
        self.assertEqual(raised.exception.code, "invalid_timeout")


class ResponseShape(unittest.TestCase):
    CONFIG = {
        "schema": 1, "enabled": True, "cadence": "daily", "timeout": "30min",
        "randomized_delay": "5min", "exclusions": ["lenzora*"],
        "enabled_revision": REV, "enabled_at": "2026-10-08T00:00:00Z",
        "disabled_at": None, "unit": "sandbox-cleanup-routine",
        "ssh_target": "root@203.0.113.9", "bearer_token": "secret-value",
    }
    RUN = {
        "schema": 1, "run_id": "b" * 32, "started_at": "2026-10-08T00:03:00Z",
        "ended_at": "2026-10-08T00:04:00Z", "outcome": "reclaimed",
        "reason": None, "bytes_reclaimed": 10, "removed": 1, "skipped": 2,
        "skipped_reasons": {"excluded_by_request": 1}, "runtime_revision": REV,
        "manifest": "deletions/" + "b" * 32 + ".jsonl", "token": "leak",
    }

    def test_envelope_matches_contract(self):
        result = contract.routine_result(
            self.CONFIG, effective_exclusions=["lenzora*", "keep-*"],
            runs=[self.RUN], next_run="2026-10-09T00:03:00Z",
        )
        envelope = contract.envelope(result, runtime_revision=REV)
        self.assertEqual(set(envelope), {"resource_schema", "transport", "service", "result"})
        self.assertEqual(envelope["resource_schema"], 1)
        self.assertEqual(envelope["transport"], "control")
        self.assertEqual(envelope["service"], {"runtime_revision": REV})
        body = envelope["result"]
        self.assertTrue(body["ok"])
        self.assertEqual(set(body), {"ok", "routine", "last_run_revision", "runs"})
        self.assertEqual(set(body["routine"]), {
            "enabled", "cadence", "timeout", "randomized_delay", "exclusions",
            "effective_exclusions", "enabled_revision", "next_run",
            "enabled_at", "disabled_at",
        })
        self.assertEqual(body["routine"]["effective_exclusions"], ["lenzora*", "keep-*"])
        self.assertEqual(body["last_run_revision"], REV)
        self.assertNotIn("token", body["runs"][0])
        rendered = json.dumps(envelope)
        for forbidden in ("ssh_target", "203.0.113.9", "bearer_token",
                          "secret-value", "leak"):
            self.assertNotIn(forbidden, rendered)

    def test_absent_routine_reports_disabled(self):
        body = contract.routine_result(None, effective_exclusions=[], runs=[], next_run=None)
        self.assertFalse(body["routine"]["enabled"])
        self.assertIsNone(body["last_run_revision"])
        self.assertEqual(body["runs"], [])

    def test_error_result_uses_contract_codes(self):
        body = contract.error_result("invalid_cadence", "bad calendar")
        self.assertEqual(body, {"ok": False, "error": {
            "code": "invalid_cadence", "message": "bad calendar"}})
        unknown = contract.error_result("weird", "x")
        self.assertEqual(unknown["error"]["code"], "invalid_request")
        self.assertEqual(contract.ERROR_CODES, frozenset({
            "invalid_request", "invalid_cadence", "invalid_exclusion",
            "invalid_timeout", "runtime_revision_mismatch", "systemd_unavailable",
            "linger_disabled", "routine_install_failed", "routine_remove_failed",
        }))


if __name__ == "__main__":
    unittest.main()
