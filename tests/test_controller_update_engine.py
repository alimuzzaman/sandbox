"""Controller auto-update engine against temporary git repositories (spec 072).

Covers US1 (fast-forward), US2 (untrusted hints), US3 (unsafe or pinned
checkouts) and US4 (rollback). No network: the GitHub origin URL is mapped to
a local bare repository with ``url.<base>.insteadOf``.
"""
import fcntl
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.controller_update import engine, state  # noqa: E402
from sandbox.controller_update.models import (  # noqa: E402
    AlreadyMoved, NoAction, Policy, RolledBack, Skip, SkipReason, Stranded, Updated,
)
from tests.controller_update_support import GITHUB_URL, Repos  # noqa: E402

FAST = Policy(lock_wait=0.2, fetch_timeout=20.0)


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.repos = Repos()
        self.addCleanup(self.repos.cleanup)
        self.home = self.repos.base / "home"
        self.state_path = self.home / "runtime" / "controller-update" / "state.json"
        self.lock_dir = self.home / "runtime" / "locks"
        env_patch = patch.dict(os.environ, self.repos.process_env())
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.refreshes = []

    def hint(self, revision, remote="demo", now=None):
        self.assertTrue(state.record_hint(remote, revision, "probe", path=self.state_path, now=now))

    def refresher_ok(self, root, policy):
        self.refreshes.append(Path(root))
        return None

    def run_engine(self, *, refresher=None, policy=FAST, is_enabled=None, now=None, root=None):
        return engine.run(
            root or self.repos.ctl, state_path=self.state_path, lock_dir=self.lock_dir,
            policy=policy, refresher=refresher or self.refresher_ok,
            is_enabled=is_enabled or (lambda: True), now=now,
        )


class FastForwardTests(EngineCase):
    """US1: catch up with a newer published remote."""

    def test_fast_forwards_to_the_exact_hint_not_past_the_upstream_tip(self):
        c2 = self.repos.publish("b.txt", "two\n", "c2")
        self.repos.publish("c.txt", "three\n", "c3")
        self.hint(c2)
        decision = self.run_engine()
        self.assertIsInstance(decision, Updated)
        self.assertEqual((decision.old, decision.new), (self.repos.c1, c2))
        self.assertEqual(decision.upstream, "origin/main")
        self.assertEqual(self.repos.head(), c2)
        self.assertEqual(self.refreshes, [self.repos.ctl.resolve()])
        status = self.repos.git(self.repos.ctl, "status", "--porcelain")
        self.assertEqual(status, "")

    def test_equal_or_older_hint_is_silent_and_never_fetches(self):
        self.hint(self.repos.c1)
        self.repos.unmap_origin()  # any fetch would now fail
        decision = self.run_engine()
        self.assertEqual(decision, NoAction(SkipReason.NO_HINT))
        c2 = self.repos.publish()
        self.repos.map_origin(self.repos.bare)
        self.repos.fetch_ctl()
        self.repos.git(self.repos.ctl, "merge", "-q", "--ff-only", c2)
        self.repos.unmap_origin()
        self.hint(self.repos.c1)  # remote is older than the checkout
        self.assertEqual(self.run_engine(), NoAction(SkipReason.NO_HINT))
        self.assertEqual(self.repos.head(), c2)

    def test_no_state_file_is_silent(self):
        self.assertEqual(self.run_engine(), NoAction(SkipReason.NO_HINT))

    def test_local_branch_with_a_differently_named_upstream(self):
        c2 = self.repos.publish()
        self.repos.git(self.repos.ctl, "checkout", "-q", "-b", "installed")
        self.repos.git(self.repos.ctl, "branch", "-q", "-u", "origin/main", "installed")
        self.hint(c2)
        decision = self.run_engine()
        self.assertIsInstance(decision, Updated)
        self.assertEqual(self.repos.head(), c2)
        self.assertEqual(self.repos.git(self.repos.ctl, "rev-parse", "--abbrev-ref", "HEAD"),
                         "installed")

    def test_newest_trusted_hint_wins_across_remotes(self):
        c2 = self.repos.publish("b.txt", "two\n", "c2")
        c3 = self.repos.publish("c.txt", "three\n", "c3")
        self.hint(c2, remote="alpha", now=200.0)
        self.hint(c3, remote="beta", now=100.0)
        decision = self.run_engine()
        self.assertIsInstance(decision, Updated)
        self.assertEqual(decision.new, c3)

    def test_mcp_requirements_change_adds_a_notice(self):
        c2 = self.repos.publish("mcp/wp-server/requirements.txt", "mcp==2\n", "deps")
        self.hint(c2)
        decision = self.run_engine()
        self.assertIsInstance(decision, Updated)
        self.assertTrue(any("mcp-install" in line for line in decision.notices))

    def test_unrelated_change_has_no_mcp_notice(self):
        c2 = self.repos.publish()
        self.hint(c2)
        self.assertEqual(self.run_engine().notices, ())

    def test_disabled_by_policy_callback_is_silent(self):
        c2 = self.repos.publish()
        self.hint(c2)
        decision = self.run_engine(is_enabled=lambda: False)
        self.assertEqual(decision, NoAction(SkipReason.DISABLED))
        self.assertEqual(self.repos.head(), self.repos.c1)


