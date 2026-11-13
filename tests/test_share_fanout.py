"""Behavior definitions for controller fanout; run by the separate checker."""
import contextlib
import io
import json
from dataclasses import replace
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.commands import host_share
from sandbox.hosting import share, stack_registry
from sandbox.hosting.share import attempts as ledger, fanout, stack_page, controller

# Capture the production boundary before each fixture replaces transport/mint ports.
_REAL_MINT_LINK = host_share.mint_link
_REAL_DESCRIPTOR = fanout.descriptor
_REAL_INSTALL_WORDPRESS_KEY = stack_page.install_wordpress_key
_REAL_INSPECT_SERVICES = fanout.inspect_services
_REAL_MANAGE = controller.BoundRuntime.manage


class FanoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = int(time.time())
        self.events = []
        self.members = [self.member("parent", ["child"]), self.member("child", [])]
        self.state = {"hosts": {f"dev/{member.stack['slug']}/dev": {"incarnation": member.stack['slug']}
                                for member in self.members}}
        self.registry = {"stacks": {stack_registry.record_key("dev", member.stack["slug"]): member.stack
                                    for member in self.members}}
        self.request = share.LinkRequest("share", "stack", share.STACK_PAGE_PATH, 300, 2)
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        def mock(obj, name, **kw):
            return self.patches.enter_context(patch.object(obj, name, **kw))
        self.audit = mock(host_share, "_audit")
        # Canonicalize only this trusted fixture base, not repository inputs.
        mock(fanout, "root", return_value=Path(self.tmp.name).resolve() / "attempts")
        import sandbox.commands.hosting as commands
        import sandbox.core._hosting as hosting
        import sandbox.core._remote as remote
        # Fail closed on unconfigured cleanup effects; never consult real remotes.
        self.remote_entry = mock(remote, "get_remote", return_value={"provisioned": True})
        mock(remote, "resolve_sandbox_home", return_value="/srv/fixture")
        self.remote_execute = mock(commands, "_remote_checked",
                                   side_effect=AssertionError("unexpected remote execution"))
        self.real_effects = commands._host_share_effects
        @contextlib.contextmanager
        def effects(targets):
            self.events.append(("locks", tuple(targets)))
            yield self.state
        mock(commands, "_host_share_effects", side_effect=effects)
        @contextlib.contextmanager
        def physical(targets):
            yield controller.Ownership([(target, {"provisioned": True}, {}) for target in targets])
        mock(controller, "hold", side_effect=physical)
        mock(controller.BoundRuntime, "require_applied")
        mock(fanout, "inspect_services", side_effect=lambda entry, compose, wp: controller.Services(
            ledger.RuntimeIdentity("runtime_fingerprint", "a" * 64), "c" * 64, "d" * 64 if wp else None))
        mock(hosting, "load_host_state", return_value=self.state)
        mock(stack_registry, "read", return_value=self.registry)
        mock(hosting, "validate_manifest", side_effect=lambda directory, env: next(
            member.validated for member in self.members if member.project_dir == directory))
        mock(host_share, "resolve_target", side_effect=lambda remote, *, stack=None, target=None,
             name=None, project_dir=None, environment=None: next(
            member for member in self.members if member.stack["slug"] == stack))
        self.capability = mock(host_share, "_remote_site", return_value={"ok": True, "capabilities": sorted(fanout.CAPABILITIES)})
        def manage(bound, verb, name, *extra, **kwargs):
            if verb == "list":
                return self.capability.return_value
            return _REAL_MANAGE(bound, verb, name, *extra, **kwargs)
        mock(controller.BoundRuntime, "manage", autospec=True, side_effect=manage)
        mock(fanout.login, "assert_applicable")
        mock(fanout.login, "enabled_until_epoch", return_value=self.now + 200)
        mock(fanout, "descriptor", side_effect=self.descriptor)
        self.real_observe = lambda entry, compose, wp: _REAL_INSPECT_SERVICES(entry, compose, wp).identity
        mock(fanout, "observe", return_value=ledger.RuntimeIdentity("runtime_fingerprint", "a" * 64))
        self.active_mint = None
        original_begin_mint = ledger.AttemptRepository.begin_mint
        def begin_mint(repo, attempt_id, grant_id):
            result = original_begin_mint(repo, attempt_id, grant_id)
            self.active_mint = (repo, attempt_id, grant_id)
            return result
        mock(ledger.AttemptRepository, "begin_mint", new=begin_mint)
        self.install = mock(stack_page, "install_wordpress_key", side_effect=self.install_key)
        self.mint = mock(host_share, "mint_link", side_effect=self.mint_link)

    def member(self, slug, uses):
        declaration = {"share": {"hostname": f"share-{slug}.example.test", "agent_mint": True,
                                  "stack_sign_in": [], "route_sign_in": {}},
                       "entries": {"share": {"mode": "site_gate", "hostname": f"share-{slug}.example.test"}},
                       "allowed_host_suffixes": ["example.test"]}
        validated = {"project": slug, "environment": "dev", "login_service": declaration,
                     "compose": {"files": ["compose.yml"], "service": "web"},
                     "routes": [{"hostname": f"{slug}.example.test", "primary": True, "mode": "serve"}]}
        record = {"remote": "dev", "slug": slug, "project": slug, "environment": "dev", "uses": uses,
                  "hostnames": [f"{slug}.example.test"]}
        return host_share.ShareTarget("dev", validated, {"provisioned": True}, slug + "/dev", "/src/" + slug, record)

    def descriptor(self, found, minted, page):
        return ledger.CleanupDescriptor(found.validated["login_service"]["share"]["hostname"],
            ledger.RuntimeIdentity("runtime_fingerprint", "a" * 64),
            ledger.ComposeCleanup("sandbox-host-" + found.stack["slug"] + "-dev",
                (f"/srv/fixture/deploy-src/hosts/{found.stack['slug']}/compose.yml",),
                f"/srv/fixture/runtime/hosts/{found.stack['slug']}/dev/compose.override.yml",
                f"/srv/fixture/runtime/hosts/{found.stack['slug']}/dev/environment.env"))

    def install_key(self, found, page, expiry, *, runtime=None):
        # Read durable storage through the owner already holding the parent lock.
        # recorded() discovers new repository instances and correctly refuses it.
        self.assertIsNotNone(self.active_mint)
        repo, attempt_id, grant_id = self.active_mint
        attempt = repo.read(attempt_id)
        self.assertIsNotNone(attempt)
        self.assertEqual(attempt.phase, "minting")
        grant = next(grant for grant in attempt.grants if grant.target.project == found.stack["slug"])
        self.assertEqual(grant.id, grant_id)
        self.assertEqual(grant.disposition, "unknown")
        self.assertIsNotNone(grant.cleanup)
        self.assertEqual(len(attempt.grants), 2)
        self.events.append(("key", found.stack["slug"]))

    def mint_link(self, validated, entry, request, *, minted, stack_record, on_completed=None, runtime=None):
        self.events.append(("mint", validated["project"], minted, stack_record))
        if on_completed is not None:
            on_completed()
        return minted, {"ok": True, "id": minted.link_id, "expires_at": minted.expires_at, "uses": request.uses}

    def test_parent_last_same_original_child_credentials_and_common_window(self):
        result = fanout.create(self.members[0], self.request)
        mints = [event for event in self.events if event[0] == "mint"]
        self.assertEqual([event[1] for event in mints], ["child", "parent"])
        child, parent = mints
        descriptor = parent[3]["companions"][0]
        self.assertEqual(descriptor["token"], child[2].token)
        self.assertEqual(descriptor["link_id"], child[2].link_id)
        self.assertEqual(child[3]["companions"], [])
        self.assertEqual(child[2].expires_at, parent[2].expires_at)
        self.assertEqual(parent[2].expires_at, self.now + 200)
        self.assertEqual(result["phase"], "active")
        self.assertNotIn("companions", result)
        self.assertIs(result["_minted"], parent[2])
        locks = next(event[1] for event in self.events if event[0] == "locks")
        self.assertEqual(locks, tuple(sorted(set(locks))))
        self.assertEqual(fanout.recorded("dev", stack="parent")[0].phase, "active")

    def test_capability_gap_has_zero_key_or_mint_effects(self):
        self.capability.return_value = {"ok": True, "capabilities": ["stack-page-v2"]}
        with self.assertRaises(fanout.CoverageRefusal) as raised:
            fanout.create(self.members[0], self.request)
        self.assertEqual(raised.exception.code, "stack_share_coverage_incomplete")
        self.assertEqual(len(raised.exception.gaps), 2)
        self.install.assert_not_called()
        self.mint.assert_not_called()
        self.assertEqual(fanout.recorded("dev", stack="parent"), ())

    def test_real_mint_boundary_preserves_preallocation_and_private_companion_payload(self):
        from sandbox.commands import hosting as commands
        from sandbox.hosting.login_service import runtime

        sent, returned = [], []

        def transport(validated, _remote, entry, verb, *, input_data, on_completed=None):
            self.assertEqual((entry, verb), ("share", "mint"))
            envelope = json.loads(input_data)
            sent.append((validated["project"], envelope))
            self.assertEqual(set(envelope), {"link", "stack"})
            config = {**validated["login_service"], "enabled_until_epoch": self.now + 200,
                      "panel_share": {"agent_mint": True}}
            output = io.StringIO()
            code = runtime.manage(config, [verb, entry],
                Path(self.tmp.name) / "gate" / validated["project"],
                stdin=io.StringIO(input_data), stdout=output, clock=lambda: self.now)
            self.assertEqual(code, 0)
            if on_completed is not None:
                on_completed()
            reply = json.loads(output.getvalue())
            self.assertTrue(reply["ok"], reply)
            return reply

        def real_mint(validated, entry, request, *, minted, stack_record, on_completed=None, runtime=None):
            actual, reply = _REAL_MINT_LINK(validated, entry, request,
                                           minted=minted, stack_record=stack_record, on_completed=on_completed,
                                           runtime=runtime)
            self.assertIs(actual, minted)
            returned.append(actual)
            return actual, reply

        def bound_transport(bound, verb, entry, *, payload, on_completed):
            name = json.loads(payload)["stack"]["title"]
            member = next(row for row in self.members if row.validated["project"] == name)
            return transport(member.validated, member.entry, entry, verb,
                             input_data=payload, on_completed=on_completed)

        with patch.object(host_share, "mint_link", side_effect=real_mint), \
                patch.object(controller.BoundRuntime, "manage", new=bound_transport), \
                patch.object(share, "mint", wraps=share.mint) as allocate:
            result = fanout.create(self.members[0], self.request)

        self.assertEqual(result["phase"], "active")
        self.assertEqual(allocate.call_count, 2)
        self.assertEqual([name for name, _ in sent], ["child", "parent"])
        child, parent = [envelope for _, envelope in sent]
        self.assertEqual(child["stack"]["companions"], [])
        companion = parent["stack"]["companions"][0]
        self.assertEqual(companion["token"], returned[0].token)
        self.assertEqual(companion["link_id"], child["link"]["id"])
        for envelope, minted in zip((child, parent), returned):
            self.assertEqual(envelope["link"]["id"], minted.link_id)
            self.assertEqual(envelope["link"]["token_sha256"], minted.token_sha256)
            self.assertEqual(envelope["stack"]["link"], minted.link_id)
            self.assertEqual(envelope["link"]["expires_at"], self.now + 200)
        self.assertIs(result["_minted"], returned[1])
        self.assertNotIn(returned[0].token, json.dumps(child))
        self.assertNotIn(returned[1].token, json.dumps(sent))
        retained = repr(fanout.recorded("dev", stack="parent"))
        self.assertNotIn(returned[0].token, retained)
        self.assertNotIn(returned[1].token, retained)

    def test_unknown_linked_access_reports_all_gaps_without_effects(self):
        for member in self.members:
            member.stack["sites"] = [{"hostname": "linked.example.test"}]
        with self.assertRaises(fanout.CoverageRefusal) as raised:
            fanout.create(self.members[0], self.request)
        self.assertEqual({row["stack"] for row in raised.exception.gaps}, {"parent", "child"})
        self.install.assert_not_called()

    def test_under_minimum_common_window_has_no_effects(self):
        with patch.object(fanout.login, "enabled_until_epoch", return_value=self.now + 30):
            with self.assertRaises(share.ShareRefusal):
                fanout.create(self.members[0], self.request)
        self.install.assert_not_called()
        self.mint.assert_not_called()

    def test_persistence_failure_has_no_effects(self):
        with patch.object(ledger.AttemptRepository, "prepare", side_effect=ledger.AttemptError("storage unavailable")):
            with self.assertRaises(ledger.AttemptError):
                fanout.create(self.members[0], self.request)
        self.install.assert_not_called()
        self.mint.assert_not_called()

    def test_symlink_storage_ancestor_is_refused_before_effects(self):
        base = Path(self.tmp.name).resolve()
        destination = base / "outside"
        destination.mkdir()
        alias = base / "alias"
        alias.symlink_to(destination, target_is_directory=True)
        with patch.object(fanout, "root", return_value=alias / "attempts"):
            with self.assertRaises(ledger.AttemptError):
                fanout.create(self.members[0], self.request)
        self.assertEqual(list(destination.iterdir()), [])
        self.install.assert_not_called()
        self.mint.assert_not_called()

    def test_failed_reply_never_remints_original_ids(self):
        self.mint.side_effect = lambda *a, **kw: (kw["minted"], {"ok": True, "id": "wrong"})
        with patch.object(fanout, "cleanup", side_effect=lambda repo, attempt, **kw: fanout.pending(attempt)):
            result = fanout.create(self.members[0], self.request)
        self.assertFalse(result["ok"])
        self.assertEqual(self.mint.call_count, 1)
        self.assertEqual(fanout.recorded("dev", stack="parent")[0].grants[1].disposition, "unknown")

    def test_lost_parent_list_retains_durable_view_and_reports_unavailability(self):
        fanout.create(self.members[0], self.request)
        with patch.object(host_share, "resolve_target", side_effect=FileNotFoundError("missing manifest")):
            result = host_share.list_shares("dev", stack="parent")
        self.assertFalse(result["ok"])
        self.assertEqual(result["partial_reasons"], ["current_parent_unavailable"])
        self.assertEqual(result["attempts"][0]["phase"], "active")
        self.assertEqual(len(result["attempts"][0]["grants"]), 2)
        self.assertEqual(result["links"], [])
        self.assertNotIn("token", str(result))

    def test_list_merges_current_legacy_links_sessions_and_attempts(self):
        fanout.create(self.members[0], self.request)
        self.capability.return_value = {"ok": True, "links": [{"id": "l-" + "e" * 12}],
                                        "sessions": [{"id": "s-" + "f" * 12}]}
        result = host_share.list_shares("dev", stack="parent")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["links"]), 1)
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(len(result["attempts"]), 1)

    def test_child_uses_its_own_named_gate_for_probe_mint_and_recovery(self):
        declaration = self.members[1].validated["login_service"]
        declaration["entries"]["child-access"] = declaration["entries"].pop("share")
        self.members[1].stack["hostnames"].append(declaration["share"]["hostname"])
        result = fanout.create(self.members[0], self.request)
        self.assertEqual(result["entry"], "share")
        self.assertEqual(self.capability.call_args_list[1].args[2], "child-access")
        self.assertEqual(self.mint.call_args_list[0].args[2].entry, "child-access")
        self.assertEqual(fanout.recorded("dev", stack="parent")[0].grants[1].entry, "child-access")

    def test_parent_refusal_does_not_hide_child_failures(self):
        self.members[0].validated["login_service"] = {}
        self.members[1].validated["login_service"]["share"]["agent_mint"] = False
        self.members[1].stack["ports"] = {"mailpit": 8025}
        self.capability.return_value = {"ok": False}
        with self.assertRaises(fanout.CoverageRefusal) as raised:
            host_share.create_share("dev", stack="parent", caller="panel")
        rows = raised.exception.gaps
        self.assertTrue(any(r["stack"] == "parent" and r["piece"] == "entry" for r in rows))
        self.assertTrue(any(r["stack"] == "child" and r["code"] == "inbox_uncovered" for r in rows))
        self.assertTrue(any(r["stack"] == "child" and r["piece"] == "opt_in" for r in rows))
        self.assertTrue(any(r["stack"] == "child" and r["piece"] == "protocol" for r in rows))
        self.install.assert_not_called()
        self.mint.assert_not_called()

    def crash_at(self, stage):
        class Crash(BaseException):
            pass
        original_prepare = ledger.AttemptRepository.prepare
        original_transition = ledger.AttemptRepository.transition
        def prepare(repo, attempt):
            value = original_prepare(repo, attempt)
            if stage == "prepare":
                raise Crash()
            return value
        def transition(repo, ident, phase):
            if stage == "active" and phase == "active":
                raise Crash()
            return original_transition(repo, ident, phase)
        def install(found, page, expiry, *, runtime=None):
            self.install_key(found, page, expiry)
            if stage == "key":
                raise Crash()
        def mint(validated, entry, request, **kwargs):
            result = self.mint_link(validated, entry, request, **kwargs)
            if validated["project"] == stage:
                raise Crash()
            return result
        with patch.object(ledger.AttemptRepository, "prepare", prepare), \
             patch.object(ledger.AttemptRepository, "transition", transition), \
             patch.object(stack_page, "install_wordpress_key", side_effect=install), \
             patch.object(host_share, "mint_link", side_effect=mint):
            with self.assertRaises(Crash):
                fanout.create(self.members[0], self.request)
        attempt = fanout.recorded("dev", stack="parent")[0]
        original_ids = tuple(g.id for g in attempt.grants)
        import sandbox.commands.hosting as commands
        import sandbox.core._remote as remote
        with patch.object(remote, "get_remote", return_value={"provisioned": True}), \
             patch.object(commands, "_remote_checked", return_value=json.dumps(
                 {"ok": True, "revoked_links": 1, "revoked_sessions": 0})):
            result = fanout.revoke_attempt(attempt)
        if stage == "key":
            self.assertFalse(result["ok"])
            self.assertEqual(result["phase"], "cleanup_pending")
            self.assertTrue(all(row["reason_code"] == "mint_completion_unknown"
                                for row in result["still_live"]))
            self.assertEqual(tuple(g.id for g in fanout.recorded("dev", stack="parent")[0].grants), original_ids)
            # A crashed process cannot prove its original writer has stopped.
            # Repeated revoke must retain the original fence and never remint.
            with patch.object(share, "mint") as allocate:
                again = fanout.create(self.members[0], self.request)
            allocate.assert_not_called()
            self.assertEqual(again["attempt_id"], attempt.id)
            self.assertFalse(again["ok"])
            return
        self.assertTrue(result["ok"])
        self.assertEqual(tuple(g.id for g in fanout.recorded("dev", stack="parent")[0].grants), original_ids)
        self.assertEqual(fanout.recorded("dev", stack="parent")[0].phase, "revoked")

    def test_crash_after_prepare_recovers_original_ids(self):
        self.crash_at("prepare")

    def test_crash_after_key_retains_original_unknown_fence(self):
        self.crash_at("key")

    def test_crash_after_child_mint_uses_durable_original_settlement(self):
        self.crash_at("child")

    def test_crash_after_parent_mint_uses_durable_original_settlement(self):
        self.crash_at("parent")

    def test_crash_before_active_recovers_original_ids(self):
        self.crash_at("active")

    def test_lost_reply_blocks_fresh_allocation_until_cleanup(self):
        self.mint.side_effect = TimeoutError("lost reply")
        with patch.object(fanout, "cleanup", side_effect=lambda repo, attempt, **kw: fanout.pending(attempt)):
            first = fanout.create(self.members[0], self.request)
            with patch.object(share, "mint") as allocate:
                again = fanout.create(self.members[0], self.request)
                allocate.assert_not_called()
        self.assertFalse(first["ok"])
        self.assertEqual(first["attempt_id"], again["attempt_id"])
        self.assertEqual(self.mint.call_count, 1)

    def test_failed_active_persistence_and_failed_rollback_return_original_ids(self):
        original = ledger.AttemptRepository.transition
        def transition(repo, ident, phase):
            if phase == "active":
                raise OSError("disk unavailable")
            return original(repo, ident, phase)
        with patch.object(ledger.AttemptRepository, "transition", transition), \
             patch.object(fanout, "cleanup", side_effect=OSError("disk unavailable")):
            result = fanout.create(self.members[0], self.request)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["still_live"]), 2)
        self.assertNotIn("token", str(result))
        self.assertNotIn("_minted", result)

    def test_same_parent_busy_lease_returns_uncertainty_without_effects(self):
        fanout.create(self.members[0], self.request)
        import sandbox.commands.hosting as commands
        with patch.object(commands, "_host_share_effects", side_effect=TimeoutError("busy")):
            reply = fanout.revoke_attempt(fanout.recorded("dev", stack="parent")[0])
        self.assertFalse(reply["ok"])
        self.assertEqual(len(reply["still_live"]), 2)
        self.assertEqual(self.mint.call_count, 2)

    def test_all_cleans_legacy_parent_ids_and_durable_without_gatewide_all(self):
        fanout.create(self.members[0], self.request)
        self.capability.return_value = {"ok": True, "links": [{"id": "l-" + "e" * 12}], "sessions": []}
        row = fanout.recorded("dev", stack="parent")[0]
        with patch.object(fanout, "revoke_attempt", return_value={"ok": True, "revoked_links": 2}), \
             patch.object(host_share, "revoke_on_gate", return_value={"revoked_links": 1,
                 "revoked_sessions": 0, "revoked_keys": 0}) as revoke:
            result = host_share.revoke_share("dev", "all", stack="parent")
        self.assertTrue(result["ok"])
        self.assertEqual(result["revoked_links"], 3)
        self.assertEqual(revoke.call_args.args[2], "l-" + "e" * 12)
        self.assertEqual(revoke.call_args.args[0].stack["slug"], row.parent.stack)

    def test_all_skipped_current_gate_never_reports_success(self):
        fanout.create(self.members[0], self.request)
        with patch.object(fanout, "revoke_attempt", return_value={"ok": True}), \
             patch.object(host_share, "resolve_target", side_effect=FileNotFoundError("manifest missing")):
            result = host_share.revoke_share("dev", "all", stack="parent")
        self.assertFalse(result["ok"])
        self.assertIn("current_parent_unavailable", result["partial_reasons"])

    def test_all_does_not_touch_other_parent_child_or_its_session(self):
        fanout.create(self.members[0], self.request)
        row = fanout.recorded("dev", stack="parent")[0]
        other_parent_id, other_child_id = "l-" + "a" * 12, "l-" + "b" * 12
        other = replace(row, id="a-" + "e" * 32,
                        parent=ledger.Parent(row.parent.target, "other"),
                        grants=(replace(row.grants[0], id=other_parent_id),
                                replace(row.grants[1], id=other_child_id)))
        other_session = "s-" + "f" * 32
        legacy = "l-" + "c" * 12
        self.capability.return_value = {"ok": True, "links": [{"id": other_parent_id},
                                                                  {"id": other_child_id},
                                                                  {"id": legacy}],
                                        "sessions": [{"id": other_session, "link": other_child_id}]}
        with patch.object(fanout, "recorded", side_effect=lambda remote, **kw: (row,) if kw.get("stack") else (row, other)), \
             patch.object(fanout, "revoke_attempt", return_value={"ok": True}) as durable, \
             patch.object(host_share, "revoke_on_gate", return_value={"revoked_links": 1,
                 "revoked_sessions": 0, "revoked_keys": 0}) as revoke:
            result = host_share.revoke_share("dev", "all", stack="parent")
        self.assertTrue(result["ok"])
        durable.assert_called_once_with(row, require_opt_in=False)
        self.assertEqual([call.args[2] for call in revoke.call_args_list], [legacy])

    def test_companion_audit_has_only_safe_identity_fields(self):
        result = fanout.create(self.members[0], self.request)
        calls = [c.kwargs for c in self.audit.call_args_list]
        self.assertTrue(any(c["target_label"] == "dev/child/dev" and c["outcome"] == "ok" for c in calls))
        self.assertNotIn(result["_minted"].token, repr(calls))
        self.assertNotIn("https://", repr(calls))
        self.assertFalse(any("hash" in c or "key" in c for c in calls))

    def test_failed_revoke_receipt_stays_pending_then_retry_finishes(self):
        fanout.create(self.members[0], self.request)
        row = fanout.recorded("dev", stack="parent")[0]
        import sandbox.commands.hosting as commands
        import sandbox.core._remote as remote
        with patch.object(remote, "get_remote", return_value={"provisioned": True}), \
             patch.object(commands, "_remote_checked", return_value="not-json"):
            result = fanout.revoke_attempt(row)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["still_live"]), 2)
        with patch.object(remote, "get_remote", return_value={"provisioned": True}), \
             patch.object(commands, "_remote_checked", return_value=json.dumps(
                 {"ok": True, "revoked_links": 1, "revoked_sessions": 0})):
            result = fanout.revoke_attempt(fanout.recorded("dev", stack="parent")[0])
        self.assertTrue(result["ok"])
        self.assertEqual(self.mint.call_count, 2)

    def test_observer_binds_both_full_container_ids_and_rejects_bad_receipt(self):
        import sandbox.commands.hosting as commands
        compose = ledger.ComposeCleanup("parent", ("/srv/deploy-src/hosts/parent/infra/compose.yml",),
                                        "/srv/override.yml", "/srv/environment.env")
        wp = ledger.WordPressCleanup("web", "/var/www/html/wp-content/mu-plugins/auto.php",
                                     "a" * 12, self.now + 200)
        receipt = {"complete": True, "epoch_start": "stable", "epoch_end": "stable",
                   "rows": [{"Service": "sandbox-login", "ID": "a" * 12},
                            {"Service": "web", "ID": "b" * 12}]}
        ids = '"' + "a" * 64 + '" "sandbox-login"\n"' + "b" * 64 + '" "web"'
        # Bypass the fixture observer mock to exercise the real bounded parser.
        with patch.object(commands, "_remote_checked", side_effect=[json.dumps(receipt), ids]):
            identity = self.real_observe({}, compose, wp)
        self.assertIsInstance(identity, ledger.RuntimeIdentity)
        receipt["epoch_end"] = "changed"
        with patch.object(commands, "_remote_checked", return_value=json.dumps(receipt)):
            with self.assertRaises(share.ShareRefusal):
                self.real_observe({}, compose, wp)

    def test_nested_compose_uses_checkout_root_and_unknown_layout_refuses(self):
        self.assertEqual(fanout._source_directory("/srv/deploy-src/hosts/parent/infra/compose.yml"),
                         "/srv/deploy-src/hosts/parent")
        with self.assertRaises(share.ShareRefusal):
            fanout._source_directory("/srv/unknown/compose.yml")

    def test_replacement_runtime_never_sends_destructive_commands(self):
        fanout.create(self.members[0], self.request)
        import sandbox.commands.hosting as commands
        import sandbox.core._remote as remote
        with patch.object(remote, "get_remote", return_value={"provisioned": True}), \
             patch.object(fanout, "inspect_services", return_value=controller.Services(
                 ledger.RuntimeIdentity("runtime_fingerprint", "b" * 64), "e" * 64, None)), \
             patch.object(commands, "_remote_checked") as execute:
            result = fanout.revoke_attempt(fanout.recorded("dev", stack="parent")[0])
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["still_live"]), 2)
        execute.assert_not_called()

    def test_panel_revoke_rechecks_running_opt_in_before_any_gate_or_key_effect(self):
        fanout.create(self.members[0], self.request)
        with patch.object(controller.BoundRuntime, "require_enabled",
                          side_effect=share.ShareRefusal("agent_mint_not_enabled", "disabled")) as enabled:
            result = fanout.revoke_attempt(fanout.recorded("dev", stack="parent")[0], require_opt_in=True)
        self.assertFalse(result["ok"])
        self.assertEqual(enabled.call_count, 2)
        self.remote_execute.assert_not_called()

    def test_gate_absence_still_requires_wordpress_key_removal(self):
        result = fanout.create(self.members[0], self.request)
        repo = fanout.repository(fanout.recorded("dev", stack="parent")[0].parent)
        # Exercise the independent acknowledgement contract on a fresh WP plan.
        parent = ledger.Parent(ledger.Target("dev", "wp", "dev"), "wp")
        wp_repo = fanout.repository(parent)
        ident = "l-" + "c" * 12
        descriptor = ledger.CleanupDescriptor("wp.example.test",
            ledger.RuntimeIdentity("runtime_fingerprint", "a" * 64),
            ledger.ComposeCleanup("sandbox-host-wp-dev", ("/srv/fixture/deploy-src/hosts/wp/compose.yml",),
                "/srv/fixture/runtime/hosts/wp/dev/compose.override.yml", "/srv/fixture/runtime/hosts/wp/dev/environment.env"),
            ledger.WordPressCleanup("web", "/var/www/html/wp-content/mu-plugins/auto.php", ident[2:], self.now + 200))
        attempt = ledger.Attempt("a-" + "d" * 32, parent, self.now, self.now + 200, "prepared",
            (ledger.Grant(parent.target, "share", ident, self.now + 200, cleanup=descriptor),))
        wp_repo.prepare(attempt)
        wp_repo.begin_mint(attempt.id, ident)
        wp_repo.update_grant(attempt.id, ident, "live")
        import sandbox.commands.hosting as commands
        import sandbox.core._remote as remote
        def execute(entry, command, **kwargs):
            if "sandbox-key-absent" in command:
                raise RuntimeError("key removal unavailable")
            raise fanout.login.LoginServiceError("site_link_unknown", "absent")
        with patch.object(remote, "get_remote", return_value={"provisioned": True}), \
             patch.object(commands, "_remote_checked", side_effect=execute):
            reply = fanout.revoke_attempt(attempt)
        self.assertFalse(reply["ok"])
        self.assertTrue(wp_repo.read(attempt.id).grants[0].gate_revoked)
        self.assertFalse(wp_repo.read(attempt.id).grants[0].wordpress_revoked)
        self.assertEqual(fanout.recorded("dev", stack="parent")[0].phase, "active")
        with patch.object(remote, "get_remote", return_value={"provisioned": True}), \
             patch.object(commands, "_remote_checked", return_value="sandbox-key-absent"):
            self.assertTrue(fanout.revoke_attempt(wp_repo.read(attempt.id))["ok"])
            self.assertTrue(fanout.revoke_attempt(wp_repo.read(attempt.id))["ok"])

    def test_legacy_unknown_with_revoke_ack_cannot_close_or_allocate_while_original_is_delayed(self):
        parent = ledger.Parent(fanout.target(self.members[0]), "parent")
        repo = fanout.repository(parent)
        ident = "l-" + "e" * 12
        safe = replace(self.descriptor(self.members[0], None, None),
            wordpress=ledger.WordPressCleanup("web", "/var/www/html/wp-content/mu-plugins/auto.php",
                                             ident[2:], self.now + 200))
        row = ledger.Attempt("a-" + "e" * 32, parent, self.now, self.now + 200, "prepared",
            (ledger.Grant(parent.target, "share", ident, self.now + 200, cleanup=safe),))
        repo.prepare(row)
        repo.begin_mint(row.id, ident)
        path = next(repo.root.glob("*.json"))
        old = json.loads(path.read_text())
        old["version"] = 2
        old["attempts"][0]["grants"][0]["gate_revoked"] = True
        del old["attempts"][0]["grants"][0]["original_settled"]
        path.write_text(json.dumps(old))
        pending = fanout.revoke_attempt(repo.read(row.id))
        self.assertFalse(pending["ok"])
        self.assertEqual(pending["still_live"][0]["reason_code"], "mint_completion_unknown")
        self.remote_execute.assert_not_called()
        # The old writer can still deliver its original result later. A new
        # request must not infer that it has finished or allocate another link.
        with patch.object(share, "mint") as allocate:
            again = fanout.create(self.members[0], self.request)
        allocate.assert_not_called()
        self.assertFalse(again["ok"])
        self.assertEqual(again["attempt_id"], row.id)
        self.assertFalse(repo.read(row.id).grants[0].original_settled)

    def test_legacy_terminal_and_mixed_attempts_keep_original_fences_after_v3_save(self):
        from dataclasses import asdict
        for case in ("non_wp", "wordpress", "mixed"):
            with self.subTest(case=case), patch.object(fanout, "root",
                    return_value=Path(self.tmp.name).resolve() / case):
                parent = ledger.Parent(fanout.target(self.members[0]), "parent")
                repo = fanout.repository(parent)
                parent_id = "l-" + "e" * 12
                safe = self.descriptor(self.members[0], None, None)
                if case == "wordpress":
                    safe = replace(safe, wordpress=ledger.WordPressCleanup("web",
                        "/var/www/html/wp-content/mu-plugins/auto.php", parent_id[2:], self.now + 200))
                grants = [ledger.Grant(parent.target, "share", parent_id, self.now + 200, cleanup=safe)]
                if case == "mixed":
                    grants.append(ledger.Grant(fanout.target(self.members[1]), "share", "l-" + "f" * 12,
                        self.now + 200, cleanup=self.descriptor(self.members[1], None, None)))
                row = ledger.Attempt("a-" + "e" * 32, parent, self.now, self.now + 200,
                                     "prepared", tuple(grants))
                repo.prepare(row)
                old = asdict(row)
                old["phase"] = "cleanup_pending" if case == "mixed" else "revoked"
                for index, grant in enumerate(old["grants"]):
                    del grant["original_settled"]
                    grant.update(disposition="unknown" if index else "revoked",
                                 gate_revoked=index == 0, wordpress_revoked=case == "wordpress")
                path = next(repo.root.glob("*.json"))
                path.write_text(json.dumps({"version": 2, "attempts": [old]}))
                saved = repo.read(row.id)
                self.assertEqual(saved.phase, "cleanup_pending")
                self.assertEqual(saved.grants[0].disposition, "unknown")
                self.assertFalse(saved.grants[0].gate_revoked)
                self.assertFalse(saved.grants[0].wordpress_revoked)
                self.assertFalse(saved.grants[0].original_settled)
                self.assertEqual(len(saved.still_live(self.now)), len(grants))
                # An unrelated save must not turn legacy terminal metadata into
                # settlement proof or let the next process skip this original.
                repo.transition(row.id, "revoking")
                self.assertEqual(json.loads(path.read_text())["version"], 3)
                self.capability.return_value = {"ok": True, "links": [], "sessions": []}
                listing = host_share.list_shares("dev", stack="parent")
                self.assertFalse(listing["ok"])
                self.assertEqual(listing["attempts"][0]["grants"][0]["status"], "unknown")
                reply = fanout.revoke_attempt(repo.read(row.id))
                self.assertFalse(reply["ok"])
                self.remote_execute.assert_not_called()
                with patch.object(share, "mint") as allocate:
                    again = fanout.create(self.members[0], self.request)
                allocate.assert_not_called()
                self.assertFalse(again["ok"])
                self.assertEqual(again["attempt_id"], row.id)
                self.assertFalse(repo.read(row.id).grants[0].original_settled)
                repo.expire(now=self.now + 200)
                self.assertEqual(repo.read(row.id).phase, "expired")
                self.assertEqual(repo.read(row.id).still_live(self.now + 200), ())

    def test_real_wordpress_descriptor_completed_mint_requires_exact_cleanup_receipt(self):
        import sandbox.commands.hosting as commands
        import sandbox.core._remote as remote

        parent = self.members[0]
        parent.validated["autologin"] = {
            "user": "admin", "service": "web", "request_path": "/wp-login.php",
            "container_path": "/var/www/html/wp-content/mu-plugins/99-sandbox-host-autologin.php"}
        parent.validated["login_service"]["share"]["route_sign_in"] = {
            "parent.example.test": "$wordpress"}
        installed, cleanup_calls = [], []
        marker = ["unconfirmed"]

        def install(found, page, expires, *, runtime=None):
            self.install_key(found, page, expires)
            if page["wordpress"]:
                installed.append((page["wordpress"], expires))
            _REAL_INSTALL_WORDPRESS_KEY(found, page, expires, runtime=runtime)

        def mint(validated, entry, request, *, minted, stack_record, on_completed=None, runtime=None):
            if validated["project"] == "parent":
                # Transport completed, but decoding/delivering its result failed.
                # This is distinct from a still-running original operation.
                on_completed()
                raise ValueError("synthetic unreadable completed mint response")
            return self.mint_link(validated, entry, request, minted=minted, stack_record=stack_record,
                                  on_completed=on_completed)

        def execute(_entry, command, **kwargs):
            if "input_data" in kwargs:
                wordpress, expires = installed[-1]
                self.assertIn(wordpress["key"], kwargs["input_data"])
                self.assertNotIn(wordpress["key"], command)
                self.assertIn(f"sandbox-share-{expires}-{wordpress['kid']}.php", command)
                return ""
            cleanup_calls.append(command)
            if "sandbox-key-absent" in command:
                return marker[0]
            return json.dumps({"ok": False, "code": "site_link_unknown"})

        with patch.object(remote, "resolve_sandbox_home", return_value="/srv/fixture"), \
                patch.object(fanout, "descriptor", side_effect=_REAL_DESCRIPTOR), \
                patch.object(stack_page, "install_wordpress_key", side_effect=install), \
                patch.object(host_share, "mint_link", side_effect=mint), \
                patch.object(commands, "_remote_checked", side_effect=execute):
            result = fanout.create(parent, self.request)

        self.assertFalse(result["ok"])
        row = fanout.recorded("dev", stack="parent")[0]
        original_ids = tuple(grant.id for grant in row.grants)
        wp = row.grants[0].cleanup.wordpress
        self.assertIsNotNone(wp)
        self.assertEqual(wp.service, "web")
        self.assertEqual(wp.key_id, installed[0][0]["kid"])
        self.assertEqual(wp.expires_at, installed[0][1])
        self.assertTrue(row.grants[0].gate_revoked)
        self.assertFalse(row.grants[0].wordpress_revoked)
        self.assertEqual(row.phase, "cleanup_pending")
        exact = f"sandbox-share-{wp.expires_at}-{wp.key_id}.php"
        self.assertTrue(any(exact in command for command in cleanup_calls))

        # Retry uses persisted original identity without a manifest. The registered
        # remote home still has to match before acquiring its physical lease.
        marker[0] = "sandbox-key-absent"
        cleanup_calls.clear()
        with patch.object(host_share, "resolve_target", side_effect=AssertionError("no manifest")), \
                patch.object(commands, "_remote_checked", side_effect=execute):
            reply = fanout.revoke_attempt(row)
            again = fanout.revoke_attempt(fanout.recorded("dev", stack="parent")[0])
        self.assertTrue(reply["ok"])
        self.assertTrue(again["ok"])
        self.assertEqual(len(cleanup_calls), 1)
        self.assertIn(exact, cleanup_calls[0])
        retained = fanout.recorded("dev", stack="parent")[0]
        self.assertEqual(tuple(grant.id for grant in retained.grants), original_ids)
        self.assertTrue(retained.grants[0].wordpress_revoked)
        self.assertEqual(retained.phase, "revoked")

    def test_prepared_wordpress_plan_closes_locally_without_remote_effects(self):
        parent = ledger.Parent(fanout.target(self.members[0]), "parent")
        repo = fanout.repository(parent)
        ident = "l-" + "c" * 12
        descriptor = replace(self.descriptor(self.members[0], None, None),
            wordpress=ledger.WordPressCleanup("web", "/var/www/html/wp-content/mu-plugins/auto.php",
                                              ident[2:], self.now + 200))
        attempt = ledger.Attempt("a-" + "d" * 32, parent, self.now, self.now + 200, "prepared",
            (ledger.Grant(parent.target, "share", ident, self.now + 200, cleanup=descriptor),))
        repo.prepare(attempt)
        result = fanout.revoke_attempt(attempt)
        self.assertTrue(result["ok"])
        saved = repo.read(attempt.id)
        self.assertEqual(saved.phase, "revoked")
        self.assertEqual(saved.grants[0].id, ident)
        self.assertEqual(saved.grants[0].disposition, "revoked")
        self.assertTrue(saved.grants[0].gate_revoked)
        self.assertTrue(saved.grants[0].wordpress_revoked)
        self.remote_entry.assert_not_called()
        self.remote_execute.assert_not_called()
        self.install.assert_not_called()
        self.mint.assert_not_called()

    def test_effect_helper_releases_shared_state_before_body(self):
        import sandbox.commands.hosting as commands
        events = []
        class Repository:
            def target_mutation_port(self, capability):
                self.assertion = capability
                return self
            @contextlib.contextmanager
            def target_mutation_transaction(self, key):
                events.append(("acquire", key))
                yield
                events.append(("release", key))
            @contextlib.contextmanager
            def state_lock(self):
                events.append(("state", "enter"))
                yield
                events.append(("state", "exit"))
        with patch.object(commands, "RecoveryRepository", side_effect=Repository), \
             patch.object(commands.hosting, "load_host_state", return_value=self.state):
            with self.real_effects([fanout.target(self.members[0]), fanout.target(self.members[1]),
                                    fanout.target(self.members[0])]):
                self.assertEqual(events[-1], ("state", "exit"))
                self.assertEqual([event[1] for event in events if event[0] == "acquire"],
                                 ["dev/child/dev", "dev/parent/dev"])
            self.assertEqual([event[1] for event in events if event[0] == "release"],
                             ["dev/parent/dev", "dev/child/dev"])
