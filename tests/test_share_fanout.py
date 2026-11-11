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
from sandbox.hosting.share import attempts as ledger, fanout, stack_page

# Capture the production boundary before each fixture replaces transport/mint ports.
_REAL_MINT_LINK = host_share.mint_link


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
        self.remote_execute = mock(commands, "_remote_checked",
                                   side_effect=AssertionError("unexpected remote execution"))
        self.real_effects = commands._host_share_effects
        @contextlib.contextmanager
        def effects(targets):
            self.events.append(("locks", tuple(targets)))
            yield self.state
        mock(commands, "_host_share_effects", side_effect=effects)
        mock(hosting, "load_host_state", return_value=self.state)
        mock(stack_registry, "read", return_value=self.registry)
        mock(hosting, "validate_manifest", side_effect=lambda directory, env: next(
            member.validated for member in self.members if member.project_dir == directory))
        mock(host_share, "resolve_target", side_effect=lambda remote, *, stack=None, target=None,
             name=None, project_dir=None, environment=None: next(
            member for member in self.members if member.stack["slug"] == stack))
        self.capability = mock(host_share, "_remote_site", return_value={"ok": True, "capabilities": sorted(fanout.CAPABILITIES)})
        mock(fanout.login, "assert_applicable")
        mock(fanout.login, "enabled_until_epoch", return_value=self.now + 200)
        mock(fanout, "descriptor", side_effect=self.descriptor)
        self.real_observe = fanout.observe
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
                     "routes": [{"hostname": f"{slug}.example.test", "primary": True}]}
        record = {"remote": "dev", "slug": slug, "project": slug, "environment": "dev", "uses": uses,
                  "hostnames": [f"{slug}.example.test"]}
        return host_share.ShareTarget("dev", validated, {"provisioned": True}, slug + "/dev", "/src/" + slug, record)

    def descriptor(self, found, minted, page):
        return ledger.CleanupDescriptor(found.validated["login_service"]["share"]["hostname"],
            ledger.RuntimeIdentity("runtime_fingerprint", "a" * 64),
            ledger.ComposeCleanup(found.stack["slug"], ("/srv/source/compose.yml",), "/srv/override.yml", "/srv/environment.env"))

    def install_key(self, found, page, expiry):
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

    def mint_link(self, validated, entry, request, *, minted, stack_record):
        self.events.append(("mint", validated["project"], minted, stack_record))
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

        def transport(validated, _remote, entry, verb, *, input_data):
            self.assertEqual((entry, verb), ("share", "mint"))
            envelope = json.loads(input_data)
            sent.append((validated["project"], envelope))
            self.assertEqual(set(envelope), {"link", "stack"})
            config = {**validated["login_service"], "enabled_until_epoch": self.now + 200}
            output = io.StringIO()
            code = runtime.manage(config, [verb, entry],
                Path(self.tmp.name) / "gate" / validated["project"],
                stdin=io.StringIO(input_data), stdout=output, clock=lambda: self.now)
            self.assertEqual(code, 0)
            reply = json.loads(output.getvalue())
            self.assertTrue(reply["ok"], reply)
            return reply

        def real_mint(validated, entry, request, *, minted, stack_record):
            actual, reply = _REAL_MINT_LINK(validated, entry, request,
                                           minted=minted, stack_record=stack_record)
            self.assertIs(actual, minted)
            returned.append(actual)
            return actual, reply

        with patch.object(host_share, "mint_link", side_effect=real_mint), \
                patch.object(commands, "_site_remote", side_effect=transport), \
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
        with patch.object(fanout, "cleanup", side_effect=lambda repo, attempt: fanout.pending(attempt)):
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
        def install(found, page, expiry):
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
        self.assertTrue(result["ok"])
        self.assertEqual(tuple(g.id for g in fanout.recorded("dev", stack="parent")[0].grants), original_ids)
        self.assertEqual(fanout.recorded("dev", stack="parent")[0].phase, "revoked")

    def test_crash_after_prepare_recovers_original_ids(self):
        self.crash_at("prepare")

    def test_crash_after_key_recovers_original_ids(self):
        self.crash_at("key")

    def test_crash_after_child_mint_recovers_original_ids(self):
        self.crash_at("child")

    def test_crash_after_parent_mint_recovers_original_ids(self):
        self.crash_at("parent")

    def test_crash_before_active_recovers_original_ids(self):
        self.crash_at("active")

    def test_lost_reply_blocks_fresh_allocation_until_cleanup(self):
        self.mint.side_effect = TimeoutError("lost reply")
        with patch.object(fanout, "cleanup", side_effect=lambda repo, attempt: fanout.pending(attempt)):
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
        durable.assert_called_once_with(row)
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
             patch.object(fanout, "observe", return_value=ledger.RuntimeIdentity("runtime_fingerprint", "b" * 64)), \
             patch.object(commands, "_remote_checked") as execute:
            result = fanout.revoke_attempt(fanout.recorded("dev", stack="parent")[0])
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["still_live"]), 2)
        execute.assert_not_called()

    def test_gate_absence_still_requires_wordpress_key_removal(self):
        result = fanout.create(self.members[0], self.request)
        repo = fanout.repository(fanout.recorded("dev", stack="parent")[0].parent)
        # Exercise the independent acknowledgement contract on a fresh WP plan.
        parent = ledger.Parent(ledger.Target("dev", "wp", "dev"), "wp")
        wp_repo = fanout.repository(parent)
        ident = "l-" + "c" * 12
        descriptor = ledger.CleanupDescriptor("wp.example.test",
            ledger.RuntimeIdentity("runtime_fingerprint", "a" * 64),
            ledger.ComposeCleanup("wp", ("/srv/compose.yml",), "/srv/override.yml", "/srv/environment.env"),
            ledger.WordPressCleanup("web", "/var/www/html/wp-content/mu-plugins/auto.php", ident[2:], self.now + 200))
        attempt = ledger.Attempt("a-" + "d" * 32, parent, self.now, self.now + 200, "prepared",
            (ledger.Grant(parent.target, "share", ident, self.now + 200, cleanup=descriptor),))
        wp_repo.prepare(attempt)
        wp_repo.begin_mint(attempt.id, ident)
        import sandbox.commands.hosting as commands
        import sandbox.core._remote as remote
        def execute(entry, command, **kwargs):
            if " exec -T web " in command:
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
