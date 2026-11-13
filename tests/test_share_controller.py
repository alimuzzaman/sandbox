"""Controller sharing proof and physical-lease contracts; all transport is synthetic."""
import contextlib
import datetime
import json
import shlex
import subprocess
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.hosting import login_service as login, target_lease
from sandbox.hosting.owner_share_record import digest, target_digest
from sandbox.hosting.share import ShareRefusal, controller, fanout
from sandbox.hosting.share import attempts as ledger


class ControllerShareTests(unittest.TestCase):
    def setUp(self):
        self.target = ledger.Target("fixture", "site", "dev")
        self.entry = {"provisioned": True}
        self.cleanup = ledger.CleanupDescriptor("share.example.test",
            ledger.RuntimeIdentity("runtime_fingerprint", "a" * 64),
            ledger.ComposeCleanup("sandbox-host-site-dev", ("/srv/fixture/deploy-src/hosts/site/compose.yml",),
                "/srv/fixture/runtime/hosts/site/dev/compose.override.yml",
                "/srv/fixture/runtime/hosts/site/dev/environment.env"))
        self.services = controller.Services(self.cleanup.identity, "b" * 64, None)
        self.physical = controller.Ownership([(self.target, self.entry, {})])
        self.declaration = {"enabled_until": (datetime.date.today() + datetime.timedelta(days=2)).isoformat(),
            "session_ttl_seconds": 600, "capture": None, "allowed_host_suffixes": ["example.test"],
            "entries": {"share": {"mode": "site_gate", "hostname": "share.example.test"}},
            "share": {"agent_mint": True, "hostname": "share.example.test"}}
        self.found = SimpleNamespace(validated={"project": "site", "environment": "dev",
            "routes": [{"hostname": "site.example.test", "mode": "serve", "primary": True}],
            "compose": {"service": "web"}, "login_service": self.declaration})
        self.scope = contextlib.ExitStack()
        self.addCleanup(self.scope.close)
        self.scope.enter_context(patch("sandbox.core._remote.resolve_sandbox_home", return_value="/srv/fixture"))
        self.inspect = self.scope.enter_context(patch.object(fanout, "inspect_services", return_value=self.services))
        self.execute = self.scope.enter_context(patch("sandbox.commands.hosting._remote_checked"))
        self.bound = controller.bind(self.target, self.entry, self.cleanup, self.physical)

    def config(self, enabled=True):
        declaration = {**self.declaration, "share": {**self.declaration["share"], "agent_mint": enabled}}
        return login.runtime_config(declaration, target_digest=target_digest(self.found.validated))

    def capability(self, config):
        return json.dumps({"ok": True, "capabilities": sorted(fanout.CAPABILITIES | {"owner-panel-gate-v1"}),
            "config_digest": digest(config), "panel_access_digest": config["panel_access_digest"],
            "panel_target_digest": config["panel_target_digest"],
            "panel_agent_mint": config["panel_share"]["agent_mint"]})

    def test_original_recovery_gate_must_still_have_applied_opt_in_for_panel_revoke(self):
        self.execute.side_effect = [json.dumps(["python3", "-c", login._LOADER, login._runtime_source()]),
                                    self.capability(self.config(False))]
        with patch.object(self.physical, "effect") as effect:
            with self.assertRaises(ShareRefusal) as error:
                self.bound.require_enabled("share")
        self.assertEqual(error.exception.code, "agent_mint_not_enabled")
        effect.assert_not_called()

    def test_unapplied_enable_and_stale_enable_both_refuse_before_effects(self):
        for case in ("unapplied_enable", "disabled_by_other_controller"):
            with self.subTest(case=case):
                self.execute.side_effect = [json.dumps(["python3", "-c", login._LOADER, login._runtime_source()]),
                                            self.capability(self.config(False))]
                with patch.object(self.physical, "effect") as effect:
                    with self.assertRaises(ShareRefusal) as error:
                        self.bound.require_applied(self.found, "share")
                self.assertEqual(error.exception.code, "share_applied_policy_changed")
                effect.assert_not_called()

    def test_running_source_and_all_digests_are_required(self):
        self.execute.side_effect = [json.dumps(["python3", "-c", login._LOADER, login._runtime_source()]),
                                    self.capability(self.config())]
        self.bound.require_applied(self.found, "share")
        self.execute.side_effect = [json.dumps(["python3", "-c", "old-loader", "old-source"])]
        with self.assertRaises(ShareRefusal):
            self.bound.require_applied(self.found, "share")

    def test_effect_uses_immutable_container_and_never_composes_a_replacement(self):
        self.execute.return_value = '{"ok":true,"revoked_links":1,"revoked_sessions":0}'
        self.bound.manage("revoke", "share", "l-" + "c" * 12)
        argv = shlex.split(self.execute.call_args.args[1])
        self.assertEqual(argv[:4], ["docker", "exec", "-i", self.services.gate])
        self.assertNotIn("compose", argv)
        self.inspect.return_value = replace(self.services, gate="d" * 64)
        self.execute.reset_mock()
        with self.assertRaises(ShareRefusal):
            self.bound.manage("revoke", "share", "l-" + "c" * 12)
        self.execute.assert_not_called()

    def test_unknown_original_blocks_another_effect_and_retains_ownership(self):
        self.execute.side_effect = TimeoutError("original transport unknown")
        with self.assertRaises(TimeoutError):
            self.bound.manage("revoke", "share", "l-" + "c" * 12)
        self.assertIn(self.target, self.physical.uncertain)
        with self.assertRaises(ShareRefusal):
            self.bound.manage("revoke", "share", "l-" + "c" * 12)
        self.assertEqual(self.execute.call_count, 1)

    def test_registered_remote_or_cleanup_path_change_cannot_borrow_a_lease(self):
        with self.assertRaises(ShareRefusal):
            controller.bind(self.target, {"provisioned": True, "ssh": "different"}, self.cleanup, self.physical)
        changed = replace(self.cleanup, compose=replace(self.cleanup.compose, env_path="/other/environment.env"))
        with self.assertRaises(ShareRefusal):
            controller.bind(self.target, self.entry, changed, self.physical)

    def test_physical_protocol_excludes_other_controllers_and_retains_unknown(self):
        # The emulator implements only target_lease's public fixed wire. Both
        # controller sharing and ordinary apply call that actual client API.
        physical = {}
        def run(entry, command, **kwargs):
            path, token, owner, action = shlex.split(command)[-4:]
            old = physical.get(path)
            if action == "acquire":
                if old and old[1] != owner:
                    return subprocess.CompletedProcess([], target_lease.HELD_EXIT, stdout="123", stderr="")
                physical[path] = (token, owner)
            elif old and old[0] == token:
                del physical[path]
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")
        path = target_lease.lease_path("/srv/fixture", "site", "dev")
        with patch("sandbox.core._remote.get_remote", return_value=self.entry), \
                patch("sandbox.core._remote.ssh_run", side_effect=run):
            with controller.hold([self.target, self.target]) as held:
                with self.assertRaises(target_lease.LeaseHeld):
                    target_lease.acquire(lambda command: run(self.entry, command), path, "e" * 32)
                with self.assertRaises(ShareRefusal):
                    with controller.hold([self.target]):
                        self.fail("second controller acquired physical ownership")
                self.assertEqual(len(held.leases), 1)
                held.uncertain.add(self.target)
            self.assertIn(path, physical)
            with self.assertRaises(target_lease.LeaseHeld):
                target_lease.acquire(lambda command: run(self.entry, command), path, "e" * 32)


if __name__ == "__main__":
    unittest.main()
