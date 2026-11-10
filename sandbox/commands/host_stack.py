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
from sandbox.hosting.stack_template import StackRef, StackTemplateError

CONFIRMATION_REQUIRED = "stack_confirmation_required"
REF_UNRESOLVED = "stack_ref_unresolved"
SLUG_IN_USE = "stack_slug_in_use"
APPLY_IN_PROGRESS = "stack_apply_in_progress"
NOT_FOUND = "stack_not_found"
SUBMISSION_FAILED = "stack_submission_failed"
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
    job = job_view((record.get("last_apply") or {}).get("job_id"))
    if job and job.get("lifecycle") in ACTIVE_LIFECYCLES:
        raise StackCommandError(
            APPLY_IN_PROGRESS,
            f"stack {record.get('slug')} apply job {record['last_apply']['job_id']} is "
            f"{job['lifecycle']}; wait for it (`sb job-status {record['last_apply']['job_id']}`)")


def _submission_timeout(validated: dict) -> int:
    return max(900, int(validated["compose"].get("build_timeout_seconds", 900)) + 600)


def _submit(argv: list[str]) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=180,
                                env=dict(os.environ))
    except subprocess.TimeoutExpired:
        raise StackCommandError(SUBMISSION_FAILED, "job-start did not answer within 180 seconds") from None
    lines = [line for line in (result.stdout or "").splitlines() if line.strip().startswith("{")]
    payload = None
    if lines:
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError:
            payload = None
    job_id = payload.get("job_id") if isinstance(payload, dict) else None
    if result.returncode != 0 or not isinstance(job_id, str) or not job_id:
        detail = remote.redact_text((result.stderr or result.stdout or "no output").strip())[-500:]
        raise StackCommandError(SUBMISSION_FAILED, f"job-start refused the apply: {detail}")
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
    entry = remote.get_remote(remote_name)
    if not isinstance(entry, dict):
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


# ----------------------------------------------------------------- commands

