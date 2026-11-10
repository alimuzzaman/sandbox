"""Decide and perform a controller fast-forward (spec 072, data-model.md).

``run()`` is the only entry point. It never fetches from a remote server: the
hints in the state file only nominate a revision, and the checkout's own
GitHub ``origin`` decides whether that revision is published history on the
branch the checkout follows.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterator

from sandbox.controller_update import gitops, state
from sandbox.controller_update.gitops import GitError
from sandbox.controller_update.models import (
    AlreadyMoved, CheckoutState, Decision, Hint, NoAction, Policy, Recovered, RolledBack,
    Skip, SkipReason, Stranded, Updated, parse_revision,
)

Refresher = Callable[[Path, Policy], "str | None"]

MCP_NOTICE = ("sb: MCP server dependencies changed; run ./sb mcp-install and "
              "restart your MCP client.")


def short(revision: str | None) -> str:
    return (revision or "unknown")[:7]


def lock_path(root: Path, lock_dir: Path) -> Path:
    digest = hashlib.sha256(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(lock_dir) / f"controller-update-{digest}.lock"


def journal_path(root: Path, lock_dir: Path) -> Path:
    """Rollback journal beside the lock: present only while a rollback runs."""
    return lock_path(root, lock_dir).with_suffix(".rollback.json")


def _write_journal(path: Path, branch: str, old: str, new: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"schema_version": 1, "branch": branch, "old": old,
                                     "new": new}), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _read_journal(path: Path) -> tuple[str, str, str] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        return None
    branch, old, new = raw.get("branch"), parse_revision(raw.get("old")), parse_revision(raw.get("new"))
    if not isinstance(branch, str) or not branch or branch.startswith("-") or not old or not new:
        return None
    return branch, old, new


def _drop_journal(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def _journal_matches_verified_update(root: Path, branch: str, old: str, new: str,
                                     policy: Policy) -> bool:
    """True when ``old -> new`` is a strict fast-forward and ``new`` is on upstream."""
    if old == new:
        return False
    checkout = gitops.read_checkout(root, timeout=policy.git_timeout)
    if (checkout is None or checkout.branch != branch
            or not checkout.upstream_remote or not checkout.upstream_branch):
        return False
    tip = f"refs/remotes/{checkout.upstream_remote}/{checkout.upstream_branch}"
    timeout = policy.git_timeout
    return (gitops.has_commit(root, old, timeout=timeout)
            and gitops.has_commit(root, new, timeout=timeout)
            and gitops.is_ancestor(root, old, new, timeout=timeout)
            and gitops.is_ancestor(root, new, tip, timeout=timeout))


def _recover(root: Path, lock_dir: Path, policy: Policy) -> Decision | None:
    """Finish a rollback a crash interrupted. ``None`` means nothing to do."""
    journal = journal_path(root, lock_dir)
    if not journal.exists():
        return None
    with _exclusive(lock_path(root, lock_dir), policy.lock_wait) as held:
        if not held:
            return None  # the holder owns the journal right now
        entry = _read_journal(journal)
        if entry is None:
            _drop_journal(journal)
            return None
        branch, old, new = entry
        resolved = Path(root).resolve()
        # Only ever act on the branch that is checked out here, under a name
        # git accepts; anything else is not a rollback of this checkout.
        if (not gitops.valid_branch_name(resolved, branch, timeout=policy.git_timeout)
                or gitops.current_branch(resolved, timeout=policy.git_timeout) != branch):
            _drop_journal(journal)
            return None
        # The journal is only a note to ourselves, so it proves nothing on its
        # own: act on it only when it still describes an update this engine
        # could have made, a fast-forward from ``old`` to a ``new`` that is on
        # the branch's published upstream. Otherwise it could name any local
        # commit as the place to roll back to.
        if not _journal_matches_verified_update(resolved, branch, old, new, policy):
            _drop_journal(journal)
            return None
        try:
            finished = gitops.finish_rollback(resolved, branch, old, new,
                                              timeout=policy.git_timeout)
        except GitError as exc:
            head = gitops.read_head(resolved, timeout=policy.git_timeout)
            # Fail closed: a partly written tree is never guessed at. Once the
            # tracked files match the old revision again, the next run clears
            # the journal by itself (docs/controller-auto-update.md).
            return Stranded(head, (
                f"sb: an interrupted controller rollback to {short(old)} could not be "
                f"finished ({exc}); {resolved} is at {short(head)}. "
                f"Recover with: git -C {resolved} status (journal: {journal}); "
                "see docs/controller-auto-update.md, 'Stranded checkout'."))
        _drop_journal(journal)
        if not finished:
            return None
        return Recovered(old, (
            f"sb: finished an interrupted controller rollback to {short(old)}; "
            "re-running the command."))


def default_lock_dir() -> Path:
    from sandbox.core._paths import RUNTIME_DIR
    return RUNTIME_DIR / "locks"


@contextlib.contextmanager
def _exclusive(path: Path, wait: float) -> Iterator[bool]:
    """Yield True while holding an exclusive flock, False if it stayed busy.

    The kernel drops the lock when the process exits, so a crash never leaves
    a stale lock behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + max(wait, 0.0)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(0.05)
        yield True
    finally:
        os.close(fd)


