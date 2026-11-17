"""Strict flag, `remote pin` and migrate pin protection (spec 061 US2, US3)."""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core._remote as sr  # noqa: E402
from sandbox import cli  # noqa: E402
from sandbox.commands import remote as remote_cmd  # noqa: E402
from sandbox.remote_runtime import pins  # noqa: E402
from sandbox.remote_runtime.verdict import STRICT_ENVIRONMENT  # noqa: E402

REV_OLD = "a" * 24
REV_NEW = "b" * 24


def _pin(holder, revision=REV_OLD, *, state="active", expires_in=600):
    import time
    now = int(time.time())
    return pins.Pin(holder, "/checkouts/other", "0" * 16, revision, "sb wp /checkouts/other",
                    now, now, now + expires_in if expires_in > 0 else now - 1,
                    state, "h-" + "9" * 16 if state == "broken" else None,
                    now if state == "broken" else None)


class _Store:
    def __init__(self, listed=(), fail=False):
        self.listed = list(listed)
        self.fail = fail
        self.broken = []
        self.released = []

    def __call__(self, *_args, **_kwargs):
        return self

    def list(self):
        if self.fail:
            raise pins.PinError(pins.PINS_UNAVAILABLE, "unreachable")
        return list(self.listed)

    def release(self, holder):
        self.released.append(holder)
        return True

    def break_pins(self, holders, by):
        self.broken.append((list(holders), by))
        return list(holders)


class StrictFlagTests(unittest.TestCase):
    def setUp(self):
        saved = os.environ.pop(STRICT_ENVIRONMENT, None)
        self.addCleanup(lambda: os.environ.pop(STRICT_ENVIRONMENT, None)
                        if saved is None else os.environ.__setitem__(STRICT_ENVIRONMENT, saved))

    def test_flag_anywhere_before_separator_sets_strict_and_is_removed(self):
        argv = cli._consume_strict_runtime(["wp", "--strict-runtime", "--remote", "x", "--",
                                            "--strict-runtime"])
        self.assertEqual(argv, ["wp", "--remote", "x", "--", "--strict-runtime"])
        self.assertEqual(os.environ.get(STRICT_ENVIRONMENT), "1")

    def test_absent_flag_leaves_environment_alone(self):
        self.assertEqual(cli._consume_strict_runtime(["remote", "list"]), ["remote", "list"])
        self.assertIsNone(os.environ.get(STRICT_ENVIRONMENT))

    def test_operation_status_only_gates_in_strict_mode(self):
        status = {"local_runtime_revision": REV_OLD, "installed_runtime_revision": REV_OLD}
        with patch.object(sr, "remote_mcp_service_status", return_value=status), \
                patch("sandbox.remote_runtime.pins.strict_gate",
                      return_value={"gated": True}) as gate:
            self.assertIs(sr.remote_runtime_status_for_operation({}), status)
            gate.assert_not_called()
            os.environ[STRICT_ENVIRONMENT] = "1"
            self.assertEqual(sr.remote_runtime_status_for_operation({}), {"gated": True})
            gate.assert_called_once()


class PinCommandTests(unittest.TestCase):
    def _run(self, store, operation, **extra):
        args = types.SimpleNamespace(name=operation, ssh_url="vps", holder=None,
                                     break_pins=[], **extra)
        with patch.object(remote_cmd.sr, "get_remote", return_value={"ssh": "target"}), \
                patch("sandbox.remote_runtime.pins.PinStore", store), \
                patch("builtins.print") as printed:
            try:
                remote_cmd._cmd_pin(args, as_json=True)
            except SystemExit:
                pass
        return json.loads(printed.call_args.args[0])

    def test_list_reports_pins_and_this_holder(self):
        store = _Store([_pin("h-" + "1" * 16)])
        payload = self._run(store, "list")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["pins"][0]["holder"], "h-" + "1" * 16)
        self.assertRegex(payload["data"]["holder"], r"^h-[0-9a-f]{16}$")

    def test_release_of_another_holder_requires_break_pin(self):
        other = "h-" + "2" * 16
        store = _Store()
        refused = self._run(store, "release", **{})
        self.assertTrue(refused["ok"])  # own pin
        args_holder = self._run_with_holder(store, other, [])
        self.assertEqual(args_holder["error"]["code"], "remote_pin_not_owned")
        self.assertNotIn(other, store.released)
        allowed = self._run_with_holder(store, other, [other])
        self.assertTrue(allowed["ok"])
        self.assertIn(other, store.released)

    def _run_with_holder(self, store, holder, break_pins):
        args = types.SimpleNamespace(name="release", ssh_url="vps", holder=holder,
                                     break_pins=break_pins)
        with patch.object(remote_cmd.sr, "get_remote", return_value={"ssh": "target"}), \
                patch("sandbox.remote_runtime.pins.PinStore", store), \
                patch("builtins.print") as printed:
            try:
                remote_cmd._cmd_pin(args, as_json=True)
            except SystemExit:
                pass
        return json.loads(printed.call_args.args[0])

    def test_cli_parser_accepts_pin_commands(self):
        from tests.test_remote_runtime_refusal import parse_with_cli
        listed = parse_with_cli("./sb remote pin list vps")
        self.assertEqual((listed.action, listed.name, listed.ssh_url), ("pin", "list", "vps"))
        released = parse_with_cli(
            f"./sb remote pin release vps --holder h-{'3' * 16} --break-pin h-{'3' * 16}")
        self.assertEqual(released.break_pins, ["h-" + "3" * 16])
        migrate = parse_with_cli(
            f"./sb remote service migrate vps --confirm --break-pin h-{'4' * 16} "
            f"--break-pin h-{'5' * 16}")
        self.assertEqual(migrate.break_pins, ["h-" + "4" * 16, "h-" + "5" * 16])