def _up(args) -> dict:
    plan, confirm = bool(getattr(args, "plan", False)), bool(getattr(args, "confirm", False))
    if plan == confirm:
        raise StackCommandError(CONFIRMATION_REQUIRED, "stack up needs exactly one of --plan or --confirm")
    if not getattr(args, "template", None) or not getattr(args, "name", None):
        raise StackCommandError(CONFIRMATION_REQUIRED, "stack up needs --template and --name")
    entry = _entry(getattr(args, "remote", None))
    remote_name = args.remote
    template_path = Path(args.template).expanduser().resolve()
    checkout, relpath = template_checkout(template_path)
    ref = resolve_ref(checkout, getattr(args, "ref", None))
    template = load_template(checkout, relpath, ref.commit)
    slug = stack_template.validate_slug(template, args.name)
    environment = _select_environment(template, getattr(args, "environment", None))
    pins = stack_registry.parse_pins(getattr(args, "port", None), template.ports)
    project = stack_template.render_project(template, slug, ref)
    compose_project = hosting.compose_project_name({"project": project, "environment": environment})
    source_rel = stack_template.template_source_root(template, relpath)

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
        ports = stack_registry.allocate_ports(
            remote_name=remote_name, slug=slug, names=template.ports,
            existing=(record or {}).get("ports") or {}, pins=pins,
            other_stacks=stack_registry.ports_of_other_stacks(data, remote_name, slug),
            reserved=_reserved_loopback_ports(state, remote_name),
            bound=probe_ports(entry) if template.ports else {},
            own_compose_project=compose_project)
        return record, ports, state

    def ownership(state: dict, data: dict, validated: dict) -> list[str]:
        hostnames = [route["hostname"] for route in validated["routes"]]
        others = [item for item in stack_registry.records_for_remote(data, remote_name)
                  if item.get("slug") != slug]
        hostname_ownership.assert_available(state, remote_name, project, hostnames, others)
        return hostnames

    def result(mode: str, validated: dict, ports: dict, hostnames: list[str], *, source_root,
               submission, job_id=None, request_id=None, missing=()) -> dict:
        return {"ok": True, "action": "stack-up", "mode": mode, "slug": slug, "remote": remote_name,
                "project": project, "environment": environment, "ref": ref.ref,
                "ref_branch": ref.ref_branch, "commit": ref.commit, "hostnames": hostnames,
                "ports": ports, "manifest": validated["manifest_path"], "source_root": source_root,
                "template": str(template_path), "submission": submission, "job_id": job_id,
                "request_id": request_id, "secrets_missing": sorted(missing)}

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
        _record, ports, state = prepare(data)
        validated, rendered, hostnames = preview(data, state, ports)
        missing = _secrets_gate(validated, generate=False, confirm=False)
        future_source = stack_registry.stack_dir(slug) / "source" / f"{ref.commit[:12]}-<token>"
        future = {**validated, "project_root": str(future_source / source_rel),
                  "manifest_root": rendered["_manifest_root"]}
        argv = hosting_cmd._host_delivery_guidance(
            future, remote_name, request_id=request_id, source_commit=ref.commit,
            timeout=_submission_timeout(validated), json_output=True)
        return result("plan", validated, ports, hostnames, source_root=None, submission=argv,
                      request_id=request_id, missing=missing)

    with stack_registry.locked() as registry:
        previous, ports, state = prepare(registry.data)
        checked, _rendered, hostnames = preview(registry.data, state, ports)
        _secrets_gate(checked, generate=bool(getattr(args, "generate_secrets", False)), confirm=True)
        worktree = _add_worktree(checkout, slug, ref.commit)
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
            raise
        now = int(time.time())
        earlier = [path for path in (previous or {}).get("worktrees") or [] if path != str(worktree)]
        record: stack_registry.StackRecord = {
            "remote": remote_name, "slug": slug, "project": project, "environment": environment,
            "template": str(template_path), "checkout": str(checkout), "ref": ref.ref,
            "ref_branch": ref.ref_branch, "commit": ref.commit, "hostnames": sorted(hostnames),
            "ports": ports, "manifest": str(manifest_path),
            "worktrees": [*earlier, str(worktree)],
            "created_at": (previous or {}).get("created_at", now), "updated_at": now,
            # Spec 076: sites `sb deploy --stack` linked to this stack outlive a re-apply.
            **({"sites": list(previous["sites"])} if (previous or {}).get("sites") else {}),
            "last_apply": {"request_id": request_id, "job_id": None, "submitted_at": now,
                           "status": "submitting"},
        }
        # Ports are held from here on, even if the submission below fails.
        registry.put(record)
        argv = hosting_cmd._host_delivery_guidance(
            validated, remote_name, request_id=request_id, source_commit=ref.commit,
            timeout=_submission_timeout(validated), json_output=True)
        try:
            job_id = _submit(argv)
        except StackCommandError as exc:
            record["last_apply"].update({"status": "submission_failed", "error": exc.detail[:500]})
            registry.put(record)
            raise
        record["last_apply"].update({"job_id": job_id, "status": "submitted"})
        # Earlier worktrees belong to terminal jobs (`up` refuses while one is
        # active); drop them now that the new job owns its own source.
        for path in earlier:
            _remove_worktree(str(checkout), path)
        record["worktrees"] = [str(worktree)]
        registry.put(record)
    return result("confirm", validated, ports, hostnames, source_root=validated["project_root"],
                  submission=argv, job_id=job_id, request_id=request_id)


def _list(args) -> dict:
    entry_name = getattr(args, "remote", None)
    if not entry_name:
        raise StackCommandError(REMOTE_UNKNOWN, "--remote is required")
    data = stack_registry.read()
    stacks = []
    for record in sorted(stack_registry.records_for_remote(data, entry_name),
                         key=lambda item: str(item.get("slug"))):
        stacks.append({key: record.get(key) for key in (
            "slug", "project", "environment", "ref", "ref_branch", "commit", "hostnames", "ports",
            "manifest", "template", "updated_at")} | {"last_apply": _last_apply_view(record)})
    return {"ok": True, "action": "stack-list", "remote": entry_name, "stacks": stacks}


