"""Focused policy/configuration tests for Spec 043 Phase 1."""

from __future__ import annotations

import builtins
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import subprocess
import tempfile
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
        self.assertEqual(len(MACHINE_CONFIG_PROVIDERS), 1)
        key, provider, owner, order = MACHINE_CONFIG_PROVIDERS[0]
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


if __name__ == "__main__":
    import unittest

    unittest.main()
