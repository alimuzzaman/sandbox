import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from sandbox.commands import remote as remote_command
from sandbox.delivery.context import registered_host_digest, registration_digest

NAME = "box"
MACHINE = "machine-one"
OLD = "sha256:" + "a" * 64


def entry(**overrides):
    return {"ssh": "user@203.0.113.10", "control_transport": "tailscale",
            "control_url": "http://100.64.0.9:9174", "tailscale_host": "100.64.0.9",
            "mcp_port": 9174, "provisioned": True, "sandbox_home": "/home/user/.sandbox",
            **overrides}


def host_state(*digests, machine=MACHINE, remote=NAME):
    return {"version": 1, "hosts": {
        f"{remote}/project-{index}/env": {"hosting_operation": {
            "target": {"remote": remote, "project": f"project-{index}", "environment": "env"},
            "evidence": {"host_identity": digest, "machine_identity": machine}}}
        for index, digest in enumerate(digests)}}


class PinnedDigestTest(unittest.TestCase):
    def test_without_a_pin_the_digest_is_the_registration_digest(self):
        current = entry()
        self.assertEqual(registered_host_digest(current, NAME, "/h"),
                         registration_digest(current, NAME, "/h"))

    def test_a_pin_for_the_current_registration_keeps_the_old_identity(self):
        current = entry()
        current["host_identity_pin"] = {
            "registered_digest": OLD,
            "accepted_digest": registration_digest(current, NAME, "/h")}
        self.assertEqual(registered_host_digest(current, NAME, "/h"), OLD)

    def test_a_pin_stops_applying_when_the_registration_changes_again(self):
        current = entry()
        current["host_identity_pin"] = {
            "registered_digest": OLD,
            "accepted_digest": registration_digest(current, NAME, "/h")}
        moved = {**current, "control_url": "http://100.64.0.77:9174"}
        self.assertEqual(registered_host_digest(moved, NAME, "/h"),
                         registration_digest(moved, NAME, "/h"))
        self.assertNotEqual(registered_host_digest(moved, NAME, "/h"), OLD)

    def test_a_malformed_pin_is_ignored(self):
        current = entry()
        accepted = registration_digest(current, NAME, "/h")
        for pin in ("text", {"accepted_digest": accepted},
                    {"accepted_digest": accepted, "registered_digest": "not-a-digest"}):
            with self.subTest(pin=pin):
                current["host_identity_pin"] = pin
                self.assertEqual(registered_host_digest(current, NAME, "/h"), accepted)


class RebindCommandTest(unittest.TestCase):
    def run_command(self, *, state, current=None, machine=MACHINE, confirm=False):
        registered = current if current is not None else entry()
        args = SimpleNamespace(name=NAME, confirm=confirm)
        out = io.StringIO()
        with mock.patch.object(remote_command.sr, "get_remote", return_value=registered), \
                mock.patch.object(remote_command.sr, "put_remote") as put, \
                mock.patch.object(remote_command.sr, "resolve_sandbox_home", return_value="/h"), \
                mock.patch.object(remote_command, "_invalidate_readiness"), \
                mock.patch("sandbox.core._hosting.load_host_state", return_value=state), \
                mock.patch("sandbox.commands.hosting._authenticated_machine_identity",
                           side_effect=machine if isinstance(machine, Exception) else None,
                           return_value=machine) as observed, \
                contextlib.redirect_stdout(out):
            try:
                remote_command._cmd_rebind_host(args, True)
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), put, observed

    def test_without_confirm_it_reports_and_writes_nothing(self):
        code, output, put, _ = self.run_command(state=host_state(OLD, OLD))
        self.assertEqual(code, 0)
        result = json.loads(output)
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["registered_digest"], OLD)
        self.assertEqual(result["targets"], 2)
        put.assert_not_called()

    def test_confirm_writes_a_pin_bound_to_the_current_registration(self):
        current = entry()
        code, output, put, _ = self.run_command(state=host_state(OLD), current=current, confirm=True)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["status"], "rebound")
        pin = put.call_args.kwargs["host_identity_pin"]
        self.assertEqual(pin["registered_digest"], OLD)
        self.assertEqual(pin["accepted_digest"], registration_digest(current, NAME, "/h"))
        self.assertEqual(pin["machine_identity"], MACHINE)

    def test_a_different_machine_is_refused(self):
        code, _, put, _ = self.run_command(state=host_state(OLD), machine="machine-two", confirm=True)
        self.assertNotEqual(code, 0)
        put.assert_not_called()

    def test_an_unprovable_machine_is_refused(self):
        code, _, put, _ = self.run_command(
            state=host_state(OLD), machine=RuntimeError("unavailable"), confirm=True)
        self.assertNotEqual(code, 0)
        put.assert_not_called()

    def test_no_recorded_target_is_refused(self):
        code, _, put, _ = self.run_command(state=host_state(OLD, remote="other"), confirm=True)
        self.assertNotEqual(code, 0)
        put.assert_not_called()

    def test_targets_that_disagree_are_refused(self):
        code, _, put, _ = self.run_command(
            state=host_state(OLD, "sha256:" + "b" * 64), confirm=True)
        self.assertNotEqual(code, 0)
        put.assert_not_called()

    def test_targets_on_different_machines_are_refused(self):
        state = host_state(OLD, OLD)
        next(iter(state["hosts"].values()))["hosting_operation"]["evidence"]["machine_identity"] = "machine-two"
        code, _, put, _ = self.run_command(state=state, confirm=True)
        self.assertNotEqual(code, 0)
        put.assert_not_called()

    def test_an_already_matching_registration_is_a_no_op(self):
        current = entry()
        digest = registered_host_digest(current, NAME, "/h")
        code, output, put, _ = self.run_command(state=host_state(digest), current=current, confirm=True)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["status"], "already_bound")
        put.assert_not_called()

    def test_rerunning_after_a_rebind_is_a_no_op(self):
        current = entry()
        current["host_identity_pin"] = {
            "registered_digest": OLD,
            "accepted_digest": registration_digest(current, NAME, "/h")}
        code, output, put, _ = self.run_command(state=host_state(OLD), current=current, confirm=True)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["status"], "already_bound")
        put.assert_not_called()

    def test_an_unknown_remote_is_refused(self):
        args = SimpleNamespace(name=NAME, confirm=True)
        with mock.patch.object(remote_command.sr, "get_remote", return_value=None), \
                mock.patch.object(remote_command.sr, "put_remote") as put, \
                contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit):
            remote_command._cmd_rebind_host(args, True)
        put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
