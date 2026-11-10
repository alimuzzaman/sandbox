"""Owner labels, repo identity and generated stack names (spec 077, research R6-R7)."""
from __future__ import annotations

import re
import secrets
import subprocess
from pathlib import Path
from typing import Callable, Iterable

from sandbox.core._hosting import HostingRefusal

OWNER_UNKNOWN = "stack_owner_unknown"
_SAFE = re.compile(r"[^a-z0-9-]+")
_SAFE_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_SCP_URL = re.compile(r"^(?:[^@/]+@)?([^:/]+):(.+)$")


def _git(checkout: Path, *arguments: str) -> str:
    result = subprocess.run(["git", "-C", str(checkout), *arguments], capture_output=True,
                            text=True, check=False, timeout=30)
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def owner_label(checkout: Path, explicit: str | None) -> str:
    label = (explicit or "").strip() or _git(checkout, "config", "user.email")
    if not label or len(label) > 200 or any(ch.isspace() for ch in label):
        raise HostingRefusal(OWNER_UNKNOWN, "no owner: set `git config user.email` in the template "
                             "checkout or pass --owner LABEL (no spaces)")
    return label


def normalize_repo(url: str) -> str:
    """``host/path`` for an origin URL, without scheme, credentials or ``.git``."""
    value = (url or "").strip()
    if not value:
        return ""
    if "://" in value:
        value = value.split("://", 1)[1]
        value = value.rsplit("@", 1)[-1] if "@" in value.split("/", 1)[0] else value
    else:
        match = _SCP_URL.match(value)
        if match:
            value = f"{match.group(1)}/{match.group(2)}"
        else:
            return "local:" + value.rstrip("/")
    host, _sep, path = value.partition("/")
    host = host.split(":", 1)[0].lower()
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{host}/{path}".lower()


def repo_identity(checkout: Path) -> str:
    url = _git(checkout, "remote", "get-url", "origin")
    return normalize_repo(url) or "local:" + str(Path(checkout).resolve())


def _part(value: str, limit: int) -> str:
    return _SAFE.sub("-", value.lower()).strip("-")[:limit].strip("-")


def generate_slug(ref_branch: str, commit: str, owner: str, taken: Iterable[str],
                  accepts: Callable[[str], bool], *, token: Callable[[], str] | None = None) -> str:
    """``<branch-short>-<owner-short>-<n>`` with the lowest free ``n``.

    The parts are shortened until ``accepts`` (the template's slug rule) passes;
    when no counter form passes, ``<branch-short>-<4 hex>`` is used.
    """
    taken = set(taken)
    branch = _part(ref_branch.rsplit("/", 1)[-1], 16) if ref_branch else ""
    branch = branch or "c" + commit[:7]
    person = _part(owner.split("@", 1)[0], 8) or "user"
    for branch_len, person_len in ((16, 8), (12, 6), (8, 4), (6, 3)):
        stem = f"{branch[:branch_len].strip('-')}-{person[:person_len].strip('-')}"
        for n in range(1, 100):
            candidate = f"{stem}-{n}"
            if candidate in taken:
                continue
            if _SAFE_SLUG.fullmatch(candidate) and accepts(candidate):
                return candidate
            break
    make = token or (lambda: secrets.token_hex(2))
    for _ in range(20):
        candidate = f"{branch[:10].strip('-')}-{make()}"
        if candidate not in taken and _SAFE_SLUG.fullmatch(candidate) and accepts(candidate):
            return candidate
    raise HostingRefusal("stack_slug_invalid", "could not generate a name the template accepts; "
                         "pass --name")
