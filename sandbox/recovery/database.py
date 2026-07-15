"""Bounded logical database capture adapters.

The adapter deliberately accepts structured database metadata only.  Credentials
are inherited by the native client (for example through ``PGPASSFILE`` or a
root-owned defaults file); neither a password nor a connection URI is accepted
as an argument to this module.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Mapping, Protocol, Sequence

from .errors import RecoveryError


class CommandRunner(Protocol):
    def run(self, argv: Sequence[str], *, cwd: str | None = None,
            env: Mapping[str, str] | None = None, timeout: float | None = None): ...


class DatabaseCapture:
    """Create and validate native logical database dumps without shell text."""

    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner

    @staticmethod
    def command(engine: str, database: str, destination: str | Path, *,
                nontransactional: bool = False, ddl_risk: bool = False) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if (not isinstance(database, str) or not database or database.startswith("-") or
                any(char in database for char in "\n\r\0")):
            raise RecoveryError("database name is invalid", "invalid_database")
        if nontransactional:
            raise RecoveryError(
                "logical single-transaction backup is unsafe for non-transactional tables",
                "nontransactional_tables",
            )
        warnings = ("DDL during a logical backup can invalidate its snapshot",) if ddl_risk else ()
        target = str(Path(destination))
        if engine == "postgresql":
            return (
                ("pg_dump", "--format=custom", "--no-owner", "--file", target, database),
                warnings,
            )
        if engine in {"mariadb", "mysql"}:
            return (
                ("mariadb-dump", "--single-transaction", "--quick", "--routines", "--events", "--triggers", "--result-file", target, database),
                warnings,
            )
        raise RecoveryError("database engine is not supported", "unsupported_database")

    def capture(self, engine: str, database: str, destination: str | Path, *,
                env: Mapping[str, str] | None = None, nontransactional: bool = False,
                ddl_risk: bool = False, timeout: float = 3600) -> dict:
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
            raise RecoveryError("database dump timeout is invalid", "invalid_database_timeout")
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        command, warnings = self.command(engine, database, target,
                                         nontransactional=nontransactional, ddl_risk=ddl_risk)
        result = self.runner.run(command, env=env, timeout=timeout)
        if result.returncode != 0:
            raise RecoveryError("database dump command failed", "database_dump_failed")
        if not target.exists() or target.stat().st_size == 0:
            raise RecoveryError("database dump is empty", "empty_database_dump")
        self._validate_format(engine, target)
        return {"path": target, "engine": engine, "warnings": warnings, "argv": command,
                "format_validated": True}

    @staticmethod
    def _validate_format(engine: str, target: Path) -> None:
        payload = target.read_bytes()
        if engine == "postgresql":
            if not payload.startswith(b"PGDMP"):
                raise RecoveryError("PostgreSQL dump format is invalid", "invalid_database_dump")
            return
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RecoveryError("SQL dump format is invalid", "invalid_database_dump") from exc
        if not re.search(r"(?:CREATE|INSERT|SET|DROP|ALTER|/\*!|--|#)", text, re.IGNORECASE):
            raise RecoveryError("SQL dump format is invalid", "invalid_database_dump")
