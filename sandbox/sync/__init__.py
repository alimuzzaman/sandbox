"""Agent-aware source synchronization foundation.

This package deliberately contains no CLI, MCP, or remote transport wiring.  It
owns the validated value objects, private local journal, and stable source
capture boundary used by those later layers.
"""

from .capture import (
    CaptureError,
    CaptureManifest,
    ManifestEntry,
    ManifestLimitExceeded,
    UnstableCapture,
    capture_manifest,
)
from .coordinator import RelationshipCoordinator
from .models import (
    DivergenceRecord,
    Participant,
    PinnedJob,
    SourceGeneration,
    SynchronizationRelationship,
    failure_envelope,
    success_envelope,
    validate_sync_envelope,
)
from .policy import CredentialDetected, SyncPolicy
from .projection import ManagedSourceProjection, SourceWriteRefused
from .repository import (
    RelationshipConflict,
    RequestDigestConflict,
    SyncJournalCorruption,
    SyncRepository,
)

__all__ = [
    "CaptureError",
    "CaptureManifest",
    "CredentialDetected",
    "DivergenceRecord",
    "ManifestEntry",
    "ManifestLimitExceeded",
    "ManagedSourceProjection",
    "Participant",
    "PinnedJob",
    "RelationshipConflict",
    "RequestDigestConflict",
    "RelationshipCoordinator",
    "SourceGeneration",
    "SourceWriteRefused",
    "SyncJournalCorruption",
    "SyncPolicy",
    "SyncRepository",
    "SynchronizationRelationship",
    "UnstableCapture",
    "capture_manifest",
    "failure_envelope",
    "success_envelope",
    "validate_sync_envelope",
]
