"""Linux filesystem operations adapter with synthetic fallback for non-Linux platforms."""

import ctypes
import os
import platform
import shutil
import stat
from pathlib import Path
from typing import Any, Dict, Optional, Union


class FileSystemAdapterError(Exception):
    """Base error for filesystem adapter operations."""


class OpenBeneathError(FileSystemAdapterError):
    """Raised when an open operation attempts to escape the root boundary or follow symlinks."""


class RenameNoReplaceError(FileSystemAdapterError):
    """Raised when rename_noreplace fails because the destination already exists."""


# Linux constants
SYS_RENAMEAT2 = 316 if platform.machine() in ("x86_64", "amd64") else 276
SYS_OPENAT2 = 437
RENAME_NOREPLACE = 1
RENAME_EXCL = 0x00000004
RESOLVE_BENEATH = 0x08
RESOLVE_NO_SYMLINKS = 0x04


class LinuxFilesystemAdapter:
    def __init__(self, root_path: Union[str, Path]):
        self.root_path = Path(root_path).resolve()
        self._is_linux = platform.system() == "Linux"

    def is_synthetic(self) -> bool:
        return not self._is_linux

    def ensure_directory(self, path: Union[str, Path], mode: int = 0o700) -> None:
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(p, mode)
        except OSError:
            pass

    @staticmethod
    def _relative_parts(rel_path: Union[str, Path]) -> tuple[str, ...]:
        rel = Path(rel_path)
        if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
            raise OpenBeneathError(f"Invalid relative path: {rel_path}")
        return tuple(rel.parts)

    @staticmethod
    def identity_from_stat(st: os.stat_result) -> Dict[str, Any]:
        return {
            "inode": st.st_ino,
            "device": st.st_dev,
            "mode": st.st_mode,
            "uid": st.st_uid,
            "gid": st.st_gid,
        }

    @staticmethod
    def identity_matches(expected: Optional[Dict[str, Any]], actual: Dict[str, Any]) -> bool:
        if not expected:
            return False
        required = ("device", "inode", "mode", "uid", "gid")
        return all(expected.get(key) == actual.get(key) for key in required)

    def open_root_directory(self) -> int:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.root_path, flags)
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            os.close(fd)
            raise OpenBeneathError("Storage root is not a directory")
        return fd

    def open_directory_beneath(
        self,
        rel_path: Union[str, Path],
        expected_identity: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Open a directory beneath the storage root without following any symlink."""
        parts = self._relative_parts(rel_path)
        current_fd = self.open_root_directory()
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            for part in parts:
                next_fd = os.open(part, flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            actual = self.identity_from_stat(os.fstat(current_fd))
            if not stat.S_ISDIR(actual["mode"]):
                raise OpenBeneathError(f"Path is not a directory: {rel_path}")
            if expected_identity is not None and not self.identity_matches(expected_identity, actual):
                raise OpenBeneathError(f"Directory identity changed: {rel_path}")
            return current_fd
        except Exception:
            os.close(current_fd)
            raise

    def ensure_directory_beneath(
        self, rel_path: Union[str, Path], mode: int = 0o700
    ) -> Dict[str, Any]:
        """Create/open a directory using no-follow descriptor-relative traversal."""
        parts = self._relative_parts(rel_path)
        if not parts:
            raise OpenBeneathError("Storage root already names the root directory")
        current_fd = self.open_root_directory()
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            for index, part in enumerate(parts):
                created = False
                try:
                    os.mkdir(part, mode, dir_fd=current_fd)
                    created = True
                except FileExistsError:
                    pass
                next_fd = os.open(part, flags, dir_fd=current_fd)
                if created:
                    self.fsync_directory_fd(current_fd)
                os.close(current_fd)
                current_fd = next_fd
                if index == len(parts) - 1:
                    if created:
                        os.fchmod(current_fd, mode)
                    elif stat.S_IMODE(os.fstat(current_fd).st_mode) != mode:
                        raise FileSystemAdapterError(
                            f"Existing directory mode does not match expected mode: {part}"
                        )
            self.fsync_directory_fd(current_fd)
            return self.identity_from_stat(os.fstat(current_fd))
        finally:
            os.close(current_fd)

    def stat_identity_at(self, parent_fd: int, name: str) -> Optional[Dict[str, Any]]:
        if not name or name in (".", "..") or "/" in name:
            raise OpenBeneathError(f"Invalid directory entry name: {name}")
        try:
            st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        return self.identity_from_stat(st)

    def open_directory_at(
        self,
        parent_fd: int,
        name: str,
        expected_identity: Optional[Dict[str, Any]] = None,
    ) -> int:
        if not name or name in (".", "..") or "/" in name:
            raise OpenBeneathError(f"Invalid directory entry name: {name}")
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(name, flags, dir_fd=parent_fd)
        actual = self.identity_from_stat(os.fstat(fd))
        if not stat.S_ISDIR(actual["mode"]) or (
            expected_identity is not None and not self.identity_matches(expected_identity, actual)
        ):
            os.close(fd)
            raise OpenBeneathError(f"Directory identity changed: {name}")
        return fd

    def fsync_directory_fd(self, directory_fd: int) -> None:
        os.fsync(directory_fd)

    def rename_directory_noreplace_at(
        self,
        source_parent_fd: int,
        source_name: str,
        destination_parent_fd: int,
        destination_name: str,
        *,
        source_identity: Dict[str, Any],
        source_parent_identity: Dict[str, Any],
        destination_parent_identity: Dict[str, Any],
    ) -> None:
        """Rename an identity-checked directory between already-open parents."""
        for name in (source_name, destination_name):
            if not name or name in (".", "..") or "/" in name:
                raise OpenBeneathError(f"Invalid directory entry name: {name}")
        if not self.identity_matches(
            source_parent_identity, self.identity_from_stat(os.fstat(source_parent_fd))
        ):
            raise OpenBeneathError("Source parent identity changed")
        if not self.identity_matches(
            destination_parent_identity,
            self.identity_from_stat(os.fstat(destination_parent_fd)),
        ):
            raise OpenBeneathError("Quarantine container identity changed")
        actual_source = self.stat_identity_at(source_parent_fd, source_name)
        if (
            not self.identity_matches(source_identity, actual_source or {})
            or not stat.S_ISDIR((actual_source or {}).get("mode", 0))
        ):
            raise OpenBeneathError("Source identity changed before quarantine")
        if self.stat_identity_at(destination_parent_fd, destination_name) is not None:
            raise RenameNoReplaceError(f"Destination already exists: {destination_name}")

        libc = ctypes.CDLL(None, use_errno=True)
        if self._is_linux:
            ret = libc.syscall(
                SYS_RENAMEAT2,
                source_parent_fd,
                source_name.encode("utf-8"),
                destination_parent_fd,
                destination_name.encode("utf-8"),
                RENAME_NOREPLACE,
            )
        elif platform.system() == "Darwin":
            renameatx = getattr(libc, "renameatx_np", None)
            if renameatx is None:
                raise FileSystemAdapterError("Descriptor-relative no-replace rename is unavailable")
            ret = renameatx(
                source_parent_fd,
                source_name.encode("utf-8"),
                destination_parent_fd,
                destination_name.encode("utf-8"),
                RENAME_EXCL,
            )
        else:
            raise FileSystemAdapterError("Descriptor-relative no-replace rename is unavailable")
        if ret != 0:
            error_number = ctypes.get_errno()
            if error_number == 17:  # EEXIST
                raise RenameNoReplaceError(f"Destination already exists: {destination_name}")
            raise FileSystemAdapterError(f"renameat2 failed with errno {error_number}")

    def measure_tree_bytes(self, directory_fd: int, expected_identity: Dict[str, Any]) -> int:
        actual = self.identity_from_stat(os.fstat(directory_fd))
        if not self.identity_matches(expected_identity, actual) or not stat.S_ISDIR(actual["mode"]):
            raise OpenBeneathError("Quarantine target identity changed during byte measurement")
        total = 0
        for name in os.listdir(directory_fd):
            child_identity = self.stat_identity_at(directory_fd, name)
            if child_identity is None:
                continue
            if stat.S_ISDIR(child_identity["mode"]):
                child_fd = self.open_directory_at(directory_fd, name, child_identity)
                try:
                    total += self.measure_tree_bytes(child_fd, child_identity)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(child_identity["mode"]):
                total += os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_size
        return total

    def remove_tree_contents_fd(
        self, directory_fd: int, expected_identity: Dict[str, Any]
    ) -> None:
        """Remove descendants through open descriptors, never following symlinks."""
        actual = self.identity_from_stat(os.fstat(directory_fd))
        if not self.identity_matches(expected_identity, actual) or not stat.S_ISDIR(actual["mode"]):
            raise OpenBeneathError("Quarantine target identity changed during removal")

        for name in os.listdir(directory_fd):
            child_identity = self.stat_identity_at(directory_fd, name)
            if child_identity is None:
                continue
            if stat.S_ISDIR(child_identity["mode"]):
                child_fd = self.open_directory_at(directory_fd, name, child_identity)
                try:
                    self.remove_tree_contents_fd(child_fd, child_identity)
                    self.fsync_directory_fd(child_fd)
                finally:
                    os.close(child_fd)
                current_identity = self.stat_identity_at(directory_fd, name)
                if not self.identity_matches(child_identity, current_identity or {}):
                    raise OpenBeneathError(f"Directory identity changed during removal: {name}")
                os.rmdir(name, dir_fd=directory_fd)
            elif stat.S_ISLNK(child_identity["mode"]):
                current_identity = self.stat_identity_at(directory_fd, name)
                if (
                    not stat.S_ISLNK((current_identity or {}).get("mode", 0))
                    or not self.identity_matches(child_identity, current_identity or {})
                ):
                    raise OpenBeneathError(f"Symlink identity changed during removal: {name}")
                # unlinkat by parent descriptor and leaf name does not follow the link.
                os.unlink(name, dir_fd=directory_fd)
            else:
                os.unlink(name, dir_fd=directory_fd)
            self.fsync_directory_fd(directory_fd)

    def remove_empty_directory_at(
        self,
        parent_fd: int,
        name: str,
        expected_identity: Dict[str, Any],
    ) -> None:
        actual = self.stat_identity_at(parent_fd, name)
        if not self.identity_matches(expected_identity, actual or {}) or not stat.S_ISDIR(
            (actual or {}).get("mode", 0)
        ):
            raise OpenBeneathError(f"Directory identity changed before final removal: {name}")
        child_fd = self.open_directory_at(parent_fd, name, expected_identity)
        try:
            if os.listdir(child_fd):
                raise FileSystemAdapterError(f"Directory is not empty: {name}")
        finally:
            os.close(child_fd)
        os.rmdir(name, dir_fd=parent_fd)
        self.fsync_directory_fd(parent_fd)

    def write_file_bytes(self, path: Union[str, Path], data: bytes, mode: int = 0o600) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = p.with_name(f"{p.name}.tmp.{os.getpid()}")
        with open(tmp_path, "wb") as f:
            f.write(data)
            f.flush()
            os.fchmod(f.fileno(), mode)
            os.fsync(f.fileno())
        os.replace(tmp_path, p)

    def open_beneath(self, rel_path: Union[str, Path], flags: int = os.O_RDONLY) -> int:
        """Opens a path strictly beneath root_path, rejecting escapes and symlinks."""
        rel = Path(rel_path)
        # Check for path traversal components
        if ".." in rel.parts:
            raise OpenBeneathError(f"Path traversal detected in relative path: {rel_path}")

        target = (self.root_path / rel).resolve()
        try:
            target.relative_to(self.root_path)
        except ValueError as exc:
            raise OpenBeneathError(f"Target path escapes root: {rel_path}") from exc

        # Check for symlinks in all components from root to target
        current = self.root_path
        for part in rel.parts:
            current = current / part
            if current.is_symlink():
                raise OpenBeneathError(f"Symlink traversal forbidden: {current}")

        if not target.exists():
            raise OpenBeneathError(f"Target path does not exist: {target}")

        return os.open(target, flags)

    def rename_noreplace(self, src: Union[str, Path], dst: Union[str, Path]) -> None:
        """Atomically renames src to dst, failing if dst already exists."""
        src_p = Path(src)
        dst_p = Path(dst)

        if not src_p.exists():
            raise FileSystemAdapterError(f"Source does not exist: {src_p}")

        if dst_p.exists():
            raise RenameNoReplaceError(f"Destination already exists: {dst_p}")

        if self._is_linux:
            # Try native renameat2 syscall
            try:
                libc = ctypes.CDLL(None, use_errno=True)
                ret = libc.syscall(
                    SYS_RENAMEAT2,
                    -100,  # AT_FDCWD
                    str(src_p).encode("utf-8"),
                    -100,  # AT_FDCWD
                    str(dst_p).encode("utf-8"),
                    RENAME_NOREPLACE,
                )
                if ret != 0:
                    errno = ctypes.get_errno()
                    if errno == 17:  # EEXIST
                        raise RenameNoReplaceError(f"Destination already exists: {dst_p}")
                    raise FileSystemAdapterError(f"renameat2 failed with errno {errno}")
                return
            except (AttributeError, OSError):
                # Fallback to python os.rename if syscall unavailable
                pass

        # Synthetic fallback
        if dst_p.exists():
            raise RenameNoReplaceError(f"Destination already exists: {dst_p}")

        try:
            os.rename(src_p, dst_p)
        except FileExistsError as exc:
            raise RenameNoReplaceError(f"Destination already exists: {dst_p}") from exc

    def fsync_directory(self, path: Union[str, Path]) -> None:
        p = Path(path)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(p, flags)
        try:
            self.fsync_directory_fd(fd)
        finally:
            os.close(fd)

    def stat_identity(self, path: Union[str, Path]) -> Dict[str, Any]:
        p = Path(path)
        st = os.lstat(p)
        if stat.S_ISLNK(st.st_mode):
            raise OpenBeneathError(f"Symlink identity is not accepted: {p}")
        return self.identity_from_stat(st)

    def remove_tree_beneath(self, directory: Union[str, Path]) -> None:
        """Removes all contents under directory without escaping or following symlinks."""
        dir_p = Path(directory)
        if not dir_p.is_dir():
            return

        for child in list(dir_p.iterdir()):
            if child.is_symlink():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    def prepare_ci_materialization(
        self,
        project_identity: str,
        workspace_id: str,
        object_id: str,
        source_path: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        """Prepares a bounded CI materialization with descriptor-only access."""
        object_root = self.root_path / "objects" / project_identity / "workspaces" / object_id
        self.ensure_directory(object_root, 0o700)

        work_dir = object_root / "work"
        meta_dir = object_root / "meta"
        self.ensure_directory(work_dir, 0o700)
        self.ensure_directory(meta_dir, 0o700)

        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        root_fd = os.open(object_root, dir_flags)
        work_fd = os.open(work_dir, dir_flags)
        source_fd = None

        if source_path is not None:
            src_p = Path(source_path)
            if src_p.exists():
                source_fd = os.open(src_p, dir_flags)

        return {
            "object_id": object_id,
            "object_root": object_root,
            "work_path": work_dir,
            "root_fd": root_fd,
            "work_fd": work_fd,
            "source_fd": source_fd,
            "mode": "bounded_interior_v1",
        }

    def verify_interior_confinement(
        self, object_root: Union[str, Path], write_target: Union[str, Path]
    ) -> bool:
        """Verifies that write_target is strictly confined to the work/ interior."""
        return verify_interior_confinement(object_root, write_target)


def verify_interior_confinement(
    object_root: Union[str, Path], write_target: Union[str, Path]
) -> bool:
    """Verifies that write_target is strictly confined to the work/ interior."""
    root_p = Path(object_root).resolve()
    work_p = (root_p / "work").resolve()
    target_p = Path(write_target).resolve()

    try:
        target_p.relative_to(work_p)
        return True
    except ValueError:
        return False
