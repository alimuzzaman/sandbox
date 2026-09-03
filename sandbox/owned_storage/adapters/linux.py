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
        if not p.is_dir():
            return
        try:
            fd = os.open(p, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass

    def stat_identity(self, path: Union[str, Path]) -> Dict[str, Any]:
        p = Path(path)
        st = os.stat(p)
        return {
            "inode": st.st_ino,
            "device": st.st_dev,
            "mode": st.st_mode,
            "uid": st.st_uid,
            "gid": st.st_gid,
        }

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
