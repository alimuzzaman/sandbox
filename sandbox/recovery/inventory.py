from __future__ import annotations

import json
import shlex

from .errors import RecoveryError


def _safe_text(value, *, allow_empty=True):
    return (isinstance(value, str) and (allow_empty or bool(value))
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _valid_mounts(value):
    if not isinstance(value, dict):
        return False
    fields = {"type", "name", "destination", "rw"}
    return all(
        _safe_text(container, allow_empty=False)
        and isinstance(records, list)
        and all(
            isinstance(record, dict)
            and set(record) == fields
            and all(_safe_text(record[field]) for field in fields - {"rw"})
            and isinstance(record["rw"], bool)
            for record in records
        )
        for container, records in value.items()
    )


def _valid_repositories(value):
    if not isinstance(value, dict):
        return False
    fields = {"head", "branch", "dirty_count", "untracked_count"}
    return all(
        _safe_text(project, allow_empty=False)
        and isinstance(record, dict)
        and set(record) == fields
        and _safe_text(record["head"])
        and _safe_text(record["branch"])
        and all(isinstance(record[field], int) and not isinstance(record[field], bool)
                and record[field] >= -1 for field in ("dirty_count", "untracked_count"))
        for project, record in value.items()
    )


class SandboxRemoteInventory:
    """Read-only discovery of Sandbox-managed hosting roots and service names."""

    def discover(self, remote_name: str) -> dict:
        import sandbox.core._remote as remote

        entry = remote.get_remote(remote_name)
        if not entry or not entry.get("provisioned"):
            raise RecoveryError("a provisioned remote is required", "remote_not_provisioned")
        home = remote.resolve_sandbox_home(entry)
        script = """
import json, pathlib, subprocess, sys
home = pathlib.Path(sys.argv[1])
warnings = []
def children(path):
    return sorted(p.name for p in path.iterdir() if p.is_dir()) if path.is_dir() else []
hosts = children(home / 'deploy-src' / 'hosts')
runtimes = {}
runtime_root = home / 'runtime' / 'hosts'
for project in children(runtime_root):
    runtimes[project] = children(runtime_root / project)
containers = []
mounts = {}
try:
    result = subprocess.run(['docker','ps','--format','{{.Names}}'], capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        warnings.append('container discovery command failed')
    containers = sorted(line for line in result.stdout.splitlines() if line.startswith('sandbox-host-'))
    if containers:
        inspected = subprocess.run(['docker','inspect',*containers], capture_output=True, text=True, timeout=20)
        if inspected.returncode != 0:
            warnings.append('container mount discovery command failed')
        for item in json.loads(inspected.stdout or '[]'):
            name = str(item.get('Name') or '').lstrip('/')
            mounts[name] = sorted([{
                'type': mount.get('Type'), 'name': mount.get('Name'),
                'destination': mount.get('Destination'), 'rw': bool(mount.get('RW')),
            } for mount in item.get('Mounts') or []], key=lambda row: (str(row['destination']), str(row['name'])))
except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
    warnings.append('container discovery unavailable')
repositories = {}
source_root = home / 'deploy-src' / 'hosts'
for project in hosts:
    repo = source_root / project
    try:
        head = subprocess.run(['git','-C',str(repo),'rev-parse','HEAD'],capture_output=True,text=True,timeout=5)
        branch = subprocess.run(['git','-C',str(repo),'branch','--show-current'],capture_output=True,text=True,timeout=5)
        status = subprocess.run(['git','-C',str(repo),'status','--porcelain'],capture_output=True,text=True,timeout=10)
        rows = status.stdout.splitlines()
        repositories[project] = {'head': head.stdout.strip(), 'branch': branch.stdout.strip(),
                                 'dirty_count': len(rows), 'untracked_count': sum(row.startswith('??') for row in rows)}
    except (OSError, subprocess.SubprocessError):
        warnings.append('git provenance discovery failed for ' + project)
        repositories[project] = {'head': '', 'branch': '', 'dirty_count': -1, 'untracked_count': -1}
print(json.dumps({'host_projects': hosts, 'runtime_environments': runtimes,
                  'managed_containers': containers, 'mounts': mounts,
                  'repositories': repositories, 'warnings': sorted(set(warnings))}))
""".strip()
        completed = remote.ssh_run(
            entry, f"python3 -c {shlex.quote(script)} {shlex.quote(home)}", timeout=30
        )
        if completed.returncode != 0:
            raise RecoveryError("could not inventory remote recovery candidates", "inventory_failed")
        try:
            data = json.loads((completed.stdout or "").splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise RecoveryError("remote inventory returned invalid data", "inventory_failed") from exc
        if (not isinstance(data, dict) or
                not isinstance(data.get("host_projects"), list) or
                not all(_safe_text(item, allow_empty=False) for item in data["host_projects"]) or
                not isinstance(data.get("runtime_environments"), dict) or
                not all(_safe_text(project, allow_empty=False)
                        and isinstance(value, list)
                        and all(_safe_text(item, allow_empty=False) for item in value)
                        for project, value in data["runtime_environments"].items()) or
                not isinstance(data.get("managed_containers"), list) or
                not all(_safe_text(item, allow_empty=False) for item in data["managed_containers"]) or
                not _valid_mounts(data.get("mounts")) or
                not _valid_repositories(data.get("repositories")) or
                not isinstance(data.get("warnings"), list) or
                not all(_safe_text(item, allow_empty=False) for item in data["warnings"])):
            raise RecoveryError("remote inventory returned invalid data", "inventory_failed")
        return {"sandbox_home": home, **data}
