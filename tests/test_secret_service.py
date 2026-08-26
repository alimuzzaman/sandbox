from __future__ import annotations

import json
import os
import stat
import subprocess
import time
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest import mock

from sandbox.secrets.audit import SecretAudit
from sandbox.secrets.models import SecretBrokerError, UseProfile
from sandbox.secrets.runner import minimal_environment, run_with_secret, run_with_secrets
from sandbox.secrets.service import SecretService
from sandbox.secrets.sources import SourceRegistry


class TestSecretSources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / ".env.fixture"
        self.source.write_text("API_TOKEN=test-only\n")
        self.source.chmod(0o600)
        self.registry = SourceRegistry(
            self.root,
            {"fixture-env": {"path": ".env.fixture", "mcp_modes": ("keys",)}},
            personal_path=self.root / "personal",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_descriptor_safe_read(self):
        opened = self.registry.read("fixture-env")
        self.assertEqual(opened.policy.alias, "fixture-env")
        self.assertEqual(opened.content, b"API_TOKEN=test-only\n")

    def test_probe_reports_registered_file_metadata_without_reading_contents(self):
        with mock.patch("sandbox.secrets.sources.os.read", side_effect=AssertionError("read")):
            result = self.registry.probe("fixture-env")

        self.assertEqual(result, {
            "source": "fixture-env",
            "scope": "project",
            "format": "dotenv",
            "exists": True,
            "file_type": "regular_file",
            "content_state": "nonempty",
            "size_bucket": "1_to_1_kib",
            "broker_readable": True,
            "safety": "safe",
        })
        exact = self.registry.probe("fixture-env", exact_size=True)
        self.assertEqual(exact["size_bytes"], self.source.stat().st_size)
        self.assertNotIn(str(self.root), repr(result))
        self.assertNotIn("test-only", repr(result))

    def test_probe_distinguishes_missing_empty_and_unsafe_types_without_following_links(self):
        self.source.unlink()
        missing = self.registry.probe("fixture-env")
        self.assertEqual(
            (missing["exists"], missing["file_type"], missing["content_state"], missing["safety"]),
            (False, "missing", "not_applicable", "missing"),
        )

        self.source.write_bytes(b"")
        self.source.chmod(0o600)
        empty = self.registry.probe("fixture-env")
        self.assertEqual((empty["content_state"], empty["size_bucket"]), ("empty", "empty"))

        target = self.root / ".env.target"
        self.source.rename(target)
        self.source.symlink_to(target)
        linked = self.registry.probe("fixture-env")
        self.assertEqual(
            (linked["file_type"], linked["content_state"], linked["broker_readable"]),
            ("symlink", "unknown", False),
        )

        self.source.unlink()
        self.source.mkdir(mode=0o700)
        directory = self.registry.probe("fixture-env")
        self.assertEqual(directory["file_type"], "directory")
        self.assertEqual(directory["safety"], "unsafe")

    def test_probe_returns_stable_inaccessible_state_without_os_error_detail(self):
        canary = "SB_SYNTHETIC_PATH_CANARY_31f2"
        with mock.patch("sandbox.secrets.sources.os.lstat", side_effect=OSError(canary)):
            result = self.registry.probe("fixture-env")
        self.assertEqual(result["safety"], "inaccessible")
        self.assertIsNone(result["exists"])
        self.assertNotIn(canary, repr(result))

    def test_probe_detects_source_swap_and_oversize_without_reading(self):
        changed = list(os.lstat(self.source))
        changed[1] += 1
        with mock.patch("sandbox.secrets.sources.os.fstat", return_value=os.stat_result(changed)), \
             mock.patch("sandbox.secrets.sources.os.read", side_effect=AssertionError("read")):
            swapped = self.registry.probe("fixture-env", exact_size=True)
        self.assertEqual(swapped["safety"], "changed")
        self.assertEqual(swapped["content_state"], "unknown")
        self.assertNotIn("size_bucket", swapped)
        self.assertNotIn("size_bytes", swapped)

        self.source.write_bytes(b"x" * (1_048_576 + 1))
        with mock.patch("sandbox.secrets.sources.os.read", side_effect=AssertionError("read")):
            oversized = self.registry.probe("fixture-env")
        self.assertEqual(oversized["safety"], "too_large")
        self.assertFalse(oversized["broker_readable"])
        self.assertEqual(oversized["size_bucket"], "over_1_mib")

    def test_broad_permissions_are_refused(self):
        self.source.chmod(0o640)
        with self.assertRaisesRegex(SecretBrokerError, "permissions"):
            self.registry.read("fixture-env")

    def test_symlink_is_refused(self):
        target = self.root / ".env.target"
        self.source.rename(target)
        self.source.symlink_to(target)
        with self.assertRaises(SecretBrokerError):
            self.registry.read("fixture-env")

    def test_directory_hardlink_and_oversized_sources_are_refused(self):
        original = self.source.read_bytes()
        self.source.unlink()
        self.source.mkdir(mode=0o700)
        with self.assertRaises(SecretBrokerError):
            self.registry.read("fixture-env")
        self.source.rmdir()
        self.source.write_bytes(original)
        self.source.chmod(0o600)
        hardlink = self.root / ".env.hardlink"
        os.link(self.source, hardlink)
        with self.assertRaises(SecretBrokerError):
            self.registry.read("fixture-env")
        hardlink.unlink()
        self.source.write_bytes(b"x" * (1_048_576 + 1))
        with self.assertRaisesRegex(SecretBrokerError, "1 MiB"):
            self.registry.read("fixture-env")

    def test_unknown_alias_does_not_expose_path(self):
        with self.assertRaises(SecretBrokerError) as raised:
            self.registry.read("missing")
        self.assertNotIn(str(self.root), str(raised.exception))


class TestSecretAudit(unittest.TestCase):
    def test_intent_and_outcome_are_owner_only_and_value_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            audit = SecretAudit(path, actor="fixture")
            correlation = audit.intent(
                "metadata", "fixture-env", ["API_TOKEN"], surface="cli"
            )
            audit.outcome(
                correlation, "metadata", "fixture-env", ["API_TOKEN"],
                surface="cli", decision="succeeded", count=1,
            )
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            events = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([event["phase"] for event in events], ["intent", "outcome"])
            rendered = json.dumps(events)
            self.assertNotIn("test-only", rendered)
            self.assertNotIn('"value"', rendered)

    def test_unsafe_audit_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            path.write_text("")
            path.chmod(0o644)
            with self.assertRaisesRegex(SecretBrokerError, "unsafe"):
                SecretAudit(path).intent("keys", "fixture", [], surface="cli")


class TestSecretRunner(unittest.TestCase):
    def test_minimal_environment_contains_one_selected_secret(self):
        secret = "TestOnly_Abcdefghijklmnop123456"
        environment = minimal_environment("API_TOKEN", secret)
        self.assertEqual(environment["API_TOKEN"], secret)
        self.assertNotIn("NODE_OPTIONS", environment)

    def test_child_output_is_redacted(self):
        secret = "TestOnly_Abcdefghijklmnop123456"
        result = run_with_secret(
            [sys.executable, "-c", "import os; print(os.environ['API_TOKEN'])"],
            destination="API_TOKEN",
            value=secret,
            timeout_seconds=5,
            max_output_bytes=1024,
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("[REDACTED]", result.output)
        self.assertNotIn(secret, result.output)

    def test_child_receives_multiple_selected_secrets_and_redacts_each(self):
        first = "TestOnly_First123456789AbCd"
        second = "TestOnly_Second12345678AbCd"
        result = run_with_secrets(
            [sys.executable, "-c",
             "import os; print(os.environ['ACCESS_KEY']); print(os.environ['ACCESS_SECRET'])"],
            secrets={"ACCESS_KEY": first, "ACCESS_SECRET": second},
            timeout_seconds=5,
            max_output_bytes=1024,
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output.count("[REDACTED]"), 2)
        self.assertNotIn(first, result.output)
        self.assertNotIn(second, result.output)

    def test_output_is_bounded(self):
        result = run_with_secret(
            [sys.executable, "-c", "print('x' * 10000)"],
            destination="API_TOKEN",
            value="TestOnly_Abcdefghijklmnop123456",
            timeout_seconds=5,
            max_output_bytes=128,
        )
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.output.encode()), 128)

    def test_cross_chunk_secret_output_is_redacted(self):
        secret = "TestOnly_CrossChunk123456789AbCd"
        program = (
            "import os,sys,time; s=os.environ['API_TOKEN']; "
            "sys.stdout.write(s[:11]); sys.stdout.flush(); time.sleep(.05); "
            "sys.stdout.write(s[11:]); sys.stdout.flush()"
        )
        result = run_with_secret(
            [sys.executable, "-c", program], destination="API_TOKEN", value=secret,
            timeout_seconds=5, max_output_bytes=1024,
        )
        self.assertNotIn(secret, result.output)
        self.assertIn("[REDACTED]", result.output)

    def test_timeout_terminates_process_group(self):
        started = time.monotonic()
        result = run_with_secret(
            [sys.executable, "-c", "import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); time.sleep(30)"],
            destination="API_TOKEN", value="TestOnly_Timeout123456789AbCd",
            timeout_seconds=1, max_output_bytes=1024,
        )
        self.assertEqual(result.termination, "timed_out")
        self.assertIsNone(result.exit_code)
        self.assertLess(time.monotonic() - started, 4)
        self.assertNotIn("elapsed_seconds", result.as_dict())


