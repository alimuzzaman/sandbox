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

    def call(self, calls: Sequence[dict]) -> list[dict]:
        results: list[dict] = []
        for start in range(0, len(calls), BATCH):
            chunk = list(calls[start:start + BATCH])
            got = self._batch(chunk)
            results.extend(got)
            if len(got) < len(chunk):
                # The child stopped early (refused token, service down): every
                # later call would fail the same way, so do not start them.
                stop = got[-1] if got else {"ok": False, "code": "notes_unreachable",
                                            "error": "the notes client did not run"}
                results.extend(dict(stop, i=None, skipped=True) for _ in calls[start + len(got):])
                break
        return results

    def _batch(self, calls: list[dict]) -> list[dict]:
        from sandbox.secrets import SecretBrokerError

        work = _private_dir(self._work_root or _work_root())
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
                raise NotesError("notes_broker_refused",
                                 f"the secrets broker refused the notes token ({exc.code})") from None
            lines = response.read_text(encoding="utf-8").splitlines() if response.exists() else []
        finally:
            shutil.rmtree(work, ignore_errors=True)
        out = []
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out
