"""Staged, bounded source transfer for agent-aware synchronization.

The transport deliberately uses the existing registered remote adapter.  It
never invokes Docker or SSH directly and never replaces a caller's active
workspace: bytes are uploaded to an owner-only generation directory and the
remote pointer is advanced only after the complete archive is present.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import tarfile
from typing import Any, Callable, Mapping

from sandbox.sync.capture import CaptureManifest
from sandbox.sync.models import SourceGeneration, SynchronizationRelationship


class RemoteSyncTransportError(RuntimeError):
    """Bounded transfer failure with a stable public code."""

    def __init__(self, message: str, code: str = "remote_unavailable", *, retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_SAFE_REMOTE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SAFE_PROJECT = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _safe_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise RemoteSyncTransportError(f"{label} is invalid", "ownership_conflict", retryable=False)
    return value


def _project_relative(project_root: Path, git_root: Path, path: str) -> str:
    """Map a Git-relative manifest entry back to the selected project root."""
    prefix = project_root.relative_to(git_root).as_posix()
    relative = path if prefix == "." else str(PurePosixPath(path).relative_to(PurePosixPath(prefix)))
    if not relative or relative.startswith("../") or relative in {".", ".."}:
        raise RemoteSyncTransportError("manifest path is outside the selected project", "ownership_conflict", retryable=False)
    return relative


def _archive(project_root: Path, manifest: CaptureManifest, *, project_relative_manifest: bool = False) -> tuple[bytes, str]:
    """Build a bounded gzip archive from the already screened manifest."""
    output = io.BytesIO()
    canonical_entries: list[dict[str, object]] = []
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for entry in manifest.entries:
            relative = _project_relative(project_root, manifest.git_root, entry.path)
            metadata = entry.canonical()
            if project_relative_manifest:
                metadata["path"] = relative
            canonical_entries.append(metadata)
            source = project_root / relative
            try:
                resolved = source.resolve(strict=True)
                resolved.relative_to(project_root.resolve())
            except (OSError, ValueError) as exc:
                raise RemoteSyncTransportError("source changed before transfer", "unstable_capture", retryable=True) from exc
            if not source.is_file() or source.is_symlink():
                raise RemoteSyncTransportError("source changed before transfer", "unstable_capture", retryable=True)
            info = tarfile.TarInfo(relative)
            content = source.read_bytes()
            if len(content) != entry.size or hashlib.sha256(content).hexdigest() != entry.sha256:
                raise RemoteSyncTransportError("source changed before transfer", "unstable_capture", retryable=True)
            info.size = len(content)
            info.mode = 0o755 if entry.executable else 0o644
            archive.addfile(info, io.BytesIO(content))
        archive_manifest_digest = hashlib.sha256(json.dumps(
            canonical_entries, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        manifest_data = json.dumps({
            "schema_version": 1,
            "generation_id": manifest.generation_id,
            "manifest_digest": manifest.manifest_digest,
            "archive_manifest_digest": archive_manifest_digest,
            "file_count": manifest.file_count,
            "byte_count": manifest.byte_count,
            "entries": canonical_entries if project_relative_manifest else manifest.canonical_entries(),
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        info = tarfile.TarInfo(".sandbox-sync-manifest.json")
        info.mode = 0o600
        info.size = len(manifest_data)
        archive.addfile(info, io.BytesIO(manifest_data))
    return output.getvalue(), archive_manifest_digest


class RemoteSyncTransport:
    """Transfer one immutable generation through the registered remote runner."""

    def __init__(self, *, remote_lookup: Callable, ssh_run: Callable, ssh_process: Callable,
                 resolve_home: Callable, workspace_preflight: Callable,
                 workspace_publish: Callable | None = None,
                 workspace_reconcile: Callable | None = None,
                 clock: Callable | None = None) -> None:
        if not callable(workspace_preflight):
            raise TypeError("workspace_preflight is required")
        self.remote_lookup = remote_lookup
        self.ssh_run = ssh_run
        self.ssh_process = ssh_process
        self.resolve_home = resolve_home
        self.workspace_preflight = workspace_preflight
        self.workspace_publish = workspace_publish
        self.workspace_reconcile = workspace_reconcile
        self.clock = clock

    def _verify_workspace_owner(
        self, relationship: SynchronizationRelationship,
    ) -> Mapping[str, Any]:
        """Recheck controller-owned workspace identity before source mutation."""
        try:
            evidence = self.workspace_preflight(relationship)
        except RemoteSyncTransportError:
            raise
        except Exception as exc:
            remote_code = getattr(exc, "code", "")
            code = (
                "ownership_conflict"
                if isinstance(remote_code, str) and any(
                    marker in remote_code
                    for marker in ("ownership", "identity", "conflict")
                )
                else "remote_unavailable"
            )
            raise RemoteSyncTransportError(
                "remote workspace ownership preflight failed", code,
                retryable=code == "remote_unavailable",
            ) from None
        if not isinstance(evidence, Mapping):
            raise RemoteSyncTransportError(
                "remote workspace ownership preflight is invalid",
                "remote_unavailable", retryable=True,
            )
        if (
            evidence.get("workspace_id") != relationship.workspace_id
            or evidence.get("project_identity") != relationship.project_identity
        ):
            raise RemoteSyncTransportError(
                "remote workspace ownership does not match the synchronization relationship",
                "ownership_conflict", retryable=False,
            )
        index = evidence.get("index")
        checkout = evidence.get("checkout")
        locator_digests = evidence.get("locator_digests")
        deployment_proof = evidence.get("deployment_proof")
        source_binding = evidence.get("source_binding")
        ready = (
            evidence.get("ok") is True
            and evidence.get("lifecycle") == "ready"
            and evidence.get("state") == "ready"
            and evidence.get("status") == "ready"
            and evidence.get("error") is None
            and isinstance(index, Mapping)
            and index.get("complete") is True
            and isinstance(index.get("generation"), int)
            and not isinstance(index.get("generation"), bool)
            and index.get("generation") >= 0
            and isinstance(checkout, Mapping)
            and checkout.get("present") is True
            and isinstance(checkout.get("identity"), str)
            and _SHA256.fullmatch(checkout["identity"]) is not None
            and isinstance(locator_digests, Mapping)
            and locator_digests.get("checkout") == checkout.get("identity")
            and isinstance(locator_digests.get("source_checkout"), str)
            and _SHA256.fullmatch(locator_digests["source_checkout"]) is not None
            and isinstance(deployment_proof, Mapping)
            and deployment_proof.get("checkout_locator_digest") == checkout.get("identity")
            and isinstance(deployment_proof.get("source_identity"), str)
            and _SHA256.fullmatch(deployment_proof["source_identity"]) is not None
            and isinstance(deployment_proof.get("source_commit"), str)
            and _FULL_COMMIT.fullmatch(deployment_proof["source_commit"]) is not None
            and isinstance(source_binding, Mapping)
            and source_binding.get("checkout_present") is True
            and source_binding.get("source_present") is True
            and source_binding.get("healthy") is True
        )
        if not ready:
            raise RemoteSyncTransportError(
                "remote workspace is not ready with complete source binding",
                "remote_unavailable", retryable=True,
            )
        return evidence

    def transfer(
        self,
        project_root: str | Path,
        manifest: CaptureManifest,
        relationship: SynchronizationRelationship,
        generation: SourceGeneration,
    ) -> dict[str, Any]:
        remote_name = relationship.remote_name
        if not _SAFE_REMOTE.fullmatch(remote_name):
            raise RemoteSyncTransportError("remote name is invalid", "ownership_conflict", retryable=False)
        remote = self.remote_lookup(remote_name)
        if not isinstance(remote, dict) or remote.get("provisioned") is not True:
            raise RemoteSyncTransportError("remote is not provisioned", "remote_unavailable", retryable=True)
        evidence = self._verify_workspace_owner(relationship)
        try:
            archive, archive_digest = _archive(
                Path(project_root).expanduser().resolve(), manifest,
                project_relative_manifest=True,
            )
            if not callable(self.workspace_publish):
                raise RemoteSyncTransportError(
                    "controller-owned synchronization publication is unavailable",
                    "remote_unavailable", retryable=True,
                )
            try:
                publication = self.workspace_publish(
                    relationship, generation, manifest, archive_digest, evidence, archive,
                )
            except RemoteSyncTransportError:
                raise
            except Exception as exc:
                remote_code = getattr(exc, "code", "") or str(exc)
                conflict = isinstance(remote_code, str) and any(
                    marker in remote_code
                    for marker in (
                        "ownership", "identity", "conflict", "not_found",
                        "destroyed", "generation", "recovery_required",
                    )
                )
                raise RemoteSyncTransportError(
                    "remote workspace changed before synchronization publication",
                    "ownership_conflict" if conflict else "remote_unavailable",
                    retryable=not conflict,
                ) from None
            if not isinstance(publication, Mapping) or publication.get("ok") is not True:
                raise RemoteSyncTransportError(
                    "remote generation publication failed",
                    "remote_unavailable", retryable=True,
                )
            return {
                "accepted_generation": generation.generation_id,
                "manifest_digest": manifest.manifest_digest,
                "file_count": manifest.file_count,
                "byte_count": manifest.byte_count,
                "remote": remote_name,
                "status": "accepted",
            }
        except RemoteSyncTransportError:
            raise
        except Exception as exc:
            raise RemoteSyncTransportError("remote synchronization transport failed") from None

    def reconcile(
        self, relationship: SynchronizationRelationship,
        generation: SourceGeneration,
    ) -> dict[str, Any]:
        """Probe one lost acknowledgment without publishing or replaying bytes."""
        remote_name = relationship.remote_name
        if not _SAFE_REMOTE.fullmatch(remote_name):
            raise RemoteSyncTransportError(
                "remote name is invalid", "ownership_conflict", retryable=False,
            )
        remote = self.remote_lookup(remote_name)
        if not isinstance(remote, dict) or remote.get("provisioned") is not True:
            raise RemoteSyncTransportError(
                "remote is not provisioned", "remote_unavailable", retryable=True,
            )
        evidence = self._verify_workspace_owner(relationship)
        try:
            if not callable(self.workspace_reconcile):
                raise ValueError("controller reconciliation is unavailable")
            payload = self.workspace_reconcile(
                relationship, generation, evidence,
            )
            if not isinstance(payload, Mapping) or payload.get("status") != "accepted":
                return {"status": "unknown", "request_id": generation.request_id}
            return {
                "status": "accepted",
                "accepted_generation": generation.generation_id,
                "manifest_digest": generation.manifest_digest,
                "file_count": generation.file_count,
                "byte_count": generation.byte_count,
                "request_id": generation.request_id,
            }
        except RemoteSyncTransportError:
            raise
        except Exception:
            raise RemoteSyncTransportError(
                "remote synchronization acknowledgment is unknown",
                "transport_unknown", retryable=False,
            ) from None


class HostSourceSyncTransport:
    """Update a hosted Compose source tree without recreating its services.

    Hosted applications bind-mount ``deploy-src/hosts/<project>`` directly.
    Replacing that directory would leave containers holding the old bind
    inode, so this adapter stages the screened archive elsewhere and copies
    each managed file into the existing source directory atomically. A small
    remote manifest records only files this relationship owns; deletions never
    touch unknown files, Git metadata, or runtime state.
    """

    def __init__(self, *, remote_lookup: Callable, ssh_run: Callable,
                 ssh_process: Callable, resolve_home: Callable,
                 project_slug: str) -> None:
        if not isinstance(project_slug, str) or not _SAFE_PROJECT.fullmatch(project_slug):
            raise RemoteSyncTransportError("host project is invalid", "ownership_conflict", retryable=False)
        self.remote_lookup = remote_lookup
        self.ssh_run = ssh_run
        self.ssh_process = ssh_process
        self.resolve_home = resolve_home
        self.project_slug = project_slug

    def transfer(
        self,
        project_root: str | Path,
        manifest: CaptureManifest,
        relationship: SynchronizationRelationship,
        generation: SourceGeneration,
    ) -> dict[str, Any]:
        remote_name = relationship.remote_name
        if not _SAFE_REMOTE.fullmatch(remote_name):
            raise RemoteSyncTransportError("remote name is invalid", "ownership_conflict", retryable=False)
        remote = self.remote_lookup(remote_name)
        if not isinstance(remote, dict) or remote.get("provisioned") is not True:
            raise RemoteSyncTransportError("remote is not provisioned", "remote_unavailable", retryable=True)
        try:
            home = self.resolve_home(remote)
            if not isinstance(home, str) or not home.startswith("/"):
                raise ValueError("remote sandbox home is invalid")
            generation_id = _safe_id(generation.generation_id, "generation id")
            source = f"{home}/deploy-src/hosts/{self.project_slug}"
            runtime = f"{home}/runtime/hosts/{self.project_slug}"
            staging_root = f"{runtime}/sync-staging"
            staging = f"{staging_root}/{generation_id}"
            state = f"{runtime}/sync-managed.json"
            archive, archive_digest = _archive(
                Path(project_root).expanduser().resolve(), manifest,
                project_relative_manifest=True,
            )
            prepare = (
                f"set -eu; test -d {shlex.quote(source)} && "
                f"mkdir -p {shlex.quote(staging_root)} && "
                f"rm -rf -- {shlex.quote(staging)} && "
                f"mkdir -m 0700 {shlex.quote(staging)}"
            )
            prepared = self.ssh_run(remote, prepare, timeout=30)
            if prepared.returncode != 0:
                raise RemoteSyncTransportError("host source tree is unavailable")
            uploaded = self.ssh_process(
                remote, f"tar -xzf - -C {shlex.quote(staging)}",
                input_data=archive, timeout=120,
            )
            if uploaded.returncode != 0:
                raise RemoteSyncTransportError("host source generation upload failed")
            publish_program = r'''import hashlib, json, os, pathlib, shutil, stat, sys, tempfile
source = pathlib.Path(sys.argv[1]).resolve()
staging = pathlib.Path(sys.argv[2]).resolve()
state = pathlib.Path(sys.argv[3]).resolve()
generation = sys.argv[4]
manifest_digest = sys.argv[5]
archive_digest = sys.argv[6]
expected_count = int(sys.argv[7])
expected_bytes = int(sys.argv[8])
manifest_path = staging / ".sandbox-sync-manifest.json"

def safe_join(root, value):
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise RuntimeError("unsafe managed source path")
    parts = pathlib.PurePosixPath(value).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise RuntimeError("unsafe managed source path")
    candidate = root / value
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError:
        raise RuntimeError("managed source path escapes source root")
    return candidate

document = json.loads(manifest_path.read_text(encoding="utf-8"))
if set(document) != {"schema_version", "generation_id", "manifest_digest", "archive_manifest_digest", "file_count", "byte_count", "entries"}:
    raise RuntimeError("host source manifest is invalid")
if (document.get("schema_version") != 1
        or document.get("generation_id") != generation
        or document.get("manifest_digest") != manifest_digest
        or document.get("archive_manifest_digest") != archive_digest
        or document.get("file_count") != expected_count
        or document.get("byte_count") != expected_bytes
        or not isinstance(document.get("entries"), list)
        or len(document["entries"]) != expected_count):
    raise RuntimeError("host source manifest is invalid")
canonical = json.dumps(document["entries"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
if hashlib.sha256(canonical).hexdigest() != archive_digest:
    raise RuntimeError("host source manifest digest is invalid")
new_entries = {}
total_bytes = 0
for item in document["entries"]:
    if (not isinstance(item, dict)
            or set(item) != {"path", "size", "sha256", "executable"}
            or not isinstance(item.get("path"), str)
            or isinstance(item.get("size"), bool)
            or not isinstance(item.get("size"), int)
            or not isinstance(item.get("sha256"), str)
            or not isinstance(item.get("executable"), bool)):
        raise RuntimeError("host source manifest is invalid")
    path = item["path"]
    incoming = safe_join(staging, path)
    safe_join(source, path)
    details = incoming.lstat()
    content = incoming.read_bytes()
    if (stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode)
            or len(content) != item["size"]
            or hashlib.sha256(content).hexdigest() != item["sha256"]
            or bool(details.st_mode & stat.S_IXUSR) != item["executable"]):
        raise RuntimeError("host source generation member mismatch")
    if path in new_entries:
        raise RuntimeError("host source manifest path is duplicated")
    new_entries[path] = {"executable": bool(item.get("executable"))}
    total_bytes += len(content)
if total_bytes != expected_bytes:
    raise RuntimeError("host source generation byte count is invalid")

old_entries = {}
if state.is_file() and not state.is_symlink():
    prior = json.loads(state.read_text(encoding="utf-8"))
    if prior.get("schema_version") != 1 or not isinstance(prior.get("entries"), dict):
        raise RuntimeError("host source ownership state is invalid")
    old_entries = prior["entries"]
for path in sorted(set(old_entries) - set(new_entries), reverse=True):
    target = safe_join(source, path)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.exists():
        raise RuntimeError("managed source deletion found a non-file path")

for path, metadata in sorted(new_entries.items()):
    incoming = safe_join(staging, path)
    target = safe_join(source, path)
    if incoming.is_symlink() or not incoming.is_file():
        raise RuntimeError("host source generation contains an invalid file")
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise RuntimeError("managed source target is not a regular file")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".sandbox-sync-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as output, incoming.open("rb") as source_file:
            shutil.copyfileobj(source_file, output)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o755 if metadata.get("executable") else 0o644)
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass

state.parent.mkdir(parents=True, exist_ok=True)
payload = json.dumps({
    "schema_version": 1,
    "generation_id": generation,
    "manifest_digest": document.get("manifest_digest"),
    "entries": new_entries,
}, sort_keys=True, separators=(",", ":")) + "\n"
fd, temporary = tempfile.mkstemp(prefix=".sandbox-sync-state-", dir=str(state.parent))
try:
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, state)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
shutil.rmtree(staging)
'''
            publish = (
                f"python3 -c {shlex.quote(publish_program)} "
                f"{shlex.quote(source)} {shlex.quote(staging)} "
                f"{shlex.quote(state)} {shlex.quote(generation_id)} "
                f"{shlex.quote(manifest.manifest_digest)} "
                f"{shlex.quote(archive_digest)} {manifest.file_count} {manifest.byte_count}"
            )
            published = self.ssh_run(remote, publish, timeout=60)
            if published.returncode != 0:
                raise RemoteSyncTransportError("host source publication failed")
            return {
                "accepted_generation": generation.generation_id,
                "manifest_digest": manifest.manifest_digest,
                "file_count": manifest.file_count,
                "byte_count": manifest.byte_count,
                "remote": remote_name,
                "source": self.project_slug,
                "restarted": False,
                "status": "accepted",
            }
        except RemoteSyncTransportError:
            raise
        except Exception:
            raise RemoteSyncTransportError("host source synchronization failed") from None


def default_remote_sync_transport() -> RemoteSyncTransport:
    from sandbox.core import _remote
    from sandbox.transports.remote_workspaces import RemoteWorkspaceTransport

    workspace_transport = RemoteWorkspaceTransport(
        remote_lookup=_remote.get_remote,
        ssh_run=_remote.ssh_run,
        ssh_process=_remote.ssh_process,
        remote_sb_path=_remote.remote_sb_path,
    )
    return RemoteSyncTransport(
        remote_lookup=_remote.get_remote,
        ssh_run=_remote.ssh_run,
        ssh_process=_remote.ssh_process,
        resolve_home=_remote.resolve_sandbox_home,
        workspace_preflight=lambda relationship: workspace_transport.status(
            relationship.remote_name,
            relationship.workspace_id,
            project_identity=relationship.project_identity,
        ),
        workspace_publish=lambda relationship, generation, manifest, archive_digest, evidence, archive:
            workspace_transport.publish_sync(
                relationship.remote_name,
                relationship.workspace_id,
                relationship.project_identity,
                generation.generation_id,
                manifest.manifest_digest,
                archive_digest,
                manifest.file_count,
                manifest.byte_count,
                evidence["index"]["generation"],
                archive,
            ),
        workspace_reconcile=lambda relationship, generation, evidence:
            workspace_transport.reconcile_sync(
                relationship.remote_name,
                relationship.workspace_id,
                relationship.project_identity,
                generation.generation_id,
                generation.manifest_digest,
                generation.file_count,
                generation.byte_count,
                evidence["index"]["generation"],
            ),
    )


__all__ = [
    "HostSourceSyncTransport", "RemoteSyncTransport", "RemoteSyncTransportError",
    "default_remote_sync_transport",
]
