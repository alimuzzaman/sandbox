"""Registered source resolution and descriptor-safe bounded reads."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from .models import MAX_SOURCE_BYTES, SafeSource, SecretBrokerError, SourcePolicy


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
            return SourcePolicy(alias="personal", scope="personal", path=self.personal_path)
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
            mcp_modes=frozenset(modes or ()),
        )

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
