"""Bounded git calls on the controller checkout (spec 072, research R4-R7).

Every call runs with stdin closed, no terminal prompt, a timeout, and an
environment stripped of variables that could point git at another
repository, work tree or index.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from sandbox.controller_update.models import CheckoutState, parse_revision

# Git variables that only configure the user's own git and are kept; every
# other GIT_* variable (GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, ...) is dropped.
_KEPT_GIT_VARS = frozenset({
    "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_SYSTEM",
    "GIT_SSH", "GIT_SSH_COMMAND", "GIT_SSL_CAINFO", "GIT_SSL_CAPATH",
})
_GITHUB_URL_RE = re.compile(
    r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)"
    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?(?:\.git)?/?"
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")


class GitError(Exception):
    """A git call failed; ``str()`` is a short, single-line detail."""


def git_environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_") or key in _KEPT_GIT_VARS}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    env["LC_ALL"] = "C"
    return env


def short_detail(text: str, limit: int = 120) -> str:
    first = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
    cleaned = _CONTROL_RE.sub(" ", first).strip()
    return cleaned[:limit] or "no detail"


def run(root: Path, *args: str, timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True,
            stdin=subprocess.DEVNULL, env=git_environment(), timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} timed out after {int(timeout)}s") from None
    except OSError as exc:
        raise GitError(f"git unavailable: {exc.strerror or exc}") from None


def _value(root: Path, *args: str, timeout: float) -> str | None:
    result = run(root, *args, timeout=timeout)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def is_github_url(url: str | None) -> bool:
    return bool(url) and _GITHUB_URL_RE.fullmatch(url.strip()) is not None


def read_head(root: Path, *, timeout: float) -> str | None:
    """``HEAD`` of the work tree rooted exactly at ``root``, else ``None``."""
    try:
        result = run(root, "rev-parse", "--show-toplevel", "HEAD",
                     timeout=timeout)
    except GitError:
        return None
    lines = result.stdout.splitlines() if result.returncode == 0 else []
    if len(lines) != 2 or Path(lines[0]).resolve() != Path(root).resolve():
        return None
    return parse_revision(lines[1].strip())


def read_checkout(root: Path, *, timeout: float) -> CheckoutState | None:
    """Describe the checkout at ``root``; ``None`` when it is not a git work tree."""
    try:
        head = read_head(root, timeout=timeout)
        if head is None:
            return None
        branch = _value(root, "symbolic-ref", "-q", "--short", "HEAD", timeout=timeout)
        upstream_remote = upstream_branch = None
        if branch:
            upstream_remote = _value(root, "config", "--get", f"branch.{branch}.remote",
                                     timeout=timeout)
            merge = _value(root, "config", "--get", f"branch.{branch}.merge", timeout=timeout)
            if merge and merge.startswith("refs/heads/"):
                upstream_branch = merge[len("refs/heads/"):]
            if upstream_remote == ".":
                upstream_remote = upstream_branch = None
        origin_url = _value(root, "config", "--get", "remote.origin.url", timeout=timeout)
        # Untracked files count (ignored ones do not): the owner's rule is a
        # clean working tree, not merely an unchanged index.
        status = run(root, "status", "--porcelain", "--untracked-files=normal",
                     timeout=timeout)
        if status.returncode != 0:
            return None
        return CheckoutState(
            root=Path(root).resolve(), head=head, branch=branch,
            upstream_remote=upstream_remote, upstream_branch=upstream_branch,
            origin_url=origin_url, clean=status.stdout.strip() == "",
        )
    except GitError:
        return None


def has_commit(root: Path, revision: str, *, timeout: float) -> bool:
    try:
        return run(root, "cat-file", "-e", f"{revision}^{{commit}}", timeout=timeout).returncode == 0
    except GitError:
        return False


def is_ancestor(root: Path, ancestor: str, descendant: str, *, timeout: float) -> bool:
    """True when ``ancestor`` is reachable from ``descendant`` (or equal)."""
    try:
        result = run(root, "merge-base", "--is-ancestor", ancestor, descendant, timeout=timeout)
    except GitError:
        return False
    return result.returncode == 0


def fetch_upstream(root: Path, branch: str, *, timeout: float) -> None:
    """Fetch ``origin/<branch>`` from the checkout's own origin. Raises GitError."""
    result = run(root, "fetch", "--no-tags", "--quiet", "origin",
                 f"+refs/heads/{branch}:refs/remotes/origin/{branch}", timeout=timeout)
    if result.returncode != 0:
        raise GitError(short_detail(result.stderr))


