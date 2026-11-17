"""Deploy keys, the pinned GitHub host key, fetch and checkout (spec 089).

The private key file is created by ``ssh-keygen`` and read only by ``ssh``.
Sandbox code reads the ``.pub`` file and fingerprints; it never opens, copies
or prints the private half.
"""
from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
from pathlib import Path

from sandbox.hosting.follow.spec import FollowError

# GitHub's published SSH host key
# (https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints,
# SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU). Pinned, never learned on
# first use: if GitHub rotates it, fetches fail closed until this is updated.
GITHUB_HOST_KEY = ("github.com ssh-ed25519 "
                   "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl")
FETCH_TIMEOUT = 120
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SAFE_PATH = re.compile(r"^/[A-Za-z0-9._/-]+$")


def _environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    from sandbox.core import _remote
    overrides = {"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    overrides.update(extra or {})
    return _remote.git_environment(overrides=overrides)


# The line that names the cause, in order of preference. Git's last stderr line
# after an SSH failure is boilerplate ("and the repository exists.").
_TELLING = ("Permission denied", "Host key verification failed", "fatal:")


def _detail(result: subprocess.CompletedProcess) -> str:
    from sandbox.core import _remote
    lines = [line.strip() for line in (result.stderr or result.stdout or "").splitlines() if line.strip()]
    chosen = next((line for marker in _TELLING for line in lines if marker in line),
                  lines[-1] if lines else f"exit {result.returncode}")
    return _remote.redact_text(chosen)[:300]


def _run(argv: list[str], *, timeout: int = 60, env: dict[str, str] | None = None,
         cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout,
                              env=env if env is not None else _environment(), cwd=cwd,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", f"{argv[0]} timed out after {timeout}s")
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 127, "", f"{argv[0]} could not run: {exc.strerror}")


# ------------------------------------------------------------------ keys

def key_id(github: str) -> str:
    """One fixed-length file name per repository URL.

    A readable ``owner__repo`` name let ``o/x.pub``'s private key share a path
    with ``o/x``'s public key. A hash of the URL never ends in ``.pub``.
    """
    url = f"git@github.com:{github.lower()}.git"
    return "github-" + hashlib.sha256(url.encode()).hexdigest()[:32]


def key_path(keys_dir: Path, github: str) -> Path:
    return Path(keys_dir) / key_id(github)


def write_known_hosts(path: Path) -> None:
    descriptor = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, (GITHUB_HOST_KEY + "\n").encode())
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def fingerprint(public_path: Path) -> str | None:
    result = _run(["ssh-keygen", "-l", "-E", "sha256", "-f", str(public_path)], timeout=15)
    fields = (result.stdout or "").split()
    return fields[1] if result.returncode == 0 and len(fields) > 1 else None


def ensure_key(keys_dir: Path, github: str) -> dict:
    """Create the repository's deploy key if missing; return its public half."""
    private = key_path(keys_dir, github)
    public = private.with_name(private.name + ".pub")
    created = False
    if private.is_symlink() or public.is_symlink():
        raise FollowError("follow_key_unsafe", f"deploy key for {github} is a symlink")
    if not private.exists():
        public.unlink(missing_ok=True)
        old = os.umask(0o077)
        try:
            result = _run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
                           f"sandbox-follow:{github}", "-f", str(private)], timeout=30,
                          env=_environment())
        finally:
            os.umask(old)
        if result.returncode != 0 or not private.exists():
            raise FollowError("follow_key_failed", f"ssh-keygen failed for {github}: {_detail(result)}")
        created = True
    os.chmod(private, 0o600)
    if not public.exists():
        # ssh-keygen reads the private file and prints only the public key.
        result = _run(["ssh-keygen", "-y", "-f", str(private)], timeout=15)
        if result.returncode != 0 or not result.stdout.startswith("ssh-ed25519 "):
            raise FollowError("follow_key_failed", f"the public key for {github} could not be derived")
        descriptor = os.open(str(public), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            os.write(descriptor, result.stdout.strip().encode() + b"\n")
        finally:
            os.close(descriptor)
    text = public.read_text().strip()
    if not text.startswith("ssh-") or "PRIVATE KEY" in text:
        raise FollowError("follow_key_failed", f"the public key file for {github} is not a public key")
    return {"github": github, "deploy_key": "created" if created else "existing", "public_key": text,
            "fingerprint": fingerprint(public)}


def key_status(keys_dir: Path, github: str) -> dict:
    public = key_path(keys_dir, github).with_name(key_id(github) + ".pub")
    if not public.exists():
        return {"github": github, "deploy_key": "missing"}
    return {"github": github, "deploy_key": "present", "fingerprint": fingerprint(public)}


def ssh_command(key: Path, known_hosts: Path) -> str:
    for path in (key, known_hosts):
        if not _SAFE_PATH.fullmatch(str(path)):
            raise FollowError("follow_store_unsafe", "follow store paths must be plain absolute paths")
    return " ".join(["ssh", "-i", shlex.quote(str(key)), "-o", "IdentitiesOnly=yes",
                     "-o", "IdentityAgent=none", "-o", "PasswordAuthentication=no",
                     "-o", f"UserKnownHostsFile={shlex.quote(str(known_hosts))}",
                     "-o", "GlobalKnownHostsFile=/dev/null", "-o", "StrictHostKeyChecking=yes",
                     "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-F", "/dev/null"])


# ------------------------------------------------------------------ git

def _git(repo: Path, *arguments: str, timeout: int = 60, env: dict | None = None) -> subprocess.CompletedProcess:
    return _run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *arguments],
                timeout=timeout, env=env)


