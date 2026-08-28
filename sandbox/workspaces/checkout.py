"""Safe workspace checkout materialization with private Git metadata."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import stat
import secrets
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SAFE_LOCK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_LOOSE_OBJECT = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{38}$")
_PACK_SUFFIXES = frozenset({".pack", ".idx", ".bitmap", ".rev"})
_FALLBACK_ERRNOS = frozenset(filter(None, (
    errno.EXDEV, getattr(errno, "EOPNOTSUPP", None),
    getattr(errno, "ENOTSUP", None), errno.EINVAL,
    getattr(errno, "ENOSYS", None), errno.EPERM, errno.EACCES,
)))


def materialization_lock_name(source_path: str | Path) -> str:
    """Return the one lock name shared by local and remote source mutation."""
    return ".sandbox-materialize-" + hashlib.sha256(
        str(source_path).encode()
    ).hexdigest()[:32] + ".lock"


class WorkspaceMaterializationError(RuntimeError):
    """Bounded checkout materialization failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class MaterializationPlan:
    source_path: Path
    workspace_path: Path
    source_identity: str | None
    workspace_label: str
    lock_key: str
    plain_copy: bool = False
    parent_identity: tuple[int, int] = (0, 0)
    source_identity_fs: tuple[int, int] = (0, 0)
    workspace_identity_fs: tuple[int, int] | None = None


@dataclass(frozen=True)
class CheckoutMaterializationReceipt:
    schema: int
    workspace_path: str
    source_identity: str | None
    history_mode: str
    hardlinked_files: int
    copied_git_entries: int
    fallback_reason: str | None
    source_mutation_check: str
    lock: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _absolute_unsymlinked(path: Path, label: str, *, must_exist: bool) -> Path:
    if not path.is_absolute():
        raise WorkspaceMaterializationError("workspace_path_invalid", f"{label} path must be absolute")
    if path.is_symlink():
        raise WorkspaceMaterializationError("workspace_path_unsafe", f"{label} path must not be a symlink")
    resolved = path.resolve(strict=False)
    if must_exist and not resolved.is_dir():
        raise WorkspaceMaterializationError("workspace_source_unavailable", "source path must be a directory")
    if path.exists() and not path.is_dir():
        raise WorkspaceMaterializationError("workspace_path_invalid", f"{label} path must be a directory")
    return resolved


