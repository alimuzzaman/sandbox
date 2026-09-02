"""Focused policy/configuration tests for Spec 043 Phase 1."""

from __future__ import annotations

import builtins
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from unittest import TestCase, mock

from sandbox.config.manifest import MACHINE_CONFIG_PROVIDERS, apply_machine_config
from sandbox.config.storage_monitor import (
    DEFAULTS,
    StorageMonitorConfigError,
    normalize_storage_monitor,
)
from sandbox.resources import reclaim
from sandbox.resources import monitor


ROOT = Path(__file__).resolve().parent.parent


class TestStorageMonitorDefaults(TestCase):
    def test_defaults_are_complete_and_auto_ratio_follows_critical(self):
        policy = normalize_storage_monitor()
        self.assertEqual(policy, {
            "warn_ratio": 0.15,
            "critical_ratio": 0.05,
            "auto_enabled": False,
            "auto_tier": "safe",
            "auto_ratio": 0.05,
            "reap_enabled": False,
            "reap_ttl": None,
            "schedule_calendar": "hourly",
            "schedule_randomized_delay": "5min",
            "schedule_timeout": "30min",
            "record_max_age_seconds": 21600,
        })

    def test_defaults_cannot_be_mutated_and_results_are_detached(self):
        with self.assertRaises(TypeError):
            DEFAULTS["auto_enabled"] = True

        first = normalize_storage_monitor()
        first["auto_enabled"] = True
        first["schedule_calendar"] = "daily"
        second = normalize_storage_monitor()
        self.assertFalse(second["auto_enabled"])
        self.assertEqual(second["schedule_calendar"], "hourly")

    def test_input_mapping_is_not_mutated(self):
        raw = {"auto_enabled": True, "auto_ratio": None}
        normalize_storage_monitor(raw)
        self.assertEqual(raw, {"auto_enabled": True, "auto_ratio": None})


class TestStorageMonitorValidation(TestCase):
    def assert_code(self, raw, code):
        with self.assertRaises(StorageMonitorConfigError) as raised:
            normalize_storage_monitor(raw)
        self.assertEqual(raised.exception.code, code)

    def test_raw_configuration_must_be_a_mapping(self):
        self.assert_code([], "invalid_schedule_field")
        self.assert_code("hourly", "invalid_schedule_field")

    def test_unknown_keys_are_rejected(self):
        self.assert_code({"autor_enabled": True}, "unknown_key")

    def test_ratio_bounds_are_strict(self):
        for value in (0, 1, -0.1, 1.1, "0.15", True, float("nan"), float("inf")):
            self.assert_code({"warn_ratio": value}, "invalid_threshold")
        for field in ("critical_ratio", "auto_ratio"):
            for value in (0, 1, -0.1, 1.1, "0.05", False):
                self.assert_code({field: value}, "invalid_threshold")

    def test_threshold_order_is_rejected(self):
        self.assert_code(
            {"warn_ratio": 0.10, "critical_ratio": 0.11},
            "invalid_threshold_order",
        )
        self.assert_code(
            {"warn_ratio": 0.10, "auto_ratio": 0.11},
            "invalid_threshold_order",
        )

    def test_null_auto_ratio_resolves_to_the_selected_critical_ratio(self):
        policy = normalize_storage_monitor({
            "warn_ratio": 0.30,
            "critical_ratio": 0.12,
            "auto_ratio": None,
        })
        self.assertEqual(policy["auto_ratio"], 0.12)

    def test_auto_tier_is_safe_only(self):
        for value in ("tmp", "all", " safe ", None, 1):
            self.assert_code({"auto_tier": value}, "invalid_auto_tier")

    def test_flags_require_real_booleans(self):
        for field in ("auto_enabled", "reap_enabled"):
            for value in ("true", "false", 1, 0, [], None):
                self.assert_code({field: value}, "invalid_flag")
        self.assertTrue(normalize_storage_monitor({"auto_enabled": True})["auto_enabled"])

    def test_reap_duration_uses_existing_parser(self):
        with mock.patch.object(reclaim, "parse_duration", wraps=reclaim.parse_duration) as parser:
            policy = normalize_storage_monitor({"reap_ttl": "14d"})
        parser.assert_called_once_with("14d")
        self.assertEqual(policy["reap_ttl"], "14d")
        for value in ("", "0h", "-1d", "2 days", 3600, True):
            self.assert_code({"reap_ttl": value}, "invalid_duration")

    def test_schedule_fields_and_spans_are_validated(self):
        for value in (None, "", "\n", "\x00", "\x7f"):
            self.assert_code({"schedule_calendar": value}, "invalid_schedule_field")
        for field in ("schedule_randomized_delay", "schedule_timeout"):
            for value in (None, "", "5", "five minutes", "\n5min", " 5min"):
                self.assert_code({field: value}, "invalid_schedule_field")
        policy = normalize_storage_monitor({
            "schedule_calendar": "Mon..Fri 09:00",
            "schedule_randomized_delay": "1h 30min",
            "schedule_timeout": "2h",
        })
        self.assertEqual(policy["schedule_calendar"], "Mon..Fri 09:00")

    def test_record_age_is_a_positive_integer(self):
        for value in (0, -1, 1.0, "21600", True, None):
            self.assert_code({"record_max_age_seconds": value}, "invalid_schedule_field")
        self.assertEqual(
            normalize_storage_monitor({"record_max_age_seconds": 1})[
                "record_max_age_seconds"
            ],
            1,
        )

    def test_normalizer_performs_no_io(self):
        with mock.patch.object(builtins, "open", side_effect=AssertionError("I/O")):
            self.assertEqual(normalize_storage_monitor()["warn_ratio"], 0.15)


