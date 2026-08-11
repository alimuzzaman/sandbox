"""Registered source resolution and descriptor-safe bounded reads."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from .models import MAX_SOURCE_BYTES, SafeSource, SecretBrokerError, SourcePolicy


def _file_type(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "regular_file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISCHR(mode):
        return "character_device"
    if stat.S_ISBLK(mode):
        return "block_device"
    return "other"


def _size_bucket(size: int) -> str:
    if size == 0:
        return "empty"
    if size <= 1_024:
        return "1_to_1_kib"
    if size <= 16_384:
        return "1_to_16_kib"
    if size <= 262_144:
        return "16_to_256_kib"
    if size <= MAX_SOURCE_BYTES:
        return "256_kib_to_1_mib"
    return "over_1_mib"


class SourceRegistry:
    def __init__(
        self,
        project_root: str | Path,
        sources: dict[str, dict[str, Any]] | None,
        *,
        personal_path: str | Path,
        project_scope: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.personal_path = Path(personal_path).expanduser()
        self.project_scope = Path(project_scope).expanduser().resolve() if project_scope else None
        if self.project_scope is not None and self.project_root != self.project_scope:
            raise SecretBrokerError("source_scope_denied", "project is outside the MCP secret scope")
        self._sources = dict(sources or {})

    def policy(self, alias: str) -> SourcePolicy:
        if alias == "personal":
            return SourcePolicy(
                alias="personal", scope="personal", path=self.personal_path, format="dotenv",
            )
        item = self._sources.get(alias)
        if not isinstance(item, dict):
            raise SecretBrokerError("source_unknown", "secret source alias is not registered")
        relative = item.get("path")
        if not isinstance(relative, str):
            raise SecretBrokerError("source_unknown", "secret source alias is invalid")
        path = self.project_root / relative
        try:
            path.resolve(strict=False).relative_to(self.project_root)
        except (OSError, ValueError) as exc:
            raise SecretBrokerError("source_unsafe", "secret source escapes the project") from exc
        modes = item.get("mcp_modes", item.get("mcpModes", ()))
        return SourcePolicy(
            alias=alias,
            scope="project",
            path=path,
            format=item.get("format", "dotenv"),
            mcp_modes=frozenset(modes or ()),
        )

    def probe(self, alias: str, *, exact_size: bool = False) -> dict[str, Any]:
        """Inspect registered-source filesystem metadata without reading bytes.

        The path is intentionally absent from the result.  ``lstat`` prevents a
        symlink from being mistaken for its target, and the optional open uses
        ``O_NONBLOCK`` plus ``O_NOFOLLOW`` so a type-swap cannot block or follow
        an attacker-controlled link.  No read syscall is made.
        """
        policy = self.policy(alias)
        try:
            observed = os.lstat(policy.path)
        except FileNotFoundError:
            return {
                "source": alias,
                "scope": policy.scope,
                "format": policy.format,
                "exists": False,
                "file_type": "missing",
                "content_state": "not_applicable",
                "broker_readable": False,
                "safety": "missing",
            }
        except OSError:
            return {
                "source": alias,
                "scope": policy.scope,
                "format": policy.format,
                "exists": None,
                "file_type": "unknown",
                "content_state": "unknown",
                "broker_readable": False,
                "safety": "inaccessible",
            }

        file_type = _file_type(observed.st_mode)
        result: dict[str, Any] = {
            "source": alias,
            "scope": policy.scope,
            "format": policy.format,
            "exists": True,
            "file_type": file_type,
            "content_state": "unknown",
            "broker_readable": False,
            "safety": "unsafe",
        }
        if file_type != "regular_file":
            return result

        result["content_state"] = "empty" if observed.st_size == 0 else "nonempty"
        result["size_bucket"] = _size_bucket(observed.st_size)
        if exact_size:
            result["size_bytes"] = observed.st_size
        if (
            observed.st_uid != os.geteuid()
            or stat.S_IMODE(observed.st_mode) & 0o077
            or observed.st_nlink != 1
        ):
            return result

        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            fd = os.open(policy.path, flags)
        except OSError:
            result["safety"] = "inaccessible"
            return result
        try:
            current = os.fstat(fd)
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_dev != observed.st_dev
                or current.st_ino != observed.st_ino
            ):
                result["content_state"] = "unknown"
                result.pop("size_bucket", None)
                result.pop("size_bytes", None)
                result["safety"] = "changed"
                return result
            result["content_state"] = "empty" if current.st_size == 0 else "nonempty"
            result["size_bucket"] = _size_bucket(current.st_size)
            if exact_size:
                result["size_bytes"] = current.st_size
            if current.st_size > MAX_SOURCE_BYTES:
                result["safety"] = "too_large"
                return result
            result["broker_readable"] = True
            result["safety"] = "safe"
            return result
        finally:
            os.close(fd)

    def read(self, alias: str) -> SafeSource:
        policy = self.policy(alias)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(policy.path, flags)
        except FileNotFoundError as exc:
            raise SecretBrokerError("source_missing", "registered secret source does not exist") from exc
        except OSError as exc:
            raise SecretBrokerError("source_unsafe", "registered secret source could not be opened") from exc
        try:
            before = os.fstat(fd)
            self._validate_stat(before)
            if before.st_size > MAX_SOURCE_BYTES:
                raise SecretBrokerError("source_too_large", "registered secret source exceeds 1 MiB")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, min(65_536, MAX_SOURCE_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_SOURCE_BYTES:
                    raise SecretBrokerError("source_too_large", "registered secret source exceeds 1 MiB")
            after = os.fstat(fd)
            before_sig = self._signature(before)
            after_sig = self._signature(after)
            if before_sig != after_sig:
                raise SecretBrokerError("source_changed", "registered secret source changed during access")
            return SafeSource(policy=policy, content=b"".join(chunks), signature=after_sig)
        finally:
            os.close(fd)

    @staticmethod
    def _validate_stat(info: os.stat_result) -> None:
        if not stat.S_ISREG(info.st_mode):
            raise SecretBrokerError("source_unsafe", "registered secret source is not a regular file")
        if info.st_uid != os.geteuid():
            raise SecretBrokerError("source_unsafe", "registered secret source has an unsafe owner")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise SecretBrokerError("source_unsafe", "registered secret source permissions are too broad")
        if info.st_nlink != 1:
            raise SecretBrokerError("source_unsafe", "registered secret source has unexpected links")

    @staticmethod
    def _signature(info: os.stat_result) -> tuple[int, int, int, int, int]:
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
