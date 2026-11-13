"""Spec 088 attempt recovery behavior. No remote, gate, or subprocess runs."""
import copy
import json
import os
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from sandbox.hosting.share.attempts import (
    Attempt, AttemptError, AttemptRepository, Grant, Parent, Target,
    CleanupDescriptor, ComposeCleanup, RuntimeIdentity, WordPressCleanup,
)


class AttemptRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "attempts"
        self.parent = Parent(Target("personal", "website", "dev"), "website-dev")
        self.companion = Target("personal", "assets", "dev")
        self.repo = AttemptRepository(self.root, self.parent)
        self.cleanup = CleanupDescriptor(
            "share.example.com", RuntimeIdentity("copy_id", "1234abcd"),
            ComposeCleanup("sandbox-website-dev", ("/srv/source/compose.yml",),
                           "/srv/runtime/compose.override.yml", "/srv/runtime/environment.env"))
        self.plan = Attempt(
            "a-" + "a" * 32, self.parent, 100, 1000, "prepared",
            (Grant(self.parent.target, "share", "l-" + "1" * 12, 1000, cleanup=self.cleanup),
             Grant(self.companion, "share", "l-" + "2" * 32, 1000, cleanup=self.cleanup)),
        )

    def restart(self):
        return AttemptRepository(self.root, self.parent)


    def wordpress_plan(self):
        grant = self.plan.grants[0]
        wp = WordPressCleanup("wordpress", "/var/www/html/wp-content/mu-plugins/login.php",
                              grant.id[2:], grant.expires_at)
        return replace(self.plan, grants=(replace(grant, cleanup=replace(self.cleanup, wordpress=wp)),))

    def test_restart_lookup_by_parent_link_without_parent_resolution(self):
        plan = self.wordpress_plan()
        self.repo.prepare(plan)
        self.repo.begin_mint(plan.id, plan.grants[0].id)
        found = self.restart().find_by_parent_link(plan.grants[0].id)
        self.assertEqual(found.grants[0].cleanup, plan.grants[0].cleanup)
        self.assertEqual(self.restart().list(), (found,))
        self.assertEqual(AttemptRepository.list_recorded(self.root), (found,))
        self.assertEqual(AttemptRepository.find_recorded_parent_link(self.root, plan.grants[0].id), found)
        self.assertIsNone(self.restart().find_by_parent_link("l-" + "f" * 12))

    def test_global_lookup_refuses_cross_parent_link_collision(self):
        self.repo.prepare(self.plan)
        other_parent = replace(self.parent, stack="other")
        AttemptRepository(self.root, other_parent).prepare(replace(self.plan, parent=other_parent))
        self.assertEqual(len(AttemptRepository.list_recorded(self.root)), 2)
        with self.assertRaises(AttemptError):
            AttemptRepository.find_recorded_parent_link(self.root, self.plan.grants[0].id)

    def test_same_slug_replacement_identity_fails_closed(self):
        self.repo.prepare(self.plan)
        with self.assertRaises(AttemptError):
            self.restart().cleanup_for(self.plan.id, self.plan.grants[0].id,
                                       RuntimeIdentity("copy_id", "87654321"))
        self.assertEqual(self.repo.cleanup_for(self.plan.id, self.plan.grants[0].id,
                                              self.cleanup.identity), self.cleanup)
        changed = replace(self.plan, grants=(replace(self.plan.grants[0], cleanup=replace(
            self.cleanup, identity=RuntimeIdentity("copy_id", "87654321"))), self.plan.grants[1]))
        with self.assertRaises(AttemptError):
            self.repo.prepare(changed)
        self.assertEqual(self.repo.read(self.plan.id), self.plan)

    def test_gate_cleanup_does_not_discharge_pending_wordpress_key(self):
        plan = self.wordpress_plan()
        gid = plan.grants[0].id
        self.repo.prepare(plan)
        self.repo.begin_mint(plan.id, gid)  # MUST precede key installation too.
        self.repo.transition(plan.id, "cleanup_pending")
        self.repo.confirm_original_settled(plan.id, gid)
        self.repo.update_grant(plan.id, gid, "revoked")
        self.repo.update_grant(plan.id, gid, "revoked")
        saved = self.restart().read(plan.id)
        self.assertTrue(saved.grants[0].gate_revoked)
        self.assertEqual(saved.grants[0].disposition, "unknown")
        self.assertEqual(saved.still_live(500), saved.grants)
        with self.assertRaises(AttemptError):
            self.repo.prepare(replace(plan, id="a-" + "b" * 32))
        with self.assertRaises(AttemptError):
            self.repo.transition(plan.id, "revoked")
        with self.assertRaises(AttemptError):
            self.repo.begin_mint(plan.id, gid)
        self.restart().confirm_wordpress_removed(plan.id, gid)
        self.repo.confirm_wordpress_removed(plan.id, gid)
        self.repo.transition(plan.id, "revoked")
        self.assertEqual(self.restart().read(plan.id).still_live(500), ())

    def test_wordpress_cleanup_first_and_protocol_expiry(self):
        plan = self.wordpress_plan()
        gid = plan.grants[0].id
        self.repo.prepare(plan)
        self.repo.begin_mint(plan.id, gid)
        self.repo.confirm_wordpress_removed(plan.id, gid)
        self.assertEqual(self.repo.read(plan.id).grants[0].disposition, "unknown")
        self.repo.expire(now=1000)
        self.assertEqual(self.repo.read(plan.id).phase, "expired")
        self.assertEqual(self.repo.prepare(plan).phase, "expired")

    def test_pending_wordpress_key_discharges_only_at_enforced_expiry(self):
        plan = self.wordpress_plan()
        self.repo.prepare(plan)
        self.repo.begin_mint(plan.id, plan.grants[0].id)
        self.repo.transition(plan.id, "cleanup_pending")
        self.repo.confirm_original_settled(plan.id, plan.grants[0].id)
        self.repo.update_grant(plan.id, plan.grants[0].id, "revoked")
        self.repo.expire(now=999)
        self.assertEqual(self.repo.read(plan.id).phase, "cleanup_pending")
        self.assertEqual(len(self.repo.read(plan.id).still_live(999)), 1)
        self.repo.expire(now=1000)
        self.assertEqual(self.repo.read(plan.id).phase, "expired")
        self.assertFalse(self.repo.read(plan.id).grants[0].wordpress_revoked)
        self.assertEqual(self.repo.read(plan.id).still_live(1000), ())

    def test_missing_descriptor_cannot_begin_effects(self):
        plan = replace(self.plan, grants=(replace(self.plan.grants[0], cleanup=None),))
        self.repo.prepare(plan)
        with self.assertRaises(AttemptError):
            self.repo.begin_mint(plan.id, plan.grants[0].id)
        self.assertEqual(self.repo.read(plan.id).phase, "prepared")

    def test_cleanup_descriptor_exact_allowlist_and_canonical_fields(self):
        self.repo.prepare(self.wordpress_plan())
        path = next(self.root.glob("*.json"))
        original = json.loads(path.read_text())
        self.assertEqual(original["version"], 3)
        self.assertEqual(self.cleanup.gate_origin, "https://share.example.com")
        variants = []
        for field in ("token", "token_sha256", "key", "email", "url", "manifest", "command", "origin"):
            bad = copy.deepcopy(original)
            bad["attempts"][0]["grants"][0]["cleanup"][field] = "forbidden"
            variants.append(bad)
        for section in ("identity", "compose", "wordpress"):
            for field in ("token", "token_sha256", "key", "email", "url", "manifest", "command"):
                bad = copy.deepcopy(original)
                bad["attempts"][0]["grants"][0]["cleanup"][section][field] = "forbidden"
                variants.append(bad)
        for field, value in (("gate_hostname", "https://share.example.com"),
                             ("gate_hostname", "127.0.0.1"), ("gate_hostname", "Share.example.com"),
                             ("identity", {"kind": "copy_id", "value": "same-slug"})):
            bad = copy.deepcopy(original)
            bad["attempts"][0]["grants"][0]["cleanup"][field] = value
            variants.append(bad)
        for section, field, value in (("compose", "files", ["/srv/../secret"]),
                                      ("compose", "env_path", "/srv/$(cmd)"),
                                      ("wordpress", "key_id", "f" * 12),
                                      ("wordpress", "expires_at", 1001),
                                      ("wordpress", "protocol", "unknown")):
            bad = copy.deepcopy(original)
            bad["attempts"][0]["grants"][0]["cleanup"][section][field] = value
            variants.append(bad)
        bad = copy.deepcopy(original)
        bad["attempts"][0]["phase"] = "minting"
        bad["attempts"][0]["grants"][0]["disposition"] = "unknown"
        bad["attempts"][0]["grants"][0]["cleanup"] = None
        variants.append(bad)
        old = copy.deepcopy(original)
        old["version"] = 1
        variants.append(old)
        for bad in variants:
            path.write_text(json.dumps(bad))
            with self.subTest(bad=bad), self.assertRaises(AttemptError):
                self.restart().list()

    def test_original_settlement_survives_restart_without_inferring_it_from_revoke(self):
        plan = self.wordpress_plan()
        self.repo.prepare(plan)
        self.repo.begin_mint(plan.id, plan.grants[0].id)
        with self.assertRaises(AttemptError):
            self.repo.update_grant(plan.id, plan.grants[0].id, "revoked")
        self.assertFalse(self.restart().read(plan.id).grants[0].original_settled)
        self.repo.confirm_original_settled(plan.id, plan.grants[0].id)
        self.assertTrue(self.restart().read(plan.id).grants[0].original_settled)

    def test_legacy_v2_revoke_bit_never_promotes_settlement_on_read_or_save(self):
        plan = self.wordpress_plan()
        self.repo.prepare(plan)
        self.repo.begin_mint(plan.id, plan.grants[0].id)
        path = next(self.root.glob("*.json"))
        document = json.loads(path.read_text())
        document["version"] = 2
        document["attempts"][0]["grants"][0]["gate_revoked"] = True
        for grant in document["attempts"][0]["grants"]:
            del grant["original_settled"]
        path.write_text(json.dumps(document))
        saved = self.restart().read(plan.id)
        self.assertFalse(saved.grants[0].gate_revoked)
        self.assertFalse(saved.grants[0].original_settled)
        self.repo.transition(plan.id, "cleanup_pending")
        self.assertFalse(self.restart().read(plan.id).grants[0].original_settled)
        self.assertEqual(json.loads(path.read_text())["version"], 3)

    def test_preallocation_survives_interrupted_mint_and_restart(self):
        self.repo.prepare(self.plan)
        self.repo.begin_mint(self.plan.id, self.plan.grants[1].id)
        restarted = self.restart()
        recovered = restarted.read(self.plan.id)
        self.assertEqual(recovered.grants[1].disposition, "unknown")
        self.assertEqual(recovered.grants[0].id, self.plan.grants[0].id)
        self.assertEqual(restarted.list(), (recovered,))
        self.assertEqual(recovered.still_live(500), (recovered.grants[1],))
        # The landed gate refuses duplicate ids. Unknown is never mint replay.
        with self.assertRaises(AttemptError):
            restarted.begin_mint(self.plan.id, self.plan.grants[1].id)
        with self.assertRaises(AttemptError):
            restarted.prepare(replace(self.plan, id="a-" + "b" * 32))
        # An acknowledgement can be reconciled using the original stable id.
        restarted.update_grant(self.plan.id, self.plan.grants[1].id, "live")
        self.assertEqual(restarted.read(self.plan.id).grants[1].id, self.plan.grants[1].id)

    def test_missing_parent_does_not_discharge_companion(self):
        self.repo.prepare(self.plan)
        for grant in self.plan.grants:
            self.repo.begin_mint(self.plan.id, grant.id)
            self.repo.update_grant(self.plan.id, grant.id, "live")
        self.repo.transition(self.plan.id, "active")
        self.repo.transition(self.plan.id, "revoking")
        self.repo.update_grant(self.plan.id, self.plan.grants[0].id, "revoked")
        self.repo.transition(self.plan.id, "cleanup_pending")
        result = self.restart().read(self.plan.id)
        self.assertEqual(result.still_live(500), (result.grants[1],))
        with self.assertRaises(AttemptError):
            self.repo.transition(self.plan.id, "revoked")
        self.repo.update_grant(self.plan.id, self.plan.grants[1].id, "revoked")
        self.repo.transition(self.plan.id, "revoked")
        self.assertEqual(self.repo.read(self.plan.id).still_live(500), ())
        self.assertEqual(len(self.repo.read(self.plan.id).grants), 2)

    def test_cleanup_retry_and_safe_same_id_replay(self):
        self.repo.prepare(self.plan)
        self.repo.begin_mint(self.plan.id, self.plan.grants[0].id)
        self.repo.transition(self.plan.id, "cleanup_pending")
        self.repo.transition(self.plan.id, "cleanup_pending")
        self.assertEqual(self.repo.prepare(self.plan), self.repo.read(self.plan.id))
        self.repo.confirm_original_settled(self.plan.id, self.plan.grants[0].id)
        for grant in self.plan.grants:
            self.repo.update_grant(self.plan.id, grant.id, "revoked")
            self.repo.update_grant(self.plan.id, grant.id, "revoked")
        self.repo.transition(self.plan.id, "revoked")
        self.repo.transition(self.plan.id, "revoked")
        self.assertEqual(self.restart().prepare(self.plan).phase, "revoked")
        with self.assertRaises(AttemptError):
            self.repo.prepare(replace(self.plan, expires_at=999))
        with self.assertRaises(AttemptError):
            self.repo.transition(self.plan.id, "minting")

    def test_independent_parents_and_sorted_target_locks(self):
        other_parent = replace(self.parent, stack="another-stack")
        other = AttemptRepository(self.root, other_parent)
        with self.repo.locked():
            self.repo.prepare(self.plan)
            other.prepare(replace(self.plan, parent=other_parent))
            self.assertEqual(self.repo.read(self.plan.id).target_order,
                             (self.companion, self.parent.target))
            with self.assertRaises(AttemptError):
                with self.restart().locked():
                    self.fail("same parent lock was not exclusive")
        self.repo.begin_mint(self.plan.id, self.plan.grants[0].id)
        self.assertEqual(other.read(self.plan.id).phase, "prepared")

    def test_expiry_keeps_terminal_records_and_grant_ids(self):
        self.repo.prepare(self.plan)
        self.repo.begin_mint(self.plan.id, self.plan.grants[1].id)
        self.repo.expire(now=999)
        self.assertEqual(self.repo.read(self.plan.id).phase, "minting")
        self.repo.expire(now=1000)
        self.repo.expire(now=1000)
        record = self.restart().read(self.plan.id)
        self.assertEqual(record.phase, "expired")
        self.assertEqual(record.grants[1].id, self.plan.grants[1].id)
        self.assertEqual(record.still_live(1000), ())
        self.repo.update_grant(self.plan.id, self.plan.grants[1].id, "revoked")
        self.assertEqual(self.repo.read(self.plan.id), record)

    def test_failed_durable_commit_never_reports_mint_ready(self):
        self.repo.prepare(self.plan)
        with patch("sandbox.hosting.share.attempts.os.replace", side_effect=OSError):
            with self.assertRaises(AttemptError):
                self.repo.begin_mint(self.plan.id, self.plan.grants[0].id)
        self.assertEqual(self.restart().read(self.plan.id), self.plan)
        self.assertFalse(tuple(self.root.glob("*.tmp")))

    def test_same_id_changed_identity_is_refused(self):
        self.repo.prepare(self.plan)
        changed = replace(self.plan, grants=(self.plan.grants[0],
                          replace(self.plan.grants[1], id="l-" + "3" * 12)))
        with self.assertRaises(AttemptError):
            self.repo.prepare(changed)
        self.assertEqual(self.restart().read(self.plan.id), self.plan)

    def test_invalid_values_and_secret_fields_refused(self):
        for field in ("token", "token_sha256", "address", "extensions"):
            value = asdict(self.plan)
            value[field] = "canary-value"
            with self.subTest(field=field), self.assertRaises(AttemptError) as raised:
                self.repo.prepare(value)
            self.assertNotIn("canary", str(raised.exception))
        bad_plans = (
            replace(self.plan, created_at=True),
            replace(self.plan, expires_at=100),
            replace(self.plan, phase="active"),
            replace(self.plan, grants=()),
            replace(self.plan, grants=self.plan.grants * 3),
            replace(self.plan, grants=(self.plan.grants[0], self.plan.grants[0])),
            replace(self.plan, grants=(replace(self.plan.grants[0], id="../escape"),)),
            replace(self.plan, grants=(replace(self.plan.grants[0], id="l-" + "f" * 64),)),
            replace(self.plan, grants=(replace(self.plan.grants[0], entry="https://secret"),)),
        )
        for plan in bad_plans:
            with self.subTest(plan=plan), self.assertRaises(AttemptError):
                self.repo.prepare(plan)

    def test_disk_schema_is_exact_and_bounded(self):
        self.repo.prepare(self.plan)
        path = next(self.root.glob("*.json"))
        original = json.loads(path.read_text())
        variants = []
        secret = copy.deepcopy(original)
        secret["attempts"][0]["grants"][0]["token"] = "canary-value"
        variants.append(json.dumps(secret))
        secret = copy.deepcopy(original)
        secret["attempts"][0]["parent"]["target"]["address"] = "canary-value"
        variants.append(json.dumps(secret))
        variants.extend(("{}", '{"version":1,"version":1,"attempts":[]}',
                         "[", " " * (1024 * 1024 + 1)))
        for value in variants:
            path.write_text(value)
            with self.subTest(size=len(value)), self.assertRaises(AttemptError):
                self.restart().list()

    def test_symlink_paths_and_records_refused_without_touching_destination(self):
        self.repo.prepare(self.plan)
        path = next(self.root.glob("*.json"))
        outside = self.root.parent / "outside"
        outside.write_text("untouched")
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(AttemptError):
            self.repo.list()
        self.assertEqual(outside.read_text(), "untouched")
        alias = self.root.parent / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(AttemptError):
            AttemptRepository(alias / "nested", self.parent).list()
        self.assertFalse((self.root / "nested").exists())

    def test_owner_only_modes_and_invalid_state_transitions(self):
        self.repo.prepare(self.plan)
        self.assertEqual(os.stat(self.root).st_mode & 0o777, 0o700)
        for path in self.root.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(AttemptError):
            self.repo.transition(self.plan.id, "active")
        with self.assertRaises(AttemptError):
            self.repo.update_grant(self.plan.id, self.plan.grants[0].id, "live")
        with self.assertRaises(AttemptError):
            self.repo.read("../escape")


if __name__ == "__main__":
    unittest.main()