def refresh_checkout(root: Path, policy: Policy) -> str | None:
    """Converge the checkout's CLI venv and import-check the new code.

    Runs ``python -m sandbox.controller_update.refresh`` from the updated
    checkout, so the new revision's own setup code does the work. Returns an
    error detail, or ``None`` on success.
    """
    venv_python = Path(root) / ".cli-venv" / "bin" / "python"
    interpreter = str(venv_python) if venv_python.exists() else sys.executable
    env = gitops.git_environment()
    env.pop("PYTHONPATH", None)
    try:
        result = subprocess.run(
            [interpreter, "-m", "sandbox.controller_update.refresh"],
            cwd=str(root), env=env, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=policy.refresh_timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return f"refresh timed out after {int(policy.refresh_timeout)}s"
    except OSError as exc:
        return f"refresh could not start: {exc.strerror or exc}"
    if result.returncode != 0:
        lines = [line for line in (result.stderr or result.stdout).splitlines() if line.strip()]
        return gitops.short_detail(lines[-1] if lines else f"exit {result.returncode}")
    return None


def _mcp_requirements_changed(paths: tuple[str, ...]) -> bool:
    return any(p.startswith("mcp/") and Path(p).name.startswith("requirements")
               and p.endswith(".txt") for p in paths)


# ---- notices ------------------------------------------------------------------

def _notice(reason: SkipReason, checkout: CheckoutState, hint: Hint | None,
            detail: str = "") -> str:
    remote = hint.remote if hint else "?"
    new7 = short(hint.revision if hint else None)
    old7 = short(checkout.head)
    upstream = checkout.upstream or "its upstream"
    texts = {
        SkipReason.DETACHED: (f"remote {remote} runs {new7}, but this checkout is pinned "
                              f"(detached at {old7})"),
        SkipReason.NO_UPSTREAM: (f"remote {remote} runs {new7}, but branch {checkout.branch} "
                                 "follows no published branch on origin"),
        SkipReason.ORIGIN_NOT_GITHUB: "origin is not a GitHub repository",
        SkipReason.DIRTY: f"remote {remote} runs {new7}, but {checkout.root} has uncommitted changes",
        SkipReason.LOCKED: f"another Sandbox process is updating {checkout.root}",
        SkipReason.FETCH_FAILED: f"could not fetch {upstream} from origin ({detail})",
        SkipReason.NOT_ON_UPSTREAM: f"remote {remote} runs {new7}, which is not on {upstream}",
        SkipReason.NOT_DESCENDANT: f"{new7} is not a fast-forward of {old7}",
        SkipReason.FF_FAILED: f"fast-forward to {new7} failed ({detail})",
    }
    return f"sb: controller not updated: {texts[reason]}."


# ---- evaluation -----------------------------------------------------------------

def _newer_candidates(root: Path, head: str, hints: tuple[Hint, ...],
                      policy: Policy) -> list[Hint]:
    """Hints that might be newer than HEAD, newest record first."""
    out = []
    for hint in sorted(hints, key=lambda h: h.recorded_at, reverse=True):
        if hint.revision == head:
            continue
        if (gitops.has_commit(root, hint.revision, timeout=policy.git_timeout)
                and gitops.is_ancestor(root, hint.revision, head, timeout=policy.git_timeout)):
            continue  # the remote runs older code than this checkout
        out.append(hint)
    return out


def _known_descendant(checkout: CheckoutState, candidates: list[Hint],
                      policy: Policy) -> Hint | None:
    for hint in candidates:
        if (gitops.has_commit(checkout.root, hint.revision, timeout=policy.git_timeout)
                and gitops.is_ancestor(checkout.root, checkout.head, hint.revision,
                                       timeout=policy.git_timeout)):
            return hint
    return None


def _state_problem(checkout: CheckoutState) -> SkipReason | None:
    if checkout.branch is None:
        return SkipReason.DETACHED
    if checkout.upstream_remote != "origin" or not checkout.upstream_branch:
        return SkipReason.NO_UPSTREAM
    if not gitops.is_github_url(checkout.origin_url):
        return SkipReason.ORIGIN_NOT_GITHUB
    if not checkout.clean:
        return SkipReason.DIRTY
    return None


def _pick_target(checkout: CheckoutState, valid: list[Hint], policy: Policy) -> Hint:
    """The valid hint that the most other valid hints are ancestors of."""
    def score(hint: Hint) -> tuple[int, float]:
        covered = sum(1 for other in valid if other is not hint and gitops.is_ancestor(
            checkout.root, other.revision, hint.revision, timeout=policy.git_timeout))
        return covered, hint.recorded_at
    return max(valid, key=score)


def run(root: Path, *, state_path: Path | None = None, lock_dir: Path | None = None,
        policy: Policy = Policy(), refresher: Refresher = refresh_checkout,
        is_enabled: Callable[[], bool] = lambda: True, now: float | None = None) -> Decision:
    """Evaluate the recorded hints and fast-forward ``root`` when it is safe.

    Cost grows only with need: no state file means no git call; a hint equal
    to ``HEAD`` costs one; only a possibly newer hint reads the full state.
    A leftover rollback journal is finished first, before anything else.
    """
    lock_dir = lock_dir or default_lock_dir()
    recovered = _recover(Path(root), lock_dir, policy)
    if recovered is not None:
        return recovered
    current = state.load(state_path, now=now)
    if not current.hints:
        return NoAction(SkipReason.NO_HINT)
    head = gitops.read_head(root, timeout=policy.git_timeout)
    if head is None:
        return NoAction(SkipReason.NOT_GIT)
    candidates = _newer_candidates(Path(root), head, current.hints, policy)
    if not candidates:
        return NoAction(SkipReason.NO_HINT)
    if not is_enabled():
        return NoAction(SkipReason.DISABLED)
    checkout = gitops.read_checkout(root, timeout=policy.git_timeout)
    if checkout is None:
        return NoAction(SkipReason.NOT_GIT)

    problem = _state_problem(checkout)
    if problem is not None:
        # Never fetch for a checkout that cannot move; speak only when the
        # newer revision is already known here, so the claim is checked.
        hint = _known_descendant(checkout, candidates, policy)
        if hint is None:
            return NoAction(problem)
        return Skip(problem, _notice(problem, checkout, hint))

    upstream = checkout.upstream
    candidates = [h for h in candidates
                  if not state.refused(current, h.revision, upstream, now=now)]
    if not candidates:
        return NoAction(SkipReason.MEMOIZED)

    with _exclusive(lock_path(checkout.root, lock_dir), policy.lock_wait) as held:
        if not held:
            return Skip(SkipReason.LOCKED, _notice(SkipReason.LOCKED, checkout, candidates[0]))
        return _apply_locked(checkout, candidates, policy, refresher, state_path, now,
                             journal_path(checkout.root, lock_dir))


def _apply_locked(started: CheckoutState, candidates: list[Hint], policy: Policy,
                  refresher: Refresher, state_path: Path | None,
                  now: float | None, journal: Path) -> Decision:
    checkout = gitops.read_checkout(started.root, timeout=policy.git_timeout)
    if checkout is None:
        return NoAction(SkipReason.NOT_GIT)
    if checkout.head != started.head:
        # Another process moved the checkout while this one waited; the code
        # this process loaded is stale either way.
        return AlreadyMoved(started.head, checkout.head)
    problem = _state_problem(checkout)
    if problem is not None:
        return Skip(problem, _notice(problem, checkout, candidates[0]))
    upstream = checkout.upstream
    root = checkout.root

    def refuse(hint: Hint, reason: SkipReason) -> None:
        state.record_refusal(hint.revision, upstream, reason, policy.ttl_for(reason),
                             path=state_path, now=now)

    try:
        gitops.fetch_upstream(root, checkout.upstream_branch, timeout=policy.fetch_timeout)
    except GitError as exc:
        for hint in candidates:
            refuse(hint, SkipReason.FETCH_FAILED)
        return Skip(SkipReason.FETCH_FAILED,
                    _notice(SkipReason.FETCH_FAILED, checkout, candidates[0], str(exc)))

    tip = f"refs/remotes/{upstream}"
    valid: list[Hint] = []
    first_refusal: tuple[Hint, SkipReason] | None = None
    for hint in candidates:
        on_upstream = (gitops.has_commit(root, hint.revision, timeout=policy.git_timeout)
                       and gitops.is_ancestor(root, hint.revision, tip,
                                              timeout=policy.git_timeout))
        if not on_upstream:
            reason = SkipReason.NOT_ON_UPSTREAM
        elif not gitops.is_ancestor(root, checkout.head, hint.revision,
                                    timeout=policy.git_timeout):
            reason = SkipReason.NOT_DESCENDANT
        else:
            valid.append(hint)
            continue
        refuse(hint, reason)
        first_refusal = first_refusal or (hint, reason)
    if not valid:
        hint, reason = first_refusal
        return Skip(reason, _notice(reason, checkout, hint))

    target = _pick_target(checkout, valid, policy)
    try:
        gitops.fast_forward(root, target.revision, timeout=policy.git_timeout)
    except GitError as exc:
        return Skip(SkipReason.FF_FAILED,
                    _notice(SkipReason.FF_FAILED, checkout, target, str(exc)))

    old, new = checkout.head, target.revision
    notices = ()
    if _mcp_requirements_changed(gitops.changed_paths(root, old, new,
                                                      timeout=policy.git_timeout)):
        notices = (MCP_NOTICE,)
    failure = refresher(root, policy)
    if failure is None:
        return Updated(old, new, upstream, notices)
    try:
        # The journal outlives a crash between the ref and tree steps, so the
        # next run can finish the rollback (see _recover).
        _write_journal(journal, checkout.branch, old, new)
        gitops.rollback(root, checkout.branch, old, new, timeout=policy.git_timeout)
        _drop_journal(journal)
    except (GitError, OSError) as exc:
        head = gitops.read_head(root, timeout=policy.git_timeout)
        return Stranded(head, (
            f"sb: controller update to {short(new)} failed ({failure}) and could not be "
            f"rolled back ({exc}); {root} is at {short(head)}. "
            f"Recover with: git -C {root} status"))
    return RolledBack(old, new, (
        f"sb: controller update to {short(new)} rolled back: setup refresh failed "
        f"({failure}); running on {short(old)}."))