class MigratePinTests(unittest.TestCase):
    def _migrate(self, store, *, confirm, break_pins=(), jobs=(0, []), install_error=None,
                 ignore_jobs=()):
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / "sandbox.local.yml"
            import sandbox.core._config as config
            with patch.object(sr, "CONFIG_LOCAL", local), patch.object(config, "CONFIG_LOCAL", local), \
                    patch.object(sr, "RUNTIME_DIR", Path(d) / "runtime"):
                sr.put_remote("vps", ssh="ubuntu@192.0.2.1", provisioned=True,
                              control_transport="https",
                              control_url="https://sandbox.example.test",
                              mcp_port=9174, bearer_token="t" * 64)
                args = types.SimpleNamespace(name="migrate", ssh_url="vps", confirm=confirm,
                                             upload_timeout=300, break_pins=list(break_pins),
                                             ignore_jobs=list(ignore_jobs),
                                             processes=False, ssh=False)
                service = sr.remote_mcp_service_record("127.0.0.1", 9174,
                                                       "https://sandbox.example.test")
                observed = {"local_runtime_revision": REV_NEW,
                            "installed_runtime_revision": REV_OLD,
                            "runtime_revision_state": "mismatch"}
                with patch.object(sr, "remote_mcp_service_status", return_value=observed), \
                        patch("sandbox.remote_runtime.pins.PinStore", store), \
                        patch.object(remote_cmd, "_local_git_revision", return_value="f" * 40), \
                        patch.object(remote_cmd, "_assert_clean_source_revision"), \
                        patch.object(remote_cmd, "_upload_runtime_source") as upload, \
                        patch.object(sr, "migrate_remote_mcp_service",
                                     return_value={"status": "applied" if confirm else "planned",
                                                   "service": service},
                                     side_effect=install_error) as migrate, \
                        patch("sandbox.remote_runtime.pins.list_active_jobs",
                              return_value=jobs) as listed, \
                        patch("sandbox.hosting.login_reapply_plan.plan",
                              return_value={"state": "complete", "needs_reapply": ["sandbox-host-b"]}), \
                        patch("builtins.print") as printed:
                    self.exit_code = 0
                    try:
                        remote_cmd._cmd_service(args, as_json=True)
                    except SystemExit as exc:
                        self.exit_code = exc.code
                    self.listed = listed
                record = sr.get_remote("vps")
        payload = json.loads(printed.call_args.args[0])
        # 85a00075: --json exits non-zero exactly when the payload failed.
        self.assertEqual(self.exit_code, 0 if payload["ok"] else 1)
        return payload, upload, migrate, record

    def test_plan_lists_each_pin_it_would_break_and_the_protocol_line(self):
        holder = "h-" + "6" * 16
        store = _Store([_pin(holder), _pin("h-" + "7" * 16, REV_NEW),
                        _pin("h-" + "8" * 16, expires_in=0)])
        payload, upload, _migrate, _record = self._migrate(store, confirm=False)
        data = payload["data"]
        self.assertEqual([pin["holder"] for pin in data["would_break_pins"]], [holder])
        self.assertIn("stops_serving_below_protocol", data)
        self.assertIn("control protocol", data["protocol_note"])
        self.assertEqual(data["login_reapply"]["needs_reapply"], ["sandbox-host-b"])
        upload.assert_not_called()

    def test_confirm_without_acknowledgment_refuses_with_zero_writes(self):
        holder = "h-" + "6" * 16
        store = _Store([_pin(holder)])
        payload, upload, migrate, record = self._migrate(store, confirm=True)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "remote_runtime_pins_unacknowledged")
        self.assertIn(holder, payload["error"]["message"])
        upload.assert_not_called()
        migrate.assert_not_called()
        self.assertNotIn("mcp_service", record)
        self.assertEqual(store.broken, [])

    def test_confirm_refuses_when_pins_cannot_be_read(self):
        payload, upload, migrate, _record = self._migrate(_Store(fail=True), confirm=True)
        self.assertEqual(payload["error"]["code"], "remote_runtime_pins_unavailable")
        upload.assert_not_called()
        migrate.assert_not_called()

    def test_acknowledged_pins_are_broken_after_the_install(self):
        holder = "h-" + "6" * 16
        broken_already = _pin("h-" + "5" * 16, state="broken")
        store = _Store([_pin(holder), broken_already])
        payload, upload, migrate, record = self._migrate(store, confirm=True,
                                                         break_pins=[holder])
        self.assertTrue(payload["ok"])
        upload.assert_called_once()
        migrate.assert_called_once()
        self.assertIn("mcp_service", record)
        self.assertEqual([holders for holders, _by in store.broken], [[holder]])
        self.assertEqual(payload["data"]["broken_pins"], [holder])


    # -- db6796fc: a migrate never restarts the service under unfinished jobs --

    JOB = {"job_id": "d" * 32, "kind": "server-cli", "lifecycle": "running"}

    def test_plan_reports_unfinished_jobs_as_a_blocker(self):
        payload, upload, _migrate, _record = self._migrate(_Store(), confirm=False,
                                                           jobs=(1, [self.JOB]))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["active_jobs"],
                         {"state": "observed", "count": 1, "jobs": [self.JOB]})
        self.assertEqual(payload["data"]["blockers"],
                         [{"code": "remote_jobs_active", "count": 1, "jobs": [self.JOB]}])
        upload.assert_not_called()
        clear, *_ = self._migrate(_Store(), confirm=False)
        self.assertEqual(clear["data"]["blockers"], [])

    def test_confirm_refuses_unfinished_jobs_before_any_upload_or_write(self):
        payload, upload, migrate, record = self._migrate(_Store(), confirm=True,
                                                         jobs=(2, [self.JOB]))
        self.assertFalse(payload["ok"])
        self.assertEqual(self.exit_code, 1)
        self.assertEqual(payload["error"]["code"], "remote_jobs_active")
        self.assertEqual(payload["error"]["jobs"], [self.JOB])
        self.assertEqual(payload["error"]["job_count"], 2)
        self.assertIn("d" * 32, payload["error"]["message"])
        upload.assert_not_called()
        migrate.assert_not_called()
        self.assertNotIn("mcp_service", record)

    def test_the_gate_refusal_under_the_admission_lock_is_reported_the_same_way(self):
        """A job admitted after the early listing is caught by the install gate."""
        refusal = pins.jobs_active_error(1, [self.JOB])
        payload, upload, migrate, record = self._migrate(_Store(), confirm=True,
                                                         install_error=refusal)
        self.assertEqual((payload["error"]["code"], self.exit_code), ("remote_jobs_active", 1))
        self.assertEqual(payload["error"]["jobs"], [self.JOB])
        upload.assert_called_once()
        migrate.assert_called_once()
        self.assertNotIn("mcp_service", record)

    def test_an_unreadable_early_listing_leaves_the_decision_to_the_gate(self):
        with patch("sandbox.remote_runtime.pins.list_active_jobs",
                   side_effect=pins.PinError(pins.JOBS_GATE_UNAVAILABLE, "x")):
            remote_cmd._migrate_jobs_precheck({"ssh": "target"})
        unavailable = pins.PinError(pins.JOBS_GATE_UNAVAILABLE, "lock")
        payload, *_ = self._migrate(_Store(), confirm=True, install_error=unavailable)
        self.assertEqual((payload["error"]["code"], self.exit_code),
                         ("remote_jobs_gate_unavailable", 1))

    def test_ignore_job_names_exact_jobs_and_reaches_the_gate(self):
        other = {**self.JOB, "job_id": "e" * 32}
        payload, upload, migrate, _record = self._migrate(
            _Store(), confirm=True, jobs=(2, [self.JOB, other]), ignore_jobs=["d" * 32])
        self.assertEqual(payload["error"]["code"], "remote_jobs_active")
        self.assertEqual(payload["error"]["jobs"], [other])
        upload.assert_not_called()
        payload, upload, migrate, _record = self._migrate(
            _Store(), confirm=True, jobs=(1, [self.JOB]), ignore_jobs=["d" * 32])
        self.assertTrue(payload["ok"])
        self.assertEqual(migrate.call_args.kwargs["ignored_jobs"], ["d" * 32])
        bad, *_ = self._migrate(_Store(), confirm=True, ignore_jobs=["not-a-job"])
        self.assertEqual((bad["ok"], self.exit_code), (False, 1))

    def test_plan_with_an_unreadable_listing_reports_a_blocker(self):
        with patch.object(remote_cmd, "_active_jobs_listing",
                          return_value={"state": "remote_jobs_gate_unavailable",
                                        "count": None, "jobs": None}):
            payload, *_ = self._migrate(_Store(), confirm=False)
        self.assertEqual(payload["data"]["blockers"], [{"code": "remote_jobs_gate_unavailable"}])

    def test_a_successful_confirm_exits_zero(self):
        payload, *_ = self._migrate(_Store(), confirm=True)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.exit_code, 0)

if __name__ == "__main__":
    unittest.main()
