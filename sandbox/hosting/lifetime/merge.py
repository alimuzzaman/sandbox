"""Merge evidence for temporary stacks, computed on a controller (spec 077, R5).

The server holds no Git history, so a controller checks, in its own checkout, that
a stack's commit is reachable from the repo's dev branch and records that on the
remote record. Only existing Git access is used (``git fetch``). Squash and rebase
merges are not reachable and fall back to the TTL.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable, Iterable, Mapping

_COMMIT = re.compile(r"[0-9a-f]{40}")


def _git(checkout: Path, *arguments: str, timeout: int = 120) -> subprocess.CompletedProcess:
    import sandbox.core._remote as remote
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(checkout), *arguments],
                          env=remote.git_environment(), capture_output=True, text=True, check=False,
                          timeout=timeout)


def dev_head(checkout: Path, dev_branch: str, *, git: Callable = _git) -> str | None:
    """Fetch ``origin/<dev_branch>`` and return its commit, or ``None``."""
    fetched = git(checkout, "fetch", "--quiet", "--no-tags", "origin",
                  f"+refs/heads/{dev_branch}:refs/remotes/origin/{dev_branch}", timeout=300)
    if fetched.returncode != 0:
        return None
    head = git(checkout, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{dev_branch}^{{commit}}")
    value = (head.stdout or "").strip().lower()
    return value if head.returncode == 0 and _COMMIT.fullmatch(value) else None


def is_merged(checkout: Path, commit: str, dev_commit: str, *, git: Callable = _git) -> bool | None:
    """True/False from ``merge-base --is-ancestor``; ``None`` when Git cannot tell."""
    if not _COMMIT.fullmatch(commit or ""):
        return None
    present = git(checkout, "cat-file", "-e", f"{commit}^{{commit}}")
    if present.returncode != 0:
        # Fetching dev brought all of its history; a commit still missing is not in it.
        return False
    result = git(checkout, "merge-base", "--is-ancestor", commit, dev_commit)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def sweep(store, *, remote_records: Iterable[dict], checkouts: Mapping[str, Path],
          dev_branch: str | None = None, repo: str | None = None, confirm: bool,
          git: Callable = _git) -> dict:
    """Check every active temporary stack record against its repo's dev branch.

    ``checkouts`` maps repo identity to a local checkout. Records of other repos,
    or without a checkout here, are reported as ``no_checkout``.
    """
    heads: dict[tuple[str, str], str | None] = {}
    checked, recorded = [], 0
    for item in remote_records:
        stack = item  # the flat record (schema_version 1)
        if (item.get("kind") != "stack" or item.get("lifetime") != "temporary"
                or item.get("state") != "active"):
            continue
        item_repo, item_dev = stack.get("repo"), stack.get("dev_branch") or "dev"
        if repo is not None and item_repo != repo:
            continue
        if dev_branch is not None and repo is not None and item_dev != dev_branch:
            continue
        row = {"slug": stack.get("slug"), "compose_project": item.get("compose_project"),
               "commit": stack.get("commit"), "merged": None, "reason": None}
        merge = item.get("merge") or {}
        if merge.get("merged") and merge.get("commit") == stack.get("commit"):
            row.update(merged=True, reason="already_recorded")
            checked.append(row)
            continue
        checkout = checkouts.get(item_repo)
        if checkout is None or not Path(checkout).is_dir():
            row["reason"] = "no_checkout"
            checked.append(row)
            continue
        key = (str(item_repo), item_dev)
        if key not in heads:
            heads[key] = dev_head(Path(checkout), item_dev, git=git)
        head = heads[key]
        if head is None:
            row["reason"] = "dev_unavailable"
            checked.append(row)
            continue
        merged = is_merged(Path(checkout), str(stack.get("commit") or ""), head, git=git)
        row.update(merged=merged, dev_commit=head)
        if merged is None:
            row["reason"] = "unknown"
        elif merged and confirm:
            result = store.call("record_merge", compose_project=item.get("compose_project"),
                                commit=stack.get("commit"),
                                evidence={"merged": True, "dev_branch": item_dev, "dev_commit": head})
            if result.get("recorded"):
                recorded += 1
                row["reason"] = "recorded"
            else:
                row["reason"] = str(result.get("reason") or "not_recorded")
        checked.append(row)
    return {"ok": True, "action": "stack-sweep", "mode": "confirm" if confirm else "plan",
            "checked": checked, "recorded": recorded}
