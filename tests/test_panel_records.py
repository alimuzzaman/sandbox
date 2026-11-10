"""Secret-free panel records written by host apply and deploy --stack (spec 076)."""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from sandbox.hosting import panel_records as pr

SECRET = "planted-secret-value-0123456789"
COMMIT = "a" * 40


def validated(stack: bool = True) -> dict:
    value = {
        "project": "xspeed-hub-demo", "environment": "staging",
        "project_root": "/controller/home/stacks/demo/source/x",
        "routes": [
            {"hostname": "hub-demo.example.test", "mode": "serve", "primary": True},
            {"hostname": "admin-demo.example.test", "mode": "serve", "primary": False, "target": None},
            {"hostname": "old-demo.example.test", "mode": "redirect", "primary": False,
             "target": "https://hub-demo.example.test"},
        ],
        "healthcheck": {"path": "/health", "statuses": [200]},
        "secrets": {"values": {"PG_PASSWORD": SECRET}, "keys": ["PG_PASSWORD"]},
        "deploy": {"derived_environment": {"PG_PASSWORD": SECRET}},
        "basic_auth": {"username": "qa", "password": SECRET},
        "stack": None,
    }
    if stack:
        value["stack"] = {"slug": "demo", "ref": "feature/demo", "ref_branch": "feature/demo",
                          "commit": COMMIT,
                          "template": "/controller/home/checkout/deploy/sandbox.hosting.yml",
                          "ports": {"mailpit": 18142}}
    return value


RUNTIME = {"compose_project": "sandbox-host-xspeed-hub-demo-staging", "loopback_port": 18017,
           "environment": f"PG_PASSWORD='{SECRET}'\n", "caddyfile": f"basicauth {SECRET}",
           "compose_override": "services: {}"}
STACK_RECORD = {"slug": "demo", "checkout": "/controller/home/checkout", "created_at": 1_700_000_000,
                "template": "/controller/home/checkout/deploy/sandbox.hosting.yml"}
LAST = {"status": "applying", "started_at": 1_700_000_100, "finished_at": None, "request_id": "req-1"}


def decoded_payload(command: str) -> str:
    match = re.search(r"printf %s '?([A-Za-z0-9+/=]+)'? \| base64 -d", command)
    assert match, command
    return base64.b64decode(match.group(1)).decode()


class BuildSummaryTest(unittest.TestCase):
    def test_allow_list_and_no_secret(self):
        summary = pr.build_target_summary(validated(), "xspeed-dev", RUNTIME, commit=COMMIT,
                                          last_apply=LAST, stack_record=STACK_RECORD)
        encoded = pr.encode_record(summary)
        self.assertNotIn(SECRET, encoded)
        self.assertNotIn("PG_PASSWORD", encoded)
        self.assertNotIn("/controller/home", encoded)
        self.assertEqual(set(summary), {"schema", "remote", "project", "environment", "compose_project",
                                        "loopback_port", "healthcheck_path", "routes", "commit",
                                        "last_apply", "stack"})
        self.assertEqual(summary["routes"][0], {"hostname": "hub-demo.example.test", "mode": "serve",
                                                "primary": True})
        self.assertEqual(summary["routes"][2]["target"], "https://hub-demo.example.test")
        self.assertNotIn("target", summary["routes"][1])
        self.assertEqual(summary["stack"]["template"], "deploy/sandbox.hosting.yml")
        self.assertEqual(summary["stack"]["ports"], {"mailpit": 18142})
        self.assertEqual(summary["stack"]["created_at"], 1_700_000_000)
        self.assertNotIn("ttl_expires_at", summary["stack"])
        self.assertEqual(summary["healthcheck_path"], "/health")

    def test_ttl_only_when_recorded(self):
        record = dict(STACK_RECORD, ttl_expires_at=1_800_000_000)
        summary = pr.build_target_summary(validated(), "r", RUNTIME, commit=None,
                                          last_apply=LAST, stack_record=record)
        self.assertEqual(summary["stack"]["ttl_expires_at"], 1_800_000_000)

    def test_template_outside_checkout_keeps_only_its_name(self):
        summary = pr.build_target_summary(validated(), "r", RUNTIME, commit=None, last_apply=LAST,
                                          stack_record={"checkout": "/elsewhere"})
        self.assertEqual(summary["stack"]["template"], "sandbox.hosting.yml")

    def test_plain_target_has_no_stack(self):
        summary = pr.build_target_summary(validated(stack=False), "r", RUNTIME, commit=COMMIT,
                                          last_apply=LAST)
        self.assertIsNone(summary["stack"])

    def test_invalid_input_is_refused(self):
        broken = validated()
        broken["routes"] = []
        with self.assertRaises(pr.RecordError):
            pr.build_target_summary(broken, "r", RUNTIME, commit=None, last_apply=LAST)