def fast_forward(root: Path, revision: str, *, timeout: float) -> None:
    result = run(root, "merge", "--ff-only", "--quiet", revision, timeout=timeout)
    if result.returncode != 0:
        raise GitError(short_detail(result.stderr or result.stdout))


def changed_paths(root: Path, old: str, new: str, *, timeout: float) -> tuple[str, ...]:
    try:
        result = run(root, "diff", "--name-only", "--no-renames", old, new, timeout=timeout)
    except GitError:
        return ()
    if result.returncode != 0:
        return ()
    return tuple(line for line in result.stdout.splitlines() if line)


def rollback(root: Path, branch: str, old: str, new: str, *, timeout: float) -> None:
    """Return ``branch`` and the work tree from ``new`` to ``old``.

    The ref moves first with a compare-and-swap, so a branch someone else
    moved is never touched. ``read-tree -m -u`` then refuses to overwrite
    local edits; if it does, the ref is swapped back to ``new``. A crash
    between the two steps leaves the ref at ``old`` and the files at ``new``;
    the engine's rollback journal makes the next run finish it
    (:func:`finish_rollback`). Raises GitError on any failure.
    """
    ref_name = f"refs/heads/{branch}"
    ref = run(root, "update-ref", "-m", "sb: controller update rolled back",
              ref_name, old, new, timeout=timeout)
    if ref.returncode != 0:
        raise GitError("branch moved during the update: " + short_detail(ref.stderr))
    _tree_to_old(root, ref_name, old, new, timeout=timeout)


def _tree_to_old(root: Path, ref_name: str, old: str, new: str, *, timeout: float) -> None:
    tree = run(root, "read-tree", "-m", "-u", new, old, timeout=timeout)
    if tree.returncode == 0:
        return
    detail = short_detail(tree.stderr)
    undo = run(root, "update-ref", "-m", "sb: controller rollback undone",
               ref_name, new, old, timeout=timeout)
    if undo.returncode != 0:
        raise GitError(f"{detail}; the branch could not be restored either")
    raise GitError(detail)


def ref_value(root: Path, ref_name: str, *, timeout: float) -> str | None:
    return parse_revision(_value(root, "rev-parse", "--verify", "-q", ref_name, timeout=timeout))


def tree_matches(root: Path, revision: str, *, timeout: float) -> bool:
    """True when both the index and the work tree equal ``revision``."""
    cached = run(root, "diff", "--cached", "--quiet", revision, "--", timeout=timeout)
    files = run(root, "diff", "--quiet", "--", timeout=timeout)
    return cached.returncode == 0 and files.returncode == 0


def finish_rollback(root: Path, branch: str, old: str, new: str, *, timeout: float) -> bool:
    """Complete a rollback that a crash interrupted.

    Returns ``True`` when the checkout is now consistently at ``old``, and
    ``False`` when someone else has since moved the branch (nothing of ours
    is left to finish). Raises GitError when the checkout cannot be
    reconciled automatically.
    """
    ref_name = f"refs/heads/{branch}"
    current = ref_value(root, ref_name, timeout=timeout)
    if current == new:
        rollback(root, branch, old, new, timeout=timeout)
        return True
    if current != old:
        return False
    if tree_matches(root, old, timeout=timeout):
        return True
    if run(root, "diff", "--cached", "--quiet", new, "--", timeout=timeout).returncode != 0:
        raise GitError("the checkout changed after an interrupted rollback")
    _tree_to_old(root, ref_name, old, new, timeout=timeout)
    return True
