"""Probe command builders and parsers for the future Ubuntu live checks.

These tests never execute a command. They assert that every plan is an argv
array of allowlisted, manifest-derived tokens with a finite timeout and bounded
output, and that parsing refuses to persist anything it should not.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from credential_vault_proof import (  # noqa: E402
    fixtures, manifest as manifest_module, probes,
)


def result(**overrides):
    value = {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False,
             "expected": []}
    value.update(overrides)
    return value


class TestProbeCommandModel(unittest.TestCase):
    def setUp(self):
        self.manifest = manifest_module.validate_manifest(fixtures.manifest())

    def test_the_catalog_covers_every_future_check_family(self):
        catalog = set(probes.catalog())
        for check_id in (
            "os_release_supported", "kernel_release_expected",
            "sandbox_revision_expected", "unit_identity_expected",
            "unit_ownership_expected", "broker_process_identity",
            "controller_process_identity", "executable_ownership_expected",
            "cgroup_identity_expected", "lease_socket_owned",
            "controller_socket_owned", "peer_credentials_observed",
            "scm_credentials_observed", "scm_rights_exactly_one",
            "memfd_type_and_seals", "descriptor_closed_on_success",
            "descriptor_closed_on_failure", "veth_identity_expected",
            "guest_cannot_reach_controller", "guest_cannot_reach_lease_socket",
            "guest_cannot_reach_host", "guest_cannot_reach_loopback",
            "guest_cannot_reach_metadata", "guest_cannot_reach_other_interface",
            "bindtodevice_enforced", "dns_pinning_enforced",
            "tls_verification_enforced", "redirect_refused",
            "response_size_bounded", "request_timeout_bounded",
            "concurrency_ceiling_enforced", "epoch_rotates_on_restart",
            "quiesce_before_drain", "drain_precedes_stop",
            "process_absent_after_cleanup", "route_absent_after_cleanup",
            "nftables_absent_after_cleanup",
            "nftables_default_drop", "apparmor_profile_enforced",
            "unit_absent_after_cleanup", "socket_absent_after_cleanup",
            "interface_absent_after_cleanup", "cgroup_absent_after_cleanup",
            "temporary_absent_after_cleanup", "descriptor_absent_after_cleanup",
        ):
            with self.subTest(check_id=check_id):
                self.assertIn(check_id, catalog)

    def test_every_host_plan_is_an_allowlisted_bounded_argv_array(self):
        for check_id in probes.catalog():
            with self.subTest(check_id=check_id):
                entry = probes.build(check_id, self.manifest)
                self.assertIsInstance(entry["argv"], tuple)
                self.assertEqual(entry["timeout_seconds"], 30)
                self.assertLessEqual(entry["max_output_bytes"], 65536)
                self.assertTrue(entry["redact"])
                if entry["kind"] == "host_command":
                    self.assertIn(entry["argv"][0], probes.ALLOWED_EXECUTABLES)
                    self.assertLessEqual(len(entry["argv"]), probes.MAX_ARGV)
                    for token in entry["argv"]:
                        self.assertNotIn(" ", token)
                        self.assertNotIn(";", token)
                        self.assertNotIn("|", token)
                        self.assertNotIn("&", token)
                else:
                    self.assertEqual(entry["argv"], ())
                    self.assertIn(entry["kind"], {"broker_status", "guest_probe"})

    def test_plans_use_only_manifest_derived_identifiers(self):
        entry = probes.build("veth_identity_expected", self.manifest)
        self.assertIn(self.manifest["transport"]["guest_interface"], entry["argv"])
        entry = probes.build("lease_socket_owned", self.manifest)
        self.assertIn(self.manifest["transport"]["lease_socket"], entry["argv"])
        entry = probes.build("unit_identity_expected", self.manifest)
        self.assertIn(self.manifest["service"]["units"][0], entry["argv"])
        entry = probes.build("nftables_default_drop", self.manifest)
        self.assertIn(self.manifest["kernel"]["nftables_table"], entry["argv"])

    def test_an_unknown_or_caller_supplied_command_is_refused(self):
        for check_id in ("rm", "../etc/passwd", "os_release_supported ; rm -rf /",
                         "", None, 7):
            with self.subTest(check_id=check_id):
                with self.assertRaises(probes.ProbeError) as raised:
                    probes.build(check_id, self.manifest)
                self.assertEqual(raised.exception.code, "check_unknown")

    def test_a_manifest_with_a_hostile_identifier_cannot_reach_argv(self):
        # The manifest validator already refuses this shape; the builder is the
        # second gate, so a bypassed validator still cannot inject a token.
        hostile = dict(self.manifest)
        hostile["transport"] = dict(self.manifest["transport"])
        hostile["transport"]["guest_interface"] = "eth0; rm -rf /"
        with self.assertRaises(probes.ProbeError) as raised:
            probes.build("veth_identity_expected", hostile)
        self.assertEqual(raised.exception.code, "argv_token_invalid")

    def test_the_plan_follows_manifest_order_and_covers_every_check(self):
        entries = probes.plan(self.manifest)
        self.assertEqual([item["check_id"] for item in entries],
                         list(manifest_module.check_ids(self.manifest)))

    def test_execution_artifact_is_bound_to_exact_typed_plan(self):
        states = {name: "passed" for name in manifest_module.check_ids(self.manifest)}
        original = fixtures.execution_artifact(self.manifest, states)
        self.assertEqual(len(probes.validate_execution_artifact(
            original, self.manifest)), len(original))
        for field, value, code in (
            ("category", "cleanup", "execution_artifact_category_mismatch"),
            ("source", "guest_probe", "execution_artifact_source_mismatch"),
            ("expectation", "empty_output",
             "execution_artifact_expectation_mismatch"),
            ("argv", ["/usr/bin/uname", "-r"], "execution_artifact_argv_mismatch"),
        ):
            with self.subTest(field=field):
                document = [dict(item) for item in original]
                document[0][field] = value
                with self.assertRaises(probes.ProbeError) as raised:
                    probes.validate_execution_artifact(document, self.manifest)
                self.assertEqual(raised.exception.code, code)
        with self.assertRaises(probes.ProbeError) as raised:
            probes.validate_execution_artifact(original[:-1], self.manifest)
        self.assertEqual(raised.exception.code, "execution_artifact_incomplete")

    def test_parsing_requires_the_exact_result_schema(self):
        for completed in ({}, {"returncode": 0}, result(extra=1),
                          result(returncode="0"), result(timed_out="no")):
            with self.subTest(completed=tuple(sorted(completed))[:2]):
                with self.assertRaises(probes.ProbeError) as raised:
                    probes.parse("os_release_supported", completed, self.manifest)
                self.assertEqual(raised.exception.code, "result_schema_invalid")

    def test_parsing_states_follow_exit_status_and_expected_observations(self):
        passed = probes.parse("os_release_supported", result(
            stdout="24.04\n", expected=["24.04"]), self.manifest)
        self.assertEqual(passed["state"], "passed")
        self.assertEqual(passed["observations"], ("24.04",))

        missing = probes.parse("os_release_supported", result(
            stdout="22.04\n", expected=["24.04"]), self.manifest)
        self.assertEqual(missing["state"], "failed")
        self.assertEqual(missing["code"], "expected_observation_missing")

        nonzero = probes.parse("os_release_supported", result(
            returncode=1, expected=[]), self.manifest)
        self.assertEqual(nonzero["state"], "failed")

        timed_out = probes.parse("os_release_supported", result(
            timed_out=True, expected=[]), self.manifest)
        self.assertEqual(timed_out["state"], "blocked")
        self.assertEqual(timed_out["code"], "probe_timeout")

    def test_oversize_or_undecodable_output_is_refused_not_truncated(self):
        with self.assertRaises(probes.ProbeError) as raised:
            probes.parse("os_release_supported",
                         result(stdout="x" * 70000), self.manifest)
        self.assertEqual(raised.exception.code, "output_oversize")
        with self.assertRaises(probes.ProbeError) as raised:
            probes.parse("os_release_supported",
                         result(stdout=b"\xff\xfe"), self.manifest)
        self.assertEqual(raised.exception.code, "output_undecodable")

    def test_secret_like_output_blocks_the_check_and_is_never_persisted(self):
        parsed = probes.parse("os_release_supported", result(
            stdout=fixtures.SECRET_SHAPED["authorization_header"] + "\n",
            expected=["24.04"]), self.manifest)
        self.assertEqual(parsed["state"], "blocked")
        self.assertEqual(parsed["code"], "secret_like_output")
        self.assertEqual(parsed["observations"], ())
        self.assertNotIn("aaaaaaaaaaaaaaaaaaaa", repr(parsed))
        self.assertIn("authorization_header", parsed["findings"])

    def test_absence_checks_pass_only_when_the_resource_is_gone(self):
        # A cleaned host makes these commands fail. Reading that as a failed
        # check reported a clean host as bad and a dirty host as good.
        for check_id in ("process_absent_after_cleanup", "route_absent_after_cleanup",
                         "nftables_absent_after_cleanup",
                         "interface_absent_after_cleanup",
                         "cgroup_absent_after_cleanup",
                         "temporary_absent_after_cleanup"):
            with self.subTest(check_id=check_id):
                self.assertEqual(probes.expectation_kind(check_id), "exit_nonzero")
                gone = probes.parse(check_id, result(returncode=1), self.manifest)
                self.assertEqual(gone["state"], "passed")
                self.assertEqual(gone["code"], "observed_absent")
                present = probes.parse(check_id, result(returncode=0), self.manifest)
                self.assertEqual(present["state"], "failed")
                self.assertEqual(present["code"], "resource_still_present")

    def test_an_empty_output_absence_check_reads_output_not_exit_status(self):
        check_id = "socket_absent_after_cleanup"
        self.assertEqual(probes.expectation_kind(check_id), "empty_output")
        gone = probes.parse(check_id, result(stdout="\n"), self.manifest)
        self.assertEqual(gone["state"], "passed")
        present = probes.parse(
            check_id, result(stdout="u_str LISTEN 0 1 @sandbox-lease 0 * 0\n"),
            self.manifest)
        self.assertEqual(present["state"], "failed")
        self.assertEqual(present["code"], "resource_still_present")
        unreadable = probes.parse(check_id, result(returncode=1), self.manifest)
        self.assertEqual(unreadable["state"], "blocked")

    def test_presence_checks_keep_the_ordinary_exit_zero_expectation(self):
        self.assertEqual(probes.expectation_kind("unit_identity_expected"), "exit_zero")
        self.assertEqual(probes.expectation_kind("unit_absent_after_cleanup"),
                         "exit_zero")
        parsed = probes.parse("unit_absent_after_cleanup", result(
            stdout="LoadState=not-found\n", expected=["LoadState=not-found"]),
            self.manifest)
        self.assertEqual(parsed["state"], "passed")

    def test_every_plan_entry_declares_its_expectation(self):
        for entry in probes.plan(self.manifest):
            with self.subTest(check_id=entry["check_id"]):
                self.assertIn(entry["expectation"], probes.EXPECTATION_KINDS)
        with self.assertRaises(probes.ProbeError):
            probes.expectation_kind("not_a_check")

    def test_parsed_results_never_carry_raw_output(self):
        parsed = probes.parse("os_release_supported", result(
            stdout="24.04 plus a lot of other host detail\n",
            stderr="a warning nobody needs to keep",
            expected=["24.04"]), self.manifest)
        self.assertNotIn("other host detail", repr(parsed))
        self.assertNotIn("warning", repr(parsed))
        self.assertEqual(set(parsed), {"check_id", "state", "code", "observations",
                                       "findings"})


if __name__ == "__main__":
    unittest.main()