class RecorderTest(unittest.TestCase):
    def recorder(self, fail: bool = False):
        commands: list[str] = []

        def run_remote(command: str) -> None:
            if fail:
                raise RuntimeError(f"ssh failed {SECRET}")
            commands.append(command)
        clock = iter([100, 200, 300])
        recorder = pr.SummaryRecorder(validated(), "xspeed-dev", RUNTIME, run_remote=run_remote,
                                      stack_record=STACK_RECORD, clock=lambda: next(clock))
        return recorder, commands

    def test_started_then_succeeded(self):
        recorder, commands = self.recorder()
        recorder.finished(True)  # before started: no write
        self.assertEqual(commands, [])
        recorder.started("/srv/sandbox", COMMIT, "req-9")
        recorder.finished(True)
        recorder.finished(False)  # already finished: no further write
        self.assertEqual(len(commands), 2)
        first, second = (json.loads(decoded_payload(command)) for command in commands)
        self.assertEqual(first["last_apply"], {"status": "applying", "started_at": 100,
                                               "finished_at": None, "request_id": "req-9"})
        self.assertEqual(second["last_apply"]["status"], "succeeded")
        self.assertEqual(second["last_apply"]["finished_at"], 200)
        self.assertIn("/srv/sandbox/runtime/hosts/xspeed-hub-demo/staging/summary.json", commands[0])
        self.assertIn("umask 077", commands[0])
        self.assertIn("chmod 0600", commands[0])
        self.assertIn("mv -f", commands[0])
        for command in commands:
            self.assertNotIn(SECRET, decoded_payload(command))

    def test_run_marks_failed_and_reraises(self):
        recorder, commands = self.recorder()

        def apply():
            recorder.started("/h", COMMIT, None)
            raise RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            recorder.run(apply)
        self.assertEqual(json.loads(decoded_payload(commands[-1]))["last_apply"]["status"], "failed")

    def test_run_before_started_writes_nothing(self):
        recorder, commands = self.recorder()
        with self.assertRaises(ValueError):
            recorder.run(lambda: (_ for _ in ()).throw(ValueError("refused")))
        self.assertEqual(commands, [])

    def test_write_failure_is_swallowed(self):
        recorder, _ = self.recorder(fail=True)
        recorder.started("/h", COMMIT, "req")
        recorder.finished(True)
        self.assertEqual(recorder.errors, ["RuntimeError", "RuntimeError"])

    def test_run_returns_result(self):
        recorder, _ = self.recorder()
        self.assertEqual(recorder.run(lambda: {"ok": 1}), {"ok": 1})


def delivery(execution="succeeded", state="incomplete", reason="required_evidence_missing"):
    return {"execution_state": execution, "delivery_state": state,
            "delivery_succeeded": state == "succeeded",
            "evidence_completeness": "complete" if state == "succeeded" else "partial", "reason": reason}