def _down(args) -> dict:
    if not getattr(args, "confirm", False):
        raise StackCommandError(CONFIRMATION_REQUIRED,
                                "stack down is destructive; pass --confirm to tear the stack down")
    if not getattr(args, "name", None):
        raise StackCommandError(CONFIRMATION_REQUIRED, "stack down needs --name")
    entry = _entry(getattr(args, "remote", None))
    remote_name, slug = args.remote, args.name
    from sandbox.commands import hosting as hosting_cmd
    from sandbox.hosting import destroy
    import sandbox.core._cloudflare as cloudflare
    lock_wait = hosting_cmd._host_apply_lock_wait(args)
    with stack_registry.locked() as registry:
        record = registry.get(remote_name, slug)
        if not record:
            raise StackCommandError(NOT_FOUND, f"no stack {slug!r} is recorded on remote {remote_name}")
        _refuse_if_active(record)
        try:
            destroyed = destroy.execute(entry, remote_name, record["project"], record["environment"],
                                        lock_wait=lock_wait)
        except TimeoutError:
            raise StackCommandError(DESTROY_FAILED, "another host operation owns this target; "
                                    "the stack and its ports are kept") from None
        except (hosting.HostingError, cloudflare.CloudflareError, RuntimeError, OSError) as exc:
            code = getattr(exc, "code", None)
            raise StackCommandError(
                DESTROY_FAILED, f"destroy failed ({code or 'host_destroy_failed'}): {exc}; "
                "the stack and its ports are kept") from None
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
            "project": record["project"], "environment": record["environment"],
            "released_ports": record.get("ports") or {}, "destroy": destroyed}


def _render_text(payload: dict) -> str:
    action = payload["action"]
    if action == "stack-list":
        if not payload["stacks"]:
            return f"no stacks on {payload['remote']}"
        lines = []
        for item in payload["stacks"]:
            last = item["last_apply"]
            ports = ", ".join(f"{name}={port}" for name, port in (item["ports"] or {}).items()) or "none"
            lines.append(f"{item['slug']}  {item['project']}/{item['environment']}  "
                         f"{item['ref']}@{(item['commit'] or '')[:12]}")
            lines.append(f"  hosts: {', '.join(item['hostnames'] or [])}")
            lines.append(f"  ports: {ports}")
            lines.append(f"  last apply: {last.get('status', 'unknown')} at {last.get('at')} "
                         f"(job {last.get('job_id') or 'none'})")
        return "\n".join(lines)
    if action == "stack-down":
        ports = ", ".join(f"{name}={port}" for name, port in payload["released_ports"].items()) or "none"
        return (f"stack {payload['slug']} torn down ({payload['destroy'].get('state')}); "
                f"released ports: {ports}")
    ports = ", ".join(f"{name}={port}" for name, port in payload["ports"].items()) or "none"
    lines = [f"stack {payload['slug']} -> {payload['project']}/{payload['environment']} on {payload['remote']}",
             f"  ref: {payload['ref']} ({payload['ref_branch'] or 'detached'}) @ {payload['commit']}",
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


def cmd_host_stack(args) -> None:
    action = getattr(args, "stack_action", None)
    handlers = {"up": _up, "list": _list, "down": _down}
    if action not in handlers:
        die("host stack needs one of: up, list, down")
    try:
        payload = handlers[action](args)
    except (hosting.HostingError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        _refuse(args, exc)
        return
    if not getattr(args, "json", False) and action == "up" and payload["mode"] == "confirm":
        info(f"host stack: submitted {payload['job_id']}")
    _emit(args, payload, _render_text(payload))
