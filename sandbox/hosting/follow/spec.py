"""Parse and validate a follow declaration (spec 089, FR-002).

Everything here is pure: it checks owner input and returns plain JSON-ready
dicts. Nothing is written and no command runs.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
REPO_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
GITHUB_RE = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100})$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
PLACEHOLDER_RE = re.compile(r"\{(dir|name|repo|commit)(?::([a-z0-9-]+))?\}")
ROUTING_KEYS = ("front_door", "mcp_port")
LIFETIMES = ("temporary", "hosted", "permanent")
MAX_REPOS = 8
MAX_PREPARE_ARGS = 64
PREPARE_TIMEOUT_DEFAULT = 1200
PREPARE_TIMEOUT_MIN = 60
PREPARE_TIMEOUT_MAX = 7200
# Room for the stack apply (its own deadline is at least 1500 s) and the wait.
APPLY_ALLOWANCE_SECONDS = 3000


class FollowError(Exception):
    """A refusal with a stable code; the message never carries a secret."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def validate_name(value: object) -> str:
    if not isinstance(value, str) or not NAME_RE.fullmatch(value):
        raise FollowError("follow_name_invalid",
                          "--name must be a stack slug: lowercase letters, digits and '-', at most 40")
    return value


def validate_branch(value: object) -> str:
    if not isinstance(value, str):
        raise FollowError("follow_branch_invalid", "a branch name must be text")
    return _branch(value)


def _branch(value: str) -> str:
    if (not BRANCH_RE.fullmatch(value) or value.startswith(("-", "/")) or value.endswith(("/", ".", ".lock"))
            or ".." in value or "//" in value or "/." in value or value == "HEAD"):
        raise FollowError("follow_branch_invalid", f"branch {value!r} is not a valid branch name")
    return value


def parse_repo(value: object) -> dict:
    """``NAME=OWNER/REPO@BRANCH`` (GitHub over SSH only)."""
    if not isinstance(value, str) or "=" not in value or "@" not in value:
        raise FollowError("follow_repo_invalid", "--repo must be NAME=OWNER/REPO@BRANCH")
    name, _sep, rest = value.partition("=")
    github, _sep, branch = rest.rpartition("@")
    if not REPO_NAME_RE.fullmatch(name):
        raise FollowError("follow_repo_invalid", f"repository name {name!r} must be lowercase letters, digits, '-'")
    if github.endswith(".git"):
        github = github[:-4]
    match = GITHUB_RE.fullmatch(github)
    if match is None or match.group(2) in {".", ".."}:
        raise FollowError("follow_repo_invalid",
                          f"{github!r} is not a GitHub OWNER/REPO; only GitHub repositories are supported")
    return {"name": name, "github": github, "branch": _branch(branch)}


def _check_placeholders(text: str, repos: set[str], where: str) -> None:
    for match in PLACEHOLDER_RE.finditer(text):
        kind, argument = match.group(1), match.group(2)
        if kind in {"repo", "commit"}:
            if argument not in repos:
                raise FollowError("follow_placeholder_invalid",
                                  f"{where} names {match.group(0)}, but no --repo is called {argument!r}")
        elif argument is not None:
            raise FollowError("follow_placeholder_invalid", f"{where}: {{{kind}}} takes no argument")


