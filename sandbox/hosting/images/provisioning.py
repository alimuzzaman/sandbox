"""Protected first-activation artifact provisioning for immutable image schema v2.

This module mints only the three machine-owned inputs consumed by the existing
verify, stage, and activate commands.  It never reads a registry credential or
accepts rollback private-key material.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
from typing import Any, Callable

from sandbox.hosting.images.activation.models import activation_digest
from sandbox.hosting.images.activation.policy import SshRollbackGrantVerifier
from sandbox.hosting.images.activation.v2_models import (
    InitExecutionContractV2, PrivateComposeInputSnapshotV2, RollbackCompatibilityGrantV2,
)
from sandbox.hosting.images.models import TargetScope, canonical_digest
from sandbox.hosting.images.plan_set import (
    decode_hosted_image_receipt, MachineImagePlanSetPolicy, SIGNATURE_MODE,
    VerifiedImagePlanSet, WorkflowIdentityV2, _load_json_bytes,
)
from sandbox.hosting.images.staging_models import HelperIdentity, StagingTarget
from sandbox.hosting.images.staging_v2 import StagedImageProofSet, StagingPolicySet


MAX_PROVISIONING_DOCUMENT_BYTES = 1024 * 1024


class ProvisioningError(RuntimeError):
    """Stable, value-free provisioning refusal."""

    CODES = frozenset({
        "authority_missing", "artifact_invalid", "conflict", "generation_mismatch",
        "ledger_mismatch", "path_unsafe", "signature_invalid", "target_mismatch",
        "preparation_expired",
    })

    def __init__(self, code: str) -> None:
        self.code = code if code in self.CODES else "artifact_invalid"
        super().__init__(self.code)


def target_policy_selector(remote: str, project: str, environment: str) -> str:
    values = (remote, project, environment)
    if any(type(value) is not str or not value or "\0" in value for value in values):
        raise ProvisioningError("target_mismatch")
    return hashlib.sha256("\0".join(values).encode()).hexdigest()


def read_installed_authority(path: Path) -> dict[str, Any]:
    """Read only the target-selected authority through its owning service."""
    from sandbox.hosting.images.plan_set import read_stable_file
    try:
        raw = _load_json_bytes(read_stable_file(path, MAX_PROVISIONING_DOCUMENT_BYTES,
                                               owner_only=True))
    except FileNotFoundError:
        raise ProvisioningError("authority_missing") from None
    fields = {"schema_version", "rollback_authority_id", "rollback_authority_revision",
              "rollback_public_key_path", "rollback_public_key", "compose_provider_revision"}
    if (type(raw) is not dict or set(raw) != fields or type(raw["schema_version"]) is not int
            or raw["schema_version"] != 2
            or any(type(raw[key]) is not str or not raw[key] for key in fields - {"schema_version"})):
        raise ProvisioningError("artifact_invalid")
    # Validate the selected public key without invoking a signer or exposing its path.
    SshRollbackGrantVerifier(raw["rollback_public_key"], raw["rollback_authority_id"])
    return raw


def public_authority_projection(authority: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 2, "ok": True, "code": "configured",
            "authority_id": authority["rollback_authority_id"],
            "authority_revision": authority["rollback_authority_revision"],
            "compose_provider_revision": authority["compose_provider_revision"],
            "public_key_digest": "sha256:" + hashlib.sha256(
                authority["rollback_public_key"].encode()).hexdigest()}


def _owned_directory(path: Path, *, create: bool) -> None:
    """Walk/create an owner-only directory without following symlinks."""
    path = path.expanduser()
    if not path.is_absolute():
        raise ProvisioningError("path_unsafe")
    descriptor = os.open("/", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        components = path.parts[1:]
        for index, component in enumerate(components):
            flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                     | getattr(os, "O_NOFOLLOW", 0))
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise ProvisioningError("path_unsafe") from None
                os.mkdir(component, 0o700, dir_fd=descriptor)
                os.fsync(descriptor)
                child = os.open(component, flags, dir_fd=descriptor)
            info = os.fstat(child)
            if (not stat.S_ISDIR(info.st_mode)
                    or (index == len(components) - 1
                        and (info.st_uid != os.geteuid()
                             or stat.S_IMODE(info.st_mode) & 0o077))):
                os.close(child)
                raise ProvisioningError("path_unsafe")
            os.close(descriptor)
            descriptor = child
    except OSError:
        raise ProvisioningError("path_unsafe") from None
    finally:
        os.close(descriptor)


def install_owner_only_json(path: Path, value: dict[str, Any]) -> str:
    """Atomically install one canonical owner-only file; exact replay is inert."""
    if not isinstance(path, Path) or type(value) is not dict:
        raise ProvisioningError("artifact_invalid")
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    if len(data) > MAX_PROVISIONING_DOCUMENT_BYTES:
        raise ProvisioningError("artifact_invalid")
    _owned_directory(path.parent, create=True)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        descriptor = None
    except OSError:
        raise ProvisioningError("path_unsafe") from None
    if descriptor is not None:
        try:
            info = os.fstat(descriptor)
            existing = os.read(descriptor, MAX_PROVISIONING_DOCUMENT_BYTES + 1)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise ProvisioningError("path_unsafe")
        finally:
            os.close(descriptor)
        if existing == data:
            return "replayed"
        raise ProvisioningError("conflict")
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".provision-", dir=path.parent)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        # Refuse a race instead of replacing an authority installed by another actor.
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            return install_owner_only_json(path, value)
        os.unlink(temporary); temporary = None
        parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try: os.fsync(parent)
        finally: os.close(parent)
        return "installed"
    except ProvisioningError:
        raise
    except OSError:
        raise ProvisioningError("path_unsafe") from None
    finally:
        if temporary is not None:
            try: os.unlink(temporary)
            except OSError: pass


def _validate_activation_bundle(raw):
    if (type(raw) is not dict or set(raw) != {"schema_version", "compose_snapshot",
            "rollback_grant", "rollback_grant_public_key", "stage_ledger"}
            or type(raw["schema_version"]) is not int or raw["schema_version"] != 2):
        raise ProvisioningError("conflict")
    snapshot = PrivateComposeInputSnapshotV2.from_mapping(raw["compose_snapshot"])
    grant = RollbackCompatibilityGrantV2.from_mapping(raw["rollback_grant"])
    ledger = raw["stage_ledger"]
    if (type(ledger) is not dict or set(ledger) != {"authority", "revision"}
            or ledger["authority"] != "feature-050-stage-ledger-v2"
            or type(ledger["revision"]) is not int or ledger["revision"] < 1
            or type(raw["rollback_grant_public_key"]) is not str
            or snapshot.target != grant.target
            or snapshot.plan_set_digest != grant.candidate_plan_set_digest
            or (snapshot.input_contract is not None
                and grant.compose_snapshot_digest != snapshot.snapshot_digest)
            or (grant.compose_snapshot_digest is not None
                and grant.compose_snapshot_digest != snapshot.snapshot_digest)
            or not SshRollbackGrantVerifier(raw["rollback_grant_public_key"],
                grant.authority_id).verify(grant)):
        raise ProvisioningError("conflict")
    return snapshot, grant


def reuse_activation_bundle(path: Path, *, plan: VerifiedImagePlanSet, proof: StagedImageProofSet,
        current_generation: int, current_generation_digest: str, stage_ledger_revision: int,
        snapshot_id: str, provider_revision: str, authority_id: str,
        authority_revision: str, public_key: str, now: int | None = None,
        input_contract: str = "candidate-v1") -> dict | None:
    """Return exact retained admission authority without signing or secret reads.

    Expired authority requires a distinct, explicitly admitted preparation. A
    retry must not silently rotate the inputs behind an existing snapshot ID.
    """
    from sandbox.hosting.images.plan_set import read_stable_file
    try:
        if input_contract not in {"candidate-v1", "candidate-v2"}:
            raise ProvisioningError("conflict")
        try:
            path.lstat()
        except FileNotFoundError:
            return None
        raw = _load_json_bytes(read_stable_file(path, MAX_PROVISIONING_DOCUMENT_BYTES, owner_only=True))
        snapshot, grant = _validate_activation_bundle(raw)
        instant = int(time.time()) if now is None else now
        prior = activation_digest("sandbox.hosting.images.activation-genesis.v2",
            {"target": proof.target.as_mapping(), "generation": 0}) if current_generation == 0 else current_generation_digest
        if (input_contract == "candidate-v2" and snapshot.input_contract == "candidate-v1"
                and snapshot.snapshot_id != snapshot_id
                and snapshot.expires_at <= instant and grant.expires_at <= instant
                and snapshot.target == proof.target.as_mapping()
                and grant.authority_id == authority_id
                and grant.authority_revision == authority_revision
                and grant.expected_generation <= current_generation
                and (grant.expected_generation != current_generation or grant.prior_generation_digest == prior)
                and raw["rollback_grant_public_key"] == public_key):
            # Explicit one-way contract migration creates a different candidate.
            # install_activation_bundle retains the old expired authority and
            # rechecks target/generation before atomic replacement. The caller
            # still must prove there is no active activation owner.
            return None
        if (snapshot.input_contract != input_contract or snapshot.snapshot_id != snapshot_id
                or snapshot.provider_revision != provider_revision
                or snapshot.plan_set_digest != plan.plan_set_digest
                or snapshot.selected_services != plan.policy.persistent_services
                or snapshot.target != proof.target.as_mapping()
                or grant.expected_generation != current_generation
                or grant.prior_generation_digest != prior
                or grant.candidate_proof_set_digest != proof.proof_digest
                or grant.policy_digest != plan.policy.policy_digest
                or grant.authority_id != authority_id or grant.authority_revision != authority_revision
                or raw["rollback_grant_public_key"] != public_key
                or raw["stage_ledger"] != {"authority": "feature-050-stage-ledger-v2", "revision": stage_ledger_revision}):
            raise ProvisioningError("conflict")
        if grant.issued_at > instant or min(snapshot.expires_at, grant.expires_at) <= instant:
            raise ProvisioningError("preparation_expired")
        return raw
    except ProvisioningError:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        raise ProvisioningError("artifact_invalid") from None


def install_activation_bundle(path: Path, value: dict[str, Any], *, now: int | None = None) -> str:
    """Install/renew v2 authority while the caller holds the target mutation lock.

    Renewal requires both old leases to have expired. Keep the old document as
    owner-only evidence; publish the replacement with one atomic rename.
    """
    from sandbox.hosting.images.plan_set import read_stable_file

    def identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                info.st_ctime_ns, info.st_uid, info.st_mode, info.st_nlink)


    try:
        fresh_snapshot, fresh_grant = _validate_activation_bundle(value)
        try:
            return install_owner_only_json(path, value)
        except ProvisioningError as exc:
            if exc.code != "conflict":
                raise
        before = path.lstat()
        retained = read_stable_file(path, MAX_PROVISIONING_DOCUMENT_BYTES, owner_only=True)
        old = _load_json_bytes(retained)
        snapshot, grant = _validate_activation_bundle(old)
        instant = int(time.time()) if now is None else now
        if (snapshot.expires_at > instant or grant.expires_at > instant
                or fresh_snapshot.expires_at <= instant or fresh_grant.expires_at <= instant
                or fresh_grant.issued_at > instant
                or snapshot.target != fresh_snapshot.target
                or grant.authority_id != fresh_grant.authority_id
                or grant.authority_revision != fresh_grant.authority_revision
                or old["rollback_grant_public_key"] != value["rollback_grant_public_key"]
                or grant.expected_generation > fresh_grant.expected_generation
                or (grant.expected_generation == fresh_grant.expected_generation
                    and grant.prior_generation_digest != fresh_grant.prior_generation_digest)):
            raise ProvisioningError("conflict")
        archive = path.with_name(path.name + ".expired-" + hashlib.sha256(retained).hexdigest())
        install_owner_only_json(archive, old)
        descriptor, temporary = tempfile.mkstemp(prefix=".provision-", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n")
                handle.flush(); os.fsync(handle.fileno())
            # Cooperating writers hold the same target lock. Also detect a
            # replaced/edited pathname before publishing against retained data.
            if (identity(path.lstat()) != identity(before) or read_stable_file(path,
                    MAX_PROVISIONING_DOCUMENT_BYTES, owner_only=True) != retained):
                raise ProvisioningError("conflict")
            os.replace(temporary, path)
            parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try: os.fsync(parent)
            finally: os.close(parent)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
        return "installed"
    except ProvisioningError:
        raise
    except (OSError, TypeError, ValueError):
        raise ProvisioningError("conflict") from None


def replace_expired_stage_bundle(path: Path, value: dict[str, Any]) -> str:
    """Rotate one expired stage binding without replacing live authority.

    Stage bundles are keyed by the immutable image plan, so a retained bundle
    can outlive its short credential lease.  The normal owner-only installer
    must keep refusing overwrites; this narrow helper permits replacement only
    when the existing document is a valid, ready binding whose expiry has
    passed.  Revoked, malformed, live, or otherwise conflicting authority is
    never replaced.
    """
    if not isinstance(path, Path) or type(value) is not dict:
        raise ProvisioningError("artifact_invalid")
    from sandbox.isolation.credential_binding import CredentialBinding

    existing = _read_owner_only_json(path)
    if existing is None:
        return install_owner_only_json(path, value)
    try:
        binding_value = existing.get("binding")
        binding = CredentialBinding.from_dict(binding_value)
    except (AttributeError, TypeError, ValueError):
        raise ProvisioningError("conflict") from None
    if binding.state != "ready" or not binding.is_expired():
        raise ProvisioningError("conflict")
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    if len(data) > MAX_PROVISIONING_DOCUMENT_BYTES:
        raise ProvisioningError("artifact_invalid")
    _owned_directory(path.parent, create=False)
    temporary = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(descriptor)
            current = b""
            while True:
                chunk = os.read(descriptor, min(65536, MAX_PROVISIONING_DOCUMENT_BYTES + 1 - len(current)))
                if not chunk:
                    break
                current += chunk
                if len(current) > MAX_PROVISIONING_DOCUMENT_BYTES:
                    raise ProvisioningError("artifact_invalid")
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise ProvisioningError("path_unsafe")
        finally:
            os.close(descriptor)
        if current != json.dumps(existing, sort_keys=True, separators=(",", ":")).encode() + b"\n":
            raise ProvisioningError("conflict")
        descriptor, temporary = tempfile.mkstemp(prefix=".provision-", dir=path.parent)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try: os.fsync(parent)
        finally: os.close(parent)
        return "rotated"
    except ProvisioningError:
        raise
    except OSError:
        raise ProvisioningError("path_unsafe") from None
    finally:
        if temporary is not None:
            try: os.unlink(temporary)
            except OSError: pass


def install_owner_only_json_pair(entries: tuple[tuple[Path, dict[str, Any]], ...]) -> tuple[str, ...]:
    """Preflight a small authority set so known conflicts publish nothing."""
    if not entries or len(entries) > 4:
        raise ProvisioningError("artifact_invalid")
    for path, value in entries:
        data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        if path.parent.exists():
            _owned_directory(path.parent, create=False)
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            continue
        except OSError:
            raise ProvisioningError("path_unsafe") from None
        try:
            info = os.fstat(descriptor); existing = os.read(
                descriptor, MAX_PROVISIONING_DOCUMENT_BYTES + 1)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise ProvisioningError("path_unsafe")
        finally: os.close(descriptor)
        if existing != data:
            raise ProvisioningError("conflict")
    return tuple(install_owner_only_json(path, value) for path, value in entries)


def _read_owner_only_json(path: Path) -> dict[str, Any] | None:
    """Read one bounded owner-only document without following a link."""
    if not isinstance(path, Path):
        raise ProvisioningError("artifact_invalid")
    try:
        _owned_directory(path.parent, create=False)
    except ProvisioningError:
        if not path.parent.exists():
            return None
        raise
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError:
        raise ProvisioningError("path_unsafe") from None
    try:
        info = os.fstat(descriptor)
        data = b""
        while True:
            chunk = os.read(
                descriptor, min(65536, MAX_PROVISIONING_DOCUMENT_BYTES + 1 - len(data)))
            if not chunk:
                break
            data += chunk
            if len(data) > MAX_PROVISIONING_DOCUMENT_BYTES:
                raise ProvisioningError("artifact_invalid")
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            raise ProvisioningError("path_unsafe")
        value = json.loads(data)
    except ProvisioningError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ProvisioningError("artifact_invalid") from None
    finally:
        os.close(descriptor)
    if type(value) is not dict:
        raise ProvisioningError("artifact_invalid")
    return value


def prepare_machine_policy(*, receipt_bytes: bytes, authority_id: str,
        policy_revision: int, target_scope: dict[str, str],
        persistent_services: tuple[str, ...], one_shot_services: tuple[str, ...],
        service_image_bindings: dict[str, str],
        activation_environment_bindings: dict[str, str]) -> MachineImagePlanSetPolicy:
    """Mint the exact policy from a closed receipt plus explicit machine authority."""
    try:
        receipt = decode_hosted_image_receipt(_load_json_bytes(receipt_bytes))
        scope = TargetScope.from_mapping(target_scope)
        if receipt.schema_version == 2 and receipt.target != scope.environment:
            raise ProvisioningError("artifact_invalid")
        body = {
            **({"receipt_schema_version": 2} if receipt.schema_version == 2 else {}),
            "schema_version": 2, "authority_id": authority_id,
            "policy_revision": policy_revision, "target_scope": scope.as_mapping(),
            "approved_receipt_digest": "sha256:" + hashlib.sha256(receipt_bytes).hexdigest(),
            "source_repository": receipt.workflow.repository,
            "source_ref": receipt.source_ref, "source_revision": receipt.source_sha,
            "platform": receipt.platform, "workflow": receipt.workflow.as_mapping(),
            "persistent_services": sorted(persistent_services),
            "one_shot_services": sorted(one_shot_services),
            "service_image_bindings": [{"service": service, "image": image}
                for service, image in sorted(service_image_bindings.items())],
            "activation_environment_bindings": [
                {"image": image, "environment_variable": variable}
                for image, variable in sorted(activation_environment_bindings.items())],
            "signature_mode": SIGNATURE_MODE,
        }
        return MachineImagePlanSetPolicy.from_mapping({**body, "policy_digest": canonical_digest(
            "sandbox.hosting.images.machine-plan-set-policy.v2", body)})
    except (TypeError, ValueError):
        raise ProvisioningError("artifact_invalid") from None


def prepare_stage_binding(*, plan: VerifiedImagePlanSet, target: StagingTarget,
        machine_identity: str, source_reference: str, expires_at: str,
        owner: str):
    """Mint the deterministic metadata-only credential binding for one plan."""
    from sandbox.isolation.credential_binding import CredentialBinding, canonical_timestamp
    try:
        if type(plan) is not VerifiedImagePlanSet or type(target) is not StagingTarget \
                or target.machine_identity != machine_identity:
            raise ValueError
        canonical_expires_at = canonical_timestamp(expires_at)
        seed = json.dumps({"target": target.as_mapping(),
            "plan_set_digest": plan.plan_set_digest,
            "source_reference": source_reference, "expires_at": canonical_expires_at},
            sort_keys=True, separators=(",", ":")).encode()
        binding_hex = hashlib.sha256(
            b"sandbox-hosting-stage-binding-v2\0" + seed).hexdigest()
        return CredentialBinding(
            binding_id="image-stage-" + binding_hex[:32],
            instance_id="host-" + hashlib.sha256(machine_identity.encode()).hexdigest()[:32],
            source_reference=source_reference,
            policy_digest=hashlib.sha256(b"policy\0" + seed).hexdigest(),
            egress_digest=hashlib.sha256(b"egress\0" + seed).hexdigest(),
            broker_digest=hashlib.sha256(b"broker\0" + seed).hexdigest(),
            scheme="https", host="ghcr.io", port=443, method="GET", path="/token",
            auth_form="authorization_bearer", expires_at=canonical_expires_at, owner=owner,
            version=1, state="ready")
    except (TypeError, ValueError):
        raise ProvisioningError("artifact_invalid") from None


def reuse_owner_only_stage_bundle(path: Path, *, plan: VerifiedImagePlanSet,
        target: StagingTarget, helper: HelperIdentity, machine_identity: str,
        source_reference: str, owner: str, credential_reference_revision: str,
        secret_sources: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Return one exact, live stage policy or refuse retained ambiguity."""
    from sandbox.hosting.images.staging_v2 import StagingPolicySet
    from sandbox.isolation.credential_binding import CredentialBinding
    value = _read_owner_only_json(path)
    if value is None:
        return None
    try:
        if set(value) != {"policy", "binding", "secret_sources"}:
            raise ProvisioningError("artifact_invalid")
        policy = StagingPolicySet.from_mapping(value["policy"])
        binding = CredentialBinding.from_dict(value["binding"])
    except ProvisioningError:
        raise
    except (TypeError, ValueError):
        raise ProvisioningError("artifact_invalid") from None
    # An expired ready lease is safe to rotate for this same immutable plan.
    # Other non-ready or mismatched authority remains a hard conflict.
    if not binding.admits_use():
        if binding.state == "ready" and binding.is_expired():
            return None
        raise ProvisioningError("conflict")
    expected_binding = prepare_stage_binding(
        plan=plan, target=target, machine_identity=machine_identity,
        source_reference=source_reference, expires_at=binding.expires_at, owner=owner)
    if binding.to_dict() != expected_binding.to_dict():
        raise ProvisioningError("conflict")
    expected = prepare_stage_bundle(
        plan=plan, target=target, helper=helper, binding=expected_binding,
        credential_reference_revision=credential_reference_revision,
        secret_sources=secret_sources)
    # Older bundles may retain the pre-canonical serialized spelling (for
    # example ``.466000Z``) even though the binding model normalizes it.  The
    # binding identity and all authority fields above are still exact; compare
    # a normalized view so equivalent timestamp spellings replay safely while
    # returning the original owner-only document unchanged.
    normalized_value = {**value, "binding": binding.to_dict()}
    if normalized_value != expected or policy.policy_digest != expected["policy"]["policy_digest"]:
        raise ProvisioningError("conflict")
    return value


