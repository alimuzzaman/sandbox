"""Probe command builders and parsers for the future Ubuntu live checks.

These tests never execute a command. They assert that every plan is an argv
array of allowlisted, manifest-derived tokens with a finite timeout and bounded
output, and that parsing refuses to persist anything it should not.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from credential_vault_proof import (  # noqa: E402
    catalog as catalog_module, fixtures, manifest as manifest_module, probes,
)


def result(**overrides):
    value = {"returncode": 0, "stdout": "", "stderr": "", "timed_out": False}
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

    def test_every_required_check_has_a_typed_predicate(self):
        for check_id, definition in catalog_module.CHECKS.items():
            if definition.required:
                with self.subTest(check_id=check_id):
                    self.assertNotEqual(
                        definition.predicate,
                        "predicate_unavailable",
                    )

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
        passed = probes.parse("os_release_supported", result(stdout="24.04\n"),
                              self.manifest)
        self.assertEqual(passed["state"], "passed")
        self.assertEqual(passed["observation"], {"kind": "exact_text", "value": "24.04"})

        missing = probes.parse("os_release_supported", result(stdout="22.04\n"),
                               self.manifest)
        self.assertEqual(missing["state"], "failed")
        self.assertEqual(missing["code"], "observation_mismatch")

        nonzero = probes.parse("os_release_supported", result(returncode=1), self.manifest)
        self.assertEqual(nonzero["state"], "failed")

        timed_out = probes.parse("os_release_supported", result(timed_out=True),
                                 self.manifest)
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
            stdout=fixtures.SECRET_SHAPED["authorization_header"] + "\n"), self.manifest)
        self.assertEqual(parsed["state"], "blocked")
        self.assertEqual(parsed["code"], "secret_like_output")
        self.assertEqual(parsed["observation"]["kind"], "secret_output")
        self.assertNotIn("aaaaaaaaaaaaaaaaaaaa", repr(parsed))
        self.assertIn("authorization_header", parsed["findings"])

    def test_absence_checks_pass_only_when_the_resource_is_gone(self):
        # A cleaned host makes these commands fail. Reading that as a failed
        # check reported a clean host as bad and a dirty host as good.
        interface = self.manifest["transport"]["guest_interface"]
        table = self.manifest["kernel"]["nftables_table"]
        diagnostics = {
            "process_absent_after_cleanup": "",
            "route_absent_after_cleanup": f'Cannot find device "{interface}"\n',
            "nftables_absent_after_cleanup": (
                "Error: Could not process rule: No such file or directory\n"
                f"list table inet {table}\n^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n"
            ),
            "interface_absent_after_cleanup": f'Device "{interface}" does not exist.\n',
            "cgroup_absent_after_cleanup": (
                "/usr/bin/stat: cannot statx "
                f"'/sys/fs/cgroup{self.manifest['service']['cgroup']}': "
                "No such file or directory\n"
            ),
            "temporary_absent_after_cleanup": (
                "/usr/bin/stat: cannot statx "
                f"'{self.manifest['cleanup']['paths'][0]}': No such file or directory\n"
            ),
        }
        for check_id, diagnostic in diagnostics.items():
            with self.subTest(check_id=check_id):
                self.assertEqual(probes.expectation_kind(check_id), "exit_nonzero")
                gone = probes.parse(check_id, result(returncode=1, stderr=diagnostic),
                                    self.manifest)
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
            stdout="LoadState=not-found\n"),
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
            stderr="a warning nobody needs to keep"), self.manifest)
        self.assertNotIn("other host detail", repr(parsed))
        self.assertNotIn("warning", repr(parsed))
        self.assertEqual(set(parsed), {"check_id", "state", "code", "observation",
                                       "result", "findings"})

    def test_caller_expectations_cannot_turn_wrong_platform_into_a_pass(self):
        with self.assertRaises(probes.ProbeError) as raised:
            probes.parse("os_release_supported", {**result(stdout="22.04\n"),
                                                   "expected": []}, self.manifest)
        self.assertEqual(raised.exception.code, "result_schema_invalid")
        parsed = probes.parse("os_release_supported", result(stdout="22.04\n"),
                              self.manifest)
        self.assertEqual((parsed["state"], parsed["code"]),
                         ("failed", "observation_mismatch"))

    def test_permission_or_tool_failure_never_proves_cleanup_absence(self):
        for returncode in (126, 127):
            with self.subTest(returncode=returncode):
                parsed = probes.parse("process_absent_after_cleanup", result(
                    returncode=returncode, stderr="permission denied\n"), self.manifest)
                self.assertEqual((parsed["state"], parsed["code"]),
                                 ("blocked", "probe_stderr"))

    def test_executable_ownership_is_typed_and_exact(self):
        passed = probes.parse("executable_ownership_expected", result(
            stdout="991:991:750:4096\n"), self.manifest)
        self.assertEqual(passed["state"], "passed")
        for output in ("1991:991:750:4096\n", "991:991:770:4096\n",
                       "991:991:750:0\n", "991:991:750:4096 extra\n"):
            with self.subTest(output=output.strip()):
                parsed = probes.parse("executable_ownership_expected",
                                      result(stdout=output), self.manifest)
                self.assertEqual((parsed["state"], parsed["code"]),
                                 ("failed", "observation_mismatch"))

    def test_veth_address_binds_the_exact_host_side_address(self):
        interface = self.manifest["transport"]["guest_interface"]
        host = self.manifest["transport"]["host_address"]
        guest = self.manifest["transport"]["guest_address"]
        passed = probes.parse("veth_address_expected", result(
            stdout=f"12: {interface} inet {host}/30 scope global {interface}\n"),
            self.manifest)
        self.assertEqual(passed["state"], "passed")
        near_miss = probes.parse("veth_address_expected", result(
            stdout=f"12: {interface} inet {guest}/30 scope global {interface}\n"),
            self.manifest)
        self.assertEqual((near_miss["state"], near_miss["code"]),
                         ("failed", "observation_mismatch"))

    def test_process_identity_requires_exact_uid_and_executable_fields(self):
        executable = self.manifest["service"]["executable"]
        good = f"4242 991 Mon Sep 1 10:00:00 2026 {executable} --fixture\n"
        self.assertEqual(probes.parse("broker_process_identity", result(stdout=good),
                                     self.manifest)["state"], "passed")
        for output in (
            good.replace(" 991 ", " 1991 "),
            good.replace(executable, f"{executable}-lookalike"),
            good + good,
        ):
            with self.subTest(output=output[:48]):
                parsed = probes.parse("broker_process_identity", result(stdout=output),
                                      self.manifest)
                self.assertEqual(parsed["state"], "failed")

        controller = self.manifest["service"]["controller_executable"]
        controller_good = (
            f"5252 501 Mon Sep 1 10:00:00 2026 {controller} --fixture\n"
        )
        self.assertEqual(probes.parse("controller_process_identity", result(
            stdout=controller_good), self.manifest)["state"], "passed")
        tmp = controller_good.replace(controller,
                                      "/tmp/sandbox-credential-controller")
        parsed = probes.parse("controller_process_identity", result(stdout=tmp),
                              self.manifest)
        self.assertEqual((parsed["state"], parsed["code"]),
                         ("failed", "observation_mismatch"))

    def test_controller_unit_and_executable_ownership_use_sealed_fields(self):
        service = self.manifest["service"]
        unit_output = "\n".join((
            f"Id={service['controller_unit']}", "LoadState=loaded", "ActiveState=active",
            "User=sandbox-credential-controller", "Group=sandbox-credential-controller",
            f"ExecStart={service['controller_executable']}", "NoNewPrivileges=yes",
            f"ControlGroup={service['controller_cgroup']}",
        )) + "\n"
        self.assertEqual(probes.parse("controller_unit_identity_expected", result(
            stdout=unit_output), self.manifest)["state"], "passed")
        wrong_path = unit_output.replace(service["controller_executable"],
                                         "/tmp/sandbox-credential-controller")
        self.assertEqual(probes.parse("controller_unit_identity_expected", result(
            stdout=wrong_path), self.manifest)["state"], "failed")
        ownership = probes.parse("controller_executable_ownership_expected", result(
            stdout="501:501:750:4096\n"), self.manifest)
        self.assertEqual(ownership["state"], "passed")

    def test_socket_identity_requires_exact_address_and_process_owner(self):
        address = self.manifest["transport"]["lease_socket"]
        good = (f"u_str LISTEN 0 16 {address} 123 * 0 uid:991 "
                'users:(("native-credenti",pid=4242,fd=7))\n')
        self.assertEqual(probes.parse("lease_socket_owned", result(stdout=good),
                                     self.manifest)["state"], "passed")
        for output in (
            good.replace("native-credenti", "other-owner"),
            good.replace(address, f"lookalike-{address}"),
            good.replace("pid=4242", "pid=1"),
            good.replace("uid:991", "uid:1991"),
        ):
            with self.subTest(output=output[:64]):
                parsed = probes.parse("lease_socket_owned", result(stdout=output),
                                      self.manifest)
                self.assertEqual(parsed["state"], "failed")

    def test_socket_pid_and_uid_are_sealed_to_the_process_observation(self):
        states = {name: "passed" for name in manifest_module.check_ids(self.manifest)}
        artifact = fixtures.execution_artifact(self.manifest, states)
        lease = next(item for item in artifact if item["check_id"] == "lease_socket_owned")
        lease["observation"] = {"kind": "socket_owner",
                                "value": {**lease["observation"]["value"], "pid": 5252}}
        with self.assertRaises(probes.ProbeError) as raised:
            probes.validate_execution_artifact(artifact, self.manifest)
        self.assertEqual(raised.exception.code,
                         "execution_artifact_socket_process_mismatch")

        expanded = fixtures.manifest()
        expanded["checks"].extend((
            {"check_id": "controller_process_identity", "category": "process_identity",
             "required": True, "description": "controller process identity matches"},
            {"check_id": "controller_socket_owned", "category": "transport",
             "required": True, "description": "controller socket ownership matches"},
        ))
        expanded = manifest_module.validate_manifest(expanded)
        states = {name: "passed" for name in manifest_module.check_ids(expanded)}
        artifact = fixtures.execution_artifact(expanded, states)
        self.assertEqual(len(probes.validate_execution_artifact(artifact, expanded)),
                         len(artifact))
        controller_process = next(item for item in artifact
                                  if item["check_id"] == "controller_process_identity")
        controller_process["observation"] = {
            "kind": "process_identity",
            "value": {**controller_process["observation"]["value"], "pid": 6262},
        }
        with self.assertRaises(probes.ProbeError) as raised:
            probes.validate_execution_artifact(artifact, expanded)
        self.assertEqual(raised.exception.code,
                         "execution_artifact_socket_process_mismatch")

    def test_cleanup_process_uses_truncated_comm_and_detects_a_live_broker(self):
        entry = probes.build("process_absent_after_cleanup", self.manifest)
        self.assertEqual(entry["argv"][-1], "native-credenti")
        live = probes.parse("process_absent_after_cleanup",
                            result(returncode=0, stdout="4242\n"), self.manifest)
        self.assertEqual((live["state"], live["code"]),
                         ("failed", "resource_still_present"))

    def test_cleanup_permission_denial_never_proves_path_absence(self):
        for check_id in ("cgroup_absent_after_cleanup",
                         "temporary_absent_after_cleanup"):
            with self.subTest(check_id=check_id):
                parsed = probes.parse(check_id, result(
                    returncode=1, stderr="/usr/bin/stat: permission denied\n"),
                    self.manifest)
                self.assertEqual((parsed["state"], parsed["code"]),
                                 ("blocked", "probe_stderr"))

    def test_cleanup_missing_resource_diagnostics_are_exact(self):
        interface = self.manifest["transport"]["guest_interface"]
        good = probes.parse("interface_absent_after_cleanup", result(
            returncode=1, stderr=f'Device "{interface}" does not exist.\n'),
            self.manifest)
        self.assertEqual(good["state"], "passed")
        near_miss = probes.parse("interface_absent_after_cleanup", result(
            returncode=1, stderr=f'Device "lookalike-{interface}" does not exist.\n'),
            self.manifest)
        self.assertEqual((near_miss["state"], near_miss["code"]),
                         ("blocked", "probe_stderr"))

    def test_mount_isolation_requires_exact_systemd_fields(self):
        good = ("BindPaths=\nBindReadOnlyPaths=\nInaccessiblePaths=/home /root\n"
                "ProtectHome=yes\n")
        self.assertEqual(probes.parse("no_unexpected_host_mount", result(stdout=good),
                                     self.manifest)["state"], "passed")
        near_miss = good.replace("BindPaths=", "BindPaths=/srv/project")
        parsed = probes.parse("no_unexpected_host_mount", result(stdout=near_miss),
                              self.manifest)
        self.assertEqual((parsed["state"], parsed["code"]),
                         ("failed", "observation_mismatch"))

    def test_nft_policy_must_belong_to_the_exact_manifest_table(self):
        table = self.manifest["kernel"]["nftables_table"]
        document = {"nftables": [
            {"table": {"family": "inet", "name": table}},
            {"chain": {"family": "inet", "table": table, "name": "output",
                       "policy": "drop"}},
        ]}
        good = json.dumps(document, sort_keys=True, separators=(",", ":"))
        self.assertEqual(probes.parse("nftables_default_drop", result(stdout=good),
                                     self.manifest)["state"], "passed")
        document["nftables"][1]["chain"]["table"] = "lookalike-table"
        wrong_chain = json.dumps(document, sort_keys=True, separators=(",", ":"))
        parsed = probes.parse("nftables_default_drop", result(stdout=wrong_chain),
                              self.manifest)
        self.assertEqual((parsed["state"], parsed["code"]),
                         ("failed", "observation_mismatch"))


if __name__ == "__main__":
    unittest.main()