class TrustTests(EngineCase):
    """US2: a server can never choose what code the controller runs."""

    def test_revision_unknown_to_origin_is_refused(self):
        self.hint("d" * 40)
        decision = self.run_engine()
        self.assertIsInstance(decision, Skip)
        self.assertIs(decision.reason, SkipReason.NOT_ON_UPSTREAM)
        self.assertIn("not on origin/main", decision.notice)
        self.assertEqual(self.repos.head(), self.repos.c1)

    def test_commit_on_another_origin_branch_is_refused_even_when_known_locally(self):
        other = self.repos.publish("x.txt", "side\n", "side", branch="feature")
        self.repos.fetch_ctl()  # the object now exists locally
        self.hint(other)
        decision = self.run_engine()
        self.assertIsInstance(decision, Skip)
        self.assertIs(decision.reason, SkipReason.NOT_ON_UPSTREAM)
        self.assertEqual(self.repos.head(), self.repos.c1)

    def test_commit_only_in_a_foreign_repository_is_refused(self):
        foreign = Repos(self.repos.base / "foreign")
        self.addCleanup(foreign.cleanup)
        sha = foreign.publish("evil.txt", "x\n", "evil")
        # Even with the object copied into the checkout, it is not on upstream.
        self.repos.git(self.repos.ctl, "fetch", "-q", str(foreign.bare), sha)
        self.hint(sha)
        decision = self.run_engine()
        self.assertIsInstance(decision, Skip)
        self.assertIs(decision.reason, SkipReason.NOT_ON_UPSTREAM)
        self.assertEqual(self.repos.head(), self.repos.c1)

    def test_diverged_history_is_refused_as_not_a_fast_forward(self):
        c2 = self.repos.publish()
        self.repos.commit(self.repos.ctl, "local.txt", "mine\n", "local work")
        before = self.repos.head()
        self.hint(c2)
        decision = self.run_engine()
        self.assertIsInstance(decision, Skip)
        self.assertIs(decision.reason, SkipReason.NOT_DESCENDANT)
        self.assertEqual(self.repos.head(), before)

    def test_malformed_hint_never_fetches(self):
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text(
            '{"schema_version":1,"hints":{"demo":{"revision":"DEADBEEF","source":"probe",'
            '"recorded_at":1}}}', encoding="utf-8")
        self.repos.unmap_origin()
        self.assertEqual(self.run_engine(), NoAction(SkipReason.NO_HINT))

    def test_fetch_failure_is_reported_once_then_memoized(self):
        c2 = self.repos.publish()
        self.hint(c2)
        self.repos.unmap_origin()
        self.repos.map_origin(self.repos.base / "missing.git")
        first = self.run_engine(now=1000.0)
        self.assertIsInstance(first, Skip)
        self.assertIs(first.reason, SkipReason.FETCH_FAILED)
        self.assertIn("could not fetch origin/main", first.notice)
        second = self.run_engine(now=1000.0 + FAST.fetch_failure_ttl - 1)
        self.assertEqual(second, NoAction(SkipReason.MEMOIZED))
        third = self.run_engine(now=1000.0 + FAST.fetch_failure_ttl + 1)
        self.assertIsInstance(third, Skip)
        self.assertEqual(self.repos.head(), self.repos.c1)

    def test_refusal_is_memoized_for_six_hours(self):
        self.hint("d" * 40)
        self.assertIsInstance(self.run_engine(now=1000.0), Skip)
        self.repos.unmap_origin()  # a refetch inside the window would fail loudly
        self.assertEqual(self.run_engine(now=1000.0 + 6 * 3600 - 1),
                         NoAction(SkipReason.MEMOIZED))

    def test_non_github_origin_is_refused(self):
        c2 = self.repos.publish()
        self.repos.fetch_ctl()
        self.repos.git(self.repos.ctl, "remote", "set-url", "origin", str(self.repos.bare))
        self.hint(c2)
        decision = self.run_engine()
        self.assertIsInstance(decision, Skip)
        self.assertIs(decision.reason, SkipReason.ORIGIN_NOT_GITHUB)
        self.assertEqual(self.repos.head(), self.repos.c1)

    def test_upstream_on_a_remote_other_than_origin_is_refused(self):
        c2 = self.repos.publish()
        self.repos.git(self.repos.ctl, "remote", "add", "mirror", GITHUB_URL)
        self.repos.git(self.repos.ctl, "fetch", "-q", "mirror")
        self.repos.git(self.repos.ctl, "branch", "-q", "-u", "mirror/main", "main")
        self.hint(c2)
        decision = self.run_engine()
        self.assertIsInstance(decision, Skip)
        self.assertIs(decision.reason, SkipReason.NO_UPSTREAM)
        self.assertEqual(self.repos.head(), self.repos.c1)


