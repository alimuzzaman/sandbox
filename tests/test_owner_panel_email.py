"""Owner-panel email selection with synthetic authority and gate effects only."""
import contextlib
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sandbox.hosting import owner_panel_share as owner
from sandbox.hosting.share import stack_page
from sandbox.hosting.share.fanout import CoverageRefusal
from tests.test_host_login_service import _environment, _login_block, _validate


class OwnerPanelEmailTests(unittest.TestCase):
    def setUp(self):
        environment = _environment(_login_block())
        environment["host"]["aliases"].append({"hostname": "share.example.test", "mode": "serve"})
        environment["share"] = {"hostname": "share.example.test", "agent_mint": True,
            "stack_sign_in": ["app", "inbox"], "inbox_sign_in": "inbox",
            "route_sign_in": {"app.example.test": "app"}}
        self.validated = _validate(environment)
        self.context = stack_page.TargetContext("fixture", "parent", self.validated,
            self.validated["login_service"], {"hostnames": [], "uses": []}, (), "a" * 40, "serving")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.key = "fixture/example-site/production"
        self.state = {"version": 1, "hosts": {self.key: {"login_default_email": "teammate@example.test"}}}
        self.grant = SimpleNamespace(owner=SimpleNamespace(sandbox_home=str(self.home)),
            validated=self.validated,
            snapshot={"project": "example-site", "environment": "production", "native_keys": [self.key]},
            summary={"stack": {"slug": "parent"}, "owner_share": {"uses": []}},
            gate_id="b" * 64, wordpress_id=None,
            require_current=Mock())
        self.request = SimpleNamespace(remote="fixture", stack="parent")
        self.store = SimpleNamespace(root=self.home, save=Mock())
        self.scope = contextlib.ExitStack()
        self.addCleanup(self.scope.close)
        self.scope.enter_context(patch.object(owner, "_context", return_value=self.context))
        self.scope.enter_context(patch.object(owner, "_held", return_value=contextlib.nullcontext()))
        self.audit = self.scope.enter_context(patch.object(owner, "_audit"))
        self.effect = self.scope.enter_context(patch.object(owner.gate, "_execute", return_value={"ok": True}))
        self.load = self.scope.enter_context(patch(
            "sandbox.hosting.recovery.repository.RecoveryRepository.load", side_effect=lambda: copy.deepcopy(self.state)))

    def create(self):
        return owner._create(self.store, self.request, self.grant, [])

    def test_owner_create_uses_exact_target_saved_email_without_persisting_it(self):
        result = self.create()
        self.assertTrue(result["ok"])
        payload = self.effect.call_args.kwargs["payload"]
        self.assertEqual(payload["stack"]["sign_in"], [
            {"entry": "app", "email": "teammate@example.test"}, {"entry": "inbox"}])
        self.load.assert_called_once_with()
        self.grant.require_current.assert_called()
        journal = json.dumps([call.args[0] for call in self.store.save.call_args_list])
        self.assertNotIn("teammate@example.test", journal)
        self.assertNotIn("teammate@example.test", repr(self.audit.call_args_list))
        self.assertNotIn("teammate@example.test", json.dumps(result))

    def test_missing_invalid_denied_or_ambiguous_email_refuses_before_mint(self):
        cases = ("missing", "invalid", "denied", "ambiguous", "unrelated", "changed_target")
        for case in cases:
            with self.subTest(case=case):
                self.state = {"version": 1, "hosts": {self.key: {"login_default_email": "teammate@example.test"}}}
                self.grant.snapshot["native_keys"] = [self.key]
                entry = self.validated["login_service"]["entries"]["app"]
                entry["magic_link"]["denied_emails"] = []
                if case == "missing":
                    self.state["hosts"][self.key] = {}
                elif case == "invalid":
                    self.state["hosts"][self.key]["login_default_email"] = "not-an-address"
                elif case == "denied":
                    entry["magic_link"]["denied_emails"] = ["teammate@example.test"]
                elif case == "ambiguous":
                    alias = "alias/example-site/production"
                    self.grant.snapshot["native_keys"].append(alias)
                    self.state["hosts"][alias] = {"login_default_email": "other@example.test"}
                elif case == "unrelated":
                    self.state["hosts"] = {self.key: {}, "fixture/another-site/production": {
                        "login_default_email": "teammate@example.test"}}
                else:
                    self.state["hosts"] = {}
                with patch.object(owner.share, "mint") as mint:
                    with self.assertRaises(CoverageRefusal) as error:
                        self.create()
                    self.assertIn("share_email_unavailable", {gap["code"] for gap in error.exception.gaps})
                    mint.assert_not_called()
                self.effect.assert_not_called()
                self.store.save.assert_not_called()

    def test_matching_native_alias_defaults_are_accepted(self):
        alias = "alias/example-site/production"
        self.grant.snapshot["native_keys"].append(alias)
        self.state["hosts"][alias] = copy.deepcopy(self.state["hosts"][self.key])
        self.assertTrue(self.create()["ok"])

    def test_email_free_inbox_does_not_read_a_default(self):
        self.validated["login_service"]["share"].update(stack_sign_in=["inbox"], route_sign_in={})
        self.assertTrue(self.create()["ok"])
        self.load.assert_not_called()

    def test_default_or_alias_change_at_lock_entry_refuses_before_mint_or_effect(self):
        alias = "alias/example-site/production"
        self.grant.snapshot["native_keys"] = [self.key, alias]
        entry = self.validated["login_service"]["entries"]["app"]
        entry["magic_link"]["denied_emails"] = ["denied@example.test"]
        for case in ("alias_conflict", "missing", "invalid", "denied"):
            with self.subTest(case=case):
                self.state["hosts"] = {key: {"login_default_email": "teammate@example.test"}
                                       for key in (self.key, alias)}

                @contextlib.contextmanager
                def changed_at_lock_entry(*args):
                    if case == "alias_conflict":
                        self.state["hosts"][alias]["login_default_email"] = "other@example.test"
                    else:
                        value = {"missing": None, "invalid": "invalid-address",
                                 "denied": "denied@example.test"}[case]
                        for record in self.state["hosts"].values():
                            record["login_default_email"] = value
                    yield

                with patch.object(owner, "_held", side_effect=changed_at_lock_entry), \
                        patch.object(owner.share, "mint", wraps=owner.share.mint) as mint:
                    with self.assertRaises(CoverageRefusal) as error:
                        self.create()
                    self.assertIn("share_email_unavailable", {gap["code"] for gap in error.exception.gaps})
                    mint.assert_not_called()
                self.effect.assert_not_called()
                self.store.save.assert_not_called()
                self.audit.assert_not_called()

    def test_replaced_default_at_lock_entry_builds_page_for_locked_identity(self):
        locked = False
        reads = []

        @contextlib.contextmanager
        def changed_at_lock_entry(*args):
            nonlocal locked
            self.state["hosts"][self.key]["login_default_email"] = "updated@example.test"
            locked = True
            try:
                yield
            finally:
                locked = False

        def read_state():
            reads.append(locked)
            return copy.deepcopy(self.state)

        self.load.side_effect = read_state
        with patch.object(owner, "_held", side_effect=changed_at_lock_entry):
            self.assertTrue(self.create()["ok"])
        self.assertEqual(reads, [True])
        actions = self.effect.call_args.kwargs["payload"]["stack"]["sign_in"]
        self.assertEqual(actions[0], {"entry": "app", "email": "updated@example.test"})


if __name__ == "__main__":
    unittest.main()
