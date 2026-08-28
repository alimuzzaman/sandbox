"""Real-filesystem regression coverage for shared Git checkout materialization."""

from __future__ import annotations

import errno
import hashlib
import inspect
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.transports.remote_jobs import workspace_refresh_command
from sandbox.workspaces.checkout import (
    WorkspaceMaterializationError, materialization_lock_name, materialize,
    plan_materialization,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                            text=True, check=True)
    return result.stdout.strip()


def collect_materialization_evidence(source: Path, workspace: Path) -> dict:
    """Bounded local evidence; creates exactly one caller-owned workspace."""
    revision = _git(source, "rev-parse", "HEAD")
    before = {
        "status": _git(source, "status", "--porcelain=v1"),
        "diff": subprocess.run(["git", "-C", str(source), "diff", "--exit-code"]).returncode,
        "fsck": subprocess.run(["git", "-C", str(source), "fsck", "--full"]).returncode,
    }
    usage_before = os.statvfs(source)
    used_before = (usage_before.f_blocks - usage_before.f_bfree) * usage_before.f_frsize
    receipt = materialize(plan_materialization(source, workspace, workspace_label="evidence"))
    usage_after = os.statvfs(source)
    used_after = (usage_after.f_blocks - usage_after.f_bfree) * usage_after.f_frsize
    after = {
        "status": _git(source, "status", "--porcelain=v1"),
        "diff": subprocess.run(["git", "-C", str(source), "diff", "--exit-code"]).returncode,
        "fsck": subprocess.run(["git", "-C", str(source), "fsck", "--full"]).returncode,
    }
    shutil.rmtree(workspace)
    return {"source_revision": revision, "history_mode": receipt.history_mode,
            "hardlinked_files": receipt.hardlinked_files,
            "used_space_observation": {"before": used_before, "after": used_after},
            "before": before, "after": after}