class UnsafeCheckoutTests(EngineCase):
    """US3: pinned or unsafe checkouts are left alone."""

    def known_newer(self):
        c2 = self.repos.publish()
        self.repos.fetch_ctl()
        self.hint(c2)
        return c2

    def test_detached_checkout_is_pinned(self):
        self.known_newer()
        self.repos.git(self.repos.ctl, "checkout", "-q", "--detach")
        decision = self.run_engine()
        self.assertIsInstance(decision, Skip)
        self.assertIs(decision.reason, SkipReason.DETACHED)
        self.assertIn("pinned", decision.notice)
        self.assertEqual(self.repos.head(), self.repos.c1)

    def test_detached_with_unknown_hint_is_silent(self):
        c2 = self.repos.publish()  # not fetched: unknown locally
        self.hint(c2)
        self.repos.git(self.repos.ctl, "checkout", "-q", "--detach")
        self.assertEqual(self.run_engine(), NoAction(SkipReason.DETACHED))

    def test_modified_tracked_file_is_dirty(self):
        self.known_newer()
        (self.repos.ctl / "a.txt").write_text("edited\n", encoding="utf-8")
        decision = self.run_engine()
        self.assertIsInstance(decision, Skip)
        self.assertIs(decision.reason, SkipReason.DIRTY)
        self.assertEqual(self.repos.head(), self.repos.c1)

    def test_staged_change_is_dirty(self):
        self.known_newer()
        (self.repos.ctl / "new.txt").write_text("staged\n", encoding="utf-8")
        self.repos.git(self.repos.ctl, "add", "new.txt")
        decision = self.run_engine()
        self.assertIsInstance(decision, Skip)
        self.assertIs(decision.reason, SkipReason.DIRTY)

    def test_untracked_file_does_not_block(self):
        c2 = self.known_newer()
        (self.repos.ctl / "scratch.txt").write_text("loose\n", encoding="utf-8")
        decision = self.run_engine()
        self.assertIsInstance(decision, Updated)
        self.assertEqual(self.repos.head(), c2)

    def test_untracked_file_in_the_way_fails_the_fast_forward(self):
        self.known_newer()
        (self.repos.ctl / "b.txt").write_text("would be overwritten\n", encoding="utf-8")
        decision = self.run_engine()
        self.assertIsInstance(decision, Skip)
        self.assertIs(decision.reason, SkipReason.FF_FAILED)
        self.assertEqual(self.repos.head(), self.repos.c1)
        self.assertEqual((self.repos.ctl / "b.txt").read_text(encoding="utf-8"),
                         "would be overwritten\n")

    def test_branch_without_upstream(self):
        self.known_newer()
        self.repos.git(self.repos.ctl, "checkout", "-q", "-b", "topic")
        decision = self.run_engine()
        self.assertIsInstance(decision, Skip)
        self.assertIs(decision.reason, SkipReason.NO_UPSTREAM)
        self.assertIn("topic", decision.notice)

    def test_lock_held_by_another_process(self):
        self.known_newer()
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = engine.lock_path(self.repos.ctl, self.lock_dir)
        with open(lock_path, "a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            decision = self.run_engine()
        self.assertIsInstance(decision, Skip)
        self.assertIs(decision.reason, SkipReason.LOCKED)
        self.assertEqual(self.repos.head(), self.repos.c1)

    def test_not_a_git_repository_is_silent(self):
        plain = self.repos.base / "plain"
        plain.mkdir()
        self.hint("e" * 40)
        self.assertEqual(self.run_engine(root=plain), NoAction(SkipReason.NOT_GIT))

    def test_another_process_moving_head_while_waiting_is_already_moved(self):
        c2 = self.known_newer()
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = engine.lock_path(self.repos.ctl, self.lock_dir)
        result = {}
        handle = open(lock_path, "a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

        def worker():
            result["decision"] = self.run_engine(policy=Policy(lock_wait=5.0))

        thread = threading.Thread(target=worker)
        thread.start()
        time.sleep(0.5)
        self.repos.git(self.repos.ctl, "merge", "-q", "--ff-only", c2)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        thread.join(10)
        self.assertEqual(result.get("decision"), AlreadyMoved(self.repos.c1, c2))

    def test_rerun_after_success_is_a_no_op(self):
        c2 = self.known_newer()
        self.assertIsInstance(self.run_engine(), Updated)
        self.assertEqual(self.run_engine(), NoAction(SkipReason.NO_HINT))
        self.assertEqual(self.repos.head(), c2)


class RollbackTests(EngineCase):
    """US4: a failed refresh rolls back."""

    def test_refresh_failure_rolls_back_to_the_previous_revision(self):
        c2 = self.repos.publish()
        self.hint(c2)
        decision = self.run_engine(refresher=lambda root, policy: "pip exploded")
        self.assertIsInstance(decision, RolledBack)
        self.assertEqual((decision.old, decision.attempted), (self.repos.c1, c2))
        self.assertIn("rolled back", decision.notice)
        self.assertIn("pip exploded", decision.notice)
        self.assertEqual(self.repos.head(), self.repos.c1)
        self.assertFalse((self.repos.ctl / "b.txt").exists())
        self.assertEqual(self.repos.git(self.repos.ctl, "status", "--porcelain"), "")

    def test_failed_rollback_is_stranded_with_a_recovery_command(self):
        c2 = self.repos.publish("a.txt", "changed upstream\n", "edit a")
        self.hint(c2)

        def refresher(root, policy):
            # A local edit to a file the update changed blocks the rollback.
            (Path(root) / "a.txt").write_text("edited during refresh\n", encoding="utf-8")
            return "refresh failed"

        decision = self.run_engine(refresher=refresher)
        self.assertIsInstance(decision, Stranded)
        self.assertEqual(decision.head, c2)
        self.assertIn(f"git -C {self.repos.ctl.resolve()} status", decision.notice)


if __name__ == "__main__":
    unittest.main()
