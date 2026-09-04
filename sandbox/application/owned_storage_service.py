"""Application-level owned storage authority port and authorization verification."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, BinaryIO, Dict, Optional

from sandbox.owned_storage.models import (
    AdoptionBindingPhase,
    AuthorityPolicy,
    PolicyMode,
)
from sandbox.owned_storage.protocol import compute_request_digest
from sandbox.owned_storage.repository import StorageAuthorityRepository
from sandbox.owned_storage.service import (
    OwnedStorageService,
    OwnedStorageServiceError,
    utc_now_iso,
)


class OwnedStorageApplicationError(Exception):
    """Application-level owned storage port error."""

    def __init__(self, message: str, code: str = "request_invalid"):
        super().__init__(f"[{code}] {message}")
        self.code = code


class OwnedStorageApplicationService:
    def __init__(
        self,
        authority_service: OwnedStorageService,
        repository: StorageAuthorityRepository,
    ):
        self.authority_service = authority_service
        self.repository = repository

    def publish(
        self,
        *,
        remote_identity: str,
        project_identity: str,
        request_id: str,
        relationship_id: str,
        workspace_id: str,
        generation_id: str,
        manifest_digest: str,
        archive_manifest_digest: str,
        file_count: int,
        byte_count: int,
        stream: BinaryIO,
        promotion_id: Optional[str] = None,
        authority_binding_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Verify application authorization and invoke owned storage authority publication."""
        if not project_identity or not isinstance(project_identity, str):
            raise OwnedStorageApplicationError(
                "Invalid or missing project identity", code="cross_project_refused"
            )
        if not remote_identity or not isinstance(remote_identity, str):
            raise OwnedStorageApplicationError(
                "Invalid or missing remote identity", code="request_invalid"
            )

        policy = self.repository.get_policy(remote_identity, project_identity)
        if not policy or policy.mode != PolicyMode.FUTURE:
            raise OwnedStorageApplicationError(
                "Policy is not future for this project and remote", code="policy_not_future"
            )

        # Look up active binding if not explicitly passed
        binding_id = authority_binding_id
        if not binding_id and policy.admission_basis:
            binding_id = policy.admission_basis.get("binding_id")

        if binding_id:
            binding = self.repository.get_adoption_binding(binding_id)
            if not binding or binding.phase != AdoptionBindingPhase.ACTIVE:
                raise OwnedStorageApplicationError(
                    "Adoption binding is not active", code="adoption_binding_missing"
                )

        # Construct canonical authorization fields
        authorization_id = f"auth_{uuid.uuid4().hex[:12]}"
        controller_epoch = f"epoch_{uuid.uuid4().hex[:8]}"
        sequence = 1
        caller_identity_digest = f"sha256:{hashlib.sha256(project_identity.encode('utf-8')).hexdigest()}"

        req_dict = {
            "protocol": "owned-storage-authority-v1",
            "operation": "publish",
            "request_id": request_id,
            "remote_identity": remote_identity,
            "project_identity": project_identity,
            "authorization": {
                "authorization_id": authorization_id,
                "controller_epoch": controller_epoch,
                "sequence": sequence,
                "caller_identity_digest": caller_identity_digest,
                "application_policy_digest": policy.request_digest,
                "policy_generation": policy.effective_generation,
                "promotion_id": promotion_id,
                "authority_binding_id": binding_id,
                "binding_generation": 1,
                "expires_at": utc_now_iso(),
            },
            "qualification": None,
            "input": {
                "relationship_id": relationship_id,
                "workspace_id": workspace_id,
                "generation_id": generation_id,
                "manifest_digest": manifest_digest,
                "archive_manifest_digest": archive_manifest_digest,
                "file_count": file_count,
                "byte_count": byte_count,
            },
        }
        request_digest = compute_request_digest(req_dict)

        try:
            return self.authority_service.publish_generation(
                remote_identity=remote_identity,
                project_identity=project_identity,
                request_id=request_id,
                request_digest=request_digest,
                authorization_id=authorization_id,
                controller_epoch=controller_epoch,
                sequence=sequence,
                caller_identity_digest=caller_identity_digest,
                relationship_id=relationship_id,
                workspace_id=workspace_id,
                generation_id=generation_id,
                manifest_digest=manifest_digest,
                archive_manifest_digest=archive_manifest_digest,
                file_count=file_count,
                byte_count=byte_count,
                stream=stream,
                promotion_id=promotion_id,
                authority_binding_id=binding_id,
            )
        except OwnedStorageServiceError as exc:
            raise OwnedStorageApplicationError(str(exc), code=exc.code) from exc