def prepare_stage_bundle(*, plan: VerifiedImagePlanSet, target: StagingTarget,
        helper: HelperIdentity, binding: object, credential_reference_revision: str,
        secret_sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build the exact stage consumer bundle without resolving its credential."""
    from sandbox.isolation.credential_binding import CredentialBinding
    try:
        if type(plan) is not VerifiedImagePlanSet or type(binding) is not CredentialBinding:
            raise ValueError
        scope = plan.policy.target_scope
        if target.target_identity != f"{scope.remote}/{scope.project}/{scope.environment}" \
                or not binding.admits_use() or binding.host != "ghcr.io":
            raise ProvisioningError("target_mismatch")
        alias = binding.source_reference.split("/", 1)[0]
        if alias != "personal" and alias not in secret_sources:
            raise ProvisioningError("authority_missing")
        body = {"schema_version": 2, "plan_set_digest": plan.plan_set_digest,
            "target": target.as_mapping(), "helper": helper.as_mapping(),
            "broker_recipient": f"ghcr-plan-set-read:{plan.plan_set_digest}",
            "broker_binding_id": binding.binding_id,
            "broker_binding_version": binding.version,
            "credential_reference_revision": credential_reference_revision,
            "operation": "ghcr.plan-set.read",
            "capability_revision": "systemd-cgroup-v2-batch-stage-v2"}
        policy = StagingPolicySet.from_mapping({**body, "policy_digest": canonical_digest(
            "sandbox.hosting.images.staging-policy-set.v2", body)})
        return {"policy": policy.as_mapping(), "binding": binding.to_dict(),
                "secret_sources": dict(secret_sources)}
    except ProvisioningError:
        raise
    except (TypeError, ValueError):
        raise ProvisioningError("artifact_invalid") from None


class SshAgentRollbackSigner:
    """Sign through ssh-agent using an owner-only public-key identity file."""

    def __init__(self, public_key_path: Path, authority_id: str,
                 runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        self.path = public_key_path; self.authority_id = authority_id; self.runner = runner
        _owned_directory(public_key_path.parent, create=False)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try: descriptor = os.open(public_key_path, flags)
        except OSError: raise ProvisioningError("authority_missing") from None
        try:
            info = os.fstat(descriptor); raw = os.read(descriptor, 8193)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077
                    or len(raw) > 8192):
                raise ProvisioningError("path_unsafe")
        finally: os.close(descriptor)
        try: self.public_key = " ".join(raw.decode().strip().split()[:2])
        except UnicodeDecodeError: raise ProvisioningError("authority_missing") from None
        SshRollbackGrantVerifier(self.public_key, authority_id)

    def sign(self, unsigned: dict[str, Any]) -> str:
        message = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        try:
            with tempfile.TemporaryDirectory(prefix="sandbox-rollback-sign-") as directory:
                path = Path(directory) / "grant.json"; path.write_bytes(message)
                result = self.runner(("/usr/bin/ssh-keygen", "-Y", "sign", "-f",
                    str(self.path), "-n", "sandbox-feature-051-rollback", str(path)),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=10, check=False,
                    env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
                if result.returncode != 0:
                    raise ProvisioningError("signature_invalid")
                signature = (path.with_name(path.name + ".sig")).read_bytes()
        except ProvisioningError:
            raise
        except (OSError, subprocess.SubprocessError):
            raise ProvisioningError("signature_invalid") from None
        if not signature or len(signature) > 4096:
            raise ProvisioningError("signature_invalid")
        return base64.b64encode(signature).decode()


def prepare_activation_bundle(*, plan: VerifiedImagePlanSet,
        proof: StagedImageProofSet, stage_record: dict[str, Any],
        stage_ledger_revision: int, current_generation: int,
        current_generation_digest: str, target: dict[str, str],
        configuration_digest: str, snapshot_id: str, provider_revision: str,
        snapshot_expires_at: int, authority_revision: str,
        signer: SshAgentRollbackSigner, grant_ttl_seconds: int = 900,
        now: int | None = None, input_contract: str | None = None,
        init_contract: InitExecutionContractV2 | None = None) -> dict[str, Any]:
    """Mint the post-stage activation bundle from retained repository evidence."""
    try:
        if (type(plan) is not VerifiedImagePlanSet or type(proof) is not StagedImageProofSet
                or proof.plan_set_digest != plan.plan_set_digest
                or proof.verified_plan_set != plan.as_mapping()
                or proof.target.as_mapping() != target
                or type(stage_record) is not dict
                or stage_record.get("phase") != "succeeded"
                or stage_record.get("request_id") != proof.request_id
                or stage_record.get("request_digest") != proof.request_digest
                or stage_record.get("generation") != proof.staging_generation
                or stage_record.get("ledger_revision") != stage_ledger_revision):
            raise ProvisioningError("ledger_mismatch")
        if type(current_generation) is not int or current_generation < 0:
            raise ProvisioningError("generation_mismatch")
        expected_prior = activation_digest("sandbox.hosting.images.activation-genesis.v2",
            {"target": target, "generation": 0}) if current_generation == 0 \
            else current_generation_digest
        issued = int(time.time()) if now is None else now
        if (type(snapshot_expires_at) is not int
                or not 60 <= snapshot_expires_at - issued <= 3600):
            raise ProvisioningError("artifact_invalid")
        snapshot = PrivateComposeInputSnapshotV2.create(
            snapshot_id=snapshot_id, provider_revision=provider_revision, target=target,
            plan_set_digest=plan.plan_set_digest,
            selected_services=plan.policy.persistent_services,
            configuration_digest=configuration_digest, expires_at=snapshot_expires_at,
            input_contract=input_contract, init_contract=init_contract)
        if type(grant_ttl_seconds) is not int or not 60 <= grant_ttl_seconds <= 3600:
            raise ProvisioningError("artifact_invalid")
        unsigned = {"schema_version": 2, "authority_id": signer.authority_id,
            "authority_revision": authority_revision, "target": target,
            "expected_generation": current_generation,
            "prior_generation_digest": expected_prior,
            "candidate_plan_set_digest": plan.plan_set_digest,
            "candidate_proof_set_digest": proof.proof_digest,
            "policy_digest": plan.policy.policy_digest, "issued_at": issued,
            "expires_at": issued + grant_ttl_seconds}
        if snapshot.input_contract is not None:
            unsigned["compose_snapshot_digest"] = snapshot.snapshot_digest
        signature = signer.sign(unsigned)
        body = {**unsigned, "authority_proof": signature}
        grant = RollbackCompatibilityGrantV2(**body, grant_digest=activation_digest(
            "sandbox.hosting.images.rollback-grant.v2", body))
        if not SshRollbackGrantVerifier(signer.public_key, signer.authority_id).verify(grant):
            raise ProvisioningError("signature_invalid")
        return {"schema_version": 2, "compose_snapshot": snapshot.as_mapping(),
                "rollback_grant": grant.as_mapping(),
                "rollback_grant_public_key": signer.public_key,
                "stage_ledger": {"authority": "feature-050-stage-ledger-v2",
                                 "revision": stage_ledger_revision}}
    except ProvisioningError:
        raise
    except (TypeError, ValueError):
        raise ProvisioningError("artifact_invalid") from None


__all__ = (
    "ProvisioningError", "SshAgentRollbackSigner", "install_owner_only_json",
    "install_owner_only_json_pair",
    "prepare_activation_bundle", "prepare_machine_policy", "prepare_stage_bundle",
    "target_policy_selector",
)