def plan_materialization(source: Path | str, workspace: Path | str, *,
                         source_identity: str | None = None,
                         workspace_label: str = "workspace",
                         plain_copy: bool = False) -> MaterializationPlan:
    """Validate a sibling checkout request without mutating either tree."""
    source_path = _absolute_unsymlinked(Path(source), "source", must_exist=True)
    workspace_path = _absolute_unsymlinked(Path(workspace), "workspace", must_exist=False)
    if source_path == workspace_path:
        raise WorkspaceMaterializationError("workspace_path_invalid", "source and workspace must differ")
    if source_path.parent != workspace_path.parent:
        raise WorkspaceMaterializationError("workspace_path_escape", "workspace must stay inside the source deployment boundary")
    if not isinstance(workspace_label, str) or not _SAFE_LABEL.fullmatch(workspace_label):
        raise WorkspaceMaterializationError("workspace_label_invalid", "workspace label is invalid")
    if source_identity is not None and (
        not isinstance(source_identity, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", source_identity)
    ):
        raise WorkspaceMaterializationError("source_identity_invalid", "source identity is invalid")
    key = materialization_lock_name(source_path).removeprefix(
        ".sandbox-materialize-"
    ).removesuffix(".lock")
    if not _SAFE_LOCK.fullmatch(key):
        raise WorkspaceMaterializationError("workspace_lock_invalid", "materialization lock key is invalid")
    if type(plain_copy) is not bool:
        raise WorkspaceMaterializationError("workspace_mode_invalid", "plain-copy mode must be boolean")
    parent_stat = os.stat(source_path.parent, follow_symlinks=False)
    source_stat = os.stat(source_path, follow_symlinks=False)
    workspace_stat = None
    try:
        observed = os.stat(workspace_path, follow_symlinks=False)
        if not stat.S_ISDIR(observed.st_mode):
            raise WorkspaceMaterializationError(
                "workspace_path_unsafe", "workspace path must remain a directory")
        workspace_stat = (observed.st_dev, observed.st_ino)
    except FileNotFoundError:
        pass
    return MaterializationPlan(
        source_path, workspace_path, source_identity, workspace_label, key,
        plain_copy, (parent_stat.st_dev, parent_stat.st_ino),
        (source_stat.st_dev, source_stat.st_ino), workspace_stat,
    )


def _count_entries(root: Path) -> int:
    return sum(1 for path in root.rglob("*") if path.is_file() or path.is_symlink())


def _copy_worktree(source: Path, staging: Path) -> None:
    staging.mkdir(mode=0o700, exist_ok=True)
    for child in source.iterdir():
        if child.name == ".git":
            continue
        target = staging / child.name
        if child.is_symlink():
            target.symlink_to(os.readlink(child))
        elif child.is_dir():
            shutil.copytree(child, target, symlinks=True)
        else:
            shutil.copy2(child, target, follow_symlinks=False)


def _eligible_object(relative: Path) -> bool:
    value = relative.as_posix()
    return bool(_LOOSE_OBJECT.fullmatch(value)) or (
        len(relative.parts) == 2 and relative.parts[0] == "pack"
        and relative.suffix in _PACK_SUFFIXES
    )


def _copy_git_hardlinked(source_git: Path, target_git: Path,
                         link: Callable[[str, str], object]) -> tuple[int, int]:
    shutil.copytree(source_git, target_git, symlinks=True,
                    ignore=shutil.ignore_patterns("objects"))
    source_objects = source_git / "objects"
    target_objects = target_git / "objects"
    target_objects.mkdir()
    linked = 0
    copied = _count_entries(target_git)
    if not source_objects.is_dir() or source_objects.is_symlink():
        raise OSError(errno.EINVAL, "Git object directory is unavailable")
    for root, dirs, files in os.walk(source_objects, followlinks=False):
        root_path = Path(root)
        relative_root = root_path.relative_to(source_objects)
        destination_root = target_objects / relative_root
        destination_root.mkdir(parents=True, exist_ok=True)
        dirs[:] = [name for name in dirs if not (root_path / name).is_symlink()]
        for name in files:
            source_file = root_path / name
            destination = destination_root / name
            relative = source_file.relative_to(source_objects)
            if source_file.is_file() and not source_file.is_symlink() and _eligible_object(relative):
                link(str(source_file), str(destination))
                linked += 1
            else:
                shutil.copy2(source_file, destination, follow_symlinks=False)
                copied += 1
    return linked, copied


def _marker_git(source: Path, staging: Path, *, source_fd: int) -> tuple[int, str | None]:
    marker = source / ".git"
    try:
        text = marker.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise WorkspaceMaterializationError(
            "workspace_git_marker_invalid", "Git marker is unreadable") from None
    if len(text.encode("utf-8")) > 4096 or not re.fullmatch(r"gitdir: [^\r\n]+\r?\n?", text):
        raise WorkspaceMaterializationError(
            "workspace_git_marker_invalid", "Git marker is malformed")
    clone = staging.parent / (staging.name + ".clone")
    try:
        completed = subprocess.run(
            ["git", "clone", "--no-hardlinks", "--no-local", "--no-checkout",
             str(source), str(clone)], capture_output=True, text=True, timeout=60,
            pass_fds=(source_fd,),
        )
        if completed.returncode != 0 or not (clone / ".git").is_dir():
            raise OSError(errno.EACCES, "private Git administration copy failed")
        private_admin = staging / ".sandbox-git-admin"
        shutil.copytree(clone / ".git", private_admin, symlinks=True)
        (staging / ".git").write_text("gitdir: .sandbox-git-admin\n", encoding="utf-8")
        return _count_entries(private_admin) + 1, "git_marker_file"
    finally:
        shutil.rmtree(clone, ignore_errors=True)


def _identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _reject_git_symlinks(git_path: Path) -> None:
    for root, directories, files in os.walk(git_path, followlinks=False):
        base = Path(root)
        for name in (*directories, *files):
            if stat.S_ISLNK(os.lstat(base / name).st_mode):
                raise WorkspaceMaterializationError(
                    "workspace_git_symlink_unsafe", "Git metadata contains a symlink")


def _workspace_identity(parent_fd: int, name: str) -> tuple[int, int] | None:
    try:
        observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(observed.st_mode):
        raise WorkspaceMaterializationError(
            "workspace_path_unsafe", "workspace path identity changed")
    return _identity(observed)


def _require_source_identity(parent_fd: int, plan: MaterializationPlan) -> None:
    if _workspace_identity(parent_fd, plan.source_path.name) != plan.source_identity_fs:
        raise WorkspaceMaterializationError(
            "workspace_identity_changed", "source identity changed")


def _open_directory(name: str, parent_fd: int) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(name, flags, dir_fd=parent_fd)


def _remove_entry(parent_fd: int, name: str) -> None:
    observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISDIR(observed.st_mode):
        child_fd = _open_directory(name, parent_fd)
        try:
            for child in os.listdir(child_fd):
                _remove_entry(child_fd, child)
        finally:
            os.close(child_fd)
        os.rmdir(name, dir_fd=parent_fd)
    else:
        os.unlink(name, dir_fd=parent_fd)


def _move_children(source_fd: int, destination_fd: int, *,
                   rename: Callable[..., object]) -> None:
    for name in tuple(os.listdir(source_fd)):
        rename(name, name, src_dir_fd=source_fd, dst_dir_fd=destination_fd)


def _publish(staging_name: str, plan: MaterializationPlan, parent_fd: int, *,
             rename: Callable[..., object]) -> None:
    expected = plan.workspace_identity_fs
    observed = _workspace_identity(parent_fd, plan.workspace_path.name)
    if observed != expected:
        raise WorkspaceMaterializationError(
            "workspace_identity_changed", "workspace path identity changed")
    if expected is None:
        os.mkdir(plan.workspace_path.name, mode=0o700, dir_fd=parent_fd)
        expected = _workspace_identity(parent_fd, plan.workspace_path.name)
    backup_name = f".{plan.workspace_path.name}.backup-{secrets.token_hex(8)}"
    os.mkdir(backup_name, mode=0o700, dir_fd=parent_fd)
    workspace_fd = _open_directory(plan.workspace_path.name, parent_fd)
    staging_fd = _open_directory(staging_name, parent_fd)
    backup_fd = _open_directory(backup_name, parent_fd)
    original_names: set[str] = set()
    backed_entries: set[str] = set()
    preserved_directories: set[str] = set()
    preserved_original_children: dict[str, set[str]] = {}
    preserved_backed_children: dict[str, set[str]] = {}
    try:
        if _identity(os.fstat(workspace_fd)) != expected:
            raise WorkspaceMaterializationError(
                "workspace_identity_changed", "workspace path identity changed")
        staged_names = set(os.listdir(staging_fd))
        original_names = set(os.listdir(workspace_fd))
        for name in tuple(original_names):
            current = os.stat(name, dir_fd=workspace_fd, follow_symlinks=False)
            incoming = None
            if name in staged_names:
                incoming = os.stat(name, dir_fd=staging_fd, follow_symlinks=False)
            if (incoming is not None and stat.S_ISDIR(current.st_mode)
                    and stat.S_ISDIR(incoming.st_mode)):
                os.mkdir(name, mode=0o700, dir_fd=backup_fd)
                current_fd = _open_directory(name, workspace_fd)
                incoming_fd = _open_directory(name, staging_fd)
                saved_fd = _open_directory(name, backup_fd)
                preserved_directories.add(name)
                preserved_original_children[name] = set(os.listdir(current_fd))
                preserved_backed_children[name] = set()
                try:
                    for child in tuple(os.listdir(current_fd)):
                        rename(child, child, src_dir_fd=current_fd,
                               dst_dir_fd=saved_fd)
                        preserved_backed_children[name].add(child)
                    _move_children(incoming_fd, current_fd, rename=rename)
                finally:
                    os.close(saved_fd)
                    os.close(incoming_fd)
                    os.close(current_fd)
                os.rmdir(name, dir_fd=staging_fd)
                staged_names.remove(name)
            else:
                rename(name, name, src_dir_fd=workspace_fd, dst_dir_fd=backup_fd)
                backed_entries.add(name)
        _move_children(staging_fd, workspace_fd, rename=rename)
        if _workspace_identity(parent_fd, plan.workspace_path.name) != expected:
            raise WorkspaceMaterializationError(
                "workspace_identity_changed", "workspace path identity changed")
    except Exception:
        try:
            for name in tuple(os.listdir(workspace_fd)):
                if name in preserved_directories:
                    current_fd = _open_directory(name, workspace_fd)
                    try:
                        for child in tuple(os.listdir(current_fd)):
                            if (child not in preserved_original_children[name]
                                    or child in preserved_backed_children[name]):
                                _remove_entry(current_fd, child)
                    finally:
                        os.close(current_fd)
                elif name in backed_entries or name not in original_names:
                    _remove_entry(workspace_fd, name)
            for name in tuple(os.listdir(backup_fd)):
                if name in preserved_directories:
                    current_fd = _open_directory(name, workspace_fd)
                    saved_fd = _open_directory(name, backup_fd)
                    try:
                        _move_children(saved_fd, current_fd, rename=os.rename)
                    finally:
                        os.close(saved_fd)
                        os.close(current_fd)
                    os.rmdir(name, dir_fd=backup_fd)
                else:
                    os.rename(name, name, src_dir_fd=backup_fd,
                              dst_dir_fd=workspace_fd)
            if plan.workspace_identity_fs is None:
                os.close(workspace_fd)
                workspace_fd = -1
                os.rmdir(plan.workspace_path.name, dir_fd=parent_fd)
            os.rmdir(backup_name, dir_fd=parent_fd)
        except Exception:
            raise WorkspaceMaterializationError(
                "workspace_publication_indeterminate",
                "workspace publication rollback is indeterminate",
            ) from None
        raise
    finally:
        os.close(backup_fd)
        os.close(staging_fd)
        if workspace_fd >= 0:
            os.close(workspace_fd)
    try:
        _remove_entry(parent_fd, backup_name)
        os.rmdir(staging_name, dir_fd=parent_fd)
    except OSError:
        pass


def materialize(plan: MaterializationPlan, *,
                link: Callable[[str, str], object] = os.link,
                publish_rename: Callable[..., object] = os.rename) -> CheckoutMaterializationReceipt:
    """Materialize one validated checkout and return a bounded receipt."""
    if type(plan) is not MaterializationPlan:
        raise WorkspaceMaterializationError("workspace_plan_invalid", "materialization plan is invalid")
    canonical_key = materialization_lock_name(plan.source_path).removeprefix(
        ".sandbox-materialize-"
    ).removesuffix(".lock")
    if plan.lock_key != canonical_key:
        raise WorkspaceMaterializationError(
            "workspace_lock_invalid", "materialization lock identity is invalid")
    parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    lock_name = f".sandbox-materialize-{plan.lock_key}.lock"
    parent_fd = -1
    source_fd = -1
    lock_acquired = False
    try:
        try:
            parent_fd = os.open(plan.source_path.parent, parent_flags)
            if _identity(os.fstat(parent_fd)) != plan.parent_identity:
                raise WorkspaceMaterializationError(
                    "workspace_identity_changed", "deployment parent identity changed")
            source_fd = os.open(plan.source_path.name, parent_flags, dir_fd=parent_fd)
            if _identity(os.fstat(source_fd)) != plan.source_identity_fs:
                raise WorkspaceMaterializationError(
                    "workspace_identity_changed", "source identity changed")
        except WorkspaceMaterializationError:
            raise
        except OSError:
            raise WorkspaceMaterializationError(
                "workspace_source_unavailable",
                "workspace materialization source is unavailable",
            ) from None
        try:
            os.mkdir(lock_name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            raise WorkspaceMaterializationError(
                "workspace_materialization_busy",
                "source materialization lock is held",
            ) from None
        except OSError:
            raise WorkspaceMaterializationError(
                "workspace_lock_unavailable",
                "workspace materialization lock is unavailable",
            ) from None
        lock_acquired = True
    finally:
        if not lock_acquired:
            for descriptor in (source_fd, parent_fd):
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
    staging_name = f".{plan.workspace_path.name}.staging-{secrets.token_hex(8)}"
    staging = plan.workspace_path.parent / staging_name
    source_view = plan.source_path
    history_mode = "none"
    linked = 0
    copied = 0
    fallback_reason = None
    staging_created = False
    receipt_fields = None
    try:
        try:
            os.mkdir(staging_name, mode=0o700, dir_fd=parent_fd)
            staging_created = True
        except OSError:
            raise WorkspaceMaterializationError(
                "workspace_staging_unavailable",
                "workspace staging directory is unavailable",
            ) from None
        # Publication and deletion remain descriptor-relative. macOS does not
        # support traversing a directory through /dev/fd, so copying uses the
        # plan-bound paths and rechecks both open descriptors before publication.
        _require_source_identity(parent_fd, plan)
        _copy_worktree(source_view, staging)
        _require_source_identity(parent_fd, plan)
        source_git = source_view / ".git"
        if source_git.is_dir() and not source_git.is_symlink():
            _reject_git_symlinks(source_git)
            if plan.plain_copy:
                shutil.copytree(source_git, staging / ".git", symlinks=True)
                history_mode, copied, fallback_reason = "copied", _count_entries(source_git), "unsupported"
            else:
                try:
                    linked, copied = _copy_git_hardlinked(source_git, staging / ".git", link)
                    history_mode = "hardlinked"
                except OSError as exc:
                    if exc.errno not in _FALLBACK_ERRNOS:
                        raise
                    shutil.rmtree(staging / ".git", ignore_errors=True)
                    shutil.copytree(source_git, staging / ".git", symlinks=True)
                    history_mode, linked, copied = "copied", 0, _count_entries(source_git)
                    fallback_reason = (
                        "cross_device" if exc.errno == errno.EXDEV else
                        "permission" if exc.errno in {errno.EPERM, errno.EACCES} else
                        "unsupported"
                    )
        elif source_git.is_file() and not source_git.is_symlink():
            copied, fallback_reason = _marker_git(source_view, staging, source_fd=source_fd)
            history_mode = "copied"
            private_admin = staging / ".sandbox-git-admin"
            if private_admin.is_dir():
                _reject_git_symlinks(private_admin)
        elif source_git.exists() or source_git.is_symlink():
            raise WorkspaceMaterializationError(
                "workspace_git_symlink_unsafe", "Git marker must not be a symlink")
        if _identity(os.fstat(source_fd)) != plan.source_identity_fs:
            raise WorkspaceMaterializationError("workspace_identity_changed", "source identity changed")
        _require_source_identity(parent_fd, plan)
        _publish(staging_name, plan, parent_fd, rename=publish_rename)
        receipt_fields = (
            1, str(plan.workspace_path), plan.source_identity, history_mode,
            linked, copied, fallback_reason, "not_run",
        )
    except WorkspaceMaterializationError:
        raise
    except (OSError, shutil.Error, subprocess.SubprocessError):
        raise WorkspaceMaterializationError(
            "workspace_materialization_failed", "workspace materialization failed"
        ) from None
    finally:
        active_exception = sys.exc_info()[0] is not None
        cleanup_failed = False
        try:
            if staging_created:
                _remove_entry(parent_fd, staging_name)
        except FileNotFoundError:
            pass
        except OSError:
            cleanup_failed = True
        lock_released = False
        try:
            os.rmdir(lock_name, dir_fd=parent_fd)
        except OSError:
            cleanup_failed = True
        else:
            lock_released = True
        for descriptor in (source_fd, parent_fd):
            try:
                os.close(descriptor)
            except OSError:
                cleanup_failed = True
        if cleanup_failed and not active_exception:
            code = (
                "workspace_lock_release_failed" if not lock_released
                else "workspace_cleanup_failed"
            )
            raise WorkspaceMaterializationError(
                code, "workspace materialization cleanup failed",
            ) from None
    if receipt_fields is None:
        raise WorkspaceMaterializationError(
            "workspace_materialization_failed", "workspace materialization failed")
    return CheckoutMaterializationReceipt(
        *receipt_fields,
        {"key": plan.lock_key, "acquired": True, "released": True},
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("materialize", nargs="?")
    parser.add_argument("--source", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--label", default="workspace")
    parser.add_argument("--source-identity")
    args = parser.parse_args(argv)
    try:
        receipt = materialize(plan_materialization(
            args.source, args.workspace, workspace_label=args.label,
            source_identity=args.source_identity,
        ))
    except WorkspaceMaterializationError as exc:
        print(json.dumps({"ok": False, "code": exc.code}, separators=(",", ":")))
        return 1
    print(json.dumps({"ok": True, "receipt": receipt.to_dict()}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