class TestStorageMonitorMachineManifest(TestCase):
    def test_manifest_registers_nested_machine_provider(self):
        providers = {item[0]: item for item in MACHINE_CONFIG_PROVIDERS}
        key, provider, owner, order = providers["resources.monitor"]
        self.assertEqual(key, "resources.monitor")
        self.assertEqual(owner, "sandbox.config.storage_monitor")
        self.assertEqual(order, 10)
        self.assertEqual(
            provider({"resources": {"monitor": {"auto_enabled": True}}})[
                "auto_enabled"
            ],
            True,
        )

    def test_apply_machine_config_normalizes_without_mutating_raw_shape(self):
        raw = {
            "resources": {"monitor": {"auto_enabled": True, "auto_ratio": 0.08}},
            "remotes": {"host": {"ssh": "user@example.invalid"}},
        }
        normalized = apply_machine_config(raw)
        self.assertTrue(normalized["resources"]["monitor"]["auto_enabled"])
        self.assertEqual(normalized["resources"]["monitor"]["auto_ratio"], 0.08)
        self.assertEqual(raw["resources"]["monitor"], {
            "auto_enabled": True,
            "auto_ratio": 0.08,
        })
        self.assertNotIn("resources.monitor", normalized)
        self.assertEqual(normalized["remotes"], raw["remotes"])

    def test_apply_machine_config_adds_defaults_when_block_is_absent(self):
        normalized = apply_machine_config({"version": "0.1.0"})
        self.assertEqual(normalized["resources"]["monitor"]["auto_ratio"], 0.05)
        self.assertFalse(normalized["resources"]["monitor"]["reap_enabled"])

    def test_machine_resources_must_be_a_mapping(self):
        self.assert_code_for_machine({"resources": []}, "invalid_schedule_field")

    def assert_code_for_machine(self, raw, code):
        with self.assertRaises(StorageMonitorConfigError) as raised:
            apply_machine_config(raw)
        self.assertEqual(raised.exception.code, code)


class TestStorageMonitorYamlDefaults(TestCase):
    def test_sandbox_yaml_declares_off_by_default_monitor(self):
        import yaml

        text = (ROOT / "sandbox.yml").read_text()
        document = yaml.safe_load(text)
        monitor = document["resources"]["monitor"]
        self.assertEqual(monitor, {
            "warn_ratio": 0.15,
            "critical_ratio": 0.05,
            "auto_enabled": False,
            "auto_tier": "safe",
            "auto_ratio": None,
            "reap_enabled": False,
            "reap_ttl": None,
            "schedule_calendar": "hourly",
            "schedule_randomized_delay": "5min",
            "schedule_timeout": "30min",
            "record_max_age_seconds": 21600,
        })
        self.assertIn("auto_enabled` and `reap_enabled` are deletion authority", text)


class TestStorageMonitorResolution(TestCase):
    def test_precedence_and_sparse_remote_override(self):
        # load_config() owns the checked-in + machine-local merge.  The
        # monitor resolver then overlays only the target's sparse keys before
        # the manifest performs its one normalization pass.
        loaded = {
            "resources": {
                "monitor": {
                    "warn_ratio": 0.22,
                    "critical_ratio": 0.09,
                    "auto_enabled": False,
                    "schedule_calendar": "daily",
                },
            },
        }
        entry = {
            "ssh": "owner@example.invalid",
            "storage_monitor": {"auto_enabled": True, "auto_ratio": 0.11},
        }
        with mock.patch.object(monitor, "load_config", return_value=loaded), \
             mock.patch.object(monitor, "get_remote", return_value=entry):
            policy = monitor.resolve_policy("remote-a")

        self.assertEqual(policy["warn_ratio"], 0.22)
        self.assertEqual(policy["critical_ratio"], 0.09)
        self.assertTrue(policy["auto_enabled"])
        self.assertEqual(policy["auto_ratio"], 0.11)
        self.assertEqual(policy["schedule_calendar"], "daily")
        self.assertFalse(policy["reap_enabled"])
        self.assertNotIn("ssh", policy)

    def test_local_resolution_uses_machine_layer_and_built_in_fallbacks(self):
        loaded = {"resources": {"monitor": {"auto_enabled": True}}}
        with mock.patch.object(monitor, "load_config", return_value=loaded) as load, \
             mock.patch.object(monitor, "apply_machine_config",
                               wraps=apply_machine_config) as apply:
            policy = monitor.resolve_policy(None)

        load.assert_called_once_with()
        apply.assert_called_once()
        self.assertTrue(policy["auto_enabled"])
        self.assertEqual(policy["warn_ratio"], 0.15)
        self.assertEqual(policy["critical_ratio"], 0.05)
        self.assertEqual(policy["auto_ratio"], 0.05)

    def test_remote_only_sparse_layer_keeps_built_in_defaults(self):
        with mock.patch.object(monitor, "load_config", return_value={}), \
             mock.patch.object(
                 monitor, "get_remote",
                 return_value={"storage_monitor": {"auto_enabled": True}},
             ):
            policy = monitor.resolve_policy("remote-a")
        self.assertTrue(policy["auto_enabled"])
        self.assertEqual(policy["warn_ratio"], 0.15)
        self.assertEqual(policy["critical_ratio"], 0.05)
        self.assertEqual(policy["auto_ratio"], 0.05)

    def test_registered_remote_from_list_remotes_supplies_sparse_override(self):
        with mock.patch.object(monitor, "load_config", return_value={}), \
             mock.patch.object(monitor, "get_remote", return_value=None), \
             mock.patch.object(
                 monitor,
                 "list_remotes",
                 return_value={"remote-a": {"storage_monitor": {"warn_ratio": 0.3}}},
             ):
            policy = monitor.resolve_policy("remote-a")
        self.assertEqual(policy["warn_ratio"], 0.3)
        self.assertEqual(policy["critical_ratio"], 0.05)

    def test_unknown_target_fails_before_config_or_process_work(self):
        with mock.patch.object(monitor, "get_remote", return_value=None), \
             mock.patch.object(monitor, "list_remotes", return_value={}), \
             mock.patch.object(monitor, "load_config",
                               side_effect=AssertionError("config should not load")), \
             mock.patch.object(subprocess, "run",
                               side_effect=AssertionError("no subprocess")):
            with self.assertRaises(StorageMonitorConfigError) as raised:
                monitor.resolve_policy("not-registered")
        self.assertEqual(raised.exception.code, "unknown_target")
        self.assertIn("not-registered", str(raised.exception))

    def test_contradictory_local_descriptor_fails_before_config_load(self):
        descriptor = {"kind": "local", "name": "remote-a"}
        with mock.patch.object(
            monitor, "load_config", side_effect=AssertionError("config should not load")
        ) as load:
            with self.assertRaises(StorageMonitorConfigError) as raised:
                monitor.resolve_policy(descriptor)
        self.assertEqual(raised.exception.code, "unknown_target")
        load.assert_not_called()


