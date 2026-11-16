"""Owner-private staged source for `sb host stack up|apply --here` (Fix 5).

A controller never runs these commands on a live checkout: it stages exactly
the bound commit into one workspace per project on the server
(``deploy_exact_working_tree``) and runs the job there. ``--here`` does the
same locally. The bound commit is fetched (a fetch runs no hook of the source
repository) into one owner-only bare store per source checkout, and one stable
worktree per source is reset to it for each run. The job runs from that
worktree, so untracked or ignored files, edits made after the checks, and the
source's later changes never reach it. The path is stable, like the
controller's workspace, so the repository, stack and delivery identities a job
derives from its checkout stay the same from run to run.

Layout, all mode 0700 under ``$SANDBOX_HOME/runtime/hosting/here/src``::

    <key>/objects.git   bare store; key = sha256 of the source top level
    <key>/tree/         the staged worktree
    <key>/.run.lock     held exclusively for a whole --here run

A run refuses (``host_here_source_busy``) while another run holds the lock or
while any unfinished job still has its project directory in the tree.
LFS objects and submodules are not staged: no LFS filter runs, so LFS files
stay pointer files, and submodule folders stay empty. The newest ``KEEP_REFS``
staged commits keep a ref in the store.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
from urllib.parse import urlsplit, urlunsplit

GIT_TIMEOUT = 600
_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
KEEP_REFS = 10


class SnapshotError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _git(cwd, *argv, timeout=60, check=True) -> str:
    from sandbox.core import _remote
    # No hook runs while staging, the owner's global ones included.
    # No LFS smudge either: it would fetch from origin with the owner's credentials.
    result = subprocess.run(["git", "-c", "protocol.file.allow=always", "-c", "core.hooksPath=/dev/null",
                             "-c", "filter.lfs.process=", "-c", "filter.lfs.smudge=",
                             "-c", "filter.lfs.required=false", *argv], cwd=str(cwd),
                            env=_remote.git_environment(overrides={"GIT_LFS_SKIP_SMUDGE": "1"}),
                            capture_output=True, text=True, check=False, timeout=timeout)
    if check and result.returncode != 0:
        raise SnapshotError("host_here_snapshot_failed",
                            f"git {argv[0]} failed while staging the --here source")
    return (result.stdout or "").strip()


def _owned(path: Path) -> bool:
    try:
        info = os.stat(path)
    except OSError:
        return False
    # Group-writable is the default with per-user groups (umask 002); world-writable is not.
    return info.st_uid == os.geteuid() and not info.st_mode & 0o002


def _not_owned(path: Path) -> SnapshotError:
    return SnapshotError("host_here_source_not_owned",
                         f"{path} and its Git directory must belong to this account and "
                         "not be world writable")


def toplevel(path: Path) -> Path:
    """The source checkout's top level; it and its Git directory must be the owner's.

    The folder itself is checked before Git reads it; Git's own ownership
    refusal ("dubious ownership") is reported with the same code.
    """
    if not _owned(path):
        raise _not_owned(path)
    from sandbox.core import _remote
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "rev-parse", "--show-toplevel"],
                            cwd=str(path), capture_output=True, text=True, check=False, timeout=60,
                            env=_remote.git_environment())
    if "dubious ownership" in (result.stderr or ""):
        raise _not_owned(path)
    top = (result.stdout or "").strip() if result.returncode == 0 else ""
    if not top:
        raise SnapshotError("host_here_source_not_git", f"{path} is not in a Git checkout")
    top_path = Path(top).resolve()
    common = _git(top_path, "rev-parse", "--path-format=absolute", "--git-common-dir", check=False)
    if not (_owned(top_path) and common and _owned(Path(common))):
        raise _not_owned(top_path)
    return top_path


def origin_url(top: Path) -> str | None:
    """The source's origin URL without user info (a token never leaves the checkout)."""
    url = _git(top, "remote", "get-url", "origin", check=False)
    if not url:
        return None
    if "://" in url:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    return url


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise SnapshotError("host_here_snapshot_failed", f"{path} is not the owner's private folder")
    if info.st_mode & 0o077:
        os.chmod(path, 0o700)
    return path


def base_dir(sandbox_home: str) -> Path:
    base = Path(sandbox_home) / "runtime" / "hosting" / "here"
    _private_dir(base)
    return _private_dir(base / "src")


def source_key(top: Path) -> str:
    return hashlib.sha256(str(top).encode()).hexdigest()[:16]


def open_registry(path: Path):
    """This host's job registry, read-only when its path allows, as ``job_view`` opens it otherwise."""
    from sandbox.jobs.registry import JobRepository, JobRepositoryError
    try:
        return JobRepository.open_existing(path)
    except JobRepositoryError:
        return JobRepository(path)


