"""Closed source bindings for owner-scoped PostgreSQL recovery."""
from dataclasses import dataclass
import hashlib
import json
import re

from .errors import RecoveryError


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def _reference(value):
    from sandbox.isolation.credential_binding import canonical_registered_source_reference
    try:
        return canonical_registered_source_reference(value)
    except (TypeError, ValueError):
        raise RecoveryError("PostgreSQL source binding is invalid", "source_binding_invalid") from None


@dataclass(frozen=True)
class PostgresSource:
    schema_version: int
    profile: str
    remote: str
    compose_project: str
    container_id: str
    volume: str
    database: str
    role: str
    image_id: str
    client_image_id: str
    credential_reference: str | None
    target_password_reference: str | None

    @classmethod
    def from_mapping(cls, value):
        if type(value) is not dict or set(value) != set(cls.__dataclass_fields__):
            raise RecoveryError("PostgreSQL source binding is invalid", "source_binding_invalid")
        source = cls(**value)
        if any(type(getattr(source, field)) is not str for field in (
                'profile', 'remote', 'compose_project', 'container_id', 'volume', 'image_id')):
            raise RecoveryError("source binding is invalid", "source_binding_invalid")
        if (type(source.schema_version) is not int or source.schema_version != 1
                or source.profile not in {"lenzora-dev", "lenzora-prod", "lenzora-prod-legacy"}
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", source.remote)
                or not re.fullmatch(r"[a-f0-9]{64}", source.container_id)
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", source.image_id)
                or type(source.client_image_id) is not str
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", source.client_image_id)
                or any(type(item) is not str or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", item)
                       for item in (source.compose_project, source.volume, source.database, source.role))):
            raise RecoveryError("PostgreSQL source binding is invalid", "source_binding_invalid")
        if type(source.client_image_id) is not str:
            raise RecoveryError("source binding is invalid", "source_binding_invalid")
        if source.target_password_reference is not None:
            _reference(source.target_password_reference)
            if source.profile != "lenzora-prod-legacy":
                raise RecoveryError("target password applies only to production transfer", "source_binding_invalid")
        if source.credential_reference is not None:
            _reference(source.credential_reference)
            if source.profile != "lenzora-prod-legacy":
                raise RecoveryError("external credential requires the legacy profile", "source_binding_invalid")
        elif source.client_image_id != source.image_id:
            raise RecoveryError("PostgreSQL source binding is invalid", "source_binding_invalid")
        elif source.profile == "lenzora-prod-legacy":
            raise RecoveryError("legacy source requires an approved broker reference", "source_binding_invalid")
        return source

    def as_mapping(self):
        return dict(vars(self))

    @property
    def source_digest(self):
        return digest(self.as_mapping())


@dataclass(frozen=True)
class StorageSource:
    schema_version: int
    profile: str
    remote: str
    compose_project: str
    container_id: str
    volume: str
    image_id: str
    mount_path: str
    credential_reference: None = None

    @classmethod
    def from_mapping(cls, value):
        if type(value) is not dict or set(value) != set(cls.__dataclass_fields__):
            raise RecoveryError('storage source binding is invalid', 'source_binding_invalid')
        source = cls(**value)
        if any(type(getattr(source, field)) is not str for field in (
                'profile', 'remote', 'compose_project', 'container_id', 'volume', 'image_id')):
            raise RecoveryError("source binding is invalid", "source_binding_invalid")
        if (type(source.schema_version) is not int or source.schema_version != 1
                or source.profile != 'lenzora-prod-storage' or source.mount_path != '/app/storage'
                or source.credential_reference is not None
                or not re.fullmatch(r'[a-f0-9]{64}', source.container_id)
                or not re.fullmatch(r'sha256:[a-f0-9]{64}', source.image_id)
                or any(not isinstance(item, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', item)
                    for item in (source.remote, source.compose_project, source.volume))):
            raise RecoveryError('storage source binding is invalid', 'source_binding_invalid')
        return source

    def as_mapping(self): return dict(vars(self))

    @property
    def source_digest(self): return digest(self.as_mapping())


def recovery_source(value):
    if isinstance(value, dict) and value.get('profile') == 'lenzora-prod-storage':
        return StorageSource.from_mapping(value)
    return PostgresSource.from_mapping(value)
