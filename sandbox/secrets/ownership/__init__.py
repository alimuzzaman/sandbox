"""Secret ownership profiles: metadata-only mapping of source keys to destinations.

Import rule (frozen, spec 069): modules in this package import only the
standard library and ``sandbox.secrets.*``. They never import ``sandbox_core``,
``sandbox.registry``, ``sandbox.hermes``, the MCP ``app`` module,
``writer.opaque_revision`` or ``writer.load_revision_key``, and never read the
instance registry JSON.
"""

from .models import (
    Approval,
    BlockReason,
    Capability,
    ClassificationReport,
    CreateOutcome,
    Destination,
    Domain,
    Existence,
    Grant,
    MapEntry,
    NameState,
    Origin,
    OwnershipConfig,
    OwnershipContext,
    OwnershipProfile,
    RevocationOutcome,
    ScopedRunRequest,
    SourceContinuity,
    StorageAdmission,
)

__all__ = [
    "Approval", "BlockReason", "Capability", "ClassificationReport",
    "CreateOutcome", "Destination", "Domain", "Existence", "Grant",
    "MapEntry", "NameState", "Origin", "OwnershipConfig", "OwnershipContext",
    "OwnershipProfile", "RevocationOutcome", "ScopedRunRequest",
    "SourceContinuity", "StorageAdmission",
]

# Public implementation entry points are loaded only when requested; importing
# the model package must not import the worker or the controller CLI.
_LAZY_EXPORTS = {
    "BrokerOwnershipAdapter": ("broker", "BrokerOwnershipAdapter"),
    "OwnershipAdapter": ("manifest", "OwnershipAdapter"),
    "OwnershipSession": ("manifest", "OwnershipSession"),
    "load_ownership_adapter": ("manifest", "load_ownership_adapter"),
    "admit_evidence": ("evidence", "admit_evidence"),
    "Journal": ("journal", "Journal"),
    "ApprovalStore": ("approval", "ApprovalStore"),
    "run_migration": ("migrate", "run_migration"),
    "run_cleanup": ("cleanup", "run_cleanup"),
    "resolve_scoped_bindings": ("scoped_run", "resolve_scoped_bindings"),
}
__all__ += list(_LAZY_EXPORTS)


def __getattr__(name):
    if name not in _LAZY_EXPORTS:
        raise AttributeError(name)
    from importlib import import_module
    module, attribute = _LAZY_EXPORTS[name]
    value = getattr(import_module("." + module, __name__), attribute)
    globals()[name] = value
    return value