def fetch(repo: Path, github: str, branch: str, *, key: Path, known_hosts: Path,
          timeout: int = FETCH_TIMEOUT) -> str:
    """Fetch the one tracked branch; return its head commit."""
    repo = Path(repo)
    if not (repo / ".git").is_dir():
        repo.mkdir(parents=True, exist_ok=True)
        result = _git(repo, "init", "-q")
        if result.returncode != 0:
            raise FollowError("follow_fetch_failed", f"git init failed: {_detail(result)}")
    url = f"git@github.com:{github}.git"
    if _git(repo, "remote", "get-url", "origin").returncode != 0:
        result = _git(repo, "remote", "add", "origin", url)
    else:
        result = _git(repo, "remote", "set-url", "origin", url)
    if result.returncode != 0:
        raise FollowError("follow_fetch_failed", f"git remote failed: {_detail(result)}")
    environment = _environment({"GIT_SSH_COMMAND": ssh_command(key, known_hosts)})
    result = _git(repo, "fetch", "--no-tags", "--quiet", "origin",
                  f"+refs/heads/{branch}:refs/remotes/origin/{branch}", timeout=timeout, env=environment)
    if result.returncode != 0:
        raise FollowError("follow_fetch_failed", f"fetch of {github} {branch} failed: {_detail(result)}")
    commit = _git(repo, "rev-parse", "--verify", "--quiet",
                  f"refs/remotes/origin/{branch}^{{commit}}").stdout.strip().lower()
    if not _COMMIT.fullmatch(commit):
        raise FollowError("follow_fetch_failed", f"{github} {branch} has no commit after fetch")
    return commit


def head(repo: Path) -> str | None:
    commit = _git(Path(repo), "rev-parse", "--verify", "--quiet", "HEAD^{commit}").stdout.strip().lower()
    return commit if _COMMIT.fullmatch(commit) else None


def checkout_argv(commit: str, branch: str | None) -> list[str]:
    """``checkout`` arguments: the tracked branch's name at the fetched commit.

    A named local branch lets ``--ref HEAD`` resolve to that branch, so a stack
    of the template's dev branch can be hosted. Without one, HEAD is detached.
    """
    if branch is None:
        return ["checkout", "--quiet", "--detach", "--force", commit]
    from sandbox.hosting.follow.spec import validate_branch
    return ["checkout", "--quiet", "--force", "-B", validate_branch(branch), commit]


def checkout(repo: Path, commit: str, branch: str | None = None) -> None:
    if not _COMMIT.fullmatch(commit or ""):
        raise FollowError("follow_checkout_failed", "checkout needs an exact commit")
    result = _git(Path(repo), *checkout_argv(commit, branch), timeout=300)
    if result.returncode != 0 or head(repo) != commit:
        raise FollowError("follow_checkout_failed", f"checkout of {commit[:12]} failed: {_detail(result)}")
    normalize_modes(Path(repo))


# What a checkout gets under a fixed umask of 0022, the user manager's default
# and so the controller path's; the poll itself runs with UMask=0077.
_TRACKED_MODES = {"100644": 0o644, "100755": 0o755}


def normalize_modes(repo: Path) -> None:
    """Give the checkout the modes it would have under umask 0022.

    The poll checks out under 0077, so files would be 0600 and directories 0700,
    and a prepare step that packs them with their modes (zip, tar, cp -p) ships
    files a non-root container cannot read (feedback d96eaf4c). Tracked files
    get exactly 0644 or 0755 from the index; every directory gets 0755; files
    an earlier attempt left untracked (build outputs, ``vendor/``) gain group
    and other read, and execute where the owner has it, as a 0022 write would
    have given them. A forced checkout keeps the modes of files it does not
    rewrite, so this repairs older clones. ``.git`` and symlinks are never
    changed or followed; the directories above the repo stay private.
    """
    repo = Path(repo)
    root = os.path.realpath(repo)
    listed = _git(repo, "ls-files", "--stage", "-z", timeout=120)
    if listed.returncode != 0:
        raise FollowError("follow_checkout_failed", f"git ls-files failed: {_detail(listed)}")
    tracked = {}
    for entry in (listed.stdout or "").split("\0"):
        meta, _tab, name = entry.partition("\t")
        mode = _TRACKED_MODES.get(meta.split(" ", 1)[0])
        if name and mode is not None:
            tracked[os.path.normpath(os.path.join(root, name))] = mode
    try:
        for directory, subdirectories, files in os.walk(root, followlinks=False):
            if directory == root and ".git" in subdirectories:
                subdirectories.remove(".git")
            _chmod_inside(root, directory, lambda current: 0o755)
            for name in files:
                path = os.path.join(directory, name)
                if os.path.islink(path):
                    continue
                wanted = tracked.get(path)
                _chmod_inside(root, path, (lambda current, wanted=wanted: wanted) if wanted else
                              (lambda current: current | 0o044 | (0o011 if current & 0o100 else 0)))
    except OSError as exc:
        raise FollowError("follow_checkout_failed",
                          f"could not set checkout file modes: {type(exc).__name__}") from None


def _chmod_inside(root: str, path: str, mode_for) -> None:
    """chmod ``path`` only when it really is inside ``root`` and not a symlink."""
    if os.path.islink(path) or os.path.commonpath([root, os.path.realpath(path)]) != root:
        return
    current = os.lstat(path).st_mode & 0o7777
    wanted = mode_for(current)
    if wanted != current:
        os.chmod(path, wanted)