def parse_prepare(value: object, repos: set[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, str):
        raise FollowError("follow_prepare_invalid", "--prepare must be one command line")
    try:
        argv = shlex.split(value)
    except ValueError as exc:
        raise FollowError("follow_prepare_invalid", f"--prepare does not parse: {exc}") from None
    if not argv:
        raise FollowError("follow_prepare_invalid", "--prepare names no program")
    if len(argv) > MAX_PREPARE_ARGS or any(len(item) > 4096 or "\0" in item for item in argv):
        raise FollowError("follow_prepare_invalid", "--prepare is too long")
    for item in argv:
        _check_placeholders(item, repos, "--prepare")
    return argv


def substitute(text: str, *, work_dir: Path, name: str, repo_dirs: dict[str, Path],
               heads: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        kind, argument = match.group(1), match.group(2)
        if kind == "dir":
            return str(work_dir)
        if kind == "name":
            return name
        if kind == "repo":
            return str(repo_dirs[argument])
        return heads[argument]
    return PLACEHOLDER_RE.sub(replace, text)


def build(args, *, origin: str, routing: dict) -> dict:
    """The stored declaration from ``host follow add`` arguments."""
    name = validate_name(getattr(args, "name", None))
    raw_repos = getattr(args, "follow_repo", None) or []
    if not raw_repos:
        raise FollowError("follow_repo_missing", "add needs at least one --repo NAME=OWNER/REPO@BRANCH")
    if len(raw_repos) > MAX_REPOS:
        raise FollowError("follow_repo_invalid", f"at most {MAX_REPOS} --repo values")
    repos = [parse_repo(item) for item in raw_repos]
    names = [item["name"] for item in repos]
    if len(set(names)) != len(names):
        raise FollowError("follow_repo_invalid", "--repo names must be distinct")
    template = getattr(args, "stack_template", None)
    if not isinstance(template, str) or not template.strip() or len(template) > 1024 or "\0" in template:
        raise FollowError("follow_template_invalid",
                          "add needs --stack-template PATH ({dir} or {repo:NAME} relative)")
    _check_placeholders(template, set(names), "--stack-template")
    if not template.startswith("{"):
        raise FollowError("follow_template_invalid",
                          "--stack-template must start with {dir} or {repo:NAME} so it stays in the follow's checkouts")
    owner = getattr(args, "owner", None)
    if not isinstance(owner, str) or not owner.strip() or len(owner) > 200:
        raise FollowError("follow_owner_missing", "add needs --owner LABEL (the stack owner recorded on the remote)")
    ref = getattr(args, "ref", None) or "HEAD"
    if not isinstance(ref, str) or ref.startswith("-") or len(ref) > 255 or any(ord(c) < 33 for c in ref):
        raise FollowError("follow_ref_invalid", "--ref must be a Git ref in the template's checkout")
    lifetime = getattr(args, "lifetime", None)
    if lifetime is not None and lifetime not in LIFETIMES:
        raise FollowError("follow_lifetime_invalid", "--lifetime must be temporary, hosted or permanent")
    environment = getattr(args, "environment", None)
    if environment is not None and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", environment):
        raise FollowError("follow_environment_invalid", "--environment is not a valid environment name")
    timeout = getattr(args, "prepare_timeout", None)
    timeout = PREPARE_TIMEOUT_DEFAULT if timeout is None else timeout
    if not isinstance(timeout, int) or not PREPARE_TIMEOUT_MIN <= timeout <= PREPARE_TIMEOUT_MAX:
        raise FollowError("follow_prepare_invalid",
                          f"--prepare-timeout must be {PREPARE_TIMEOUT_MIN}-{PREPARE_TIMEOUT_MAX} seconds")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", origin or ""):
        raise FollowError("follow_requires_joined_remote", "the follow's origin remote name is unavailable")
    kept = {key: routing[key] for key in ROUTING_KEYS if routing.get(key) is not None}
    if kept.get("front_door") not in ("caddy", "nginx"):
        raise FollowError("follow_requires_joined_remote", "the remote's front-door metadata is unavailable")
    return {
        "name": name, "remote": origin, "routing": kept,
        "repos": repos,
        "template": template, "ref": ref, "owner": owner.strip(),
        "environment": environment, "lifetime": lifetime,
        "generate": bool(getattr(args, "generate_secrets", False)),
        "prepare": parse_prepare(getattr(args, "prepare", None), set(names)),
        "prepare_timeout": timeout,
    }


def job_timeout(spec: dict) -> int:
    return int(spec["prepare_timeout"]) + APPLY_ALLOWANCE_SECONDS


def template_repo(template: str) -> str | None:
    """The ``--repo`` whose checkout holds the stack template, or None for ``{dir}``."""
    match = re.match(r"\{repo:([a-z0-9-]+)\}", template or "")
    return match.group(1) if match else None


# Second-level labels that country-code registries sell names under
# (example.co.uk, example.com.bd). Sandbox ships no public suffix list, so a
# two-letter top-level domain under one of these labels is treated as a
# public suffix.
_SECOND_LEVEL_SUFFIXES = frozenset({
    "ac", "co", "com", "edu", "firm", "gen", "go", "gob", "gouv", "gov", "govt", "info", "biz",
    "ltd", "mil", "ne", "net", "nic", "nom", "or", "org", "plc", "sch", "web",
})


def registrable_domain(hostname: str) -> str:
    """The name a registrant owns: ``a.b.example.co.uk`` -> ``example.co.uk``."""
    labels = [label for label in hostname.strip().strip(".").lower().split(".") if label]
    size = 3 if (len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL_SUFFIXES) else 2
    return ".".join(labels[-size:])


def is_apex(hostname: str) -> bool:
    """A bare registrable name (``example.com``, ``example.co.uk``): never a stack hostname here."""
    labels = [label for label in hostname.strip().strip(".").lower().split(".") if label]
    return len(labels) <= 2 or ".".join(labels) == registrable_domain(hostname)
