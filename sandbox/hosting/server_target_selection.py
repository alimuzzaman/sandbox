"""Read an existing owner's deployed exec selector without controller history.

Lifetime copy, successful server summary, Git revision and current Docker
Compose labels must agree. Nothing here adopts, claims, repairs or deploys a
target. Missing historical evidence remains unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import shlex
import time

from sandbox.hosting.target_resolution import ResolvedTarget, TargetRefusal, parse_target

UNAVAILABLE = "host_server_target_evidence_unavailable"
BUSY = "host_target_busy"
_ISSUER = object()


def _refuse(code=UNAVAILABLE):
    raise TargetRefusal(code, "server target evidence is missing, changed or uncertain")


@dataclass(frozen=True)
class OwnerExecSelection:
    target: ResolvedTarget
    copy_id: str | None
    source_commit: str
    source_dir: str
    compose_files: tuple[str, ...]
    native_keys: tuple[str, ...]
    fingerprint: str
    _entry: object = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)

    def require_current(self):
        if type(self) is not OwnerExecSelection or self._seal is not _ISSUER:
            _refuse()
        current = read_owner_exec_selection(self._entry,
            target=self.target.label, name=None, environment=self.target.environment)
        if current != self:
            _refuse()
        return self

    def compose_prefix(self):
        self.require_current()
        from sandbox.hosting.lifetime.hoststore import compose_project_for
        home = self._entry.sandbox_home()
        env = str(Path(home) / "runtime" / "hosts" / self.target.project /
                  self.target.environment / "environment.env")
        argv = ["env", "SANDBOX_HOST_ENV_FILE=" + env, "docker", "compose",
                "--env-file", env, "-p", compose_project_for(
                    self.target.project, self.target.environment)]
        for path in self.compose_files:
            argv.extend(["-f", path])
        return shlex.join(argv)


def _output(entry, argv):
    result = entry.process(shlex.join(argv), timeout=30)
    if result.returncode or result.stderr or len(result.stdout or "") > 65536:
        _refuse()
    return (result.stdout or "").strip()


def read_owner_exec_selection(entry, *, target=None, name=None, environment=None):
    from sandbox.hosting.server_cli_target import require_owner_target
    from sandbox.hosting.lifetime.hoststore import Store, compose_project_for
    from sandbox.hosting.panel_records import read_server_records
    from sandbox.hosting.recovery.repository import RecoveryRepository
    from sandbox.hosting.host_exec import busy_reason
    context = require_owner_target(entry)
    if bool(target) == bool(name):
        raise TargetRefusal("host_target_invalid", "pass exactly one of --target or --name")
    if name and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", name) is None:
        raise TargetRefusal("host_target_invalid", "invalid stack name")
    pair = parse_target(target) if target else None
    home = Path(context.owner.sandbox_home)
    inventory = Store(home).peek(int(time.time()))
    if inventory.get("truncated") or inventory.get("invalid"):
        _refuse()
    matches = [item for item in inventory["records"] if
        ((item.get("project"), item.get("environment")) == pair if pair else item.get("slug") == name)]
    if len(matches) != 1:
        _refuse()
    lifetime = matches[0]
    project, env = parse_target(f"{lifetime.get('project')}/{lifetime.get('environment')}")
    if environment and environment != env:
        raise TargetRefusal("host_target_invalid", "environment does not match server target")
    if lifetime.get("state") != "active" or lifetime.get("reap_run") is not None:
        _refuse(BUSY)
    compose_project = compose_project_for(project, env)
    if lifetime.get("compose_project") != compose_project:
        _refuse()
    records = read_server_records(home / "runtime")
    if records["partial_reasons"]:
        _refuse()
    summaries = [item for item in records["targets"] if
                 (item["project"], item["environment"]) == (project, env)]
    if len(summaries) != 1:
        _refuse()
    summary = summaries[0]
    commit = summary.get("commit")
    if (summary["last_apply"]["status"] != "succeeded"
            or summary["compose_project"] != compose_project
            or commit != lifetime.get("commit")
            or not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None):
        _refuse(BUSY)
    stack = summary.get("stack")
    if lifetime.get("kind") == "stack":
        if (not stack or stack.get("slug") != lifetime.get("slug")
                or stack.get("commit") != commit or stack.get("ports") != lifetime.get("ports")
                or re.fullmatch(r"[0-9a-f]{8}", lifetime.get("copy_id") or "") is None):
            _refuse()
    repository = RecoveryRepository()
    state = repository.load()
    keys = sorted(key for key in state["hosts"] if
                  isinstance(key, str) and key.split("/")[1:] == [project, env])
    for key in keys:
        if busy_reason(state["hosts"][key], None):
            _refuse(BUSY)
    source = home / "deploy-src" / "hosts" / project
    if not source.is_dir() or source.is_symlink() or source.resolve() != source:
        _refuse()
    if _output(entry, ["git", "-c", "core.hooksPath=/dev/null", "-C", str(source),
                        "rev-parse", "--verify", "HEAD"]) != commit:
        _refuse()
    ids = _output(entry, ["docker", "ps", "--filter", "label=com.docker.compose.project=" +
                         compose_project, "--format", "{{.ID}}"]).splitlines()
    if not ids or len(ids) > 64 or any(re.fullmatch(r"[0-9a-f]{12,64}", item) is None for item in ids):
        _refuse()
    # Only three known nonsecret labels are requested. No container environment,
    # full inspect/config output or runtime environment file is read or returned.
    projection = ('[{{json (index .Config.Labels "com.docker.compose.project")}},'
                  '{{json (index .Config.Labels "com.docker.compose.project.config_files")}},'
                  '{{json (index .Config.Labels "com.docker.compose.project.working_dir")}}]')
    lines = _output(entry, ["docker", "inspect", "--format", projection, *ids]).splitlines()
    if len(lines) != len(ids):
        _refuse()
    try:
        labels = [json.loads(line) for line in lines]
        if any(item != labels[0] for item in labels):
            _refuse()
        observed_project, config_files, working = labels[0]
        if observed_project != compose_project:
            _refuse()
        paths = tuple(config_files.split(","))
    except (ValueError, TypeError, AttributeError):
        _refuse()
    override = home / "runtime" / "hosts" / project / env / "compose.override.yml"
    if not 2 <= len(paths) <= 33 or paths[-1] != str(override):
        _refuse()
    if working != str(Path(paths[0]).parent):
        _refuse()
    for raw in paths:
        path = Path(raw)
        if path != override and (source not in path.parents or path.suffix not in {".yml", ".yaml", ".json"}):
            _refuse()
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            _refuse()
        if path != override:
            relative = path.relative_to(source).as_posix()
            _output(entry, ["git", "-c", "core.hooksPath=/dev/null", "-C", str(source),
                            "ls-files", "--error-unmatch", "--", relative])
            _output(entry, ["git", "-c", "core.hooksPath=/dev/null", "-C", str(source),
                            "diff", "--quiet", "HEAD", "--", relative])
    # Bind measured public source/manifest selection and runtime container set,
    # not provider bytes or opaque owner claims from the controller.
    digest = hashlib.sha256(json.dumps({"lifetime": lifetime, "summary": summary,
        "containers": sorted(ids), "labels": labels[0], "keys": keys},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    entry.require_current()
    resolved = ResolvedTarget(entry.remote_name, project, env,
        "stack" if stack else "plain", str(source), slug=lifetime.get("slug"))
    native_keys = tuple(sorted(set(keys + [resolved.state_key])))
    return OwnerExecSelection(resolved, lifetime.get("copy_id"), commit, str(source),
        paths, native_keys, digest, entry, _ISSUER)