class TestSecretBrokerService(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / ".env.fixture"
        self.source.write_bytes(
            b"# preserved\nPUBLIC_NAME=visible-label\n"
            b"API_TOKEN=sk_test_" + b"Fixture1234567890AbCd\n"
        )
        self.source.chmod(0o600)
        registry = SourceRegistry(
            self.root, {"fixture": {"path": ".env.fixture", "mcpModes": [
                "source_info", "keys", "metadata", "validate", "masked", "use",
            ]}}, personal_path=self.root / ".personal",
        )
        self.service = SecretService(
            registry, SecretAudit(self.root / "runtime/audit.jsonl"),
            revision_key_path=self.root / "runtime/revision.key",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_default_keys_metadata_validation_and_fixed_mask(self):
        keys = self.service.inspect("fixture")
        self.assertEqual(keys["keys"], ["API_TOKEN", "PUBLIC_NAME"])
        meta = self.service.inspect("fixture", keys=["API_TOKEN"], mode="metadata")
        self.assertEqual(meta["entries"][0]["state"], "present")
        self.assertNotIn("exact_length", meta["entries"][0])
        checked = self.service.validate("fixture", "API_TOKEN", "stripe-secret-v1")
        self.assertFalse(checked["validation"]["live_checked"])
        masked = self.service.inspect("fixture", keys=["API_TOKEN"], mode="masked")
        self.assertEqual(masked["entries"][0]["public_prefix"], "sk_test_")
        self.assertIn("<redacted>", masked["entries"][0]["masked"])
        for payload in (keys, meta, checked, masked):
            self.assertNotIn("Fixture1234567890", repr(payload))

    def test_mcp_modes_fail_closed(self):
        restricted = SourceRegistry(
            self.root, {"fixture": {"path": ".env.fixture", "mcpModes": ["keys"]}},
            personal_path=self.root / ".personal",
        )
        service = SecretService(restricted, SecretAudit(self.root / "runtime/audit2.jsonl"),
                                revision_key_path=self.root / "runtime/revision.key")
        service.inspect("fixture", surface="mcp")
        with self.assertRaisesRegex(SecretBrokerError, "not authorized"):
            service.inspect("fixture", keys=["API_TOKEN"], mode="masked", surface="mcp")

    def test_source_info_does_not_parse_content_and_exact_size_is_cli_only(self):
        with mock.patch.object(self.service.registry, "read", side_effect=AssertionError("read")):
            result = self.service.source_info("fixture", surface="mcp")
        self.assertEqual(result["operation"], "source_info")
        self.assertTrue(result["exists"])
        self.assertEqual(result["content_state"], "nonempty")
        self.assertIn("size_bucket", result)
        self.assertNotIn("size_bytes", result)
        self.assertNotIn("Fixture1234567890", repr(result))

        exact = self.service.source_info("fixture", exact_size=True)
        self.assertEqual(exact["size_bytes"], self.source.stat().st_size)
        with self.assertRaisesRegex(SecretBrokerError, "local CLI"):
            self.service.source_info("fixture", exact_size=True, surface="mcp")

        self.service.registry._sources["missing"] = {
            "path": ".env.missing", "mcpModes": ["source_info"],
        }
        missing = self.service.source_info("missing", surface="mcp")
        self.assertFalse(missing["exists"])
        self.assertEqual(missing["safety"], "missing")

    def test_structured_source_lists_paths_and_denies_derived_disclosure_and_update(self):
        structured = self.root / "credentials.json"
        structured.write_text(json.dumps({
            "type": "service_account",
            "private_key": "SB_SYNTHETIC_PRIVATE_KEY_NOT_REAL",
            "nested": {"token": "SB_SYNTHETIC_TOKEN_NOT_REAL"},
        }))
        structured.chmod(0o600)
        self.service.registry._sources["structured"] = {
            "path": "credentials.json", "format": "json",
            "mcpModes": ["keys", "metadata"],
        }

        listed = self.service.inspect("structured")
        self.assertEqual(
            listed["keys"], ["/nested/token", "/private_key", "/type"],
        )
        metadata_result = self.service.inspect(
            "structured", keys=["/private_key"], mode="metadata",
        )
        self.assertEqual(metadata_result["entries"][0]["kind"], "password")
        self.assertNotIn("SB_SYNTHETIC", repr(metadata_result))
        with self.assertRaisesRegex(SecretBrokerError, "exact length"):
            self.service.inspect(
                "structured", keys=["/private_key"], mode="metadata", exact_length=True,
            )
        with self.assertRaisesRegex(SecretBrokerError, "mask"):
            self.service.inspect("structured", keys=["/nested/token"], mode="masked")
        with self.assertRaisesRegex(SecretBrokerError, "update"):
            self.service.set("structured", "/nested/token", "SB_SYNTHETIC_REPLACEMENT")

        with mock.patch.object(self.service, "_document") as read_reference, \
             self.assertRaisesRegex(SecretBrokerError, "update"):
            self.service.copy_reference(
                "structured", "/nested/token", "fixture", "API_TOKEN",
            )
        read_reference.assert_not_called()

    def test_mcp_use_accepts_only_an_authorized_fixed_profile(self):
        profile = UseProfile(
            name="fixture-status", source="fixture", key="API_TOKEN",
            argv=(sys.executable, "-c", "import os; print(os.environ['API_TOKEN'])"),
            destination="API_TOKEN", timeout_seconds=5, max_output_bytes=1024, mcp=True,
        )
        self.service.use_profiles[profile.name] = profile
        result = self.service.use_profile(profile.name, surface="mcp")
        self.assertIn("[REDACTED]", result["result"]["output"])
        self.assertNotIn("elapsed_seconds", result["result"])
        with self.assertRaisesRegex(SecretBrokerError, "not registered"):
            self.service.use_profile("missing", surface="mcp")

    def test_run_many_injects_paired_credentials_in_one_child(self):
        self.source.write_bytes(
            self.source.read_bytes()
            + b"ACCESS_KEY=AccessKeyFixture123456789AbCd\n"
            + b"ACCESS_SECRET=AccessSecretFixture123456789AbCd\n"
        )
        result = self.service.run_many(
            "fixture",
            (("ACCESS_KEY", "AWS_ACCESS_KEY_ID"),
             ("ACCESS_SECRET", "AWS_SECRET_ACCESS_KEY")),
            [sys.executable, "-c", "import os; print(os.environ['AWS_ACCESS_KEY_ID']); print(os.environ['AWS_SECRET_ACCESS_KEY'])"],
            timeout_seconds=5,
            max_output_bytes=1024,
        )
        self.assertEqual(result["keys"], ["ACCESS_KEY", "ACCESS_SECRET"])
        self.assertEqual(result["result"]["output"].count("[REDACTED]"), 2)
        self.assertNotIn("AccessKeyFixture", repr(result))
        self.assertNotIn("AccessSecretFixture", repr(result))

    def test_update_preserves_unrelated_bytes_and_checks_revision_and_intent(self):
        before = self.source.read_bytes()
        metadata_result = self.service.inspect("fixture", keys=["API_TOKEN"], mode="metadata")
        revision = metadata_result["revision"]
        result = self.service.set(
            "fixture", "API_TOKEN", "sk_test_" + "Replaced123456789AbCd",
            intent="replace", expected_revision=revision, input_channel="stdin",
        )
        after = self.source.read_bytes()
        self.assertEqual(result["action"], "updated")
        self.assertTrue(after.startswith(before.split(b"API_TOKEN=", 1)[0]))
        self.assertNotIn("Replaced123", repr(result))
        with self.assertRaisesRegex(SecretBrokerError, "revision"):
            self.service.set("fixture", "API_TOKEN", "another-Fixture-123456789",
                             expected_revision=revision)
        with self.assertRaisesRegex(SecretBrokerError, "already exists"):
            self.service.set("fixture", "API_TOKEN", "another-Fixture-123456789", intent="create")

    def test_update_can_create_one_key_and_preserves_owner_only_modes(self):
        result = self.service.set(
            "fixture", "NEW_TOKEN", "Synthetic-NewValue123456789AbCd", intent="create",
        )
        self.assertEqual(result["action"], "created")
        self.assertIn(b"NEW_TOKEN=", self.source.read_bytes())
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / "runtime/revision.key").stat().st_mode), 0o600)
        self.assertEqual(
            stat.S_IMODE((self.root / ".env.fixture.sb-secrets.lock").stat().st_mode), 0o600,
        )

    def test_unset_removes_only_the_requested_assignment_and_returns_revision(self):
        before = self.source.read_bytes()
        metadata_result = self.service.inspect("fixture", keys=["API_TOKEN"], mode="metadata")
        result = self.service.unset(
            "fixture", "API_TOKEN", expected_revision=metadata_result["revision"],
        )
        after = self.source.read_bytes()
        self.assertEqual(result["action"], "removed")
        self.assertNotIn(b"API_TOKEN=", after)
        self.assertIn(b"# preserved\nPUBLIC_NAME=visible-label\n", after)
        self.assertNotEqual(after, before)
        self.assertNotIn("Fixture1234567890", repr(result))
        self.assertTrue(result["revision"].startswith("r1_"))

    def test_unset_is_revision_checked_local_dotenv_only_and_strict_on_missing_keys(self):
        metadata_result = self.service.inspect("fixture", keys=["API_TOKEN"], mode="metadata")
        self.source.write_bytes(self.source.read_bytes() + b"OTHER=fixture\n")
        with self.assertRaisesRegex(SecretBrokerError, "revision"):
            self.service.unset(
                "fixture", "API_TOKEN", expected_revision=metadata_result["revision"],
            )
        with self.assertRaisesRegex(SecretBrokerError, "does not exist"):
            self.service.unset("fixture", "MISSING")
        with self.assertRaisesRegex(SecretBrokerError, "local CLI"):
            self.service.unset("fixture", "API_TOKEN", surface="mcp")

    def test_reference_generation_profile_validation_and_atomic_failure(self):
        other = self.root / ".env.other"
        other.write_text("COPY_FROM=SyntheticReference123456789AbCd\n")
        other.chmod(0o600)
        self.service.registry._sources["other"] = {"path": ".env.other"}
        copied = self.service.copy_reference(
            "fixture", "COPIED_TOKEN", "other", "COPY_FROM", intent="create",
        )
        self.assertEqual(copied["action"], "created")
        generated = self.service.generate(
            "fixture", "GENERATED_TOKEN", "random-base64url-32-v1", intent="create",
            validation_profile="opaque-token-v1",
        )
        self.assertEqual(generated["validation"]["syntax"], "pass")
        before = self.source.read_bytes()
        with mock.patch("sandbox.secrets.writer.os.replace", side_effect=OSError("fixture")), \
             self.assertRaisesRegex(SecretBrokerError, "update"):
            self.service.set("fixture", "API_TOKEN", "SyntheticAtomic123456789AbCd")
        self.assertEqual(self.source.read_bytes(), before)
        leftovers = [
            path for path in self.root.iterdir()
            if path.name.startswith(".env.fixture.") and not path.name.endswith(".lock")
        ]
        self.assertEqual(leftovers, [])

    def test_near_limit_inventory_completes_within_budget(self):
        content = b"".join(f"KEY_{index}=fixture-{index}\n".encode() for index in range(4096))
        self.source.write_bytes(content)
        started = time.monotonic()
        result = self.service.inspect("fixture")
        self.assertEqual(result["count"], 4096)
        self.assertLess(time.monotonic() - started, 2)

    def test_reveal_uses_callback_and_returns_no_value_model(self):
        seen = []
        result = self.service.reveal("fixture", "PUBLIC_NAME", seen.append, confirmed=True)
        self.assertIsNone(result)
        self.assertEqual(len(seen), 1)
        self.assertFalse(any("value" in name.lower() for name in vars(self.service)))

    def test_reveal_confirmation_failure_is_audited_before_read(self):
        seen = []
        with self.assertRaisesRegex(SecretBrokerError, "confirmation"):
            self.service.reveal("fixture", "PUBLIC_NAME", seen.append, confirmed=False)
        self.assertEqual(seen, [])
        events = [json.loads(line) for line in (self.root / "runtime/audit.jsonl").read_text().splitlines()]
        self.assertEqual(events[-1]["reason_code"], "confirmation_failed")

    def test_unknown_failure_is_bounded_without_exception_or_traceback_leakage(self):
        canary = "SB_SYNTHETIC_SECRET_CANARY_7f34"
        retained_failures = (
            RuntimeError(canary),
            json.JSONDecodeError("invalid", canary, 0),
            subprocess.CalledProcessError(
                7, ["synthetic-backend"], output=canary, stderr=canary,
            ),
        )
        for retained in retained_failures:
            with self.subTest(kind=type(retained).__name__), mock.patch.object(
                self.service.registry, "read", side_effect=retained
            ), self.assertRaises(SecretBrokerError) as raised:
                self.service.inspect("fixture")

            error = raised.exception
            self.assertEqual(error.code, "operation_failed")
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            self.assertNotIn(canary, str(error))
            self.assertNotIn(canary, repr(error))
            self.assertNotIn(canary, "".join(traceback.format_exception(error)))
        events = [
            json.loads(line)
            for line in (self.root / "runtime/audit.jsonl").read_text().splitlines()
        ]
        self.assertEqual(events[-1]["reason_code"], "operation_failed")

    def test_malformed_structured_source_does_not_leak_parser_input_or_chain(self):
        canary = "SB_SYNTHETIC_SECRET_CANARY_91ac"
        structured = self.root / "credentials.json"
        structured.write_text('{"private_key":"' + canary + '",')
        structured.chmod(0o600)
        self.service.registry._sources["structured"] = {
            "path": "credentials.json", "format": "json",
            "mcpModes": ["keys", "metadata"],
        }

        with self.assertRaises(SecretBrokerError) as raised:
            self.service.inspect("structured")

        error = raised.exception
        self.assertEqual(error.code, "syntax_unsupported")
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = "".join(traceback.format_exception(error))
        self.assertNotIn(canary, rendered)
        self.assertNotIn("credentials.json", rendered)


if __name__ == "__main__":
    unittest.main()
