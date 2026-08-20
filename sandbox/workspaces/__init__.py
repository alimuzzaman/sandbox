"""Durable workspace metadata and legacy-index migration primitives.

The workspace index is deliberately a metadata registry.  A migration never
rewrites or moves the legacy workspace directory; it only records an exact,
reviewable observation in the owner-only SQLite index.
"""

from .models import (
    JobEvidence,
    LegacyWorkspace,
    MigrationItem,
    MigrationPlan,
    ProjectEvidence,
    ResourceBinding,
    WorkspaceEvidence,
    WorkspaceRecord,
)
from .repository import (
    AliasCollisionError,
    MigrationStaleError,
    WorkspaceIndexError,
    WorkspaceNotFoundError,
    WorkspaceRepository,
    read_only_projection,
)

__all__ = [
    "AliasCollisionError",
    "JobEvidence",
    "LegacyWorkspace",
    "MigrationItem",
    "MigrationPlan",
    "MigrationStaleError",
    "ProjectEvidence",
    "ResourceBinding",
    "WorkspaceEvidence",
    "WorkspaceIndexError",
    "WorkspaceNotFoundError",
    "WorkspaceRecord",
    "WorkspaceRepository",
    "read_only_projection",
]
