"""`sb host ... --here`: the installed owner drives its own host without SSH (Fix 5).

A controller runs owner hosting commands on a joined remote as a server CLI
durable job (``server_cli_dispatch``). On the host itself that SSH leg is not
available: a tagged Tailscale node cannot SSH to itself, and MagicDNS does not
resolve there. ``--here`` submits the same fixed server CLI job locally, the
way the follow timer's tick already does (``follow/tick.py`` ``job_argv``):
``job-start --local --server-cli`` bound to the installed owner's target,
revision and marker. The command then runs inside that job exactly as it
would for a controller, with ``--remote <origin>`` resolving to this host's
owner target.

Bounds:
- only the commands ``selected()`` admits (the stack commands except ``reap``,
  ``host apply --confirm``, and the follow owner commands);
- only for the installed owner: ``read_current_server_owner()`` must succeed,
  which needs the effective uid's own installed unit and CLI agreeing on marker
  and revision; the real uid must equal the effective uid, and ``$HOME`` and the
  Sandbox home must be that account's. A delegated sudo rule is the real
  boundary for anyone else (see docs/remote-server-cli.md);
- only with this host's owner origin record (``owner_origin``), written by
  ``sb remote service migrate`` from the controller and bound to the installed
  unit. The origin remote name, front door and control port come from it, never
  from the caller; ``--remote``, when given, must name that origin;
- ``stack up`` and ``apply`` never run on the caller's checkout: the bound
  commit is staged into an owner-private snapshot (``here_source``), as a
  controller stages it on the server.

Unlike the controller form, which prints the acceptance receipt, ``--here``
waits for the job (and for the apply job ``stack up``/``apply`` submit) and
prints the command's own last JSON line, exiting non-zero when the command or
a job failed; job ids go to stderr first.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import sys
import time

_TERMINAL = frozenset({"succeeded", "failed", "timed_out", "cancelled", "interrupted"})
STACK_ACTIONS = frozenset({"up", "list", "down", "ttl", "sweep", "limits"})
FOLLOW_ACTIONS = frozenset({"add", "status", "pause", "resume", "remove", "run-now", "key"})
# `--here` already means something else for these: they run on the host directly.
_EXISTING_HERE = {("login-url", None), ("stack", "reap"), ("follow", "tick"), ("follow", "apply")}
_CREDENTIAL_FIELDS = ("credential_scope", "credential_project", "credential_source",
                      "credential_source_revision", "credential_profile", "credential_profile_revision")
# Options --here rewrites or adds; argparse would accept any unique prefix of them.
_FULL_SPELLING = ("--here", "--remote", "--json", "--project-dir", "--template")
# Real host options that are also a prefix of one above (argparse takes the exact match).
_OWN_OPTIONS = frozenset({"--project"})
_RESERVED = ("--server-cli-stack-ref", "--server-cli-source-branch")
_JOB_ID_RE = re.compile(r"(?:[0-9a-f]{16}|[0-9a-f]{32})\Z")
DEFAULT_TIMEOUT = 900
WAIT_GRACE_SECONDS = 120
# The apply job `stack up` and `apply` submit has its own deadline (at least 1500 s).
CHILD_WAIT_SECONDS = 3600
MAX_RELAY_BYTES = 1_048_576


class HereRefusal(RuntimeError):
    def __init__(self, code: str, message: str, *, job_id: str | None = None, unknown: bool = False,
                 extra: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.job_id, self.unknown = code, message, job_id, unknown
        self.extra = dict(extra or {})


def claims(args) -> bool:
    """True when this invocation is a Fix 5 ``--here`` request (selected or not)."""
    if getattr(args, "cmd", "host") != "host" or not getattr(args, "here", False):
        return False
    action = getattr(args, "action", None)
    sub = getattr(args, "image_action", None)
    key = (action, None) if action == "login-url" else (action, sub)
    return key not in _EXISTING_HERE


def selected(args) -> bool:
    action, sub = getattr(args, "action", None), getattr(args, "image_action", None)
    return ((action == "apply" and bool(getattr(args, "confirm", False)))
            or (action == "stack" and sub in STACK_ACTIONS)
            or (action == "follow" and sub in FOLLOW_ACTIONS))


def _owner():
    from sandbox.application.server_cli import ServerCLIUnavailable, read_current_server_owner
    try:
        owner = read_current_server_owner()
    except ServerCLIUnavailable:
        raise HereRefusal("host_here_owner_unavailable",
                          "--here runs only as this host's installed Sandbox owner") from None
    if os.getuid() != owner.uid or os.geteuid() != owner.uid:
        raise HereRefusal("host_here_owner_unavailable",
                          "--here runs only as this host's installed Sandbox owner")
    return owner


def _require_owner_home(owner) -> None:
    """A sudo caller can keep its own $HOME; job store and folders must be the owner's."""
    home = Path(owner.home).resolve()
    try:
        env_home = Path(os.environ.get("HOME", "")).resolve()
        sandbox_home = Path(owner.sandbox_home).resolve()
    except (OSError, RuntimeError):
        env_home = sandbox_home = None
    if env_home != home or sandbox_home is None or home not in sandbox_home.parents:
        raise HereRefusal("host_here_owner_unavailable",
                          "--here needs the owner's own $HOME (use `sudo -H -u <owner>`, not -E)")


def _head(argv: list[str]) -> int:
    return argv.index("--") if "--" in argv else len(argv)


def check_argv(original: list[str]) -> None:
    """Refuse option spellings --here would have to guess about."""
    for token in original[:_head(original)]:
        option = token.partition("=")[0]
        if not option.startswith("--") or len(option) <= 2:
            continue
        if (option not in _FULL_SPELLING and option not in _OWN_OPTIONS
                and any(full.startswith(option) for full in _FULL_SPELLING)):
            raise HereRefusal("host_here_argv_invalid", f"spell {option} out in full with --here")
        if option in _RESERVED or any(full.startswith(option) for full in _RESERVED):
            raise HereRefusal("host_here_argv_invalid", f"{option} is reserved")


def inner_argv(original: list[str], origin: str) -> list[str]:
    """The command as a controller would send it: ``--here`` dropped, ``--remote`` the
    origin, and ``--json`` always (the result is relayed from the job's last JSON line)."""
    check_argv(original)
    argv = list(original)
    head = _head(argv)
    out, index = [], 0
    while index < head:
        token = argv[index]
        option, equals, _ = token.partition("=")
        if option == "--here" and not equals:
            index += 1
            continue
        if option == "--remote":
            index += 1 if equals else 2
            continue
        out.append(token)
        index += 1
    if "--json" not in out:
        out.append("--json")
    return out + ["--remote", origin] + argv[head:]


def _path_options(argv: list[str]):
    """(index, option, value, equals) for each --project-dir/--template before ``--``."""
    head, index = _head(argv), 0
    while index < head:
        option, equals, value = argv[index].partition("=")
        if option in {"--project-dir", "--template"}:
            if not equals:
                if index + 1 >= head:
                    raise HereRefusal("host_here_argv_invalid", f"{option} needs a value")
                yield index + 1, option, argv[index + 1], False
                index += 1
            else:
                yield index, option, value, True
        index += 1


def relocate_paths(argv: list[str], root: Path, staged: Path) -> list[str]:
    """Map the caller's paths onto the snapshot, as a controller maps them onto its
    staged copy: ``--project-dir`` must be the source root and ``--template`` inside it."""
    argv = list(argv)
    for index, option, value, equals in list(_path_options(argv)):
        path = Path(value).expanduser().resolve(strict=True)
        if option == "--project-dir" and path != root:
            raise HereRefusal("host_here_path_outside_source", "--project-dir must be the source root")
        if option == "--template" and root not in path.parents:
            raise HereRefusal("host_here_path_outside_source", "--template must be inside the source root")
        moved = str(staged / path.relative_to(root)) if path != root else str(staged)
        argv[index] = option + "=" + moved if equals else moved
    return argv


def _needs_source(args) -> bool:
    return getattr(args, "action", None) == "apply" or (
        getattr(args, "action", None) == "stack" and getattr(args, "image_action", None) == "up")


def _no_source_root(owner) -> Path:
    # No source is read: run in a fixed owner-private folder, never the
    # caller's working directory (a sudo caller's may not even be readable).
    root = Path(owner.sandbox_home) / "runtime" / "hosting" / "here"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root.resolve(strict=True)


def _source_root(args) -> Path:
    project_dir, template = getattr(args, "project_dir", None), getattr(args, "template", None)
    if project_dir is None and template:
        from sandbox.commands.host_stack import template_checkout
        root, _ = template_checkout(Path(template).expanduser().resolve(strict=True))
        return Path(root).resolve()
    return Path(project_dir or ".").expanduser().resolve(strict=True)


def _source_bindings(args, root: Path) -> tuple[list[str], str]:
    """The bound commit and the source options a controller adds for it.

    ``stack up`` binds its ref's commit, committed only, as the controller's
    ``committed_only`` staging does. ``apply`` binds the checkout's HEAD and
    branch, and refuses changes the controller's job would refuse.
    """
    from sandbox.hosting.server_cli_dispatch import SOURCE_BRANCH_OPTION, _branch_name_valid, _head_commit
    from sandbox.core import _remote
    if getattr(args, "action", None) == "stack":
        from sandbox.commands.host_stack import resolve_ref
        ref = resolve_ref(root, getattr(args, "ref", None))
        return ["--server-cli-stack-ref=" + json.dumps(
            {"ref": ref.ref, "commit": ref.commit, "ref_branch": ref.ref_branch},
            sort_keys=True, separators=(",", ":"))], ref.commit
    head = _head_commit(root)
    if head is None:
        raise HereRefusal("host_here_source_not_git", f"{root} has no commit to apply")
    dirty, untracked = _remote.capture_uncommitted(str(root))
    if dirty or untracked:
        # The controller stages these, and the apply job refuses them
        # (recovery_source_dirty); refuse before staging anything.
        raise HereRefusal("host_here_source_dirty",
                          f"{root} has uncommitted or untracked changes; commit or drop them first")
    try:
        branch = _remote.current_branch(root, allow_detached=True)
    except RuntimeError:
        branch = None
    # As the controller does: a detached checkout binds nothing.
    if branch is not None and _branch_name_valid(branch):
        return [SOURCE_BRANCH_OPTION + "=" + json.dumps(
            {"branch": branch, "commit": head}, sort_keys=True, separators=(",", ":"))], head
    return [], head


def _owner_label(args, original: list[str], top: Path) -> list[str]:
    """``stack up`` labels its owner from the checkout's ``user.name``; the staged
    copy has no repository config, so carry the live checkout's label."""
    if getattr(args, "action", None) != "stack" or getattr(args, "owner", None) is not None:
        return []
    if any(token.partition("=")[0] == "--owner" for token in original[:_head(original)]):
        return []
    from sandbox.hosting import here_source
    label = " ".join(here_source._git(top, "config", "user.name", check=False).split())
    return ["--owner=" + label] if label else []


def job_argv(args, original: list[str], *, owner, record: dict, capability: dict,
             request_id: str, root: Path, commit: str | None = None,
             bindings: list[str] = ()) -> list[str]:
    """``root`` is the folder the job runs in: a snapshot for stack up and apply
    (the caller's paths already relocated into it), the here folder otherwise."""
    origin = record["remote_name"]
    argv = inner_argv(original, origin)
    head = _head(argv)
    argv[head:head] = list(bindings)
    observed = capability.get("target_metadata") or {}
    routing = {"front_door": record["front_door"], "mcp_port": record["mcp_port"],
               **{key: observed[key] for key in ("origin_ipv4", "origin_ipv6", "tailscale_host")
                  if observed.get(key)}}
    from sandbox.jobs.server_cli_retained import routing_metadata
    routing = routing_metadata(routing)
    job = [owner.executable, "job-start", "--local", "--server-cli",
           "--server-cli-target", capability["target_identity"],
           "--server-cli-revision", capability["runtime_revision"],
           "--server-cli-marker", capability["ownership_marker"],
           "--server-cli-origin", origin,
           "--server-cli-target-metadata", json.dumps(routing, sort_keys=True, separators=(",", ":")),
           "--project-dir", str(root), "--exact-project-dir", "--request-id", request_id]
    if commit:
        job += ["--source-commit", commit]
    timeout = getattr(args, "timeout", None)
    job += ["--timeout", str(timeout if isinstance(timeout, int) and timeout > 0 else DEFAULT_TIMEOUT),
            "--json", "--", *argv]
    return job


def _capability(owner) -> dict:
    from sandbox.application.server_cli import ServerCLIUnavailable, capabilities
    try:
        capability = capabilities()
    except ServerCLIUnavailable:
        raise HereRefusal("server_cli_capability_unavailable",
                          "the installed server CLI job contract is unavailable") from None
    if (capability.get("target_identity") != owner.target_identity
            or capability.get("runtime_revision") != owner.runtime_revision
            or capability.get("ownership_marker") != owner.ownership_marker
            or capability.get("executable") != owner.executable):
        raise HereRefusal("server_cli_capability_unavailable",
                          "the installed owner changed while --here was being prepared")
    return capability


NOT_FOUND_LIMIT = 10


class _Missing(dict):
    """A readable registry with no row for the job."""


def job_state(job_id: str) -> dict | None:
    """The job's row, ``_Missing()`` when the registry has none, None when unreadable."""
    from sandbox.core import _paths
    path = _paths.RUNTIME_DIR / "jobs" / "registry.sqlite3"
    if not path.exists():
        return None
    try:
        from sandbox.jobs.registry import JobNotFound
        from sandbox.hosting.here_source import open_registry
        repository = open_registry(path)
        try:
            return repository.get(job_id)
        except JobNotFound:
            return _Missing()
        finally:
            repository.close()
    except Exception:  # noqa: BLE001 -- busy or unreadable: keep waiting
        return None


def _wait(job_id: str, deadline: float, *, view, sleep=time.sleep, clock=time.monotonic) -> dict:
    delay, missing = 0.2, 0
    while True:
        state = view(job_id)
        if isinstance(state, _Missing):
            missing += 1
            if missing >= NOT_FOUND_LIMIT:
                raise HereRefusal("host_here_job_not_found",
                                  f"job {job_id} is not in this host's job registry; inspect it with "
                                  f"`sb job-status {job_id}`, do not resubmit",
                                  job_id=job_id, unknown=True)
        else:
            missing = 0
        state = state or {}
        if state.get("lifecycle") in _TERMINAL:
            return state
        if clock() >= deadline:
            raise HereRefusal("host_here_job_unfinished",
                              f"job {job_id} has not finished; inspect it with `sb job-status {job_id}`",
                              job_id=job_id, unknown=True)
        sleep(delay)
        delay = min(delay * 2, 2.0)


RELAY_MAX_PAGES = 256


def _stdout_tail(job_id: str, *, service=None) -> str:
    """The last ``MAX_RELAY_BYTES`` of the job's stdout, read to its end in pages the
    job output API admits (at most ``MAX_OUTPUT_PAGE_BYTES``, and the output profile's
    own cap), following its cursor. The tail selection starts on an event boundary,
    so the range can exceed the budget; only the newest bytes are kept."""
    import base64
    from sandbox.jobs.models import MAX_OUTPUT_PAGE_BYTES, OutputQuery
    if service is None:
        from sandbox.application.context import durable_job_dependencies
        service = durable_job_dependencies()["job_service"]
    query = OutputQuery(stream="stdout", tail_bytes=MAX_RELAY_BYTES, max_bytes=MAX_OUTPUT_PAGE_BYTES,
                        encoding="base64")
    window, seen = b"", set()
    for _ in range(RELAY_MAX_PAGES):
        try:
            page = service.read_output(job_id, query)
        except RuntimeError as exc:
            if str(exc) != "output_unavailable":
                raise
            raise HereRefusal("host_here_output_unavailable", f"job {job_id}'s output is no longer retained; "
                              f"check `sb job-status {job_id}`", job_id=job_id) from None
        if not isinstance(page, dict):
            break
        window = (window + base64.b64decode(page.get("data") or "", validate=True))[-MAX_RELAY_BYTES:]
        cursor = page.get("cursor")
        if page.get("has_more") is not True:
            return window.decode("utf-8", errors="replace")
        if not isinstance(cursor, str) or cursor in seen:
            break
        seen.add(cursor)
        query = OutputQuery(stream="stdout", cursor=cursor, max_bytes=MAX_OUTPUT_PAGE_BYTES, encoding="base64")
    # The end was not reached: a partial tail could hold an older JSON line.
    raise HereRefusal("host_here_result_unreadable", f"job {job_id}'s result could not be read to its end; "
                      f"read it with `sb job-output {job_id}`", job_id=job_id)


def _last_json(text: str) -> dict | None:
    """The last line that parses as a JSON object (earlier ones if a later one does not)."""
    for line in reversed(text.splitlines()):
        if line.strip().startswith("{"):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
    return None


def _follow_child(result: dict, outer_id: str, *, view, sleep, clock) -> dict:
    """``stack up``/``apply`` inside the job only submit the apply job: wait for it too."""
    child = result.get("job_id")
    if (result.get("ok") is not True or not isinstance(child, str)
            or not _JOB_ID_RE.fullmatch(child) or child == outer_id):
        return result
    print(f"host --here: apply job {child}", file=sys.stderr)
    try:
        state = _wait(child, clock() + CHILD_WAIT_SECONDS, view=view, sleep=sleep, clock=clock)
    except HereRefusal as exc:
        exc.extra["outer_job_id"] = outer_id
        raise
    lifecycle = state.get("lifecycle")
    result = {**result, "outer_job_id": outer_id, "completion": lifecycle,
              "apply_exit_code": state.get("exit_code")}
    if lifecycle != "succeeded":
        result.update(ok=False, code=result.get("code") or f"host_here_apply_{lifecycle}")
    return result


def run(args, *, submit=None, view=None, output=None, owner=None, record=None,
        capability=None, sleep=time.sleep, clock=time.monotonic) -> dict:
    """Submit and wait; returns the command's own JSON result (with ``job_id``)."""
    from sandbox.hosting import owner_origin
    if not selected(args):
        raise HereRefusal("host_here_unsupported",
                          "--here supports host stack up|list|down|ttl|sweep|limits, "
                          "host apply --confirm and host follow add|status|pause|resume|remove|run-now|key")
    from sandbox.application.server_cli import server_cli_context_if_selected
    if server_cli_context_if_selected() is not None:
        raise HereRefusal("host_here_argv_invalid", "--here is never forwarded into a server CLI job")
    if any(getattr(args, field, None) is not None for field in _CREDENTIAL_FIELDS):
        raise HereRefusal("host_here_argv_invalid", "--here uses server-held credentials only")
    if (getattr(args, "server_cli_stack_ref", None) is not None
            or getattr(args, "server_cli_source_branch", None) is not None):
        raise HereRefusal("host_here_argv_invalid", "server CLI source bindings are reserved")
    original = list(getattr(args, "_server_cli_argv", None) or ())
    if not original or "--here" not in original[:_head(original)]:
        raise HereRefusal("host_here_argv_invalid", "spell --here out in full")
    from sandbox.hosting.host_exec import split_passthrough
    if split_passthrough(list(original))[1] is not None:
        raise HereRefusal("host_here_argv_invalid", "--here takes no command after --")
    check_argv(original)
    if owner is None:
        owner = _owner()
        _require_owner_home(owner)
    if record is None:
        try:
            record = owner_origin.read_for_owner(owner)
        except owner_origin.OwnerOriginUnavailable as exc:
            raise HereRefusal(exc.code, str(exc)) from None
    remote = getattr(args, "remote", None)
    if remote is not None and remote != record["remote_name"]:
        raise HereRefusal("host_here_remote_mismatch",
                          f"this host is remote {record['remote_name']!r} to its controller, not {remote!r}")
    capability = capability or _capability(owner)
    request_id = getattr(args, "request_id", None) or "host-here-" + secrets.token_hex(12)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", request_id) is None:
        raise HereRefusal("host_here_argv_invalid", "--request-id is invalid")
    if not _needs_source(args):
        if any(True for _ in _path_options(original)):
            raise HereRefusal("host_here_argv_invalid",
                              "--project-dir and --template apply only to stack up and apply with --here")
        root = _no_source_root(owner)
        return _submit_and_wait(args, job_argv(args, original, owner=owner, record=record,
                                capability=capability, request_id=request_id, root=root),
                                request_id, submit=submit, view=view, output=output, sleep=sleep, clock=clock)
    from sandbox.hosting import here_source
    try:
        root = _source_root(args)
        # Ownership first: no Git read of a source another account controls.
        top = here_source.toplevel(root)
        rel = root.relative_to(top)
        bindings, commit = _source_bindings(args, root)
        bindings += _owner_label(args, original, top)
        with here_source.snapshot(owner.sandbox_home, top, commit) as staged_top:
            staged = staged_top / rel
            relocated = relocate_paths(original, root, staged)
            argv = job_argv(args, relocated, owner=owner, record=record, capability=capability,
                            request_id=request_id, root=staged, commit=commit, bindings=bindings)
            return _submit_and_wait(args, argv, request_id, submit=submit, view=view, output=output,
                                    sleep=sleep, clock=clock)
    except here_source.SnapshotError as exc:
        raise HereRefusal(exc.code, exc.message) from None
    except OSError:
        if getattr(args, "_here_submitted", False):
            raise
        raise HereRefusal("host_here_argv_invalid",
                          "the project directory, template or a path option does not exist") from None


def _submit_and_wait(args, argv, request_id, *, submit, view, output, sleep, clock) -> dict:
    if submit is None:
        from sandbox.commands.host_stack import _submit as submit
    if view is None:
        view = job_state
    output = output or _stdout_tail
    from sandbox.commands.host_stack import StackCommandError, SUBMISSION_UNKNOWN
    args._here_submitted = True
    try:
        job_id = submit(argv)
    except StackCommandError as exc:
        raise HereRefusal("server_cli_acceptance_unknown" if exc.code == SUBMISSION_UNKNOWN
                          else "server_cli_execution_unavailable", exc.args[-1] if exc.args else str(exc),
                          unknown=exc.code == SUBMISSION_UNKNOWN) from None
    args._here_job_id = job_id
    print(f"host --here: job {job_id} (request {request_id})", file=sys.stderr)
    timeout = int(argv[argv.index("--timeout") + 1])
    state = _wait(job_id, clock() + timeout + WAIT_GRACE_SECONDS, view=view, sleep=sleep, clock=clock)
    text = output(job_id)
    result = _last_json(text)
    if result is None:
        lifecycle = state.get("lifecycle")
        code = ("host_here_result_unreadable" if text.strip() else
                f"host_here_job_{lifecycle}" if lifecycle != "succeeded" else "host_here_no_result")
        raise HereRefusal(code, f"job {job_id} ended {lifecycle} without a readable JSON result; "
                          f"read it with `sb job-output {job_id}`", job_id=job_id)
    if state.get("lifecycle") == "succeeded" and _needs_source(args):
        result = _follow_child(result, job_id, view=view, sleep=sleep, clock=clock)
    result.setdefault("job_id", job_id)
    if state.get("lifecycle") != "succeeded" and result.get("ok") is not False:
        result = {**result, "ok": False}
    return result


def _known_code(exc: Exception) -> str | None:
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) else None


def dispatch(args) -> None:
    """One JSON line on stdout, always; exits 1 unless the command succeeded."""
    try:
        payload = run(args)
    except HereRefusal as exc:
        payload = {"ok": False, "code": exc.code, "error": exc.message,
                   "acceptance": "unknown" if exc.unknown else ("accepted" if exc.job_id else "not_accepted")}
        if exc.job_id:
            payload["job_id"] = exc.job_id
        payload.update({key: value for key, value in exc.extra.items() if key not in payload})
    except Exception as exc:  # noqa: BLE001 -- the contract is one JSON line, never a traceback
        submitted = bool(getattr(args, "_here_submitted", False))
        job_id = getattr(args, "_here_job_id", None)
        payload = {"ok": False, "code": _known_code(exc) or "host_here_failed",
                   "error": f"{type(exc).__name__} " + ("after submission" if submitted else "before submission"),
                   "acceptance": ("accepted" if job_id else "unknown") if submitted else "not_accepted"}
        if job_id:
            payload["job_id"] = job_id
    print(json.dumps(payload, sort_keys=True))
    if payload.get("ok") is False:
        raise SystemExit(1)