class WorkspaceGitDedupTests(unittest.TestCase):
    def fixture(self, parent: Path) -> Path:
        source = parent / "source"
        source.mkdir()
        _git(source, "init")
        _git(source, "config", "user.email", "sandbox@example.invalid")
        _git(source, "config", "user.name", "Sandbox Test")
        (source / "tracked.txt").write_text("one\n")
        _git(source, "add", "tracked.txt")
        _git(source, "commit", "-m", "one")
        for index in range(4):
            (source / f"packed-{index}.txt").write_text((str(index) + "\n") * 100)
            _git(source, "add", f"packed-{index}.txt")
            _git(source, "commit", "-m", f"packed {index}")
        _git(source, "gc", "--aggressive", "--prune=now")
        (source / "loose.txt").write_text("loose\n")
        _git(source, "add", "loose.txt")
        _git(source, "commit", "-m", "loose")
        return source

    def test_plan_rejects_unsafe_paths_and_labels_before_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            for workspace, kwargs in (
                (source, {}),
                (root / "elsewhere" / "workspace", {}),
                (root / "workspace", {"workspace_label": "bad/label"}),
            ):
                with self.subTest(workspace=workspace), self.assertRaises(WorkspaceMaterializationError):
                    plan_materialization(source, workspace, **kwargs)
            link = root / "link"
            link.symlink_to(source)
            with self.assertRaises(WorkspaceMaterializationError):
                plan_materialization(link, root / "workspace")

    def test_absent_workspace_replaced_by_symlink_never_touches_victim(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            victim = root / "victim"
            workspace = root / "workspace"
            source.mkdir()
            victim.mkdir()
            (source / "new.txt").write_text("new")
            (victim / "keep.txt").write_text("keep")
            plan = plan_materialization(source, workspace)
            workspace.symlink_to(victim, target_is_directory=True)
            with self.assertRaises(WorkspaceMaterializationError):
                materialize(plan)
            self.assertEqual((victim / "keep.txt").read_text(), "keep")
            self.assertFalse((victim / "new.txt").exists())

    def test_source_identity_replacement_after_plan_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            plan = plan_materialization(source, root / "workspace")
            source.rename(root / "original")
            source.mkdir()
            with self.assertRaises(WorkspaceMaterializationError):
                materialize(plan)

    def test_source_identity_replacement_during_copy_is_refused_before_publish(self):
        from sandbox.workspaces import checkout

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            workspace = root / "workspace"
            source.mkdir()
            (source / "old.txt").write_text("old")
            original_copy = checkout._copy_worktree
            def replace_after_copy(source_view, staging):
                original_copy(source_view, staging)
                source.rename(root / "original")
                source.mkdir()
                (source / "new.txt").write_text("new")
            with patch.object(checkout, "_copy_worktree", side_effect=replace_after_copy), \
                    self.assertRaises(WorkspaceMaterializationError):
                materialize(plan_materialization(source, workspace))
            self.assertFalse(workspace.exists())

    def test_publication_failure_rolls_back_prior_workspace_exactly(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            workspace = root / "workspace"
            source.mkdir()
            workspace.mkdir()
            (source / "value.txt").write_text("new")
            (workspace / "value.txt").write_text("old")
            prior_inode = workspace.stat().st_ino
            calls = [0]
            def fail_second(*args, **kwargs):
                calls[0] += 1
                if calls[0] == 2:
                    raise OSError(errno.EIO, "injected publication failure")
                return os.rename(*args, **kwargs)
            with self.assertRaises(WorkspaceMaterializationError):
                materialize(plan_materialization(source, workspace),
                            publish_rename=fail_second)
            self.assertEqual((workspace / "value.txt").read_text(), "old")
            self.assertEqual(workspace.stat().st_ino, prior_inode)

    def test_partial_backup_failure_preserves_moved_and_unmoved_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            workspace = root / "workspace"
            source.mkdir()
            workspace.mkdir()
            (source / "a.txt").write_text("new-a")
            (source / "b.txt").write_text("new-b")
            (workspace / "a.txt").write_text("old-a")
            (workspace / "b.txt").write_text("old-b")
            prior_inode = workspace.stat().st_ino
            calls = [0]
            def fail_second(*args, **kwargs):
                calls[0] += 1
                if calls[0] == 2:
                    raise OSError(errno.EIO, "injected backup failure")
                return os.rename(*args, **kwargs)
            with self.assertRaises(WorkspaceMaterializationError):
                materialize(plan_materialization(source, workspace),
                            publish_rename=fail_second)
            self.assertEqual((workspace / "a.txt").read_text(), "old-a")
            self.assertEqual((workspace / "b.txt").read_text(), "old-b")
            self.assertEqual(workspace.stat().st_ino, prior_inode)

    def test_partial_nested_backup_failure_preserves_directory_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            workspace = root / "workspace"
            (source / "mounted").mkdir(parents=True)
            (workspace / "mounted").mkdir(parents=True)
            (source / "mounted" / "a.txt").write_text("new-a")
            (workspace / "mounted" / "a.txt").write_text("old-a")
            (workspace / "mounted" / "b.txt").write_text("old-b")
            directory_inode = (workspace / "mounted").stat().st_ino
            calls = [0]
            def fail_second(*args, **kwargs):
                calls[0] += 1
                if calls[0] == 2:
                    raise OSError(errno.EIO, "injected nested backup failure")
                return os.rename(*args, **kwargs)
            with self.assertRaises(WorkspaceMaterializationError):
                materialize(plan_materialization(source, workspace),
                            publish_rename=fail_second)
            self.assertEqual((workspace / "mounted" / "a.txt").read_text(), "old-a")
            self.assertEqual((workspace / "mounted" / "b.txt").read_text(), "old-b")
            self.assertEqual((workspace / "mounted").stat().st_ino, directory_inode)

    def test_git_symlinks_are_refused_and_never_propagated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            victim = root / "victim-git"
            source.mkdir()
            victim.mkdir()
            (victim / "HEAD").write_text("ref: refs/heads/main\n")
            (source / ".git").symlink_to(victim, target_is_directory=True)
            with self.assertRaises(WorkspaceMaterializationError):
                materialize(plan_materialization(source, root / "workspace"))
            self.assertFalse((root / "workspace" / ".git").exists())

            (source / ".git").unlink()
            (source / ".git" / "objects").mkdir(parents=True)
            (source / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
            (source / ".git" / "unsafe").symlink_to(victim)
            with self.assertRaises(WorkspaceMaterializationError):
                materialize(plan_materialization(source, root / "workspace-two"))

    def test_remote_reset_and_overlay_apply_use_the_materializer_lock(self):
        from sandbox.core import _remote

        calls = []
        def run(_remote_record, command, **_kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as temp, patch.object(_remote, "ssh_run", side_effect=run):
            target = "/srv/source"
            _remote.reset_target_to({}, target, "a" * 40)
            _remote.apply_uncommitted({}, target, Path(temp), "", [])
        lock_commands = [command for command in calls if ".sandbox-materialize-" in command]
        self.assertEqual(len(lock_commands), 4)
        names = {command.split(".sandbox-materialize-", 1)[1].split(".lock", 1)[0]
                 for command in lock_commands}
        self.assertEqual(len(names), 1)

    def test_combined_remote_update_holds_one_lock_through_reset_and_publish(self):
        from sandbox.core import _remote

        events = []
        def run(_remote_record, command, **_kwargs):
            events.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch.object(_remote, "ssh_run", side_effect=run), \
                patch.object(_remote, "_reset_target_to_unlocked",
                             side_effect=lambda *_args: events.append("reset")), \
                patch.object(_remote, "_apply_uncommitted_unlocked",
                             side_effect=lambda *_args: events.append("publish") or 2):
            applied = _remote.update_target_to(
                {}, "/srv/source", "a" * 40,
                project_root=Path("/local/source"), diff_text="diff",
                untracked=["new.txt"],
            )
        self.assertEqual(applied, 2)
        self.assertEqual(len(events), 4)
        self.assertIn("mkdir --", events[0])
        self.assertEqual(events[1:3], ["reset", "publish"])
        self.assertIn("rmdir --", events[3])

    def test_real_git_objects_share_only_immutable_inodes_and_source_stays_clean(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.fixture(root)
            workspace = root / "workspace"
            source_head_inode = (source / ".git" / "HEAD").stat().st_ino
            evidence = collect_materialization_evidence(source, root / "evidence-workspace")
            self.assertEqual(evidence["history_mode"], "hardlinked")
            self.assertGreater(evidence["hardlinked_files"], 0)
            self.assertEqual(evidence["before"], evidence["after"])
            materialize(plan_materialization(source, workspace))
            self.assertNotEqual(source_head_inode, (workspace / ".git" / "HEAD").stat().st_ino)
            for relative in ("index", "config", "packed-refs"):
                source_entry = source / ".git" / relative
                workspace_entry = workspace / ".git" / relative
                if source_entry.is_file() and workspace_entry.is_file():
                    self.assertNotEqual(source_entry.stat().st_ino,
                                        workspace_entry.stat().st_ino)
            for directory in ("refs", "logs"):
                for source_entry in (source / ".git" / directory).rglob("*"):
                    if source_entry.is_file():
                        workspace_entry = workspace / ".git" / source_entry.relative_to(source / ".git")
                        self.assertNotEqual(source_entry.stat().st_ino,
                                            workspace_entry.stat().st_ino)
            source_objects = {p.relative_to(source / ".git" / "objects"): p
                              for p in (source / ".git" / "objects").rglob("*") if p.is_file()}
            shared = [relative for relative, path in source_objects.items()
                      if (workspace / ".git" / "objects" / relative).exists()
                      and path.stat().st_ino == (workspace / ".git" / "objects" / relative).stat().st_ino]
            self.assertTrue(shared)
            (workspace / "scratch.txt").write_text("scratch")
            _git(workspace, "reset", "--hard", "HEAD")
            _git(workspace, "clean", "-fd")
            _git(workspace, "config", "sandbox.private", "yes")
            _git(workspace, "branch", "private-branch")
            self.assertEqual(_git(source, "status", "--porcelain=v1"), "")
            self.assertEqual(subprocess.run(["git", "-C", str(source), "fsck", "--full"]).returncode, 0)

    def test_link_failure_falls_back_without_mixed_inodes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.fixture(root)
            workspace = root / "workspace"
            def denied(_source, _target):
                raise OSError(errno.EXDEV, "cross device")
            receipt = materialize(plan_materialization(source, workspace), link=denied)
            self.assertEqual((receipt.history_mode, receipt.fallback_reason,
                              receipt.hardlinked_files), ("copied", "cross_device", 0))
            self.assertEqual(_git(workspace, "rev-parse", "HEAD"), _git(source, "rev-parse", "HEAD"))

    def test_missing_history_lock_contention_and_existing_directory_inode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            workspace = root / "workspace"
            source.mkdir()
            (source / "nested").mkdir()
            (source / "nested" / "value").write_text("new")
            workspace.mkdir()
            (workspace / "nested").mkdir()
            inode = (workspace / "nested").stat().st_ino
            plan = plan_materialization(source, workspace)
            lock = plan.source_path.parent / materialization_lock_name(plan.source_path)
            lock.mkdir()
            with self.assertRaisesRegex(WorkspaceMaterializationError, "lock is held"):
                materialize(plan)
            lock.rmdir()
            receipt = materialize(plan)
            self.assertEqual(receipt.history_mode, "none")
            self.assertEqual((workspace / "nested").stat().st_ino, inode)
            self.assertEqual((workspace / "nested" / "value").read_text(), "new")

    def test_caller_cannot_override_or_forge_the_source_lock_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            with self.assertRaises(TypeError):
                plan_materialization(source, root / "workspace", lock_key="caller")
            plan = plan_materialization(source, root / "workspace")
            with self.assertRaises(WorkspaceMaterializationError):
                materialize(replace(plan, lock_key="forged"))

    def test_staging_creation_failure_releases_lock_and_descriptors(self):
        from sandbox.workspaces import checkout

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            plan = plan_materialization(source, root / "workspace")
            original_mkdir = checkout.os.mkdir
            original_close = checkout.os.close
            closed = []
            def mkdir(name, *args, **kwargs):
                if ".staging-" in str(name):
                    raise OSError(errno.ENOSPC, "injected staging failure")
                return original_mkdir(name, *args, **kwargs)
            def close(descriptor):
                closed.append(descriptor)
                return original_close(descriptor)
            with patch.object(checkout.os, "mkdir", side_effect=mkdir), \
                    patch.object(checkout.os, "close", side_effect=close), \
                    self.assertRaises(WorkspaceMaterializationError) as raised:
                materialize(plan)
            self.assertEqual(raised.exception.code, "workspace_staging_unavailable")
            self.assertFalse((root / materialization_lock_name(plan.source_path)).exists())
            self.assertEqual(len(closed), 2)

    def test_source_open_failure_closes_parent_once_with_bounded_error(self):
        from sandbox.workspaces import checkout

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            plan = plan_materialization(source, root / "workspace")
            original_open = checkout.os.open
            original_close = checkout.os.close
            calls = [0]
            closed = []
            def opened(*args, **kwargs):
                calls[0] += 1
                if calls[0] == 2:
                    raise PermissionError(errno.EACCES, "sensitive injected path")
                return original_open(*args, **kwargs)
            def closed_once(descriptor):
                closed.append(descriptor)
                return original_close(descriptor)
            with patch.object(checkout.os, "open", side_effect=opened), \
                    patch.object(checkout.os, "close", side_effect=closed_once), \
                    self.assertRaises(WorkspaceMaterializationError) as raised:
                materialize(plan)
            self.assertEqual(raised.exception.code, "workspace_source_unavailable")
            self.assertNotIn("sensitive", str(raised.exception))
            self.assertEqual(len(closed), 1)

    def test_lock_acquisition_failure_closes_both_descriptors_once(self):
        from sandbox.workspaces import checkout

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            plan = plan_materialization(source, root / "workspace")
            lock_name = materialization_lock_name(plan.source_path)
            original_mkdir = checkout.os.mkdir
            original_close = checkout.os.close
            closed = []
            def mkdir(name, *args, **kwargs):
                if name == lock_name:
                    raise PermissionError(errno.EACCES, "sensitive injected path")
                return original_mkdir(name, *args, **kwargs)
            def closed_once(descriptor):
                closed.append(descriptor)
                return original_close(descriptor)
            with patch.object(checkout.os, "mkdir", side_effect=mkdir), \
                    patch.object(checkout.os, "close", side_effect=closed_once), \
                    self.assertRaises(WorkspaceMaterializationError) as raised:
                materialize(plan)
            self.assertEqual(raised.exception.code, "workspace_lock_unavailable")
            self.assertNotIn("sensitive", str(raised.exception))
            self.assertEqual(len(closed), 2)
            self.assertFalse((root / lock_name).exists())

    def test_lock_release_failure_never_returns_a_released_receipt(self):
        from sandbox.workspaces import checkout

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            (source / "value.txt").write_text("value")
            plan = plan_materialization(source, root / "workspace")
            lock_name = materialization_lock_name(plan.source_path)
            original_rmdir = checkout.os.rmdir
            def rmdir(name, *args, **kwargs):
                if name == lock_name:
                    raise OSError(errno.EIO, "injected lock release failure")
                return original_rmdir(name, *args, **kwargs)
            with patch.object(checkout.os, "rmdir", side_effect=rmdir), \
                    self.assertRaises(WorkspaceMaterializationError) as raised:
                materialize(plan)
            self.assertEqual(raised.exception.code, "workspace_lock_release_failed")
            self.assertTrue((root / lock_name).is_dir())
            (root / lock_name).rmdir()

    def test_malformed_git_marker_is_refused_and_never_copied(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            workspace = root / "workspace"
            source.mkdir()
            (source / ".git").write_text("gitdir: /outside\nsecond-line\n")
            with self.assertRaises(WorkspaceMaterializationError) as raised:
                materialize(plan_materialization(source, workspace))
            self.assertEqual(raised.exception.code, "workspace_git_marker_invalid")
            self.assertFalse(workspace.exists())

    def test_linked_worktree_marker_becomes_private_and_survives_source_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = self.fixture(root)
            linked = root / "linked"
            _git(repository, "worktree", "add", "--detach", str(linked), "HEAD")
            workspace = root / "workspace"
            receipt = materialize(plan_materialization(linked, workspace))
            self.assertEqual((receipt.history_mode, receipt.fallback_reason),
                             ("copied", "git_marker_file"))
            marker = (workspace / ".git").read_text()
            self.assertEqual(marker, "gitdir: .sandbox-git-admin\n")
            self.assertNotIn(str(repository), marker)
            shutil.rmtree(linked)
            self.assertEqual(Path(_git(workspace, "rev-parse", "--show-toplevel")).resolve(),
                             workspace.resolve())

    def test_explicit_plain_copy_is_a_usable_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.fixture(root)
            workspace = root / "workspace"
            receipt = materialize(plan_materialization(source, workspace,
                                                        plain_copy=True))
            self.assertEqual(receipt.history_mode, "copied")
            self.assertEqual(receipt.hardlinked_files, 0)
            source_object = next(path for path in (source / ".git" / "objects").rglob("*")
                                 if path.is_file())
            target_object = workspace / ".git" / "objects" / source_object.relative_to(source / ".git" / "objects")
            self.assertNotEqual(source_object.stat().st_ino, target_object.stat().st_ino)

    def test_remote_renderer_is_shell_safe_and_uses_shared_module(self):
        command = workspace_refresh_command("/srv/source name", "/srv/work space")
        argv = shlex.split(command)
        self.assertEqual(argv[:4], ["python3", "-m", "sandbox.workspaces.checkout", "materialize"])
        self.assertIn("/srv/source name", argv)
        self.assertIn("/srv/work space", argv)
        self.assertNotIn("cp -a", command)
        self.assertNotIn("rm -rf /", command)

    def test_receipt_backed_reset_delegates_to_materializer(self):
        from sandbox.application.workspace_service import WorkspaceService
        source = inspect.getsource(WorkspaceService._local_lifecycle)
        self.assertIn("materialize(plan_materialization(", source)
        self.assertNotIn("shutil.copytree(source_checkout, checkout", source)


if __name__ == "__main__":
    unittest.main()