class TestStorageMonitorRecords(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.runtime_patch = mock.patch.object(monitor, "RUNTIME_DIR", self.runtime)
        self.runtime_patch.start()

    def tearDown(self):
        self.runtime_patch.stop()
        self.temp.cleanup()

    @staticmethod
    def record(target="local", **extra):
        value = {
            "schema": 1,
            "target": target,
            "at": "2026-08-20T00:00:00Z",
            "level": "normal",
            "free_bytes": 100,
            "total_bytes": 200,
        }
        value.update(extra)
        return value

    def test_path_is_opaque_and_uses_required_digest(self):
        path = monitor.record_path({"kind": "remote", "name": "secret-host"})
        expected = hashlib.sha256(b"remote:secret-host").hexdigest()[:24] + ".json"
        self.assertEqual(path.name, expected)
        self.assertNotIn("secret-host", str(path))
        self.assertEqual(
            monitor.record_path("local").name,
            hashlib.sha256(b"local").hexdigest()[:24] + ".json",
        )

    def test_round_trip_is_private_and_missing_or_corrupt_reads_fail_closed(self):
        record = self.record({
            "kind": "remote",
            "name": "remote-a",
            "ssh": "do-not-persist",
        })
        path = monitor.write_record(record)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        loaded = monitor.read_record({"kind": "remote", "name": "remote-a"})
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["target"], {"kind": "remote", "name": "remote-a"})
        self.assertNotIn("do-not-persist", path.read_text())

        missing = monitor.read_record({"kind": "remote", "name": "other"})
        self.assertIsNone(missing)
        path.write_text("not-json", encoding="utf-8")
        self.assertIsNone(monitor.read_record({"kind": "remote", "name": "remote-a"}))

    def test_record_schema_is_closed_finite_and_all_runner_fields_are_preserved(self):
        full = self.record(
            {
                "kind": "remote",
                "name": "remote-a",
            },
            trigger="scheduled",
            free_ratio=0.5,
            warn_ratio=0.15,
            critical_ratio=0.05,
            auto_ratio=0.05,
            threshold_crossed=None,
            guidance="no action required",
            auto={
                "enabled": False,
                "eligible": False,
                "tier": None,
                "ran": False,
                "reclaimed_bytes": 0,
                "run_id": None,
                "reason": "disabled",
            },
            reap={
                "enabled": False,
                "dry_run": True,
                "candidates": 0,
                "reclaimed_bytes": 0,
                "reason": "disabled",
            },
            inventory_status="complete",
            errors=[],
        )
        path = monitor.write_record(full)
        self.assertEqual(monitor.read_record({"kind": "remote", "name": "remote-a"}), full)
        self.assertNotIn("ssh", path.read_text(encoding="utf-8"))

        for invalid in (
            self.record(ssh="owner@example.invalid"),
            self.record(free_ratio=float("nan")),
            self.record(auto={"enabled": False, "unexpected": "field"}),
        ):
            with self.assertRaises(ValueError):
                monitor.write_record(invalid)

    def test_read_record_rejects_foreign_or_malformed_embedded_target(self):
        path = monitor.write_record(self.record("local"))
        path.write_text(
            '{"schema":1,"target":{"kind":"remote","name":"other"},'
            '"at":"2026-08-20T00:00:00Z","level":"normal",'
            '"free_bytes":100,"total_bytes":200}',
            encoding="utf-8",
        )
        self.assertIsNone(monitor.read_record("local"))

        path.write_text(
            '{"schema":1,"target":{"kind":"local","name":"local"},'
            '"at":"2026-08-20T00:00:00Z","level":"normal",'
            '"free_bytes":100,"total_bytes":200,"ssh":"secret"}',
            encoding="utf-8",
        )
        self.assertIsNone(monitor.read_record("local"))

    def test_atomic_replacement_keeps_previous_record_on_failure_and_latest_only(self):
        first = self.record(level="warning")
        path = monitor.write_record(first)
        original = path.read_text(encoding="utf-8")
        second = self.record(level="critical")
        with mock.patch.object(monitor.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                monitor.write_record(second)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

        monitor.write_record(second)
        self.assertEqual(monitor.read_record("local")["level"], "critical")
        self.assertEqual(
            [item for item in path.parent.iterdir() if item.suffix == ".json"],
            [path],
        )

    def test_record_age_handles_valid_future_and_invalid_evidence(self):
        now = datetime(2026, 8, 20, tzinfo=timezone.utc)
        record = self.record(at="2026-08-19T23:00:00Z")
        self.assertEqual(monitor.record_age_seconds(record, now), 3600.0)
        self.assertEqual(
            monitor.record_age_seconds(self.record(at="2026-08-20T01:00:00Z"), now),
            0.0,
        )
        self.assertIsNone(monitor.record_age_seconds({}, now))
        self.assertIsNone(monitor.record_age_seconds(self.record(at="invalid"), now))
        self.assertIsNone(monitor.record_age_seconds(self.record(at=None), now))

    def test_write_record_uses_owner_only_fchmod_and_same_directory_tempfile(self):
        real_mkstemp = monitor.tempfile.mkstemp
        seen = {}

        def capture_mkstemp(*args, **kwargs):
            seen["dir"] = kwargs.get("dir")
            return real_mkstemp(*args, **kwargs)

        with mock.patch.object(monitor.tempfile, "mkstemp", side_effect=capture_mkstemp), \
             mock.patch.object(monitor.os, "fchmod", wraps=monitor.os.fchmod) as fchmod:
            path = monitor.write_record(self.record())
        self.assertEqual(Path(seen["dir"]), path.parent)
        fchmod.assert_called_once()
        self.assertEqual(fchmod.call_args.args[1], 0o600)


class TestStorageMonitorLocks(TestCase):
    """Persistent fd-owned lease contract (Spec 043 T006)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        self.runtime_patch = mock.patch.object(monitor, "RUNTIME_DIR", self.runtime)
        self.runtime_patch.start()
        self.local = {"kind": "local", "name": "local"}
        self.remote = {"kind": "remote", "name": "secret-remote"}

    def tearDown(self):
        self.runtime_patch.stop()
        self.temp.cleanup()

    @staticmethod
    def _timestamp(delta_seconds=0):
        from datetime import timedelta

        return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat().replace(
            "+00:00", "Z"
        )

    def _guard_file(self, target, *, state="active", created_delta=0,
                    released_delta=60, pid=99999999, token="a" * 32,
                    payload=None, mode=0o600):
        path = monitor.guard_path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        value = payload if payload is not None else {
            "schema": 2,
            "state": state,
            "pid": pid,
            "created_at": self._timestamp(created_delta),
            "released_at": (
                None if state == "active" else self._timestamp(created_delta + released_delta)
            ),
            "owner_token": token,
        }
        path.write_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii"))
        os.chmod(path, mode)
        return path

    def _legacy_file(self, target, *, created_delta=0, pid=99999999,
                     token="a" * 32, raw=None, mode=0o600):
        path = monitor.lock_path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "schema": 1,
            "pid": pid,
            "created_at": self._timestamp(created_delta),
            "owner_token": token,
        }
        path.write_bytes(
            raw if raw is not None else json.dumps(
                value, sort_keys=True, separators=(",", ":")
            ).encode("ascii")
        )
        os.chmod(path, mode)
        return path

    @staticmethod
    def _read_json(path):
        return json.loads(path.read_text(encoding="ascii"))

    def test_opaque_local_remote_paths_modes_and_schema2_payload(self):
        locks = [monitor.monitor_lock(self.local), monitor.monitor_lock(self.remote)]
        try:
            self.assertTrue(all(lock.acquired for lock in locks))
            self.assertEqual({lock.reason for lock in locks}, {"acquired"})
            self.assertEqual(monitor.guard_path(self.local).parent.stat().st_mode & 0o777, 0o700)
            for target, lock in zip((self.local, self.remote), locks):
                path = monitor.guard_path(target)
                self.assertNotIn("secret-remote", str(path))
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                raw = path.read_bytes()
                self.assertLessEqual(len(raw), 4096)
                raw.decode("ascii")
                payload = self._read_json(path)
                self.assertEqual(set(payload), {
                    "schema", "state", "pid", "created_at", "released_at", "owner_token",
                })
                self.assertEqual(payload["schema"], 2)
                self.assertEqual(payload["state"], "active")
                self.assertIsNone(payload["released_at"])
                self.assertGreater(payload["pid"], 0)
                self.assertRegex(payload["owner_token"], r"^[0-9a-f]{32}$")
                self.assertTrue(payload["created_at"].endswith("Z"))
                self.assertEqual(
                    raw,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii"),
                )
                self.assertNotIn("secret-remote", raw.decode("ascii"))
        finally:
            for lock in locks:
                lock.release()
        for target in (self.local, self.remote):
            self.assertTrue(monitor.guard_path(target).exists())
            self.assertEqual(self._read_json(monitor.guard_path(target))["state"], "released")
            self.assertFalse(monitor.lock_path(target).exists())

    def test_same_target_contender_is_immediate_but_different_target_acquires(self):
        first = monitor.monitor_lock(self.remote)
        other = None
        try:
            contender = monitor.monitor_lock(self.remote)
            self.assertFalse(contender.acquired)
            self.assertEqual(contender.reason, "lock_held")
            other = monitor.monitor_lock(self.local)
            self.assertTrue(other.acquired)
        finally:
            first.release()
            if other is not None:
                other.release()

    def test_normal_and_exception_release_keep_released_marker_and_reacquire(self):
        lock = monitor.monitor_lock(self.remote)
        with lock:
            pass
        path = monitor.guard_path(self.remote)
        released = self._read_json(path)
        self.assertEqual(released["state"], "released")
        self.assertIsNotNone(released["released_at"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        lock.release()

        reacquired = monitor.monitor_lock(self.remote)
        self.assertTrue(reacquired.acquired)
        try:
            with self.assertRaisesRegex(RuntimeError, "body failure"):
                with reacquired:
                    raise RuntimeError("body failure")
        finally:
            reacquired.release()
        released_again = self._read_json(path)
        self.assertEqual(released_again["state"], "released")
        self.assertIsNotNone(released_again["released_at"])

    def test_invalid_grace_is_bounded_and_does_not_leak_target(self):
        values = (True, False, 0, -1, 86401, 1.0, "1", None, float("nan"), float("inf"), 10**400)
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(monitor.StorageMonitorLockError) as raised:
                    monitor.monitor_lock(self.remote, stale_after_seconds=value)
                self.assertEqual(raised.exception.code, "invalid_lock_grace")
                self.assertNotIn("secret-remote", str(raised.exception))
                self.assertNotIn(str(monitor.RUNTIME_DIR), str(raised.exception))

    def test_held_lock_is_immediate_and_exceptional_context_releases(self):
        first = monitor.monitor_lock(self.remote)
        self.assertTrue(first.acquired)
        try:
            second = monitor.monitor_lock(self.remote)
            self.assertFalse(second.acquired)
            self.assertEqual(second.reason, "lock_held")
            with self.assertRaisesRegex(RuntimeError, "boom"):
                with first:
                    raise RuntimeError("boom")
            released = self._read_json(monitor.guard_path(self.remote))
            self.assertEqual(released["state"], "released")
            third = monitor.monitor_lock(self.remote)
            self.assertTrue(third.acquired)
            third.release()
            third.release()
        finally:
            first.release()

    def test_active_evidence_matrix_is_fail_closed_except_old_dead(self):
        cases = (
            ("live", -3600, None, "lock_held", os.getpid()),
            ("young-dead", -10, ProcessLookupError(), "lock_held", 99999999),
            ("old-dead", -3600, ProcessLookupError(), "stale_lock_recovered", 99999999),
            ("eperm", -3600, OSError(errno.EPERM, "denied"), "lock_held", 99999999),
            ("future", 3600, ProcessLookupError(), "lock_held", 99999999),
        )
        for label, age, kill_error, expected, pid in cases:
            with self.subTest(label=label):
                target = {"kind": "remote", "name": f"remote-{label}"}
                path = self._guard_file(target, created_delta=age, pid=pid)
                effect = None if kill_error is None else kill_error
                with mock.patch.object(monitor.os, "kill", side_effect=effect):
                    lock = monitor.monitor_lock(target, stale_after_seconds=1800)
                self.assertEqual(lock.reason, expected)
                if lock.acquired:
                    self.assertEqual(self._read_json(path)["state"], "active")
                    lock.release()
                else:
                    self.assertTrue(path.exists())
                self.assertTrue(path.exists())

    def test_malformed_unreadable_symlink_multilink_and_wrong_mode_are_held(self):
        target = self.remote
        malformed = self._guard_file(target, payload={"schema": 2}, mode=0o600)
        self.assertEqual(monitor.monitor_lock(target).reason, "lock_held")
        malformed.unlink()

        oversized = self._guard_file(target, payload={"schema": 2, "blob": "x" * 5000}, mode=0o600)
        self.assertEqual(monitor.monitor_lock(target).reason, "lock_held")
        oversized.unlink()

        wrong_state = self._guard_file(target, payload={
            "schema": 2, "state": "active", "pid": os.getpid(),
            "created_at": self._timestamp(-3600), "released_at": self._timestamp(-3590),
            "owner_token": "a" * 32,
        })
        with mock.patch.object(monitor.os, "kill", side_effect=ProcessLookupError()):
            self.assertEqual(monitor.monitor_lock(target).reason, "lock_held")
        wrong_state.unlink()

        source = self.runtime / "source"
        source.write_text("not-a-lock", encoding="utf-8")
        link = monitor.guard_path(target)
        link.symlink_to(source)
        self.assertEqual(monitor.monitor_lock(target).reason, "lock_held")
        self.assertTrue(link.is_symlink())
        link.unlink()

        safe = self._guard_file(target)
        sibling = safe.with_name("sibling")
        os.link(safe, sibling)
        self.assertEqual(monitor.monitor_lock(target).reason, "lock_held")
        safe.unlink()
        sibling.unlink()

    def test_one_stale_recovery_winner_and_guard_contention(self):
        target = self.remote
        self._guard_file(target, created_delta=-3600)
        barrier = threading.Barrier(2)
        results = []

        def take():
            barrier.wait()
            with mock.patch.object(monitor.os, "kill", side_effect=ProcessLookupError()):
                result = monitor.monitor_lock(target, stale_after_seconds=1800)
            results.append(result)

        workers = [threading.Thread(target=take) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
        self.assertEqual(sum(result.acquired for result in results), 1)
        self.assertEqual({result.reason for result in results}, {"stale_lock_recovered", "lock_held"})
        for result in results:
            result.release()

    def test_old_release_detaches_after_interleaved_replacement_and_preserves_successor_flock(self):
        target = self.remote
        old = monitor.monitor_lock(target)
        self.assertTrue(old.acquired)
        successor_fd = None
        try:
            guard = monitor.guard_path(target)
            successor = self.runtime / "successor"
            successor_payload = {
                "schema": 2,
                "state": "released",
                "pid": os.getpid(),
                "created_at": self._timestamp(-60),
                "released_at": self._timestamp(-1),
                "owner_token": "b" * 32,
            }
            successor_bytes = json.dumps(
                successor_payload, sort_keys=True, separators=(",", ":")
            ).encode("ascii")
            successor.write_bytes(successor_bytes)
            os.chmod(successor, 0o600)
            successor_stat = successor.stat()
            successor_fd = os.open(successor, os.O_RDWR)
            monitor.fcntl.flock(successor_fd, monitor.fcntl.LOCK_EX | monitor.fcntl.LOCK_NB)
            real_replace = monitor.os.replace
            real_write = monitor.os.write
            replaced = False

            def replace_before_retained_fd_write(fd, data):
                nonlocal replaced
                if not replaced:
                    replaced = True
                    # The release path has already performed its identity and
                    # payload read checks; only the old retained fd is written
                    # after this atomic successor replacement.
                    real_replace(successor, guard)
                return real_write(fd, data)

            with mock.patch.object(monitor.os, "write", side_effect=replace_before_retained_fd_write), \
                 mock.patch.object(monitor.os, "unlink", side_effect=AssertionError("unlink")), \
                 mock.patch.object(monitor.os, "replace", side_effect=AssertionError("replace")):
                old.release()
            self.assertTrue(replaced)
            self.assertEqual(guard.read_bytes(), successor_bytes)
            current_stat = guard.stat()
            self.assertEqual((current_stat.st_dev, current_stat.st_ino), (successor_stat.st_dev, successor_stat.st_ino))
            self.assertEqual(current_stat.st_mode & 0o777, 0o600)
            self.assertEqual(self._read_json(guard)["owner_token"], "b" * 32)
            probe_fd = os.open(guard, os.O_RDWR)
            try:
                with self.assertRaises(OSError) as raised:
                    monitor.fcntl.flock(probe_fd, monitor.fcntl.LOCK_EX | monitor.fcntl.LOCK_NB)
                self.assertIn(raised.exception.errno, (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK))
            finally:
                os.close(probe_fd)
        finally:
            old.release()
            if successor_fd is not None:
                monitor.fcntl.flock(successor_fd, monitor.fcntl.LOCK_UN)
                os.close(successor_fd)

    def test_wrong_mode_old_dead_active_guard_is_held_and_untouched(self):
        target = {"kind": "remote", "name": "wrong-mode-old-dead"}
        path = self._guard_file(target, created_delta=-3600, pid=99999999, mode=0o644)
        original = path.read_bytes()
        with mock.patch.object(monitor.os, "kill", side_effect=AssertionError("must not inspect PID")):
            lease = monitor.monitor_lock(target, stale_after_seconds=1800)
        self.assertFalse(lease.acquired)
        self.assertEqual(lease.reason, "lock_held")
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_release_write_failure_leaves_active_evidence_and_unlocks(self):
        target = self.remote
        lock = monitor.monitor_lock(target)
        path = monitor.guard_path(target)
        with mock.patch.object(monitor.os, "fsync", side_effect=OSError(errno.EIO, "disk")):
            lock.release()
        payload = self._read_json(path)
        self.assertEqual(payload["state"], "active")
        contender = monitor.monitor_lock(target)
        self.assertFalse(contender.acquired)
        self.assertEqual(contender.reason, "lock_held")

    def test_acquired_write_failure_leaves_replaced_successor(self):
        target = self.remote
        guard = monitor.guard_path(target)
        successor = self.runtime / "successor"
        successor.write_bytes(b"successor-state")
        os.chmod(successor, 0o600)
        original_fsync = monitor.os.fsync
        replaced = False

        def replace_then_fail(descriptor):
            nonlocal replaced
            if not replaced:
                replaced = True
                os.replace(successor, guard)
                raise OSError(errno.EIO, "postwrite failure")
            return original_fsync(descriptor)

        with mock.patch.object(monitor.os, "fsync", side_effect=replace_then_fail):
            with self.assertRaises(monitor.StorageMonitorLockError):
                monitor.monitor_lock(target)
        self.assertEqual(guard.read_bytes(), b"successor-state")

    def test_legacy_empty_missing_old_dead_young_live_malformed_never_deletes(self):
        # Empty/missing legacy bootstraps the v2 guard.
        target = {"kind": "remote", "name": "legacy-missing"}
        lock = monitor.monitor_lock(target)
        self.assertTrue(lock.acquired)
        lock.release()
        self.assertFalse(monitor.lock_path(target).exists())

        cases = (
            ("old-dead", -3600, ProcessLookupError(), "stale_lock_recovered"),
            ("young-dead", -10, ProcessLookupError(), "lock_held"),
            ("live", -3600, None, "lock_held"),
        )
        for label, age, kill_error, expected in cases:
            with self.subTest(label=label):
                target = {"kind": "remote", "name": f"legacy-{label}"}
                path = self._legacy_file(target, created_delta=age, pid=(os.getpid() if label == "live" else 99999999))
                original = path.read_bytes()
                effect = None if kill_error is None else kill_error
                with mock.patch.object(monitor.os, "kill", side_effect=effect):
                    lease = monitor.monitor_lock(target)
                self.assertEqual(lease.reason, expected)
                self.assertTrue(path.exists())
                self.assertEqual(path.read_bytes(), original)
                if lease.acquired:
                    self.assertEqual(self._read_json(monitor.guard_path(target))["schema"], 2)
                    lease.release()

        target = {"kind": "remote", "name": "legacy-malformed"}
        path = self._legacy_file(target, raw=b"not-json")
        original = path.read_bytes()
        lease = monitor.monitor_lock(target)
        self.assertFalse(lease.acquired)
        self.assertEqual(lease.reason, "lock_held")
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), original)

    def test_denied_or_unreadable_legacy_is_held_and_preserved(self):
        target = {"kind": "remote", "name": "legacy-denied"}
        path = self._legacy_file(target)
        real_open = monitor.os.open

        def deny_lock_open(value, flags, *args, **kwargs):
            if Path(value).name == path.name:
                raise PermissionError(errno.EACCES, "denied")
            return real_open(value, flags, *args, **kwargs)

        with mock.patch.object(monitor.os, "open", side_effect=deny_lock_open):
            lock = monitor.monitor_lock(target)
        self.assertFalse(lock.acquired)
        self.assertEqual(lock.reason, "lock_held")
        self.assertTrue(path.exists())

        target = {"kind": "remote", "name": "legacy-unreadable"}
        path = self._legacy_file(target)
        original = path.read_bytes()

        def deny_lock_read(descriptor, size):
            raise PermissionError(errno.EACCES, "unreadable")

        with mock.patch.object(monitor.os, "read", side_effect=deny_lock_read):
            lock = monitor.monitor_lock(target)
        self.assertFalse(lock.acquired)
        self.assertEqual(lock.reason, "lock_held")
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes(), original)

    def test_unsafe_guard_artifact_is_rejected_without_following_or_removing(self):
        target = self.remote
        guard = monitor.guard_path(target)
        guard.parent.mkdir(parents=True, exist_ok=True)
        source = self.runtime / "guard-target"
        source.write_text("guard", encoding="utf-8")
        guard.symlink_to(source)
        lock = monitor.monitor_lock(target)
        self.assertFalse(lock.acquired)
        self.assertEqual(lock.reason, "lock_held")
        self.assertTrue(guard.is_symlink())
        self.assertEqual(source.read_text(encoding="utf-8"), "guard")
        guard.unlink()

    def test_lock_never_resolves_config_or_remote_registry(self):
        blockers = (
            mock.patch.object(monitor, "load_config", side_effect=AssertionError("config")),
            mock.patch.object(monitor, "get_remote", side_effect=AssertionError("remote")),
            mock.patch.object(monitor, "list_remotes", side_effect=AssertionError("registry")),
        )
        with blockers[0], blockers[1], blockers[2]:
            lock = monitor.monitor_lock(self.remote)
        try:
            self.assertTrue(lock.acquired)
        finally:
            lock.release()

    def test_no_subprocess_or_remote_runner_is_involved(self):
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("runner")) as run:
            lock = monitor.monitor_lock(self.remote)
            try:
                self.assertTrue(lock.acquired)
            finally:
                lock.release()
        run.assert_not_called()


class TestStorageDoctorChecks(TestCase):
    """Offline, record-only checks for Spec 043 T007."""

    def setUp(self):
        self.base_config = {
            "resources": {"monitor": {"record_max_age_seconds": 100}},
        }
        self.records = {}
        self.remotes = {}

    @staticmethod
    def _key(target):
        return (target.get("kind"), target.get("name"))

    def _record(self, target, *, level="normal", free=80, total=100, **extra):
        record = {
            "schema": 1,
            "target": {"kind": target[0], "name": target[1]},
            "at": "2026-08-20T00:00:00Z",
            "level": level,
            "free_bytes": free,
            "total_bytes": total,
        }
        record.update(extra)
        return record

    def _read(self, target):
        return self.records.get(self._key(target))

    def _run(self, *, age=10, remotes=None, records=None, config=None):
        self.remotes = self.remotes if remotes is None else remotes
        self.records = self.records if records is None else records
        with mock.patch.object(monitor, "list_remotes", return_value=self.remotes), \
             mock.patch.object(monitor, "load_config",
                               return_value=config or self.base_config), \
             mock.patch.object(monitor, "read_record", side_effect=self._read), \
             mock.patch.object(monitor, "record_age_seconds", return_value=age):
            return monitor.storage_doctor_checks()

    def test_local_normal_fresh_is_healthy(self):
        rows = self._run(
            records={("local", "local"): self._record(("local", "local"))},
        )
        self.assertEqual(rows, [{"label": "local", "ok": True, "hint": ""}])

    def test_warning_critical_and_unknown_are_failed_with_safe_public_hints(self):
        for level, free, expected in (
            ("warning", 10, "WARNING"),
            ("critical", 2, "CRITICAL"),
            ("unknown", None, "UNKNOWN"),
        ):
            with self.subTest(level=level):
                records = {
                    ("local", "local"): self._record(
                        ("local", "local"), level=level, free=free,
                        total=None if level == "unknown" else 100,
                        guidance="record-guidance-without-secrets",
                    ),
                }
                row = self._run(records=records)[0]
                self.assertFalse(row["ok"])
                self.assertIn(expected, row["hint"])
                self.assertIn("sb resources monitor --json", row["hint"])
                if level != "unknown":
                    self.assertIn("free of", row["hint"])
                    self.assertIn("review the safe-tier plan", row["hint"])
                self.assertNotIn("record-guidance-without-secrets", row["hint"])

    def test_missing_and_stale_records_fail_with_exact_refresh_commands(self):
        missing = self._run(records={})[0]
        self.assertFalse(missing["ok"])
        self.assertIn("no valid monitor run recorded", missing["hint"])
        self.assertIn("sb resources monitor --json", missing["hint"])

        remote_records = {
            ("remote", "alpha"): self._record(("remote", "alpha")),
        }
        rows = self._run(
            age=101,
            remotes={"alpha": {}},
            records=remote_records,
        )
        self.assertFalse(rows[0]["ok"])
        self.assertFalse(rows[1]["ok"])
        self.assertIn("age", rows[1]["hint"])
        self.assertIn(
            "sb resources monitor --remote alpha --json", rows[1]["hint"],
        )

    def test_remote_rows_are_sorted_and_sparse_age_override_is_target_specific(self):
        remotes = {
            "zeta": {"storage_monitor": {"record_max_age_seconds": 100}},
            "alpha": {"storage_monitor": {"record_max_age_seconds": 5}},
        }
        records = {
            ("local", "local"): self._record(("local", "local")),
            ("remote", "alpha"): self._record(("remote", "alpha")),
            ("remote", "zeta"): self._record(("remote", "zeta")),
        }
        rows = self._run(age=10, remotes=remotes, records=records)
        self.assertEqual([row["label"] for row in rows], ["local", "alpha", "zeta"])
        self.assertTrue(rows[0]["ok"])
        self.assertFalse(rows[1]["ok"])
        self.assertTrue(rows[2]["ok"])
        self.assertIn("sb resources monitor --remote alpha --json", rows[1]["hint"])

    def test_doctor_never_resolves_remote_or_runs_processes(self):
        records = {
            ("local", "local"): self._record(("local", "local")),
        }
        with mock.patch.object(monitor, "resolve_policy",
                               side_effect=AssertionError("must stay offline")), \
             mock.patch.object(monitor, "get_remote",
                               side_effect=AssertionError("must stay offline")), \
             mock.patch.object(subprocess, "run",
                               side_effect=AssertionError("must stay offline")):
            rows = self._run(records=records)
        self.assertTrue(rows[0]["ok"])

    def test_malformed_configured_name_is_not_healthy_and_hints_never_leak(self):
        secret = "/private/remote-token"
        rows = self._run(
            remotes={
                "../remote": {"ssh": secret, "storage_monitor": {}},
                "valid": {},
            },
            records={
                ("local", "local"): self._record(("local", "local")),
                ("remote", "valid"): self._record(("remote", "valid")),
            },
        )
        malformed = [row for row in rows if "invalid name" in row["label"]]
        self.assertEqual(len(malformed), 1)
        self.assertFalse(malformed[0]["ok"])
        rendered = " ".join(str(row) for row in rows)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("../remote", rendered)
        self.assertNotIn(str(monitor.record_path("local")), rendered)

    def test_remote_registry_failure_is_one_bounded_failed_row(self):
        local = self._record(("local", "local"))
        with mock.patch.object(
            monitor, "list_remotes", side_effect=RuntimeError("secret/path")
        ), mock.patch.object(monitor, "load_config", return_value=self.base_config), \
             mock.patch.object(monitor, "read_record", return_value=local), \
             mock.patch.object(monitor, "record_age_seconds", return_value=10):
            rows = monitor.storage_doctor_checks()
        self.assertEqual([row["label"] for row in rows], ["local", "remote configuration"])
        self.assertFalse(rows[1]["ok"])
        self.assertIn("configured remotes could not be read", rows[1]["hint"])
        self.assertNotIn("secret/path", str(rows))

    def test_non_mapping_remote_registry_is_one_bounded_failed_row(self):
        local = self._record(("local", "local"))
        with mock.patch.object(monitor, "list_remotes", return_value=["not-a-map"]), \
             mock.patch.object(monitor, "load_config", return_value=self.base_config), \
             mock.patch.object(monitor, "read_record", return_value=local), \
             mock.patch.object(monitor, "record_age_seconds", return_value=10):
            rows = monitor.storage_doctor_checks()
        self.assertEqual([row["label"] for row in rows], ["local", "remote configuration"])
        self.assertFalse(rows[1]["ok"])


if __name__ == "__main__":
    import unittest

    unittest.main()
