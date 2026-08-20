"""Focused policy/configuration tests for Spec 043 Phase 1."""

from __future__ import annotations

import builtins
from pathlib import Path
from unittest import TestCase, mock

from sandbox.config.manifest import MACHINE_CONFIG_PROVIDERS, apply_machine_config
from sandbox.config.storage_monitor import (
    DEFAULTS,
    StorageMonitorConfigError,
    normalize_storage_monitor,
)
from sandbox.resources import reclaim


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


if __name__ == "__main__":
    import unittest

    unittest.main()
