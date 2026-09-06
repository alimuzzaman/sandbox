"""Registered-remote controller for the scoped hosted recovery profile.

The recovery coordinator owns encryption and Drive publication.  This adapter
owns only the fixed, reviewed production source declarations for
``amarsonar-bangla``.  It never accepts caller paths or prints captured bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import tarfile
import tempfile
from typing import Callable

from sandbox.recovery.database import DatabaseCapture
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.filesystem import validate_archive
from sandbox.recovery.hosted import HostedCaptureReceipt, HostedObservation
from sandbox.recovery.materialize import SourceBinding
from sandbox.recovery.models import ArtifactPlan, RecoveryPlan


_REMOTE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REVISION = re.compile(r"^[0-9a-f]{20,64}$")
_SUPPORTED = {"control-plane", "amarsonar-bangla-prod"}

_WP_CONTAINER = "sandbox-host-amarsonar-bangla-production-wordpress-1"
_DB_CONTAINER = "sandbox-host-amarsonar-bangla-production-db-1"
_EXPECTED_MOUNTS = {
    _WP_CONTAINER: {
        ("volume", "sandbox-host-amarsonar-bangla-production_wordpress-root", "/var/www/html"),
        ("volume", "sandbox-host-amarsonar-bangla-production_wordpress-uploads", "/var/www/html/wp-content/uploads"),
    },
    _DB_CONTAINER: {
        ("volume", "sandbox-host-amarsonar-bangla-production_wordpress-db", "/var/lib/mysql"),
    },
}


def _completed_ok(value: object) -> tuple[bytes, int]:
    """Read one injected subprocess result without exposing diagnostics."""
    code = getattr(value, "returncode", 1)
    stdout = getattr(value, "stdout", b"")
    if isinstance(stdout, str):
        stdout = stdout.encode()
    if not isinstance(stdout, bytes) or isinstance(code, bool) or not isinstance(code, int):
        raise RecoveryError("remote capture response is invalid", "remote_capture_failed")
    return stdout, code


def _safe_json_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class _RemoteState:
    entry: dict
    inventory: dict
    revision: str
    machine_identity: str
    source_digest: str


class RegisteredRemoteRecoveryController:
    """Capture one reviewed hosted production declaration over registered SSH."""

    def __init__(self, *, remote_lookup: Callable | None = None,
                 ssh_run: Callable | None = None, ssh_process: Callable | None = None,
                 resolve_home: Callable | None = None, service_status: Callable | None = None,
                 inventory: Callable | None = None, environment: dict[str, str] | None = None):
        if any(value is None for value in (remote_lookup, ssh_run, ssh_process,
                                           resolve_home, service_status, inventory)):
            from sandbox.core import _remote
            from sandbox.recovery.inventory import SandboxRemoteInventory
            remote_lookup = remote_lookup or _remote.get_remote
            ssh_run = ssh_run or _remote.ssh_run
            ssh_process = ssh_process or _remote.ssh_process
            resolve_home = resolve_home or _remote.resolve_sandbox_home
            service_status = service_status or _remote.remote_mcp_service_status
            inventory = inventory or SandboxRemoteInventory().discover
        self._lookup = remote_lookup
        self._ssh_run = ssh_run
        self._ssh_process = ssh_process
        self._resolve_home = resolve_home
        self._service_status = service_status
        self._inventory = inventory
        self._environment = environment if environment is not None else os.environ

    @staticmethod
    def _check_inventory(inventory: dict) -> None:
        containers = set(inventory.get("managed_containers") or ())
        if not _WP_CONTAINER in containers or not _DB_CONTAINER in containers:
            raise RecoveryError("amarsonar production containers are unavailable", "remote_source_unavailable")
        mounts = inventory.get("mounts")
        if not isinstance(mounts, dict):
            raise RecoveryError("remote mount inventory is invalid", "remote_source_unavailable")
        for container, expected in _EXPECTED_MOUNTS.items():
            records = mounts.get(container)
            actual = {(item.get("type"), item.get("name"), item.get("destination"))
                      for item in records or () if isinstance(item, dict)}
            if not expected.issubset(actual):
                raise RecoveryError("amarsonar production mounts are unavailable", "remote_source_unavailable")

    def _state(self, remote: str) -> _RemoteState:
        if not isinstance(remote, str) or not _REMOTE.fullmatch(remote):
            raise RecoveryError("remote name is invalid", "remote_unavailable")
        entry = self._lookup(remote)
        if not isinstance(entry, dict) or entry.get("provisioned") is not True:
            raise RecoveryError("remote is not provisioned", "remote_unavailable")
        try:
            inventory = self._inventory(remote)
            status = self._service_status(entry)
            home = self._resolve_home(entry)
            hostname_result = self._ssh_run(entry, "hostname -s", timeout=15)
        except RecoveryError:
            raise
        except Exception as exc:
            raise RecoveryError("remote recovery preflight failed", "remote_unavailable") from exc
        if not isinstance(inventory, dict) or not isinstance(home, str) or not home.startswith("/"):
            raise RecoveryError("remote recovery preflight is invalid", "remote_unavailable")
        self._check_inventory(inventory)
        revision = status.get("installed_runtime_revision") if isinstance(status, dict) else None
        if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
            raise RecoveryError("remote runtime revision is unavailable", "remote_unavailable")
        if isinstance(status, dict) and status.get("runtime_revision_state") not in {"match", "unknown"}:
            raise RecoveryError("remote runtime revision is stale", "remote_revision_mismatch")
        hostname, code = _completed_ok(hostname_result)
        hostname_text = hostname.decode("utf-8", errors="replace").strip()
        if code != 0 or not _HOST.fullmatch(hostname_text):
            raise RecoveryError("remote machine identity is unavailable", "remote_unavailable")
        machine_identity = f"{remote}:{hostname_text}"
        digest_input = {
            "remote": remote, "home": home, "machine_identity": machine_identity,
            "revision": revision,
            "containers": sorted(item for item in inventory.get("managed_containers", ())
                                  if item in _EXPECTED_MOUNTS),
            "mounts": {name: sorted([{
                "type": item.get("type"), "name": item.get("name"),
                "destination": item.get("destination"), "rw": item.get("rw"),
            } for item in inventory.get("mounts", {}).get(name, ()) if isinstance(item, dict)],
                        key=lambda item: (str(item["destination"]), str(item["name"])))
                      for name in sorted(_EXPECTED_MOUNTS)},
            "repository": inventory.get("repositories", {}).get("amarsonar-bangla", {}),
        }
        return _RemoteState(entry, inventory, revision, machine_identity,
                            _safe_json_digest(digest_input))

    def observe(self, remote: str, plan: RecoveryPlan) -> HostedObservation:
        state = self._state(remote)
        profiles = set(plan.profiles)
        if not profiles.issubset(_SUPPORTED):
            raise RecoveryError("recovery profile is not supported by the hosted controller",
                                "unsupported_materialization")
        coverage = {}
        for artifact in plan.artifacts:
            if artifact.profile_id == "control-plane":
                coverage[artifact.profile_id] = tuple(artifact.sources)
            elif artifact.profile_id == "amarsonar-bangla-prod":
                coverage[artifact.profile_id] = tuple(artifact.sources)
            else:
                raise RecoveryError("recovery profile is not supported by the hosted controller",
                                    "unsupported_materialization")
        return HostedObservation(
            SourceBinding(remote, state.machine_identity, state.revision, state.source_digest),
            coverage,
        )

    @staticmethod
    def _write_private(path: Path, payload: bytes) -> Path:
        if path.exists() or path.is_symlink() or not payload:
            raise RecoveryError("remote recovery artifact is invalid", "invalid_materialization_receipt")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path

    @staticmethod
    def _validate_combined_archive(path: Path) -> None:
        members = validate_archive(path)
        if set(members) != {"database.sql", "wordpress.tar"}:
            raise RecoveryError("remote WordPress archive is incomplete", "invalid_materialization_receipt")
        with tarfile.open(path, "r") as archive:
            database = archive.extractfile("database.sql")
            wordpress = archive.extractfile("wordpress.tar")
            if database is None or wordpress is None:
                raise RecoveryError("remote WordPress archive is incomplete", "invalid_materialization_receipt")
            with tempfile.TemporaryDirectory(prefix="sandbox-recovery-validate-", dir=str(path.parent)) as directory:
                db_path = Path(directory) / "database.sql"
                wp_path = Path(directory) / "wordpress.tar"
                db_path.write_bytes(database.read())
                wp_path.write_bytes(wordpress.read())
                os.chmod(db_path, 0o600); os.chmod(wp_path, 0o600)
                DatabaseCapture._validate_format("mariadb", db_path)
                # GNU tar emits a leading ``./`` for the directory-root
                # capture.  Validate its canonical members here while keeping
                # the shared strict validator for the outer recovery archive.
                with tarfile.open(wp_path, "r") as nested:
                    seen = set()
                    for member in nested.getmembers():
                        raw_name = member.name
                        # GNU tar's directory-root capture has one harmless
                        # root marker.  It is not a restore member.
                        if raw_name in {".", "./"}:
                            continue
                        name = raw_name.removeprefix("./")
                        parts = Path(name).parts
                        if (not name or name in seen or name.startswith("/")
                                or ".." in parts or member.isdev() or member.isfifo()):
                            raise RecoveryError("remote WordPress archive is invalid",
                                                "invalid_materialization_receipt")
                        if member.issym() or member.islnk():
                            target = Path(member.linkname)
                            if target.is_absolute() or ".." in target.parts:
                                raise RecoveryError("remote WordPress archive is invalid",
                                                    "invalid_materialization_receipt")
                        seen.add(name)

    def _capture_wordpress(self, state: _RemoteState, destination: Path,
                           request_id: str) -> Path:
        database_password = self._environment.get("SANDBOX_RECOVERY_DB_PASSWORD")
        if not isinstance(database_password, str) or not database_password:
            raise RecoveryError("database credential is not available", "missing_database_credential")
        home = self._resolve_home(state.entry)
        root = f"{home}/runtime/recovery-controller"
        script = r'''import pathlib, subprocess, sys, tarfile, tempfile
root = pathlib.Path(sys.argv[1]); output = pathlib.Path(sys.argv[2])
root.mkdir(mode=0o700, parents=True, exist_ok=True)
if root.is_symlink() or not root.is_dir(): raise SystemExit(7)
root.chmod(0o700)
if output.is_symlink(): raise SystemExit(7)
if output.exists():
    if not output.is_file() or output.stat().st_size == 0: raise SystemExit(7)
    sys.stdout.buffer.write(output.read_bytes()); raise SystemExit(0)
with tempfile.TemporaryDirectory(prefix="capture-", dir=str(root)) as work:
    work = pathlib.Path(work); database = work / "database.sql"; wordpress_tar = work / "wordpress.tar"
    # The password is supplied on stdin by the local brokered child and never
    # appears in argv or remote command text.
    password = sys.stdin.buffer.readline().rstrip(b"\r\n")
    if not password: raise SystemExit(3)
    env = {"PATH": "/usr/bin:/bin", "MYSQL_PWD": password.decode("utf-8")}
    with database.open("wb") as stream:
        dump = subprocess.run(["docker", "exec", "-e", "MYSQL_PWD", "sandbox-host-amarsonar-bangla-production-db-1",
                               "mariadb-dump", "--single-transaction", "--quick", "--routines", "--events", "--triggers",
                               "-u", "amarsonar", "amarsonar"], stdout=stream, stderr=subprocess.DEVNULL,
                              env=env, check=False, timeout=1800)
    if dump.returncode != 0 or not database.stat().st_size: raise SystemExit(4)
    # The WordPress container is the source of truth for the complete mounted
    # tree.  Its tar stream is copied into a private remote file first.
    with wordpress_tar.open("wb") as stream:
        tar_result = subprocess.run(["docker", "exec", "sandbox-host-amarsonar-bangla-production-wordpress-1",
                                     "tar", "-cf", "-", "-C", "/var/www/html", "--numeric-owner", "."],
                                    stdout=stream, stderr=subprocess.DEVNULL,
                                    check=False, timeout=1800)
    if tar_result.returncode != 0 or not wordpress_tar.stat().st_size: raise SystemExit(5)
    check_tar = work / "wordpress.check.tar"
    with check_tar.open("wb") as stream:
        check_result = subprocess.run(["docker", "exec", "sandbox-host-amarsonar-bangla-production-wordpress-1",
                                       "tar", "-cf", "-", "-C", "/var/www/html", "--numeric-owner", "."],
                                      stdout=stream, stderr=subprocess.DEVNULL,
                                      check=False, timeout=1800)
    if check_result.returncode != 0 or check_tar.stat().st_size != wordpress_tar.stat().st_size:
        raise SystemExit(6)
    import hashlib
    if hashlib.sha256(check_tar.read_bytes()).digest() != hashlib.sha256(wordpress_tar.read_bytes()).digest():
        raise SystemExit(6)
    temporary = output.with_name(output.name + ".pending")
    with tarfile.open(temporary, "w") as archive:
        archive.add(database, arcname="database.sql", recursive=False)
        archive.add(wordpress_tar, arcname="wordpress.tar", recursive=False)
    temporary.replace(output); output.chmod(0o600)
sys.stdout.buffer.write(output.read_bytes())'''
        command = "python3 -c " + shlex.quote(script) + " " + " ".join(shlex.quote(value) for value in (
            root, f"{root}/{request_id}.tar"))
        result = self._ssh_process(state.entry, command,
                                   input_data=(database_password + "\n").encode(), timeout=3600)
        payload, code = _completed_ok(result)
        if code != 0 or not payload:
            raise RecoveryError("remote WordPress capture failed", "remote_capture_failed")
        return self._write_private(destination / "amarsonar-bangla.tar", payload)

    def capture(self, remote: str, artifact: ArtifactPlan, destination: Path,
                binding: SourceBinding, request_id: str) -> HostedCaptureReceipt:
        if (not isinstance(request_id, str) or not re.fullmatch(r"recovery-[0-9a-f]{64}", request_id)
                or binding.remote != remote):
            raise RecoveryError("recovery controller request is invalid", "invalid_materialization_receipt")
        state = self._state(remote)
        if binding != SourceBinding(remote, state.machine_identity, state.revision, state.source_digest):
            raise RecoveryError("remote recovery source changed", "source_changed")
        if artifact.profile_id == "control-plane":
            safe = {
                "schema_version": 1, "sources": list(artifact.sources),
                "remote": remote, "machine_identity": state.machine_identity,
                "runtime_revision": state.revision,
                "source_digest": state.source_digest,
                "host_projects": sorted(state.inventory.get("host_projects", ())),
                "runtime_environments": state.inventory.get("runtime_environments", {}),
            }
            path = self._write_private(destination / "control-plane-declarations.json",
                                       (json.dumps(safe, sort_keys=True, separators=(",", ":")) + "\n").encode())
            return HostedCaptureReceipt(artifact.profile_id, artifact.artifact_id, request_id,
                                        (path,), tuple(artifact.sources), "declarations")
        if artifact.profile_id != "amarsonar-bangla-prod" or artifact.source_type != "filesystem":
            raise RecoveryError("recovery profile is not supported by the hosted controller",
                                "unsupported_materialization")
        path = self._capture_wordpress(state, destination, request_id)
        self._validate_combined_archive(path)
        return HostedCaptureReceipt(artifact.profile_id, artifact.artifact_id, request_id,
                                    (path,), tuple(artifact.sources), "tar")


__all__ = ["RegisteredRemoteRecoveryController"]