def _active_jobs_inside(tree: Path, sandbox_home: str) -> bool | None:
    """True when a job may still run from inside ``tree``; None when unknown.

    The registry's own in-use rule (``read_checkout_admissions``): an
    unfinished lifecycle, or any job still holding a workspace lease (an
    interrupted job whose process may live on). Read-only, no row cap.
    """
    import sqlite3
    path = Path(sandbox_home) / "runtime" / "jobs" / "registry.sqlite3"
    if not path.exists():
        return False
    resolved = str(tree.resolve(strict=False))
    escaped = resolved.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            row = connection.execute(
                "SELECT 1 FROM jobs WHERE (project_root=? OR project_root LIKE ? ESCAPE '\\') "
                "AND (lifecycle IN ('accepted','queued','running','cancelling') "
                "OR EXISTS (SELECT 1 FROM workspace_leases l WHERE l.job_id=jobs.job_id)) LIMIT 1",
                (resolved, escaped + "/%")).fetchone()
        finally:
            connection.close()
    except Exception:  # noqa: BLE001 -- an unreadable registry is unknown, never "free"
        return None
    return row is not None


@contextmanager
def snapshot(sandbox_home: str, top: Path, commit: str):
    """Yield the staged worktree of ``commit`` from the checkout ``top``.

    The run lock is held exclusively for the whole ``with`` body.
    """
    if not _COMMIT_RE.fullmatch(commit or ""):
        raise SnapshotError("host_here_snapshot_failed", "the bound commit is not a full commit id")
    group = _private_dir(base_dir(sandbox_home) / source_key(top))
    store, tree = group / "objects.git", group / "tree"
    with ExitStack() as stack:
        fd = os.open(group / ".run.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_CLOEXEC", 0), 0o600)
        stack.callback(os.close, fd)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SnapshotError("host_here_source_busy",
                                "another --here run is using this source; wait for it") from None
        busy = _active_jobs_inside(tree, sandbox_home)
        if busy is not False:
            raise SnapshotError("host_here_source_busy",
                                "a job still runs from this source's staged copy" if busy else
                                "could not tell whether a job still runs from this source's staged copy")
        if not store.exists():
            tmp = group / ".objects.git.new"
            shutil.rmtree(tmp, ignore_errors=True)
            _git(group, "init", "--bare", "--quiet", "--template=", str(tmp))
            os.rename(tmp, store)
        _private_dir(store)
        url = origin_url(top)
        # The job reads the repository identity from origin, as it would in the source.
        if url:
            _git(store, "config", "remote.origin.url", url)
        else:
            _git(store, "config", "--unset", "remote.origin.url", check=False)
        if _git(store, "rev-parse", "--verify", "--quiet", f"refs/here/{commit}^{{commit}}", check=False) != commit:
            _git(store, "fetch", "--quiet", "--no-tags", "--update-shallow", "--no-recurse-submodules",
                 str(top), f"+{commit}:refs/here/{commit}", timeout=GIT_TIMEOUT)
            if _git(store, "rev-parse", "--verify", f"refs/here/{commit}^{{commit}}") != commit:
                raise SnapshotError("host_here_snapshot_failed", "the staged commit does not match")
        try:
            if tree.is_symlink() or (tree.exists() and _git(tree, "rev-parse", "--path-format=absolute",
                                                            "--git-common-dir", check=False)
                                     != str(store.resolve())):
                raise SnapshotError("host_here_snapshot_failed", "the staged worktree is not this store's")
            if not tree.exists():
                _git(store, "worktree", "prune")
                _git(store, "worktree", "add", "--detach", "--quiet", str(tree), commit, timeout=GIT_TIMEOUT)
            else:
                _git(tree, "checkout", "--quiet", "--detach", "--force", commit, timeout=GIT_TIMEOUT)
            _git(tree, "clean", "-ffdxq", timeout=GIT_TIMEOUT)
            os.chmod(tree, 0o700)
            if (_git(tree, "rev-parse", "HEAD") != commit
                    or _git(tree, "status", "--porcelain", "--ignored", "--untracked-files=all")):
                raise SnapshotError("host_here_snapshot_failed", "the staged worktree does not match its commit")
        except BaseException:
            # A half-made tree is never used: move it aside whole (a job may have
            # left folders rmtree cannot enter) so the next run starts clean.
            if tree.exists() or tree.is_symlink():
                aside = group / f".tree.broken-{secrets.token_hex(6)}"
                try:
                    os.rename(tree, aside)
                except OSError:
                    raise SnapshotError("host_here_snapshot_stuck",
                                        f"the staged tree {tree} could not be reset or moved aside; "
                                        "remove it by hand") from None
                _remove_aside(aside)
            _git(store, "worktree", "prune", check=False)
            raise
        _trim_refs(store, commit)
        yield tree.resolve()


def _remove_aside(path: Path) -> None:
    """Best effort: make every folder writable, then remove it."""
    for root, dirs, _files in os.walk(path):
        for name in dirs:
            try:
                os.chmod(os.path.join(root, name), 0o700, follow_symlinks=False)
            except (OSError, NotImplementedError):
                pass
    shutil.rmtree(path, ignore_errors=True)


def _trim_refs(store: Path, current: str) -> None:
    """Keep the newest KEEP_REFS staged commits; ``git gc --auto`` drops the rest."""
    refs = _git(store, "for-each-ref", "--sort=-creatordate", "--format=%(refname)", "refs/here/",
                check=False).splitlines()
    for ref in refs[KEEP_REFS:]:
        if ref != f"refs/here/{current}":
            _git(store, "update-ref", "-d", ref, check=False)
    _git(store, "gc", "--auto", "--quiet", check=False, timeout=GIT_TIMEOUT)
