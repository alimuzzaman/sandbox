"""CAS persistence for managed Credential Vault binding metadata.

Only :class:`~sandbox.isolation.credential_binding.CredentialBinding` records
are stored.  The wrapper delegates locking, atomic replacement, and state-file
ownership to :class:`NativeRepository`; it never accepts or serializes resolved
credential material.
"""

from __future__ import annotations

from typing import Any

from sandbox.isolation.credential_binding import (
    CredentialBinding,
    CredentialBindingVersionConflict,
)


SECTION = "credential_bindings"


class CredentialRepositoryError(ValueError):
    """Stable repository refusal without echoing stored record contents."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CredentialRepositoryConflict(CredentialRepositoryError):
    def __init__(self, message: str = "credential binding version does not match") -> None:
        super().__init__("binding_version_conflict", message)


class CredentialRepositoryOwnershipError(CredentialRepositoryError):
    def __init__(self) -> None:
        super().__init__("binding_owner_denied", "credential binding is owned by another instance")


def _safe_identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 \
            or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise CredentialRepositoryError("binding_invalid", f"credential {label} is invalid")
    return value


class CredentialRepository:
    """Persist and retrieve binding metadata through the native lock authority."""

    def __init__(self, repository) -> None:
        if repository is None or not callable(getattr(repository, "snapshot", None)) \
                or not callable(getattr(repository, "transaction", None)):
            raise ValueError("credential repository requires a native repository")
        self.repository = repository

    @staticmethod
    def _check_binding(binding: CredentialBinding) -> CredentialBinding:
        if not isinstance(binding, CredentialBinding):
            raise CredentialRepositoryError("binding_invalid", "credential binding is invalid")
        # Round-trip before persistence.  Besides catching a future mutable
        # implementation, this is a secret-free schema check that rejects
        # unknown fields and duplicate security fields.
        try:
            return CredentialBinding.from_dict(binding.to_dict())
        except Exception:
            raise CredentialRepositoryError("binding_invalid", "credential binding is invalid") from None

    @staticmethod
    def _decode(raw: Any) -> CredentialBinding:
        try:
            return CredentialBinding.from_dict(raw)
        except Exception:
            # A corrupt state file is not permission to forget or overwrite an
            # existing binding.  Keep the public failure bounded and value-free.
            raise CredentialRepositoryError("binding_corrupt", "credential binding state is invalid") from None

    def _records(self, state: dict[str, Any]) -> dict[str, Any]:
        records = state.get(SECTION)
        if not isinstance(records, dict):
            raise CredentialRepositoryError("binding_corrupt", "credential binding state is invalid")
        return records

    def _snapshot(self) -> dict[str, Any]:
        # Status/report callers must not create or migrate a state file.  The
        # native repository keeps the read-only path behind its own lock and
        # parsing authority; older repository doubles continue to use snapshot.
        readonly = getattr(self.repository, "readonly_snapshot", None)
        return readonly() if callable(readonly) else self.repository.snapshot()

    def get(self, binding_id: str, *, owner: str | None = None) -> CredentialBinding | None:
        binding_id = _safe_identity(binding_id, "binding id")
        raw = self._records(self._snapshot()).get(binding_id)
        if raw is None:
            return None
        binding = self._decode(raw)
        if owner is not None and binding.owner != owner:
            raise CredentialRepositoryOwnershipError()
        return binding

    def list(
        self,
        *,
        instance_id: str | None = None,
        owner: str | None = None,
    ) -> tuple[CredentialBinding, ...]:
        if instance_id is not None:
            instance_id = _safe_identity(instance_id, "instance id")
        if owner is not None:
            _safe_identity(owner, "owner")
        records = self._records(self._snapshot())
        result: list[CredentialBinding] = []
        for raw in records.values():
            binding = self._decode(raw)
            if instance_id is not None and binding.instance_id != instance_id:
                continue
            if owner is not None and binding.owner != owner:
                continue
            result.append(binding)
        return tuple(sorted(result, key=lambda item: item.binding_id))

    def create(self, binding: CredentialBinding) -> CredentialBinding:
        binding = self._check_binding(binding)
        if binding.version != 1 or binding.state != "credential_pending":
            raise CredentialRepositoryError(
                "binding_initial_state", "new credential bindings must start pending at version one",
            )
        if binding.is_expired():
            raise CredentialRepositoryError(
                "binding_expired", "new credential bindings must have a future expiry",
            )
        with self.repository.transaction() as state:
            records = self._records(state)
            if binding.binding_id in records:
                raise CredentialRepositoryConflict("credential binding already exists")
            records[binding.binding_id] = binding.to_dict()
        return binding

    def put(
        self,
        binding: CredentialBinding,
        *,
        expected_version: int | None = None,
        owner: str | None = None,
    ) -> CredentialBinding:
        """Create or CAS-update one binding through the same safe paths."""

        if expected_version is None:
            if owner is not None and binding.owner != owner:
                raise CredentialRepositoryOwnershipError()
            return self.create(binding)
        return self.update(binding, expected_version=expected_version, owner=owner)

    def update(
        self,
        binding: CredentialBinding,
        *,
        expected_version: int,
        owner: str | None = None,
    ) -> CredentialBinding:
        binding = self._check_binding(binding)
        if isinstance(expected_version, bool) or not isinstance(expected_version, int):
            raise CredentialRepositoryConflict()
        if owner is not None and binding.owner != owner:
            raise CredentialRepositoryOwnershipError()
        with self.repository.transaction() as state:
            records = self._records(state)
            raw = records.get(binding.binding_id)
            if raw is None:
                raise CredentialRepositoryConflict("credential binding does not exist")
            current = self._decode(raw)
            if owner is not None and current.owner != owner:
                raise CredentialRepositoryOwnershipError()
            if current.version != expected_version or binding.version != expected_version + 1:
                raise CredentialRepositoryConflict()
            if binding.instance_id != current.instance_id or binding.owner != current.owner:
                raise CredentialRepositoryError(
                    "binding_identity_changed", "credential binding ownership identity cannot change",
                )
            if binding.state != "credential_pending":
                raise CredentialRepositoryError(
                    "binding_update_state", "credential binding updates must return to pending",
                )
            records[binding.binding_id] = binding.to_dict()
        return binding

    def cas_update(
        self,
        binding_id: str,
        *,
        expected_version: int,
        owner: str | None = None,
        **changes: Any,
    ) -> CredentialBinding:
        current = self.get(binding_id, owner=owner)
        if current is None:
            raise CredentialRepositoryConflict("credential binding does not exist")
        try:
            updated = current.cas_update(expected_version, **changes)
        except CredentialBindingVersionConflict:
            raise CredentialRepositoryConflict() from None
        return self.update(updated, expected_version=expected_version, owner=owner)

    def transition(
        self,
        binding_id: str,
        state: str,
        *,
        expected_version: int,
        owner: str | None = None,
    ) -> CredentialBinding:
        current = self.get(binding_id, owner=owner)
        if current is None:
            raise CredentialRepositoryConflict("credential binding does not exist")
        try:
            updated = current.transition(state)
        except ValueError as exc:
            raise CredentialRepositoryError(
                "binding_transition_denied", "credential binding transition is not allowed",
            ) from None
        # ``update`` intentionally only accepts pending desired-state changes;
        # lifecycle transitions use a single repository transaction below.
        if updated.version != expected_version + 1 or current.version != expected_version:
            raise CredentialRepositoryConflict()
        with self.repository.transaction() as state_doc:
            records = self._records(state_doc)
            raw = records.get(binding_id)
            if raw is None:
                raise CredentialRepositoryConflict("credential binding does not exist")
            latest = self._decode(raw)
            if latest.version != expected_version:
                raise CredentialRepositoryConflict()
            if owner is not None and latest.owner != owner:
                raise CredentialRepositoryOwnershipError()
            records[binding_id] = updated.to_dict()
        return updated

    def revoke(
        self,
        binding_id: str,
        *,
        expected_version: int,
        owner: str | None = None,
    ) -> CredentialBinding:
        current = self.get(binding_id, owner=owner)
        if current is None:
            raise CredentialRepositoryConflict("credential binding does not exist")
        if current.version != expected_version:
            raise CredentialRepositoryConflict()
        if current.state == "revoked":
            return current
        # Persist the admission-closing state first.  A ready binding takes the
        # explicit revoking path; a pending/blocked binding has no active
        # sessions and can close directly.
        target = "revoking" if current.state == "ready" else "revoked"
        return self.transition(binding_id, target, expected_version=expected_version, owner=owner)

    def complete_revoke(
        self,
        binding_id: str,
        *,
        expected_version: int,
        owner: str | None = None,
    ) -> CredentialBinding:
        current = self.get(binding_id, owner=owner)
        if current is None:
            raise CredentialRepositoryConflict("credential binding does not exist")
        if current.version != expected_version:
            raise CredentialRepositoryConflict()
        if current.state == "revoked":
            return current
        return self.transition(binding_id, "revoked", expected_version=expected_version, owner=owner)

    def remove(
        self,
        binding_id: str,
        *,
        expected_version: int,
        owner: str | None = None,
    ) -> bool:
        binding_id = _safe_identity(binding_id, "binding id")
        with self.repository.transaction() as state:
            records = self._records(state)
            raw = records.get(binding_id)
            if raw is None:
                return False
            binding = self._decode(raw)
            if owner is not None and binding.owner != owner:
                raise CredentialRepositoryOwnershipError()
            if binding.version != expected_version:
                raise CredentialRepositoryConflict()
            if binding.state not in {"revoked", "expired", "blocked"}:
                raise CredentialRepositoryError(
                    "binding_remove_denied", "only closed credential bindings may be removed",
                )
            del records[binding_id]
            return True


__all__ = [
    "CredentialRepository", "CredentialRepositoryConflict",
    "CredentialRepositoryError", "CredentialRepositoryOwnershipError", "SECTION",
]
