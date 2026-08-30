from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parent.parent
HELPER = ROOT / "tools" / "resolver-helper.sh"


def candidate_text(suffix: str = "test") -> str:
    return (
        f"# sandbox-resolver v1 suffix={suffix}\n"
        "[Resolve]\n"
        "DNS=127.0.0.54:5300\n"
        f"Domains=~{suffix}\n"
    )


class TestResolverHelper(unittest.TestCase):
    def run_helper(self, *args: str):
        return subprocess.run(
            [str(HELPER), *args], capture_output=True, text=True, timeout=5,
            env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")},
        )

    def test_unknown_verb_is_rejected(self):
        result = self.run_helper("shell", "whoami")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("usage", result.stderr.lower())

    def test_resolved_mutation_is_rendered_by_root_and_never_consumes_a_candidate(self):
        text = HELPER.read_text()
        self.assertIn("resolved-apply OWNER_SHA256 SUFFIX ADDRESS PORT", text)
        resolved = text.split("    resolved-apply)", 1)[1].split("    resolved-remove)", 1)[0]
        self.assertNotIn("canonical_candidate", resolved)
        self.assertIn("printf '# sandbox-resolver v1", resolved)
        self.assertIn("sandbox-resolver-helper resolved-apply *", text)
        self.assertNotIn("NOPASSWD: /usr/local/libexec/sandbox-resolver-helper *", text)
        self.assertNotIn("sandbox-resolver-helper authorize *", text)
        self.assertIn("visudo -cf", text)

    def test_resolved_status_is_read_only_and_binds_live_service_identity(self):
        text = HELPER.read_text()
        status = text.split("    resolved-status)", 1)[1].split(
            "    resolved-apply)", 1,
        )[0]
        identity = text.split("resolved_identity_fields()", 1)[1].split(
            "require_resolved_identity()", 1,
        )[0]
        self.assertIn("MainPID", identity)
        self.assertIn("/proc/$service_pid/stat", identity)
        self.assertIn("ControlGroup", identity)
        self.assertIn("sandbox-resolved-service-v1", status)
        self.assertNotIn("install ", status)
        self.assertNotIn("rm -f", status)
        self.assertNotIn("systemctl reload", status)
        self.assertIn("sandbox-resolver-helper resolved-status", text)

    def test_resolved_apply_revalidates_authorized_identity_immediately_before_write(self):
        text = HELPER.read_text()
        apply = text.split("    resolved-apply)", 1)[1].split(
            "    resolved-remove)", 1,
        )[0]
        self.assertIn("require_resolved_identity", apply)
        self.assertLess(apply.index("check_authorization resolved"),
                        apply.index("require_resolved_identity"))
        self.assertLess(apply.index("require_resolved_identity"),
                        apply.index("install -d"))
        for name in ("service_pid", "service_start", "service_uid", "service_control"):
            self.assertIn(name, apply)
        self.assertIn("sandbox-resolver-authorization-v2", text)
        self.assertIn("pid=%s start=%s service_uid=%s control=%s", text)
        self.assertIn("legacy_payload", text)

    def test_resolved_remove_rechecks_identity_before_every_removal_branch(self):
        text = HELPER.read_text()
        remove = text.split("    resolved-remove)", 1)[1].split(
            "    macos-apply)", 1,
        )[0]
        self.assertGreaterEqual(remove.count("require_resolved_identity"), 5)
        self.assertLess(remove.index("require_resolved_identity"),
                        remove.index('rm -f -- "$applied" "$receipt"'))
        self.assertLess(remove.rindex("require_resolved_identity"),
                        remove.index("systemctl reload-or-restart"))

    def test_apply_and_remove_require_exact_root_authorization_receipts(self):
        text = HELPER.read_text()
        resolved_apply = text.split("    resolved-apply)", 1)[1].split(
            "    resolved-remove)", 1,
        )[0]
        resolved_remove = text.split("    resolved-remove)", 1)[1].split(
            "    macos-apply)", 1,
        )[0]
        self.assertIn("check_authorization resolved", resolved_apply)
        self.assertIn("check_authorization resolved", resolved_remove)
        self.assertIn("/var/lib/sandbox/resolver/authorizations", text)
        self.assertIn("owner=%s", text)
        self.assertIn("echo \"retained\"", resolved_remove)
        self.assertIn("check_applied resolved", resolved_remove)
        self.assertIn("refusing to adopt an identical foreign resolver fragment", resolved_apply)
        revoke = text.split("    revoke-authorization)", 1)[1].split(
            "    authorization-status)", 1,
        )[0]
        self.assertIn("applied resolver ownership cannot be revoked", revoke)
        self.assertNotIn("resolved-remove", revoke)
        self.assertNotIn("macos-remove", revoke)

    def test_direct_mutation_without_a_receipt_fails_closed(self):
        result = self.run_helper(
            "resolved-apply", "b" * 64, "test", "127.0.0.54", "5300", "a" * 64,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_candidate_schema_accepts_only_owned_bounded_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            authority = root / "authority"
            authority.mkdir()
            candidate = authority / "resolved-test.conf"
            candidate.write_text(candidate_text())
            candidate.chmod(0o600)
            result = self.run_helper(
                "check-candidate", str(root), str(candidate),
                "test", "127.0.0.54", "5300",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "candidate-ok")

    def test_invalid_suffix_and_non_loopback_address_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            authority = root / "authority"
            authority.mkdir()
            candidate = authority / "candidate.conf"
            candidate.write_text(candidate_text())
            candidate.chmod(0o600)
            suffix = self.run_helper(
                "check-candidate", str(root), str(candidate),
                "../test", "127.0.0.54", "5300",
            )
            address = self.run_helper(
                "check-candidate", str(root), str(candidate),
                "test", "0.0.0.0", "5300",
            )
            injected = self.run_helper(
                "check-candidate", str(root), str(candidate),
                "test", "127.0.0.1\nDomains=~.", "5300",
            )
            public = self.run_helper(
                "check-candidate", str(root), str(candidate),
                "com", "127.0.0.54", "5300",
            )
        self.assertNotEqual(suffix.returncode, 0)
        self.assertNotEqual(address.returncode, 0)
        self.assertNotEqual(injected.returncode, 0)
        self.assertNotEqual(public.returncode, 0)

    def test_symlink_and_outside_root_candidates_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "network"
            authority = root / "authority"
            authority.mkdir(parents=True)
            outside = Path(tmp) / "outside.conf"
            outside.write_text(candidate_text())
            outside.chmod(0o600)
            symlink = authority / "resolved-test.conf"
            symlink.symlink_to(outside)
            linked = self.run_helper(
                "check-candidate", str(root), str(symlink),
                "test", "127.0.0.54", "5300",
            )
            external = self.run_helper(
                "check-candidate", str(root), str(outside),
                "test", "127.0.0.54", "5300",
            )
        self.assertNotEqual(linked.returncode, 0)
        self.assertNotEqual(external.returncode, 0)

    def test_group_or_world_writable_candidate_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            authority = root / "authority"
            authority.mkdir()
            candidate = authority / "resolved-test.conf"
            candidate.write_text(candidate_text())
            candidate.chmod(0o666)
            result = self.run_helper(
                "check-candidate", str(root), str(candidate),
                "test", "127.0.0.54", "5300",
            )
        self.assertNotEqual(result.returncode, 0)

    def test_macos_candidate_has_a_separate_exact_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            authority = root / "authority"
            authority.mkdir()
            candidate = authority / "macos-test.resolver"
            candidate.write_text(
                "# sandbox-resolver v1 suffix=test\n"
                "nameserver 127.0.0.54\n"
                "port 5300\n"
            )
            candidate.chmod(0o600)
            accepted = self.run_helper(
                "check-macos-candidate", str(root), str(candidate),
                "test", "127.0.0.54", "5300",
            )
            wrong_schema = self.run_helper(
                "check-candidate", str(root), str(candidate),
                "test", "127.0.0.54", "5300",
            )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertNotEqual(wrong_schema.returncode, 0)

    def test_mutation_verbs_reject_invalid_values_before_privilege(self):
        for invocation in (
            ("hosts-apply", "../bad", "127.0.0.1"),
            ("hosts-remove", "demo.test", "8.8.8.8"),
            ("macos-remove", "../bad", "not-a-digest"),
        ):
            result = self.run_helper(*invocation)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