class ApplyOutcomeTest(unittest.TestCase):
    """Spec 076 follow-up: the outcome comes from the delivery record, not a bool."""

    def test_outcome_from_delivery_fields(self):
        cases = [
            ((True, delivery(state="succeeded", reason="none")), ("succeeded", None)),
            ((False, delivery()), ("unverified", "required_evidence_missing")),
            ((False, delivery(state="unknown", reason="effect_unknown")), ("unverified", "effect_unknown")),
            ((False, delivery(execution="failed", state="failed", reason="binding_mismatch")),
             ("failed", "binding_mismatch")),
            ((False, delivery(execution="timed_out", state="incomplete")), ("failed", "required_evidence_missing")),
            ((False, delivery(state="failed", reason="initializer_refused")), ("failed", "initializer_refused")),
            ((True, None), ("succeeded", None)),
            ((False, None), ("failed", None)),
            ((False, {"execution_state": "succeeded", "delivery_state": "incomplete",
                      "reason": "<script>"}), ("unverified", None)),
            ((True, "not a mapping"), ("succeeded", None)),
        ]
        for (ok, record), expected in cases:
            with self.subTest(ok=ok, record=record):
                self.assertEqual(pr.apply_outcome(ok, record), expected)

    def recorder(self):
        commands: list[str] = []
        clock = iter([100, 200, 300])
        recorder = pr.SummaryRecorder(validated(), "xspeed-dev", RUNTIME, run_remote=commands.append,
                                      stack_record=STACK_RECORD, clock=lambda: next(clock))
        return recorder, commands

    def run_apply(self, record, ok):
        recorder, commands = self.recorder()

        def apply():
            recorder.started("/h", COMMIT, "req-1")
            # The call inside the apply passes a bool (hosting.py); run() finishes from the result.
            recorder.finished(ok)
            return {"commit": COMMIT, "delivery": record}
        result = recorder.run(apply)
        self.assertEqual(result["delivery"], record)
        return [json.loads(decoded_payload(command))["last_apply"] for command in commands]

    def test_executed_but_unverified_is_not_failed(self):
        writes = self.run_apply(delivery(), ok=False)
        self.assertEqual([write["status"] for write in writes], ["applying", "unverified"])
        self.assertEqual(writes[-1]["reason"], "required_evidence_missing")
        self.assertEqual(writes[-1]["finished_at"], 200)

    def test_execution_failure_is_failed(self):
        writes = self.run_apply(delivery(execution="failed", state="failed", reason="binding_mismatch"), ok=False)
        self.assertEqual([write["status"] for write in writes], ["applying", "failed"])
        self.assertEqual(writes[-1]["reason"], "binding_mismatch")

    def test_complete_delivery_is_succeeded(self):
        writes = self.run_apply(delivery(state="succeeded", reason="none"), ok=True)
        self.assertEqual(writes[-1]["status"], "succeeded")
        self.assertNotIn("reason", writes[-1])

    def test_finished_outside_run_still_works(self):
        recorder, commands = self.recorder()
        recorder.started("/h", COMMIT, None)
        recorder.finished(False, delivery())
        self.assertEqual(json.loads(decoded_payload(commands[-1]))["last_apply"]["status"], "unverified")

    def test_apply_that_never_finishes_stays_applying(self):
        recorder, commands = self.recorder()

        def apply():
            recorder.started("/h", COMMIT, None)
            return {"refused": True}
        recorder.run(apply)
        self.assertEqual([json.loads(decoded_payload(c))["last_apply"]["status"] for c in commands], ["applying"])

    def test_nested_run_keeps_the_outer_deferral(self):
        recorder, commands = self.recorder()

        def apply():
            recorder.started("/h", COMMIT, None)
            recorder.run(lambda: None)  # a nested run must not end the outer deferral
            recorder.finished(False)
            return {"delivery": delivery()}
        recorder.run(apply)
        self.assertEqual([json.loads(decoded_payload(c))["last_apply"]["status"] for c in commands],
                         ["applying", "unverified"])

    def test_summary_parser_accepts_unverified_and_validates_reason(self):
        base = json.loads(pr.encode_record(pr.build_target_summary(
            validated(), "xspeed-dev", RUNTIME, commit=COMMIT,
            last_apply={"status": "unverified", "started_at": 1, "finished_at": 2, "request_id": None,
                        "reason": "required_evidence_missing"})))
        self.assertEqual(pr.parse_target_summary(base)["last_apply"]["reason"], "required_evidence_missing")
        base["last_apply"]["reason"] = "Not A Reason!"
        with self.assertRaises(pr.RecordError):
            pr.parse_target_summary(base)


class ReadServerRecordsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name) / "runtime"

    def write(self, relative: str, data) -> Path:
        path = self.runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return path

    def summary(self, **changes) -> dict:
        value = pr.build_target_summary(validated(), "xspeed-dev", RUNTIME, commit=COMMIT,
                                        last_apply=LAST, stack_record=STACK_RECORD)
        value.update(changes)
        return value

    def test_reads_targets_legacy_and_links(self):
        self.write("hosts/xspeed-hub-demo/staging/summary.json", self.summary())
        self.write("hosts/xspeed-hub-demo/staging/environment.env", f"PG_PASSWORD={SECRET}")
        self.write("hosts/legacy/production/compose.override.yml", "services: {}")
        self.write("hosts/empty/production/apply.log", "")
        link = pr.build_site_link(remote="xspeed-dev", slug="demo", hostname="wp-demo.example.test",
                                  instance="sandbox-wp-demo", label="default", commit=COMMIT,
                                  recorded_at=1)
        self.write("stack-sites/demo/wp-demo.example.test.json", link)
        records = pr.read_server_records(self.runtime)
        self.assertEqual([t["project"] for t in records["targets"]], ["xspeed-hub-demo"])
        self.assertEqual(records["legacy"], [{"project": "legacy", "environment": "production"}])
        self.assertEqual(records["links"], [link])
        self.assertEqual(records["partial_reasons"], [])
        self.assertNotIn(SECRET, json.dumps(records))

    def test_never_opens_environment_file(self):
        self.write("hosts/xspeed-hub-demo/staging/summary.json", self.summary())
        self.write("hosts/xspeed-hub-demo/staging/environment.env", f"X={SECRET}")
        opened = []
        real_open = os.open

        def spy(path, *args, **kwargs):
            opened.append(str(path))
            return real_open(path, *args, **kwargs)
        with unittest.mock.patch.object(pr.os, "open", side_effect=spy):
            pr.read_server_records(self.runtime)
        self.assertTrue(opened)
        self.assertFalse([path for path in opened if path.endswith("environment.env")])

    def test_malformed_and_mismatched_records_are_partial(self):
        self.write("hosts/xspeed-hub-demo/staging/summary.json", "{not json")
        self.write("hosts/other/production/summary.json", self.summary(schema=2))
        self.write("hosts/moved/production/summary.json", self.summary())  # names another target
        self.write("stack-sites/demo/wp-other.example.test.json",
                   pr.build_site_link(remote="r", slug="demo", hostname="wp-demo.example.test",
                                      instance="i", label="default", commit=None, recorded_at=1))
        records = pr.read_server_records(self.runtime)
        self.assertEqual(records["targets"], [])
        self.assertEqual(len(records["partial_reasons"]), 4)
        self.assertTrue(all(":" in reason for reason in records["partial_reasons"]))

    def test_symlinked_summary_is_refused(self):
        secret_file = self.write("elsewhere.json", self.summary())
        target_dir = self.runtime / "hosts/xspeed-hub-demo/staging"
        target_dir.mkdir(parents=True)
        (target_dir / "summary.json").symlink_to(secret_file)
        records = pr.read_server_records(self.runtime)
        self.assertEqual(records["targets"], [])
        self.assertEqual(len(records["partial_reasons"]), 1)

    def link_file(self, host: str) -> None:
        self.write(f"stack-sites/demo/{host}.json",
                   pr.build_site_link(remote="r", slug="demo", hostname=host, instance="i",
                                      label="default", commit=None, recorded_at=1))

    def test_site_links_are_bounded(self):
        for index in range(5):
            self.link_file(f"wp{index}-demo.example.test")
        with unittest.mock.patch.object(pr, "MAX_SITE_LINKS", 2):
            records = pr.read_server_records(self.runtime)
        self.assertEqual(len(records["links"]), 2)
        self.assertEqual(records["partial_reasons"], ["site_links_truncated"])

    def test_directory_scans_are_bounded(self):
        for index in range(5):
            self.link_file(f"wp{index}-demo.example.test")
        with unittest.mock.patch.object(pr, "SCAN_LIMIT", 3):
            records = pr.read_server_records(self.runtime)
        self.assertLessEqual(len(records["links"]), 3)
        self.assertIn("site_links_truncated:demo", records["partial_reasons"])

    def test_total_scan_budget_spans_the_tree(self):
        for index in range(3):
            self.write(f"hosts/p{index}/production/compose.override.yml", "services: {}")
        with unittest.mock.patch.object(pr, "TOTAL_SCAN_LIMIT", 4):
            records = pr.read_server_records(self.runtime)
        self.assertLess(len(records["legacy"]), 3)
        self.assertTrue(any(reason.startswith("hosted_targets_truncated") for reason in records["partial_reasons"]))

    def test_targets_and_reasons_are_bounded(self):
        self.write("hosts/xspeed-hub-demo/staging/summary.json", self.summary())
        self.write("hosts/zz-legacy/production/compose.override.yml", "services: {}")
        with unittest.mock.patch.object(pr, "MAX_TARGETS", 1):
            records = pr.read_server_records(self.runtime)
        self.assertEqual(len(records["targets"]) + len(records["legacy"]), 1)
        self.assertEqual(records["partial_reasons"], ["hosted_targets_truncated"])
        for index in range(4):
            self.write(f"hosts/bad{index}/production/summary.json", "{")
        with unittest.mock.patch.object(pr, "MAX_PARTIAL_REASONS", 2):
            records = pr.read_server_records(self.runtime)
        self.assertEqual(len(records["partial_reasons"]), 2)  # never more than the cap
        self.assertEqual(records["partial_reasons"][-1], "partial_reasons_truncated")

    def test_missing_runtime_is_empty(self):
        self.assertEqual(pr.read_server_records(self.runtime),
                         {"targets": [], "legacy": [], "links": [], "partial_reasons": []})


if __name__ == "__main__":
    unittest.main()
