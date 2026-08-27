"""Cleanup observation must tell absence and drift apart (039 T072).

A resource that provisioning never created, or that an earlier cleanup already
removed, has nothing left to remove; reporting it as changed ownership stopped
cleanup there permanently. Absence is only ever a successful read that found
nothing — a read that could not be made stays a residual.
"""

from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _helper():
    path = (Path(__file__).resolve().parents[1] / "tools" / "native-helper"
            / "native-helper.py")
    spec = importlib.util.spec_from_file_location("native_helper_cleanup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _result(returncode=0, stdout=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


class TestAbsenceIsRead(unittest.TestCase):
    def setUp(self):
        self.helper = _helper()

    def test_an_unanswered_unit_query_is_not_absence(self):
        with mock.patch.object(self.helper, "run_optional",
                               return_value=_result(returncode=1)):
            self.assertFalse(self.helper.unit_absent("sandbox-native-sb-1.service"))

    def test_only_a_not_found_load_state_proves_the_unit_absent(self):
        for value, expected in (("not-found", True), ("loaded", False), ("", False)):
            with self.subTest(load_state=value):
                with mock.patch.object(self.helper, "run_optional",
                                       return_value=_result(stdout=value + "\n")):
                    self.assertIs(self.helper.unit_absent("unit"), expected)

    def test_an_unlistable_registry_is_not_an_absent_machine(self):
        with mock.patch.object(self.helper, "run_optional",
                               return_value=_result(returncode=1)):
            self.assertFalse(self.helper.machine_absent("sb-0123456789ab"))

    def test_a_registered_machine_is_present_even_with_no_unit(self):
        def run_optional(argv, **_kwargs):
            if argv[:2] == ("machinectl", "list"):
                return _result(stdout="sb-0123456789ab container systemd-nspawn\n")
            return _result(stdout="not-found\n")

        with mock.patch.object(self.helper, "run_optional", run_optional):
            self.assertFalse(self.helper.machine_absent("sb-0123456789ab"))

    def test_a_machine_missing_from_both_reads_is_absent(self):
        def run_optional(argv, **_kwargs):
            if argv[:2] == ("machinectl", "list"):
                return _result(stdout="other-machine container systemd-nspawn\n")
            return _result(stdout="not-found\n")

        with mock.patch.object(self.helper, "run_optional", run_optional):
            self.assertTrue(self.helper.machine_absent("sb-0123456789ab"))

    def _show(self, *pairs):
        return _result(stdout="\n\n".join(f"Id={unit}\nLoadState={state}"
                                          for unit, state in pairs) + "\n")

    def test_a_stopped_or_already_masked_service_is_owned_not_changed(self):
        # Ownership comes from the marker, not from the unit running. Requiring
        # `is-active` refused units installed but never started, and `masked` is
        # what this cleanup's own stop step leaves, so a rerun must accept it.
        units = ("mariadb.service", "nginx.service")
        for states in (("loaded", "loaded"), ("masked", "masked"), ("loaded", "masked")):
            with self.subTest(states=states):
                with mock.patch.object(self.helper, "service_plan",
                                       return_value=({}, units)), \
                        mock.patch.object(self.helper, "run_optional",
                                          return_value=self._show(*zip(units, states))):
                    self.helper.services_ownership_status(
                        "sb-0123456789ab", "d" * 64, "e" * 64)

        with mock.patch.object(self.helper, "service_plan", return_value=({}, units)), \
                mock.patch.object(self.helper, "run_optional",
                                  return_value=self._show(("mariadb.service", "loaded"),
                                                          ("nginx.service", "not-found"))), \
                self.assertRaises(SystemExit):
            self.helper.services_ownership_status("sb-0123456789ab", "d" * 64, "e" * 64)

    def test_unit_states_are_mapped_by_name_not_by_output_position(self):
        # A blank value with `--value` shifted every later unit's state onto the
        # wrong unit, so a masked unit could read as a missing one.
        units = ("mariadb.service", "nginx.service", "cron.service")
        with mock.patch.object(self.helper, "run_optional",
                               return_value=self._show(("mariadb.service", "masked"),
                                                       ("nginx.service", ""),
                                                       ("cron.service", "not-found"))):
            states = self.helper.guest_unit_load_states("sb-0123456789ab", units)
        self.assertEqual(states, {"mariadb.service": "masked", "nginx.service": "",
                                  "cron.service": "not-found"})
        with mock.patch.object(self.helper, "run_optional",
                               return_value=_result(returncode=1)):
            self.assertIsNone(self.helper.guest_unit_load_states("sb-0123456789ab", units))

    def test_a_missing_fixed_guest_path_is_absence_only_when_answered(self):
        cases = (
            (_result(returncode=1, stdout="absent\n"), False),  # the guest never answered
            (_result(stdout="present\n"), False),
            (_result(stdout="absent\n"), True),
        )
        for name in ("marker", "database-socket"):
            for outcome, expected in cases:
                with self.subTest(path=name, stdout=outcome.stdout,
                                  returncode=outcome.returncode):
                    with mock.patch.object(self.helper, "run_optional",
                                           return_value=outcome):
                        self.assertIs(
                            self.helper.guest_path_absent("sb-0123456789ab", name), expected)

    def test_only_enumerated_guest_paths_can_be_probed(self):
        # Nothing caller-controlled reaches the guest shell.
        with self.assertRaises(KeyError):
            self.helper.guest_path_absent("sb-0123456789ab", "/etc/shadow; rm -rf /")

    def test_guest_units_are_absent_only_when_the_guest_answered_for_each(self):
        units = ("nginx.service", "php-fpm.service")
        cases = (
            (_result(returncode=1), False),                        # never answered
            (self._show(("nginx.service", "not-found")), False),   # one answer for two units
            (self._show(("nginx.service", "not-found"),
                        ("php-fpm.service", "loaded")), False),
            (self._show(("nginx.service", "not-found"),
                        ("php-fpm.service", "not-found")), True),
        )
        for outcome, expected in cases:
            with self.subTest(stdout=outcome.stdout, returncode=outcome.returncode):
                with mock.patch.object(self.helper, "run_optional", return_value=outcome):
                    self.assertIs(
                        self.helper.guest_units_absent("sb-0123456789ab", units), expected)


class TestCleanupObserveReportsState(unittest.TestCase):
    def setUp(self):
        self.helper = _helper()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def observe(self, resource):
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.helper.cleanup_observe(resource, "sb-0123456789ab", "d" * 64, "d" * 64)
        return json.loads(stream.getvalue())

    def _policy(self, size):
        return mock.patch.object(
            self.helper, "applied_policy",
            return_value=(self.root / "policy.json", {"root_image": {"bytes": size}}))

    def test_a_missing_image_is_absent_and_a_wrong_one_still_fails(self):
        image = self.root / "root.img"
        mountpoint = self.root / "mount"
        paths = mock.patch.object(self.helper, "image_paths",
                                  return_value=(self.root, image, mountpoint))
        with self._policy(16), paths, mock.patch.object(self.helper, "digest_value",
                                                        side_effect=lambda value: value):
            self.assertEqual(self.observe("image")["state"], "absent")
            image.write_bytes(b"x" * 8)
            image.chmod(0o600)
            with self.assertRaises(SystemExit):
                self.observe("image")

    def _policy_patches(self):
        return (
            self._policy(16),
            mock.patch.object(self.helper, "APPARMOR_ROOT", self.root),
            mock.patch.object(self.helper, "compile_apparmor_profile", return_value="profile"),
            mock.patch.object(self.helper, "digest_value", side_effect=lambda value: value),
        )

    def test_a_removed_profile_leaves_the_policy_present_so_its_record_is_removed(self):
        # `policy-remove` also removes the applied record and the instance root,
        # so an absent profile alone is a half-removed policy. Calling it absent
        # would skip the removal and strand both on the host.
        policy, root, compile_profile, digest = self._policy_patches()
        with policy, root, compile_profile, digest, \
                mock.patch.object(self.helper, "_apparmor_loaded_state", return_value=False):
            self.assertEqual(self.observe("policy")["state"], "present")

    def test_a_changed_profile_still_fails(self):
        policy, root, compile_profile, digest = self._policy_patches()
        (self.root / "sandbox-native-sb-0123456789ab").write_text("something else")
        with policy, root, compile_profile, digest, \
                mock.patch.object(self.helper, "_apparmor_loaded_state", return_value=True), \
                self.assertRaises(SystemExit):
            self.observe("policy")

    def test_an_unreadable_apparmor_state_is_never_treated_as_a_removed_profile(self):
        policy, root, compile_profile, digest = self._policy_patches()
        with policy, root, compile_profile, digest, \
                mock.patch.object(self.helper, "_apparmor_loaded_state", return_value=None), \
                self.assertRaises(SystemExit):
            self.observe("policy")

    def test_a_record_left_after_its_nft_table_is_a_network_still_to_finish_removing(self):
        network = {"veth": "sb-veth0", "host_address": "10.0.0.1/30"}
        record = {"marker": "sandbox-native-sb-0123456789ab"}
        policy = mock.patch.object(
            self.helper, "applied_policy",
            return_value=(self.root / "policy.json", {"network": network}))
        with policy, \
                mock.patch.object(self.helper, "digest_value", side_effect=lambda value: value), \
                mock.patch.object(self.helper, "network_names",
                                  return_value=("sb_0123456789ab", "sandbox-native")), \
                mock.patch.object(self.helper, "network_state_record", return_value=record), \
                mock.patch.object(self.helper, "observed_nft_table", return_value=None), \
                mock.patch.object(self.helper, "observed_link", return_value=None), \
                mock.patch.object(self.helper, "desired_network_state", return_value=record):
            self.assertEqual(self.observe("network")["state"], "present")

    def test_a_table_with_no_ownership_record_is_never_touched(self):
        network = {"veth": "sb-veth0", "host_address": "10.0.0.1/30"}
        policy = mock.patch.object(
            self.helper, "applied_policy",
            return_value=(self.root / "policy.json", {"network": network}))
        with policy, \
                mock.patch.object(self.helper, "digest_value", side_effect=lambda value: value), \
                mock.patch.object(self.helper, "network_names",
                                  return_value=("sb_0123456789ab", "sandbox-native")), \
                mock.patch.object(self.helper, "network_state_record", return_value=None), \
                mock.patch.object(self.helper, "observed_nft_table",
                                  return_value={"comment": "someone else"}), \
                mock.patch.object(self.helper, "observed_link", return_value=None), \
                self.assertRaises(SystemExit):
            self.observe("network")

    def test_a_fully_removed_network_is_absent(self):
        network = {"veth": "sb-veth0", "host_address": "10.0.0.1/30"}
        policy = mock.patch.object(
            self.helper, "applied_policy",
            return_value=(self.root / "policy.json", {"network": network}))
        with policy, \
                mock.patch.object(self.helper, "digest_value", side_effect=lambda value: value), \
                mock.patch.object(self.helper, "network_names",
                                  return_value=("sb_0123456789ab", "sandbox-native")), \
                mock.patch.object(self.helper, "network_state_record", return_value=None), \
                mock.patch.object(self.helper, "observed_nft_table", return_value=None), \
                mock.patch.object(self.helper, "observed_link", return_value=None):
            self.assertEqual(self.observe("network")["state"], "absent")


class TestObserverAcceptsState(unittest.TestCase):
    def observer(self, payload):
        from sandbox.runtimes.managed.helper import ManagedCleanupObserver

        class Process:
            def run(self, _argv, **_kwargs):
                return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

        return ManagedCleanupObserver(process=Process(), helper="/helper")

    def identity(self, **extra):
        return {"machine_id": "sb-1", "policy_digest": "d", "resource": "image",
                "resource_digest": "d", **extra}

    def test_a_stateless_observation_is_still_read_as_present(self):
        observed = self.observer(self.identity())("image", {"machine_id": "sb-1",
                                                            "policy_digest": "d"})
        self.assertEqual(observed["state"], "present")

    def test_absence_is_carried_through_to_the_coordinator(self):
        observed = self.observer(self.identity(state="absent"))(
            "image", {"machine_id": "sb-1", "policy_digest": "d"})
        self.assertEqual(observed["state"], "absent")

    def test_a_mismatched_identity_is_still_rejected(self):
        with self.assertRaises(RuntimeError):
            self.observer(self.identity(machine_id="sb-other", state="absent"))(
                "image", {"machine_id": "sb-1", "policy_digest": "d"})



class TestNftRuleCanonicalisation(unittest.TestCase):
    """A set we build and the same set nft echoes back must digest identically."""

    def setUp(self):
        self.helper = _helper()

    def match(self, right):
        return {"match": {"op": "in", "left": {"ct": {"key": "state"}}, "right": right}}

    def test_a_built_set_and_an_echoed_list_are_the_same_rule(self):
        built = self.helper.normalize_nft_value(
            [self.match({"set": ["new", "established"]})])
        echoed = self.helper.normalize_nft_value(
            [self.match(["established", "new"])])
        self.assertEqual(built, echoed)

    def test_equality_against_a_set_is_the_membership_test_nft_renders(self):
        built = self.helper.normalize_nft_value([{"match": {
            "op": "==", "left": {"ct": {"key": "state"}},
            "right": {"set": ["new", "established"]}}}])
        echoed = self.helper.normalize_nft_value([{"match": {
            "op": "in", "left": {"ct": {"key": "state"}},
            "right": ["established", "new"]}}])
        self.assertEqual(built, echoed)

    def test_inequality_is_never_folded_into_membership(self):
        negated = self.helper.normalize_nft_value([{"match": {
            "op": "!=", "left": {"ct": {"key": "state"}},
            "right": {"set": ["new"]}}}])
        self.assertEqual(negated[0]["match"]["op"], "!=")

    def test_set_membership_still_distinguishes_different_sets(self):
        one = self.helper.normalize_nft_value([self.match(["established", "new"])])
        other = self.helper.normalize_nft_value([self.match(["established", "related"])])
        self.assertNotEqual(one, other)

    def test_a_scalar_right_operand_is_untouched(self):
        value = self.helper.normalize_nft_value([self.match("new")])
        self.assertEqual(value, [self.match("new")])

    def test_the_two_ct_state_rules_match_what_nft_echoes_back(self):
        # The exact failure seen live: rules identical to the policy, refused as
        # changed ownership because only these two carry a set.
        network = {"guest_address": "10.203.118.246/30", "veth": "ve-db081dbdcb",
                   "host_address": "10.203.118.245/30", "ingress_port": 8080}
        expected = dict(self.helper.expected_network_rules(network))
        echoed = {
            "guest_host_established": ("input", [
                {"match": {"op": "==", "left": {"meta": {"key": "iifname"}},
                           "right": "ve-db081dbdcb"}},
                {"match": {"op": "==",
                           "left": {"payload": {"protocol": "ip", "field": "saddr"}},
                           "right": "10.203.118.246"}},
                {"match": {"op": "in", "left": {"ct": {"key": "state"}},
                           "right": ["established", "related"]}},
                {"counter": {"packets": 0, "bytes": 0}}, {"accept": None},
            ]),
            "ingress": ("output", [
                {"match": {"op": "==", "left": {"meta": {"key": "oifname"}},
                           "right": "ve-db081dbdcb"}},
                {"match": {"op": "==",
                           "left": {"payload": {"protocol": "ip", "field": "daddr"}},
                           "right": "10.203.118.246"}},
                {"match": {"op": "==",
                           "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                           "right": 8080}},
                {"match": {"op": "in", "left": {"ct": {"key": "state"}},
                           "right": ["established", "new"]}},
                {"counter": {"packets": 3, "bytes": 180}}, {"accept": None},
            ]),
        }
        for name, (chain, expressions) in echoed.items():
            with self.subTest(rule=name):
                observed = self.helper.canonical_digest({
                    "chain": chain,
                    "expr": self.helper.normalize_nft_value(expressions),
                })
                self.assertEqual(observed, expected[name])



class TestNetworkRecordVersioning(unittest.TestCase):
    """A rendering change must not strand a network the product still owns."""

    def setUp(self):
        self.helper = _helper()
        self.network = {"guest_address": "10.203.118.246/30", "veth": "ve-db081dbdcb",
                        "host_address": "10.203.118.245/30", "ingress_port": 8080}

    def desired(self):
        return self.helper.desired_network_state("sb-0123456789ab", "d" * 64, self.network)

    def test_a_current_record_must_match_exactly(self):
        desired = self.desired()
        self.assertTrue(self.helper.network_record_matches(dict(desired), desired))
        changed = {**desired, "marker": "someone-else"}
        self.assertFalse(self.helper.network_record_matches(changed, desired))

    def test_an_older_record_is_trusted_for_identity_but_not_for_rule_spelling(self):
        desired = self.desired()
        legacy = {**desired, "version": 1,
                  "rules": [["guest_host_established", "0" * 64]],
                  "digest": "0" * 64}
        self.assertTrue(self.helper.network_record_matches(legacy, desired))
        # Identity still has to bind: a record for another policy is refused
        # however it spells its rules.
        foreign = {**legacy, "policy_digest": "e" * 64}
        self.assertFalse(self.helper.network_record_matches(foreign, desired))
        foreign_machine = {**legacy, "machine_id": "sb-ffffffffffff"}
        self.assertFalse(self.helper.network_record_matches(foreign_machine, desired))

    def test_every_version_ever_written_stays_readable(self):
        # Bumping the writer's version without the reader's made the record
        # unreadable, so cleanup could not read its own record and refused to
        # remove the network it owned.
        self.assertIn(self.helper.NETWORK_RECORD_VERSION,
                      self.helper.KNOWN_NETWORK_RECORD_VERSIONS)
        self.assertIn(1, self.helper.KNOWN_NETWORK_RECORD_VERSIONS)

    def test_a_missing_record_is_never_a_match(self):
        self.assertFalse(self.helper.network_record_matches(None, self.desired()))



class TestObservedNftStateShape(unittest.TestCase):
    """The observed rules must be comparable to the expected ones."""

    def setUp(self):
        self.helper = _helper()

    def observe(self, rules):
        document = {"nftables": [{"table": {"family": "inet", "name": "sb_demo",
                                            "comment": "marker"}}] + rules}
        with mock.patch.object(self.helper, "run_optional",
                               return_value=_result(stdout=json.dumps(document))):
            return self.helper.observed_nft_state("sb_demo")

    def rule(self, comment):
        return {"rule": {"family": "inet", "table": "sb_demo", "chain": "input",
                         "comment": comment,
                         "expr": [{"match": {"op": "==",
                                             "left": {"meta": {"key": "iifname"}},
                                             "right": "ve-demo"}},
                                  {"counter": {"packets": 0, "bytes": 0}},
                                  {"drop": None}]}}

    def test_observed_rules_compare_equal_to_the_expected_tuple(self):
        # A list never equals a tuple however identical the contents, so this
        # comparison could not succeed on any real host: rules matched exactly
        # and cleanup still refused the network as changed ownership.
        observed = self.observe([self.rule("guest_host_drop")])
        self.assertIsInstance(observed["rules"], tuple)
        record = {"chains": observed["chains"],
                  "rules": [list(item) for item in observed["rules"]]}
        self.assertTrue(self.helper.nft_state_matches_record(observed, record))

    def test_a_changed_rule_is_still_refused(self):
        observed = self.observe([self.rule("guest_host_drop")])
        record = {"chains": observed["chains"],
                  "rules": [["guest_host_drop", "0" * 64]]}
        self.assertFalse(self.helper.nft_state_matches_record(observed, record))



class TestInstalledProfileVersioning(unittest.TestCase):
    """A profile written by an earlier release is still ours to remove."""

    def setUp(self):
        self.helper = _helper()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.machine = "sb-0123456789ab"
        self.digest = "d" * 64
        self.path = self.root / f"sandbox-native-{self.machine}"

    def write(self, payload):
        self.path.write_bytes(payload)
        self.path.chmod(0o644)

    def ours(self):
        return self.helper.installed_profile_is_ours(self.path, self.machine, self.digest)

    def test_the_current_version_must_match_byte_for_byte(self):
        self.write(self.helper.compile_apparmor_profile(self.machine, self.digest).encode())
        self.assertTrue(self.ours())
        self.write(self.helper.compile_apparmor_profile(
            self.machine, self.digest).replace("deny userns,", "userns,").encode())
        self.assertFalse(self.ours())

    def test_an_earlier_release_is_recognised_and_not_required_to_match(self):
        legacy = (f"#include <tunables/global>\n\n# Sandbox policy {self.digest}\n"
                  f"profile sandbox-native-{self.machine} {{\n  /** ix,\n}}\n")
        self.write(legacy.encode())
        self.assertIsNone(self.helper.installed_profile_version(legacy.encode()))
        self.assertTrue(self.ours())

    def test_a_profile_for_another_policy_is_refused_at_any_version(self):
        foreign = (f"#include <tunables/global>\n\n# Sandbox policy {'e' * 64}\n"
                   f"profile sandbox-native-{self.machine} {{\n}}\n")
        self.write(foreign.encode())
        self.assertFalse(self.ours())

    def test_a_group_or_world_writable_profile_is_refused(self):
        self.write(self.helper.compile_apparmor_profile(self.machine, self.digest).encode())
        self.path.chmod(0o666)
        self.assertFalse(self.ours())

    def test_the_version_marker_is_read_from_the_header(self):
        payload = self.helper.compile_apparmor_profile(self.machine, self.digest).encode()
        self.assertEqual(self.helper.installed_profile_version(payload),
                         self.helper.APPARMOR_PROFILE_VERSION)


class TestCredentialBrokerCleanupObservation(unittest.TestCase):
    def plan(self):
        return {"machine_id": "sb-0123456789ab", "policy_digest": "1" * 64,
                "egress_digest": "2" * 64, "broker_digest": "3" * 64,
                "executable_digest": "4" * 64, "config_digest": "5" * 64,
                "digest": "6" * 64}

    def observer(self, status):
        from sandbox.runtimes.managed.helper import ManagedCleanupObserver
        return ManagedCleanupObserver(process=SimpleNamespace(), helper="/fixed/helper",
                                      credential_status=lambda _plan: status)

    def test_exact_stopped_is_absent_and_closed_is_present(self):
        plan = self.plan()
        base = {key: plan[key] for key in ("machine_id", "policy_digest", "egress_digest",
                                            "broker_digest", "executable_digest", "config_digest")}
        base.update({"ok": True, "admission_open": False, "mutated": False,
                     "broker_epoch": "7" * 64, "pid": 123,
                     "process_start_identity": "123:991827", "service_uid": 991,
                     "unit_identity": f"sandbox-credential-broker@{plan['machine_id']}.service",
                     "cgroup_identity": f"/sandbox.slice/credential-broker/{plan['machine_id']}"})
        stopped = self.observer({**base, "state": "stopped"})("credential_broker", plan)
        present = self.observer({**base, "state": "credential_pending"})("credential_broker", plan)
        self.assertEqual(stopped["state"], "absent")
        self.assertEqual(present["state"], "present")

    def test_failed_stopped_drift_and_unavailable_are_not_absence(self):
        plan = self.plan(); base = {key: plan[key] for key in
                                    ("machine_id", "policy_digest", "egress_digest",
                                     "broker_digest", "executable_digest", "config_digest")}
        base.update({"admission_open": False, "mutated": False, "broker_epoch": "7" * 64,
                     "pid": 123, "process_start_identity": "123:991827", "service_uid": 991,
                     "unit_identity": f"sandbox-credential-broker@{plan['machine_id']}.service",
                     "cgroup_identity": f"/sandbox.slice/credential-broker/{plan['machine_id']}"})
        for status in ({**base, "ok": False, "state": "stopped"},
                       {**base, "ok": True, "state": "drifted"},
                       {"ok": False, "state": "unavailable"}):
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                self.observer(status)("credential_broker", plan)


if __name__ == "__main__":
    unittest.main()
