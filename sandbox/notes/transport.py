"""Run Agent Notes MCP tool calls through the secrets broker.

The parent never holds the token. Each batch becomes one broker use profile
whose child (``child.py``) gets the token in its environment, makes the calls
and appends one JSON line per call to a response file in a private work
directory. The broker redacts the token from the child's output and audits
the use.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets as _random
import shutil
import sys
from typing import Callable, Sequence

from . import child
from .config import NotesService
from .errors import NotesError

PROFILE = "sandbox-notes-mcp"
BATCH = 25
MAX_TIMEOUT = 1800


def _work_root() -> Path:
    from sandbox.core._paths import BASE
    return Path(BASE) / "runtime" / "notes" / "work"


def _private_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    path = root / _random.token_hex(8)
    path.mkdir(mode=0o700)
    return path


def default_service_factory(source: str) -> Callable[[dict], object]:
    """Build a SecretService that knows our one use profile.

    The ``personal`` source needs no project descriptor. Any other source must
    be registered in the Sandbox home's ``sandbox.config.json``.
    """
    import copy

    from sandbox.core._paths import BASE
    from sandbox.core._secrets import secret_file
    from sandbox.secrets.context import build_secret_service
    from sandbox.secrets.service import load_secret_project_descriptor

    root = Path(BASE)

    def factory(profiles: dict):
        descriptor = {}
        if source != "personal":
            descriptor = copy.deepcopy(dict(load_secret_project_descriptor(root) or {}))
        block = dict(descriptor.get("secrets") or {})
        merged = dict(block.get("useProfiles") or {})
        if set(merged) & set(profiles):
            raise NotesError("notes_config_invalid",
                             f"the Sandbox home defines the reserved use profile {PROFILE}")
        merged.update(profiles)
        block["useProfiles"] = merged
        descriptor["secrets"] = block
        return build_secret_service(project_root=root, config=descriptor,
                                    personal_path=secret_file(), runtime_root=root / "runtime")

    return factory


class BrokerTransport:
    def __init__(self, service: NotesService, *, service_factory=None,
                 work_root: Path | None = None, executable: str | None = None) -> None:
        self.service = service
        self._factory = service_factory or default_service_factory(service.token_source)
        self._work_root = work_root
        self._executable = executable or sys.executable

    def call(self, calls: Sequence[dict], on_batch=None) -> list[dict]:
        """Run ``calls`` in batches. ``on_batch(offset, results)`` sees each batch as it ends,
        including the finished part of a batch that was interrupted."""
        results: list[dict] = []
        for start in range(0, len(calls), BATCH):
            chunk = list(calls[start:start + BATCH])
            try:
                got = self._batch(chunk)
            except _Interrupted as stop:
                if on_batch is not None and stop.partial:
                    on_batch(start, stop.partial)
                raise stop.cause from None
            # A child that wrote fewer lines than calls stopped early: on a refused
            # token or dead service by itself, or killed (timeout, crash). The
            # missing calls failed; never reuse another call's result for them.
            # Only a child that stopped by itself names the cause; anything else is incomplete.
            last = got[-1] if got else None
            code = (last["code"] if last and last.get("code") in _SELF_STOP else "notes_incomplete")
            got.extend({"ok": False, "code": code, "skipped": True, "i": None,
                        "error": "the notes client stopped before this call"}
                       for _ in chunk[len(got):])
            if on_batch is not None:
                on_batch(start, got)
            results.extend(got)
            if any(item.get("skipped") for item in got):
                results.extend({"ok": False, "code": code, "skipped": True, "i": None,
                                "error": "the notes client stopped before this call"}
                               for _ in calls[start + len(chunk):])
                break
        return results

    def _batch(self, calls: list[dict]) -> list[dict]:
        from sandbox.secrets import SecretBrokerError

        root = self._work_root or _work_root()
        _sweep(root)
        work = _private_dir(root)
        request, response = work / "request.json", work / "response.jsonl"
        try:
            fd = os.open(request, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"endpoint": self.service.endpoint, "calls": calls}, handle)
            profile = {
                "source": self.service.token_source, "key": self.service.token_key,
                "destination": child.TOKEN_ENV, "mcp": False, "maxOutputBytes": 65_536,
                "timeoutSeconds": min(MAX_TIMEOUT, 60 + 75 * len(calls)),
                "argv": [self._executable, "-I", child.__file__, str(request), str(response)],
            }
            try:
                self._factory({PROFILE: profile}).use_profile(PROFILE, surface="cli")
            except SecretBrokerError as exc:
                if exc.code in ("key_missing", "source_missing"):
                    raise NotesError(
                        "notes_token_missing",
                        f"no notes token is stored for '{self.service.remote}' "
                        f"({self.service.token_source}:{self.service.token_key}); "
                        f"run `sb notes login --remote {self.service.remote}`") from None
                raise _Interrupted(NotesError(
                    "notes_broker_refused",
                    f"the secrets broker refused the notes token ({exc.code})"), _read(response)) from None
            except NotesError:
                raise
            except Exception as exc:
                raise _Interrupted(NotesError(
                    "notes_broker_refused",
                    f"the secrets broker could not run the notes client ({type(exc).__name__})"),
                    _read(response)) from None
            except BaseException as exc:  # Ctrl-C or kill: keep what finished
                raise _Interrupted(exc, _read(response)) from None
            return _read(response)
        finally:
            shutil.rmtree(work, ignore_errors=True)


_SELF_STOP = ("notes_unauthorized", "notes_unreachable")


class _Interrupted(Exception):
    def __init__(self, cause: BaseException, partial: list[dict]) -> None:
        super().__init__("interrupted")
        self.cause = cause
        self.partial = partial


def _read(response: Path) -> list[dict]:
    try:
        lines = response.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _sweep(root: Path, max_age: float = 3600.0) -> None:
    """Remove work directories a killed run left behind (they can hold share URLs)."""
    import time
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    now = time.time()
    for entry in entries:
        try:
            if entry.is_dir() and not entry.is_symlink() and now - entry.stat().st_mtime > max_age:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue
