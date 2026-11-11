"""`sb host stack up|list|down`: per-slug hosting stacks from one template (spec 071).

`up` renders a committed template for one slug at a controller-resolved commit,
checks that commit out into a detached worktree, and submits the existing
durable `job-start` + `host apply` path from it. Nothing here mutates a remote
except through that path (`up`) and the existing destroy (`down`).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath

import sandbox.core._hosting as hosting
import sandbox.core._remote as remote
from sandbox.core import die, info
from sandbox.core._config import ensure_pyyaml
from sandbox.hosting import hostname_ownership, stack_registry, stack_template
from sandbox.hosting.lifetime import client as lifetime_client, hoststore, naming
from sandbox.hosting.stack_template import StackRef, StackTemplateError
from sandbox.hosting.server_cli_target import host_target, is_owner_target

CONFIRMATION_REQUIRED = "stack_confirmation_required"
REF_UNRESOLVED = "stack_ref_unresolved"
SLUG_IN_USE = "stack_slug_in_use"
APPLY_IN_PROGRESS = "stack_apply_in_progress"
NOT_FOUND = "stack_not_found"
SUBMISSION_FAILED = "stack_submission_failed"
SUBMISSION_UNKNOWN = "stack_submission_unknown"
DESTROY_FAILED = "stack_destroy_failed"
PROBE_UNAVAILABLE = "stack_port_probe_unavailable"
SECRETS_MISSING = "stack_secrets_missing"
REMOTE_UNKNOWN = "stack_remote_unknown"

ACTIVE_LIFECYCLES = frozenset({"accepted", "queued", "running", "cancelling"})
_PORT_PROBE = (
    "command -v ss >/dev/null 2>&1 || { echo 'ss is not installed' >&2; exit 3; }; "
    "echo '--listen--'; ss -H -ltn | awk '{print $4}'; "
    "echo '--docker--'; "
    "docker ps --format '{{.Label \"com.docker.compose.project\"}}|{{.Ports}}' 2>/dev/null || true"
)
_PUBLISHED_RE = re.compile(r":(\d+)->")


class StackCommandError(hosting.HostingRefusal):
    pass


# ---------------------------------------------------------------- git helpers

def _git(cwd: Path, *arguments: str, check: bool = True, timeout: int = 60) -> str:
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(cwd), *arguments],
        env=remote.git_environment(), capture_output=True, text=True, check=False,
        timeout=timeout)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or ["git failed"]
        raise RuntimeError(f"git {arguments[0]} failed: {detail[0][:300]}")
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def template_checkout(template_path: Path) -> tuple[Path, str]:
    """Return the template's checkout root and its path relative to it."""
    if not template_path.is_file():
        raise StackTemplateError(stack_template.TEMPLATE_INVALID, f"template not found: {template_path}")
    top = _git(template_path.parent, "rev-parse", "--show-toplevel", check=False)
    if not top:
        raise StackTemplateError(stack_template.TEMPLATE_INVALID,
                                 "the template must live in a Git checkout")
    checkout = Path(top).resolve()
    return checkout, template_path.resolve().relative_to(checkout).as_posix()


def resolve_ref(checkout: Path, ref: str | None) -> StackRef:
    """Resolve ``ref`` on the controller to a commit and the branch it names."""
    ref = (ref or "HEAD").strip()
    if not ref or ref.startswith("-"):
        raise StackCommandError(REF_UNRESOLVED, f"--ref {ref!r} is not a Git ref")
    commit = _git(checkout, "rev-parse", "--verify", "--quiet", "--end-of-options",
                  f"{ref}^{{commit}}", check=False).lower()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise StackCommandError(REF_UNRESOLVED, f"--ref {ref!r} does not name a commit in {checkout}")
    # A leading "-" was refused above; without --verify, rev-parse would echo
    # --end-of-options back as output.
    full = _git(checkout, "rev-parse", "--symbolic-full-name", ref, check=False).splitlines()
    full = full[-1].strip() if full else ""
    branch = ""
    if full.startswith("refs/heads/"):
        branch = full[len("refs/heads/"):]
    elif full.startswith("refs/remotes/"):
        _remote_name, _sep, rest = full[len("refs/remotes/"):].partition("/")
        branch = "" if rest == "HEAD" else rest
    return StackRef(ref=ref, commit=commit, ref_branch=branch)


def load_template(checkout: Path, relpath: str, commit: str) -> stack_template.StackTemplate:
    """Parse the template exactly as committed at ``commit``."""
    text = subprocess.run(
        ["git", "-C", str(checkout), "show", f"{commit}:{relpath}"],
        env=remote.git_environment(), capture_output=True, text=True, check=False, timeout=60)
    if text.returncode != 0:
        raise StackTemplateError(stack_template.TEMPLATE_INVALID,
                                 f"{relpath} is not committed at {commit[:12]}")
    ensure_pyyaml()
    import yaml
    try:
        data = yaml.safe_load(text.stdout) or {}
    except yaml.YAMLError as exc:
        raise StackTemplateError(stack_template.TEMPLATE_INVALID, f"invalid template YAML: {exc}") from None
    return stack_template.parse_template(data)


def _add_worktree(checkout: Path, slug: str, commit: str) -> Path:
    parent = stack_registry.stack_dir(slug) / "source"
    parent.mkdir(parents=True, exist_ok=True)
    path = parent.resolve() / f"{commit[:12]}-{secrets.token_hex(3)}"
    _git(checkout, "worktree", "add", "--detach", "--quiet", str(path), commit, timeout=600)
    return path


def _remove_worktree(checkout: str | None, path: str) -> None:
    target = Path(path)
    root = stack_registry.stacks_dir().resolve()
    if checkout and Path(checkout).is_dir():
        _git(Path(checkout), "worktree", "remove", "--force", str(target), check=False, timeout=300)
    if target.exists() and root in target.resolve().parents:
        shutil.rmtree(target, ignore_errors=True)
    if checkout and Path(checkout).is_dir():
        _git(Path(checkout), "worktree", "prune", check=False)


# ------------------------------------------------------------- remote probe

def parse_port_probe(output: str) -> dict[int, set[str]]:
    """Map listening/published ports to the Compose projects publishing them."""
    bound: dict[int, set[str]] = {}
    section = None
    for line in output.splitlines():
        line = line.strip()
        if line in {"--listen--", "--docker--"}:
            section = line
            continue
        if not line:
            continue
        if section == "--listen--":
            port = line.rsplit(":", 1)[-1]
            if port.isdigit():
                bound.setdefault(int(port), set())
        elif section == "--docker--":
            owner, _sep, ports = line.partition("|")
            for match in _PUBLISHED_RE.finditer(ports):
                bound.setdefault(int(match.group(1)), set()).add(owner or "a non-Compose container")
    return bound


def probe_ports(entry: dict) -> dict[int, set[str]]:
    """One read-only SSH call listing listeners and Docker-published ports."""
    try:
        result = remote.ssh_run(entry, _PORT_PROBE, timeout=60)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        raise StackCommandError(
            PROBE_UNAVAILABLE, "could not list listening ports on the remote: "
            + remote.redact_text(str(exc))[:300]) from None
    output = result.stdout or ""
    if result.returncode != 0:
        detail = remote.redact_text((result.stderr or output or "no output").strip())[-300:]
        raise StackCommandError(
            PROBE_UNAVAILABLE, f"could not list listening ports on the remote (exit {result.returncode}): {detail}")
    if "--listen--" not in output:
        raise StackCommandError(PROBE_UNAVAILABLE, "the remote port probe returned no listener list")
    return parse_port_probe(output)


# ---------------------------------------------------------------- durable job

def job_view(job_id: str | None) -> dict | None:
    if not job_id:
        return None
    from sandbox.core import _paths
    path = _paths.RUNTIME_DIR / "jobs" / "registry.sqlite3"
    if not path.exists():
        return None
    try:
        from sandbox.jobs.registry import JobRepository
        repository = JobRepository(path)
        try:
            row = repository.get(job_id)
        finally:
            repository.close()
    except Exception:
        return None
    if not isinstance(row, dict):
        return None
    return {key: row.get(key) for key in ("lifecycle", "exit_code", "accepted_at", "started_at",
                                          "finished_at")}


def _last_apply_view(record: dict) -> dict:
    last = dict(record.get("last_apply") or {})
    job = job_view(last.get("job_id"))
    if job and job.get("lifecycle"):
        last["status"] = job["lifecycle"]
        last["finished_at"] = job.get("finished_at")
    last["at"] = (job or {}).get("finished_at") or last.get("submitted_at")
    return last


def _refuse_if_active(record: dict | None) -> None:
    if not record:
        return
    last = record.get("last_apply") or {}
    job = job_view(last.get("job_id"))
    if (not job and (last.get("job_id") or last.get("status") in
            {"submitting", "submitted", "acceptance_unknown", "submission_failed"})):
        raise StackCommandError(SUBMISSION_UNKNOWN,
            f"stack {record.get('slug')} original apply request {last.get('request_id')} "
            "has no established acceptance/outcome; inspect that original request/job before another apply")
    if job and job.get("lifecycle") in ACTIVE_LIFECYCLES:
        raise StackCommandError(
            APPLY_IN_PROGRESS,
            f"stack {record.get('slug')} apply job {record['last_apply']['job_id']} is "
            f"{job['lifecycle']}; wait for it (`sb job-status {record['last_apply']['job_id']}`)")


def _submission_timeout(validated: dict) -> int:
    return max(900, int(validated["compose"].get("build_timeout_seconds", 900)) + 600)


def _submit(argv: list[str]) -> str:
    from sandbox.core._paths import BASE
    from sandbox.services.environment import compatible_subprocess_environment
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=180,
                                env=compatible_subprocess_environment({"SANDBOX_HOME": str(BASE)}))
    except subprocess.TimeoutExpired:
        raise StackCommandError(SUBMISSION_UNKNOWN,
            "job-start acceptance is unknown after its response deadline; inspect the original request") from None
    except OSError:
        raise StackCommandError(SUBMISSION_FAILED, "job-start could not be launched") from None
    lines = [line for line in (result.stdout or "").splitlines() if line.strip().startswith("{")]
    payload = None
    if lines:
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError:
            payload = None
    job_id = payload.get("job_id") if isinstance(payload, dict) else None
    if (result.returncode != 0 or not isinstance(job_id, str)
            or re.fullmatch(r"(?:[0-9a-f]{16}|[0-9a-f]{32})", job_id) is None
            or payload.get("ok") is False):
        if (isinstance(payload, dict) and payload.get("status") == "refused"
                and payload.get("acceptance") == "not_accepted"):
            raise StackCommandError(SUBMISSION_FAILED, "job-start explicitly refused before acceptance")
        raise StackCommandError(SUBMISSION_UNKNOWN,
            "job-start returned no valid original acceptance receipt; inspect the original request")
    return job_id


# ------------------------------------------------------------------ helpers

def _emit(args, payload: dict, text: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, sort_keys=True))
    else:
        print(text)


def _refuse(args, exc: Exception) -> None:
    code = getattr(exc, "code", None) or stack_template.TEMPLATE_INVALID
    message = remote.redact_text(str(exc))[:2000]
    if getattr(args, "json", False):
        print(json.dumps({"ok": False, "code": code, "error": message}, sort_keys=True))
        raise SystemExit(1)
    die(message if message.startswith(code) else f"{code}: {message}")


def _entry(remote_name: str | None) -> dict:
    if not remote_name:
        raise StackCommandError(REMOTE_UNKNOWN, "--remote is required")
    entry = host_target(remote_name)
    if not isinstance(entry, dict) and not is_owner_target(entry):
        raise StackCommandError(REMOTE_UNKNOWN, f"no remote named {remote_name!r}")
    return entry


def _reserved_loopback_ports(state: dict, remote_name: str) -> set[int]:
    return {record["loopback_port"] for key, record in (state.get("hosts") or {}).items()
            if isinstance(key, str) and key.startswith(f"{remote_name}/")
            and isinstance(record, dict) and isinstance(record.get("loopback_port"), int)}


def _rendered_manifest(template, *, slug, ref, ports, source_root, template_path) -> dict:
    rendered = stack_template.render(template, slug=slug, ref=ref, ports=ports,
                                     source_root=source_root, template_path=template_path)
    target = stack_registry.stack_dir(slug) / stack_template.MANIFEST_NAME
    return {**rendered, "_manifest_path": str(target), "_manifest_root": str(target.parent)}


def _validate_rendered(rendered: dict, environment: str | None, *, require_checkout: bool) -> dict:
    try:
        return hosting.validate_loaded(Path(rendered["_manifest_root"]), rendered, environment,
                                       require_checkout=require_checkout)
    except hosting.HostingRefusal:
        raise
    except hosting.HostingError as exc:
        raise StackTemplateError(stack_template.TEMPLATE_INVALID,
                                 f"the rendered manifest is invalid: {exc}") from None


def _export_compose_files(checkout: Path, commit: str, source_rel: PurePosixPath,
                          files: list, destination: Path) -> None:
    """Copy just the compose files a plan validates, from the commit, into a temp dir."""
    for name in files if isinstance(files, list) else []:
        if not isinstance(name, str) or not name or Path(name).is_absolute():
            continue
        spec = (source_rel / name).as_posix() if str(source_rel) != "." else PurePosixPath(name).as_posix()
        result = subprocess.run(["git", "-C", str(checkout), "show", f"{commit}:{spec}"],
                                env=remote.git_environment(), capture_output=True, check=False,
                                timeout=60)
        if result.returncode != 0:
            continue
        target = (destination / name).resolve()
        if destination.resolve() not in target.parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(result.stdout)


def _select_environment(template, requested: str | None) -> str:
    names = template.environment_names()
    if requested:
        if requested not in names:
            raise StackTemplateError(stack_template.TEMPLATE_INVALID,
                                     f"unknown environment {requested!r}; available: {', '.join(names)}")
        return requested
    if len(names) != 1:
        raise StackTemplateError(stack_template.TEMPLATE_INVALID,
                                 "--environment is required when the template has several environments")
    return names[0]


def _write_manifest(path: Path, rendered: dict) -> None:
    ensure_pyyaml()
    import yaml
    body = {key: value for key, value in rendered.items() if not str(key).startswith("_")}
    text = ("# Generated by `sb host stack up`; do not edit. Re-run stack up instead.\n"
            + yaml.safe_dump(body, sort_keys=False, default_flow_style=False))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _secrets_gate(validated: dict, *, generate: bool, confirm: bool) -> list[str]:
    """Return missing secret source names; with --generate, create generated ones."""
    from sandbox.commands import hosting as hosting_cmd
    _values, missing = hosting_cmd._secret_status(validated)
    if not confirm or not missing:
        return missing
    generated = set(validated["secrets"]["generated"].values())
    required = sorted(set(missing) - generated)
    if required:
        raise StackCommandError(
            SECRETS_MISSING, "required hosting secrets are missing: " + ", ".join(required)
            + "; set each with `sb secrets` before stack up")
    if not generate:
        raise StackCommandError(
            SECRETS_MISSING, "generated hosting secrets are missing: " + ", ".join(sorted(missing))
            + "; re-run with --generate to create them")
    for key in sorted(missing):
        hosting_cmd.personal_secrets.write_secret(key, hosting_cmd.personal_secrets.generate_secret())
    return []


# ---------------------------------------------------------------- lifetime

_COPY_RE = re.compile(r"[0-9a-f]{8}")


def _store(entry: dict, caller: dict | None) -> lifetime_client.StoreClient:
    """The remote lifetime store; tests replace this to use a local directory."""
    return lifetime_client.StoreClient(lifetime_client.ssh_runner(entry), caller)


def _caller(checkout: Path, args) -> dict:
    return lifetime_client.controller_owner(naming.owner_label(checkout, getattr(args, "owner", None)))


def _requested_lifetime(args, ref: StackRef, dev_branch: str) -> tuple[str, str, int | None, bool]:
    """(lifetime, source, ttl_seconds, new_ttl) from the flags; hosted only for dev."""
    lifetime = getattr(args, "lifetime", None)
    ttl_text = getattr(args, "ttl", None)
    if lifetime is not None and lifetime not in hoststore.LIFETIMES:
        raise StackCommandError(hoststore.LIFETIME_INVALID,
                                "--lifetime must be temporary, hosted or permanent")
    if lifetime == "hosted" and ref.ref_branch != dev_branch:
        raise StackCommandError(
            hoststore.LIFETIME_INVALID,
            f"--lifetime hosted is only for the dev branch {dev_branch}; {ref.ref!r} is "
            f"{ref.ref_branch or 'a detached commit'}")
    effective = lifetime or "temporary"
    try:
        ttl = hoststore.parse_ttl(ttl_text) if ttl_text is not None else None
    except hoststore.StoreRefusal as exc:
        raise StackCommandError(exc.code, exc.message) from None
    if ttl is not None and effective != "temporary":
        raise StackCommandError(hoststore.TTL_INVALID, "--ttl is only for a temporary stack")
    return effective, "flag" if lifetime else "default", ttl, ttl is not None


def _copy_id(args, previous: dict | None) -> tuple[str, bool]:
    explicit = getattr(args, "copy", None)
    if explicit is not None:
        if not _COPY_RE.fullmatch(str(explicit)):
            raise StackCommandError(hoststore.REQUEST_INVALID, "--copy must be the 8-character copy id "
                                    "shown by `sb host stack list`")
        return str(explicit), True
    recorded = (previous or {}).get("copy_id")
    if isinstance(recorded, str) and _COPY_RE.fullmatch(recorded):
        return recorded, False
    return secrets.token_hex(4), False


def _remote_records(listing: dict, remote_name: str) -> list[dict]:
    return [item for item in listing.get("records") or [] if isinstance(item, dict)]


def _remote_ports(records: list[dict], compose_project: str) -> dict[int, str]:
    ports: dict[int, str] = {}
    for item in records:
        if item.get("compose_project") == compose_project or item.get("state") == "reaped":
            continue
        for port in (item.get("ports") or {}).values():
            if isinstance(port, int):
                ports[port] = str(item.get("slug") or item.get("compose_project"))
    return ports


def _remote_holdings(records: list[dict], remote_name: str) -> list[dict]:
    """Remote records shaped like stack records for the hostname guard."""
    return [{"remote": remote_name, "project": item.get("project"), "hostnames": item.get("hostnames") or []}
            for item in records if item.get("state") != "reaped"]


def _draft(*, project, environment, slug, copy_id, caller, repo, ref, dev_branch, lifetime, source,
           ttl, template_path, ports, hostnames, front_door, now) -> dict:
    stamp = hoststore.to_rfc3339(now)
    temporary = lifetime == "temporary"
    ttl = (ttl or hoststore.DEFAULT_TTL_SECONDS) if temporary else None
    return {
        "schema_version": hoststore.SCHEMA,
        "compose_project": hoststore.compose_project_for(project, environment),
        "project": project, "environment": environment, "kind": "stack",
        "lifetime": lifetime, "lifetime_source": source, "ttl_seconds": ttl,
        "expires_at": hoststore.to_rfc3339(now + ttl) if temporary else None, "extended_count": 0,
        "state": "active", "owner": caller, "created_at": stamp, "updated_at": stamp,
        "classified_by": None, "classified_at": None,
        "front_door": front_door, "dns_mode": "unknown", "hostnames": sorted(hostnames),
        "slug": slug, "copy_id": copy_id, "repo": repo, "ref": ref.ref, "ref_branch": ref.ref_branch,
        "commit": ref.commit, "dev_branch": dev_branch, "canonical": lifetime == "hosted",
        "template": str(template_path), "ports": dict(ports),
        "merge": None, "reap_run": None, "overrides": [],
    }


# The agreed panel fields (spec 077, FR-026) that `stack list --json` returns per row.
RECORD_FIELDS = (
    "schema_version", "compose_project", "project", "environment", "kind", "lifetime",
    "lifetime_source", "ttl_seconds", "expires_at", "extended_count", "state", "owner",
    "created_at", "updated_at", "classified_by", "classified_at", "slug", "copy_id", "repo",
    "ref", "ref_branch", "commit", "dev_branch", "canonical", "template", "ports", "hostnames",
    "front_door", "dns_mode", "merge", "reap", "reap_run")


def _lifetime_fields(record: dict) -> dict:
    return {"lifetime": record.get("lifetime"), "lifetime_source": record.get("lifetime_source"),
            "ttl_seconds": record.get("ttl_seconds"), "expires_at": record.get("expires_at"),
            "extended_count": record.get("extended_count", 0), "owner": record.get("owner"),
            "copy_id": record.get("copy_id"), "canonical": bool(record.get("canonical")),
            "state": record.get("state"), "reap": record.get("reap")}


# ----------------------------------------------------------------- commands

def _up(args) -> dict:
    plan, confirm = bool(getattr(args, "plan", False)), bool(getattr(args, "confirm", False))
    if plan == confirm:
        raise StackCommandError(CONFIRMATION_REQUIRED, "stack up needs exactly one of --plan or --confirm")
    if not getattr(args, "template", None):
        raise StackCommandError(CONFIRMATION_REQUIRED, "stack up needs --template")
    entry = _entry(getattr(args, "remote", None))
    remote_name = args.remote
    template_path = Path(args.template).expanduser().resolve()
    checkout, relpath = template_checkout(template_path)
    ref = resolve_ref(checkout, getattr(args, "ref", None))
    template = load_template(checkout, relpath, ref.commit)
    lifetime, lifetime_source, ttl, new_ttl = _requested_lifetime(args, ref, template.dev_branch)
    caller = _caller(checkout, args)
    repo = naming.repo_identity(checkout)
    store = _store(entry, caller)
    listing = store.list()
    remote_records = _remote_records(listing, remote_name)
    generated = not getattr(args, "name", None)
    if generated:
        taken = {str(item.get("slug")) for item in remote_records if item.get("slug")}
        taken |= {str(item.get("slug")) for item in stack_registry.read()["stacks"].values()
                  if isinstance(item, dict)}

        def accepts(candidate: str) -> bool:
            try:
                stack_template.validate_slug(template, candidate)
            except StackTemplateError:
                return False
            return True
        slug = naming.generate_slug(ref.ref_branch, ref.commit, caller["label"], taken, accepts)
    else:
        slug = stack_template.validate_slug(template, args.name)
    environment = _select_environment(template, getattr(args, "environment", None))
    pins = stack_registry.parse_pins(getattr(args, "port", None), template.ports)
    project = stack_template.render_project(template, slug, ref)
    compose_project = hosting.compose_project_name({"project": project, "environment": environment})
    source_rel = stack_template.template_source_root(template, relpath)
    from sandbox.hosting.front_door import front_door_mode
    front_door = front_door_mode(entry)

    def prepare(data: dict) -> tuple[dict | None, dict[str, int], dict]:
        """Checks and port choice shared by plan and confirm; no writes."""
        record = data["stacks"].get(stack_registry.record_key(remote_name, slug))
        for other in stack_registry.records_for_slug(data, slug):
            if other.get("remote") != remote_name:
                raise StackCommandError(SLUG_IN_USE, f"stack {slug} already exists on remote {other.get('remote')}")
        if record and (record.get("project"), record.get("environment")) != (project, environment):
            raise StackCommandError(
                SLUG_IN_USE, f"stack {slug} is recorded as {record.get('project')}/{record.get('environment')}; "
                "tear it down before changing its project or environment")
        _refuse_if_active(record)
        state = hosting.load_host_state()
        other_ports = stack_registry.ports_of_other_stacks(data, remote_name, slug)
        other_ports.update(_remote_ports(remote_records, compose_project))
        ports = stack_registry.allocate_ports(
            remote_name=remote_name, slug=slug, names=template.ports,
            existing=(record or {}).get("ports") or {}, pins=pins,
            other_stacks=other_ports,
            reserved=_reserved_loopback_ports(state, remote_name),
            bound=probe_ports(entry) if template.ports else {},
            own_compose_project=compose_project)
        return record, ports, state

    def ownership(state: dict, data: dict, validated: dict) -> list[str]:
        hostnames = [route["hostname"] for route in validated["routes"]]
        others = [item for item in stack_registry.records_for_remote(data, remote_name)
                  if item.get("slug") != slug]
        others += _remote_holdings(remote_records, remote_name)
        hostname_ownership.assert_available(state, remote_name, project, hostnames, others)
        return hostnames

    def lifetime_request(previous: dict | None, ports: dict, hostnames: list[str]) -> dict:
        copy_id, explicit_copy = _copy_id(args, previous)
        draft = _draft(project=project, environment=environment, slug=slug, copy_id=copy_id,
                       caller=caller, repo=repo, ref=ref, dev_branch=template.dev_branch,
                       lifetime=lifetime, source=lifetime_source, ttl=ttl, template_path=template_path,
                       ports=ports, hostnames=hostnames, front_door=front_door, now=store.now())
        legacy = bool(previous) and not (previous or {}).get("copy_id")
        return {"record": draft, "explicit_copy": explicit_copy, "legacy": legacy,
                "lifetime_explicit": getattr(args, "lifetime", None) is not None, "new_ttl": new_ttl}

    def result(mode: str, validated: dict, ports: dict, hostnames: list[str], *, source_root,
               submission, stored: dict, job_id=None, request_id=None, missing=()) -> dict:
        return {"ok": True, "action": "stack-up", "mode": mode, "slug": slug, "remote": remote_name,
                "project": project, "environment": environment, "ref": ref.ref,
                "ref_branch": ref.ref_branch, "commit": ref.commit, "hostnames": hostnames,
                "ports": ports, "manifest": validated["manifest_path"], "source_root": source_root,
                "template": str(template_path), "submission": submission, "job_id": job_id,
                "request_id": request_id, "secrets_missing": sorted(missing),
                "generated_name": generated, "repo": repo, **_lifetime_fields(stored)}

    request_id = getattr(args, "request_id", None) or f"stack-{slug}-{ref.commit[:12]}-{secrets.token_hex(3)}"
    from sandbox.commands import hosting as hosting_cmd

    def preview(data: dict, state: dict, ports: dict) -> tuple[dict, dict, list[str]]:
        """Render and validate against a throwaway export of the compose files.

        Every refusal (render, manifest validation, hostname ownership) happens
        here, before a worktree, manifest or record exists.
        """
        with tempfile.TemporaryDirectory(prefix="sandbox-stack-plan-") as export:
            export_root = Path(export)
            env_compose = ((template.raw.get("environments") or {}).get(environment) or {}).get("compose") or {}
            _export_compose_files(checkout, ref.commit, source_rel, env_compose.get("files"), export_root)
            rendered = _rendered_manifest(template, slug=slug, ref=ref, ports=ports,
                                          source_root=export_root, template_path=template_path)
            validated = _validate_rendered(rendered, environment, require_checkout=False)
            hostnames = ownership(state, data, validated)
        return validated, rendered, hostnames

    if plan:
        data = stack_registry.read()
        record, ports, state = prepare(data)
        validated, rendered, hostnames = preview(data, state, ports)
        checked = store.call("check", **lifetime_request(record, ports, hostnames))
        missing = _secrets_gate(validated, generate=False, confirm=False)
        future_source = stack_registry.stack_dir(slug) / "source" / f"{ref.commit[:12]}-<token>"
        future = {**validated, "project_root": str(future_source / source_rel),
                  "manifest_root": rendered["_manifest_root"]}
        argv = hosting_cmd._host_delivery_guidance(
            future, remote_name, request_id=request_id, source_commit=ref.commit,
            timeout=_submission_timeout(validated), json_output=True)
        return result("plan", validated, ports, hostnames, source_root=None, submission=argv,
                      stored=checked["record"], request_id=request_id, missing=missing)

    sweep = None
    with stack_registry.locked() as registry:
        previous, ports, state = prepare(registry.data)
        checked, _rendered, hostnames = preview(registry.data, state, ports)
        _secrets_gate(checked, generate=bool(getattr(args, "generate_secrets", False)), confirm=True)
        # The remote claim is the last refusal and the first remote change.
        claim_request = lifetime_request(previous, ports, hostnames)
        claimed = store.call("claim", **claim_request)
        stored = claimed["record"]
        try:
            worktree = _add_worktree(checkout, slug, ref.commit)
        except BaseException:
            _release_fresh_claim(store, claimed, compose_project)
            raise
        manifest_path = stack_registry.stack_dir(slug) / stack_template.MANIFEST_NAME
        previous_text = manifest_path.read_text() if manifest_path.exists() else None
        try:
            source_root = (worktree / source_rel).resolve()
            if worktree.resolve() != source_root and worktree.resolve() not in source_root.parents:
                raise StackTemplateError(stack_template.TEMPLATE_INVALID,
                                         "source_root must stay within the template checkout")
            rendered = _rendered_manifest(template, slug=slug, ref=ref, ports=ports,
                                          source_root=source_root, template_path=template_path)
            _validate_rendered(rendered, environment, require_checkout=True)
            _write_manifest(manifest_path, rendered)
            validated = hosting.validate_manifest(manifest_path, environment)
        except BaseException:
            _remove_worktree(str(checkout), str(worktree))
            if previous_text is None:
                manifest_path.unlink(missing_ok=True)
            else:
                manifest_path.write_text(previous_text)
            _release_fresh_claim(store, claimed, compose_project)
            raise
        now = int(time.time())
        earlier = [path for path in (previous or {}).get("worktrees") or [] if path != str(worktree)]
        record: stack_registry.StackRecord = {
            "remote": remote_name, "slug": slug, "project": project, "environment": environment,
            "template": str(template_path), "checkout": str(checkout), "ref": ref.ref,
            "ref_branch": ref.ref_branch, "hostnames": sorted(hostnames),
            # `commit` stays what an apply last delivered; `sb host apply` moves
            # it to last_apply.commit once this one completes (record_delivery).
            "commit": (stack_registry.delivered_commit(previous, _last_apply_view(previous).get("status"))
                       if previous else None),
            "ports": ports, "manifest": str(manifest_path),
            "worktrees": [*earlier, str(worktree)],
            "created_at": (previous or {}).get("created_at", now), "updated_at": now,
            # Spec 076: sites `sb deploy --stack` linked to this stack outlive a re-apply.
            **({"sites": list(previous["sites"])} if (previous or {}).get("sites") else {}),
            "last_apply": {"request_id": request_id, "job_id": None, "submitted_at": now,
                           "status": "submitting", "commit": ref.commit},
            "copy_id": claim_request["record"]["copy_id"], "owner": stored.get("owner"),
            "lifetime": stored.get("lifetime"), "lifetime_source": stored.get("lifetime_source"),
            "ttl_seconds": stored.get("ttl_seconds"), "expires_at": stored.get("expires_at"),
            "repo": repo, "dev_branch": template.dev_branch,
            "canonical": bool(stored.get("canonical")),
        }
        # Ports are held from here on, even if the submission below fails.
        registry.put(record)
        argv = hosting_cmd._host_delivery_guidance(
            validated, remote_name, request_id=request_id, source_commit=ref.commit,
            timeout=_submission_timeout(validated), json_output=True)
        try:
            job_id = _submit(argv)
        except StackCommandError as exc:
            record["last_apply"].update({"status": "acceptance_unknown" if exc.code == SUBMISSION_UNKNOWN
                else "refused", "error": exc.detail[:500]})
            registry.put(record)
            raise
        record["last_apply"].update({"job_id": job_id, "status": "submitted"})
        # Earlier worktrees belong to terminal jobs (`up` refuses while one is
        # active); drop them now that the new job owns its own source.
        for path in earlier:
            _remove_worktree(str(checkout), path)
        record["worktrees"] = [str(worktree)]
        registry.put(record)
    if record["canonical"]:
        # The dev stack just moved: every temporary copy merged into dev is now
        # eligible. Best effort; the TTL still applies if this cannot run.
        from sandbox.hosting.lifetime import merge
        try:
            sweep = merge.sweep(store, remote_records=store.list().get("records") or [],
                                checkouts={repo: checkout}, dev_branch=template.dev_branch,
                                repo=repo, confirm=True)
        except (hosting.HostingError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
            sweep = {"ok": False, "error": remote.redact_text(str(exc))[:300]}
    payload = result("confirm", validated, ports, hostnames, source_root=validated["project_root"],
                     submission=argv, stored=stored, job_id=job_id, request_id=request_id)
    if sweep is not None:
        payload["sweep"] = sweep
    return payload


def _release_fresh_claim(store, claimed: dict, compose_project: str) -> None:
    """Undo a brand-new claim when this run fails before anything was delivered."""
    if not claimed.get("new"):
        return
    try:
        store.call("release", compose_project=compose_project,
                   copy_id=(claimed.get("record") or {}).get("copy_id"))
    except hosting.HostingError:
        pass


def _owner_checkout(record: dict | None) -> Path:
    checkout = Path((record or {}).get("checkout") or ".")
    return checkout if checkout.is_dir() else Path.cwd()


def _remote_stack(listing: dict, slug: str) -> dict | None:
    for item in listing.get("records") or []:
        if isinstance(item, dict) and item.get("kind") == "stack" and item.get("slug") == slug:
            return item
    return None


def _list_row(local: dict | None, remote_item: dict | None, remote_state: str) -> dict:
    """One stack: the remote record's fields, plus what only this controller knows."""
    local = local or {}
    if remote_item:
        row = {key: remote_item.get(key) for key in RECORD_FIELDS}
    else:
        # Not claimed on the remote (yet): this controller's view, unclassified.
        row = {key: None for key in RECORD_FIELDS}
        row.update({key: local.get(key) for key in (
            "project", "environment", "slug", "copy_id", "repo", "ref", "ref_branch", "commit",
            "dev_branch", "template", "ports", "hostnames", "owner")})
        row.update({"kind": "stack", "canonical": False, "extended_count": 0,
                    "compose_project": hosting.compose_project_name(
                        {"project": local.get("project"), "environment": local.get("environment")})})
    row["manifest"] = local.get("manifest")
    row["mine"] = bool(local) and (not remote_item or local.get("copy_id") == remote_item.get("copy_id"))
    row["remote_record"] = remote_state
    row["last_apply"] = _last_apply_view(local) if local else None
    if row["mine"]:
        # The remote record holds the commit its claim was made for; this
        # controller's record holds the one an apply delivered.
        row["commit"] = stack_registry.delivered_commit(local, row["last_apply"].get("status"))
    return row


def _list(args) -> dict:
    entry_name = getattr(args, "remote", None)
    if not entry_name:
        raise StackCommandError(REMOTE_UNKNOWN, "--remote is required")
    entry = _entry(entry_name)
    data = stack_registry.read()
    local = {(record.get("project"), record.get("environment")): record
             for record in stack_registry.records_for_remote(data, entry_name)}
    payload: dict = {"ok": True, "action": "stack-list", "remote": entry_name}
    try:
        listing = _store(entry, None).list()
    except lifetime_client.LifetimeRefusal as exc:
        # The remote record cannot be read: show this controller's stacks only.
        listing = None
        payload["remote_error"] = {"code": exc.code, "error": exc.detail}
    stacks, targets, seen = [], [], set()
    for item in (listing or {}).get("records") or []:
        key = (item.get("project"), item.get("environment"))
        if item.get("kind") == "stack":
            seen.add(key)
            stacks.append(_list_row(local.get(key), item, "present"))
        else:
            targets.append({key: item.get(key) for key in RECORD_FIELDS})
    invalid = {row.get("compose_project") for row in (listing or {}).get("invalid") or []}
    for key, record in local.items():
        if key in seen:
            continue
        compose_project = hosting.compose_project_name({"project": key[0], "environment": key[1]})
        state = "unknown" if listing is None else "invalid" if compose_project in invalid else "missing"
        stacks.append(_list_row(record, None, state))
    payload["stacks"] = sorted(stacks, key=lambda row: str(row.get("slug")))
    if listing is not None:
        payload.update({"targets": targets, "unclassified": listing.get("unclassified") or [],
                        "invalid": listing.get("invalid") or [], "policy": listing.get("policy")})
    return payload


def _destroy(entry, remote_name, project, environment, lock_wait) -> dict:
    from sandbox.hosting import destroy
    import sandbox.core._cloudflare as cloudflare
    try:
        return destroy.execute(entry, remote_name, project, environment, lock_wait=lock_wait)
    except TimeoutError:
        raise StackCommandError(DESTROY_FAILED, "another host operation owns this target; "
                                "the stack and its ports are kept") from None
    except (hosting.HostingError, cloudflare.CloudflareError, RuntimeError, OSError) as exc:
        code = getattr(exc, "code", None)
        raise StackCommandError(
            DESTROY_FAILED, f"destroy failed ({code or 'host_destroy_failed'}): {exc}; "
            "the stack and its ports are kept") from None


def _check_owner(remote_item: dict | None, caller: dict, local: dict | None, force: bool, action: str) -> None:
    if not remote_item:
        return
    owner = remote_item.get("owner") or {}
    copy = remote_item.get("copy_id")
    if str(owner.get("label", "")).lower() == caller["label"].lower():
        return
    if local and local.get("copy_id") == copy and not force:
        # This controller made the copy but its owner label changed since.
        return
    if not force:
        raise StackCommandError(
            hoststore.NOT_OWNER, f"stack {remote_item.get('slug')} belongs to "
            f"{owner.get('label')}; pass --force-owner to {action} it anyway")


def _down(args) -> dict:
    if not getattr(args, "confirm", False):
        raise StackCommandError(CONFIRMATION_REQUIRED,
                                "stack down is destructive; pass --confirm to tear the stack down")
    if not getattr(args, "name", None):
        raise StackCommandError(CONFIRMATION_REQUIRED, "stack down needs --name")
    entry = _entry(getattr(args, "remote", None))
    remote_name, slug = args.remote, args.name
    force = bool(getattr(args, "force_owner", False))
    from sandbox.commands import hosting as hosting_cmd
    lock_wait = hosting_cmd._host_apply_lock_wait(args)
    with stack_registry.locked() as registry:
        record = registry.get(remote_name, slug)
        caller = _caller(_owner_checkout(record), args)
        store = _store(entry, caller)
        remote_item = _remote_stack(store.list(), slug)
        if not record and not remote_item:
            raise StackCommandError(NOT_FOUND, f"no stack {slug!r} is recorded on remote {remote_name}")
        if record and remote_item and (remote_item.get("project"), remote_item.get("environment")) != (
                record.get("project"), record.get("environment")):
            raise StackCommandError(SLUG_IN_USE, f"stack {slug} on {remote_name} is "
                                    f"{remote_item.get('project')}/{remote_item.get('environment')}, "
                                    f"not this controller's {record.get('project')}/{record.get('environment')}")
        _check_owner(remote_item, caller, record, force, "tear down")
        if remote_item and remote_item.get("state") == "reaping":
            raise StackCommandError(hoststore.REAPING, f"stack {slug} is being torn down by server cleanup; "
                                    "retry when it is reaped")
        _refuse_if_active(record)
        project = (record or remote_item)["project"]
        environment = (record or remote_item)["environment"]
        destroyed = _destroy(entry, remote_name, project, environment, lock_wait)
        if remote_item:
            store.call("release", compose_project=remote_item["compose_project"],
                       copy_id=(record or {}).get("copy_id"), force_owner=force)
        if record:
            for path in record.get("worktrees") or []:
                _remove_worktree(record.get("checkout"), path)
            manifest = Path(record.get("manifest") or "")
            if manifest.name == stack_template.MANIFEST_NAME and manifest.parent == stack_registry.stack_dir(slug):
                manifest.unlink(missing_ok=True)
            directory = stack_registry.stack_dir(slug)
            if directory.is_dir() and stack_registry.stacks_dir().resolve() in directory.resolve().parents:
                shutil.rmtree(directory, ignore_errors=True)
            registry.delete(remote_name, slug)
    return {"ok": True, "action": "stack-down", "slug": slug, "remote": remote_name,
            "project": project, "environment": environment,
            "released_ports": (record or {}).get("ports") or (remote_item or {}).get("ports") or {},
            "destroy": destroyed, "forced_owner": force and bool(remote_item) and not (
                str((remote_item.get("owner") or {}).get("label", "")).lower() == caller["label"].lower()),
            "was_reaped": bool(remote_item and remote_item.get("state") == "reaped")}


def _ttl(args) -> dict:
    if not getattr(args, "confirm", False):
        raise StackCommandError(CONFIRMATION_REQUIRED, "stack ttl changes the stack's expiry; pass --confirm")
    if not getattr(args, "name", None):
        raise StackCommandError(CONFIRMATION_REQUIRED, "stack ttl needs --name")
    lifetime = getattr(args, "lifetime", None)
    ttl_text = getattr(args, "ttl", None)
    if ttl_text is None and lifetime is None:
        raise StackCommandError(hoststore.TTL_INVALID, "stack ttl needs --ttl, --lifetime or both")
    if lifetime is not None and lifetime not in hoststore.LIFETIMES:
        raise StackCommandError(hoststore.LIFETIME_INVALID, "--lifetime must be temporary, hosted or permanent")
    try:
        ttl = hoststore.parse_ttl(ttl_text) if ttl_text is not None else None
    except hoststore.StoreRefusal as exc:
        raise StackCommandError(exc.code, exc.message) from None
    entry = _entry(getattr(args, "remote", None))
    remote_name, slug = args.remote, args.name
    with stack_registry.locked() as registry:
        record = registry.get(remote_name, slug)
        store = _store(entry, _caller(_owner_checkout(record), args))
        remote_item = _remote_stack(store.list(), slug)
        if not remote_item:
            raise StackCommandError(NOT_FOUND, f"no stack {slug!r} has a lifetime record on {remote_name}"
                                    + ("; re-run stack up to claim it" if record else ""))
        fields = {"compose_project": remote_item["compose_project"],
                  "force_owner": bool(getattr(args, "force_owner", False))}
        if ttl is not None:
            fields["ttl_seconds"] = ttl
        if lifetime is not None:
            fields["lifetime"] = lifetime
        updated = store.call("update", **fields)["record"]
        if record:
            record.update({key: updated.get(key) for key in (
                "lifetime", "lifetime_source", "ttl_seconds", "expires_at")})
            record["canonical"] = bool(updated.get("canonical"))
            registry.put(record)
    return {"ok": True, "action": "stack-ttl", "slug": slug, "remote": remote_name,
            **_lifetime_fields(updated)}


def _sweep(args) -> dict:
    entry = _entry(getattr(args, "remote", None))
    from sandbox.hosting.lifetime import merge
    checkouts: dict[str, Path] = {}
    for record in stack_registry.records_for_remote(stack_registry.read(), args.remote):
        path = Path(record.get("checkout") or "")
        if record.get("repo") and path.is_dir():
            checkouts.setdefault(record["repo"], path)
    for value in getattr(args, "checkout", None) or []:
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise StackCommandError(hoststore.REQUEST_INVALID, f"--checkout {value}: not a directory")
        checkouts[naming.repo_identity(path)] = path
    first = next(iter(checkouts.values()), Path.cwd())
    store = _store(entry, _caller(first, args))
    result = merge.sweep(store, remote_records=store.list().get("records") or [], checkouts=checkouts,
                         confirm=bool(getattr(args, "confirm", False)))
    result["remote"] = args.remote
    return result


def _limits(args) -> dict:
    entry = _entry(getattr(args, "remote", None))
    wanted = {}
    for flag, key in (("per_owner", "per_owner"), ("total", "total")):
        value = getattr(args, flag, None)
        if value is not None:
            wanted[key] = value
    gigabytes = getattr(args, "min_free_gb", None)
    if gigabytes is not None:
        if gigabytes < 0:
            raise StackCommandError(hoststore.LIMITS_INVALID, "--min-free-gb must not be negative")
        wanted["min_free_bytes"] = int(gigabytes * 1024 ** 3)
    if not wanted:
        policy = _store(entry, None).call("policy")["policy"]
        return {"ok": True, "action": "stack-limits", "mode": "show", "remote": args.remote, "policy": policy}
    if not getattr(args, "confirm", False):
        raise StackCommandError(CONFIRMATION_REQUIRED, "changing stack limits needs --confirm")
    store = _store(entry, _caller(Path.cwd(), args))
    policy = store.call("policy", set=wanted)["policy"]
    return {"ok": True, "action": "stack-limits", "mode": "confirm", "remote": args.remote, "policy": policy}


def _reap(args) -> dict:
    from sandbox.hosting.lifetime import reaper
    plan, confirm = bool(getattr(args, "plan", False)), bool(getattr(args, "confirm", False))
    if plan == confirm:
        raise StackCommandError(CONFIRMATION_REQUIRED, "stack reap needs exactly one of --plan or --confirm")
    if getattr(args, "here", False):
        if getattr(args, "remote", None):
            raise StackCommandError(CONFIRMATION_REQUIRED, "--here runs on the host itself; drop --remote")
        return reaper.run_here(confirm=confirm, trigger=getattr(args, "trigger", None) or "stack_reap")
    entry = _entry(getattr(args, "remote", None))
    return reaper.run_remote(entry, args.remote, confirm=confirm)


def _when(value) -> str:
    return str(value) if value else "never"


def _render_text(payload: dict) -> str:
    action = payload["action"]
    if action == "stack-list":
        lines = []
        if payload.get("remote_error"):
            lines.append(f"remote records unavailable ({payload['remote_error']['code']}); "
                         "showing this controller's stacks only")
        if not payload["stacks"]:
            lines.append(f"no stacks on {payload['remote']}")
        for item in payload["stacks"]:
            last = item.get("last_apply") or {}
            ports = ", ".join(f"{name}={port}" for name, port in (item.get("ports") or {}).items()) or "none"
            owner = (item.get("owner") or {}).get("label") or "unknown"
            lines.append(f"{item['slug']}  {item['project']}/{item['environment']}  "
                         f"{item.get('ref')}@{(item.get('commit') or '')[:12] or 'not delivered'}"
                         f"{'  (mine)' if item.get('mine') else ''}")
            lines.append(f"  owner: {owner}  copy: {item.get('copy_id') or 'none'}  "
                         f"lifetime: {item.get('lifetime') or 'unclassified'}"
                         f"{' (canonical)' if item.get('canonical') else ''}  "
                         f"expires: {_when(item.get('expires_at'))}  state: {item.get('state') or 'n/a'}")
            if item.get("remote_record") != "present":
                lines.append(f"  remote record: {item['remote_record']} (protected until claimed)")
            lines.append(f"  hosts: {', '.join(item.get('hostnames') or [])}")
            lines.append(f"  ports: {ports}")
            if last:
                requested = f", commit {last['commit'][:12]}" if last.get("commit") else ""
                lines.append(f"  last apply: {last.get('status', 'unknown')} at {last.get('at')} "
                             f"(job {last.get('job_id') or 'none'}{requested})")
        for item in payload.get("targets") or []:
            lines.append(f"target {item['project']}/{item['environment']}  lifetime: {item.get('lifetime')}  "
                         f"expires: {_when(item.get('expires_at'))}")
        for name in payload.get("unclassified") or []:
            lines.append(f"unclassified {name} (protected)")
        for item in payload.get("invalid") or []:
            lines.append(f"invalid record {item['compose_project']}: {item['reason']} (protected)")
        return "\n".join(lines)
    if action == "stack-down":
        ports = ", ".join(f"{name}={port}" for name, port in payload["released_ports"].items()) or "none"
        return (f"stack {payload['slug']} torn down ({payload['destroy'].get('state')}); "
                f"released ports: {ports}")
    if action == "stack-ttl":
        return (f"stack {payload['slug']}: {payload['lifetime']}, expires {_when(payload['expires_at'])}")
    if action == "stack-limits":
        policy = payload["policy"]
        return (f"stack limits on {payload['remote']}: {policy['per_owner']} temporary stacks per owner, "
                f"{policy['total']} targets in all, {policy['min_free_bytes'] / 1024 ** 3:.1f} GiB free minimum")
    if action == "stack-sweep":
        lines = [f"merge sweep ({payload['mode']}): {payload['recorded']} recorded"]
        for row in payload["checked"]:
            lines.append(f"  {row['slug']} @ {(row.get('commit') or '')[:12]}: merged={row['merged']} "
                         f"({row.get('reason') or 'checked'})")
        return "\n".join(lines)
    if action == "stack-reap":
        lines = [f"stack reap ({payload['mode']}) on {payload.get('remote') or 'this host'}: "
                 f"{len(payload.get('candidates') or [])} eligible"]
        for row in payload.get("outcomes") or payload.get("candidates") or []:
            lines.append(f"  {row.get('compose_project')}: {row.get('status') or row.get('reason')}"
                         f"{' (' + str(row.get('reason')) + ')' if row.get('status') else ''}")
        return "\n".join(lines)
    ports = ", ".join(f"{name}={port}" for name, port in payload["ports"].items()) or "none"
    lines = [f"stack {payload['slug']} -> {payload['project']}/{payload['environment']} on {payload['remote']}",
             f"  ref: {payload['ref']} ({payload['ref_branch'] or 'detached'}) @ {payload['commit']}",
             f"  owner: {(payload.get('owner') or {}).get('label')}  copy: {payload.get('copy_id')}"
             f"{'  (generated name)' if payload.get('generated_name') else ''}",
             f"  lifetime: {payload.get('lifetime')} ({payload.get('lifetime_source')})"
             f"{' canonical' if payload.get('canonical') else ''}, expires {_when(payload.get('expires_at'))}",
             f"  hosts: {', '.join(payload['hostnames'])}",
             f"  ports: {ports}",
             f"  manifest: {payload['manifest']}"]
    if payload["secrets_missing"]:
        lines.append("  missing secrets: " + ", ".join(payload["secrets_missing"])
                     + " (stack up --confirm --generate creates generated ones)")
    if payload["mode"] == "plan":
        lines.append("  would submit: " + " ".join(payload["submission"]))
    else:
        lines.append(f"  submitted job {payload['job_id']} (request {payload['request_id']}); "
                     f"follow with `sb job-status {payload['job_id']}`")
    return "\n".join(lines)


STACK_ACTIONS = ("up", "list", "down", "ttl", "sweep", "reap", "limits")


def cmd_host_stack(args) -> None:
    action = getattr(args, "stack_action", None)
    handlers = {"up": _up, "list": _list, "down": _down, "ttl": _ttl, "sweep": _sweep,
                "reap": _reap, "limits": _limits}
    if action not in handlers:
        die("host stack needs one of: " + ", ".join(STACK_ACTIONS))
    try:
        payload = handlers[action](args)
    except (hosting.HostingError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        _refuse(args, exc)
        return
    if not getattr(args, "json", False) and action == "up" and payload["mode"] == "confirm":
        info(f"host stack: submitted {payload['job_id']}")
    _emit(args, payload, _render_text(payload))
