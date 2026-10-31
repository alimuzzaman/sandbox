# Contract: Python interfaces (integration seams for implementers A, B, C)

Rules for all modules in `sandbox/secrets/ownership/`: stdlib + `sandbox.secrets.*` only; no import of `sandbox_core`, `sandbox.registry`, `sandbox.hermes`, MCP `app`; no import of `writer.opaque_revision` / `writer.load_revision_key`; no reading `registry.json`. Errors are `sandbox.secrets.models.SecretBrokerError(code, message)` with codes from contracts/cli.md. Values are `bytearray` and wiped (`buf[:] = b"\0"*len(buf)`) after use.

## [A] `ownership/models.py` (frozen first; B and C import only these names)
```python
Domain = Literal["office", "personal"]
Origin = Literal["source", "destination"]
Capability = Literal["read", "create"]

class NameState(str, Enum):
    PENDING="pending"; ATTEMPTED="attempted"; CREATED="created"; PRE_EXISTING="pre_existing"
    CONFLICT="conflict"; RECONCILED="reconciled"; ACCEPTANCE_UNKNOWN="acceptance_unknown"
    VERIFIED="verified"; FAILED="failed"; UNRESOLVED="unresolved"

class BlockReason(str, Enum):
    UNMAPPED="unmapped"; UNDECIDED="undecided"; MULTIPLY_ASSIGNED="multiply_assigned"
    DUPLICATE_DESTINATION="duplicate_destination"; OTHER_DOMAIN="other_domain"
    UNREGISTERED_SOURCE="unregistered_source"; SOURCE_UNSUPPORTED="source_unsupported"

class Existence(str, Enum):
    ABSENT="absent"; PRESENT="present"; UNCHECKED="unchecked"; UNKNOWN="unknown"

@dataclass(frozen=True)
class Destination:
    endpoint: str; project_id: str; environment: str; path: str
    read_client_id: str | None; create_client_id: str | None; timeout_seconds: int
    gateway: Literal["none", "cloudflare-access"] = "none"

@dataclass(frozen=True)
class OwnershipProfile:
    name: str; domain: Domain; destination: Destination

@dataclass(frozen=True)
class MapEntry:
    source: str; key: str; profile: str; name: str; consumer: str; origin: Origin

@dataclass(frozen=True)
class OwnershipConfig:
    profiles: Mapping[str, OwnershipProfile]; entries: tuple[MapEntry, ...]
    map_sha256: str            # sha256 of canonical normalized ownership block
    @staticmethod
    def from_normalized(block: Mapping) -> "OwnershipConfig": ...

@dataclass(frozen=True)
class ClassificationReport:
    profile: str
    states: Mapping[str, tuple[str, ...]]        # state/reason value -> sorted names
    blocking: bool
    def counts(self) -> dict[str, int]: ...
    def as_dict(self) -> dict: ...

@dataclass(frozen=True)
class SourceContinuity:
    alias: str; dev: int; ino: int; size: int; mtime_ns: int; ctime_ns: int
    nlink: int; uid: int; mode: int; names_sha256: str
    reliable: bool; unreliable_reason: str | None
    def same_as(self, other: "SourceContinuity") -> bool: ...   # all fields except reliable/unreliable_reason

@dataclass(frozen=True)
class Grant:                     # what a worker is allowed to do; non-secret
    profile: str; capability: Capability; destination: Destination
    client_id: str; authority_digest: str        # "o1-..."
    names: tuple[str, ...]                       # exact allowlist for this call
    project_root: str; ownership: Mapping          # complete canonical non-secret authority

@dataclass(frozen=True)
class StorageAdmission:
    evidence_sha256: str; destination_origin: str; project_id: str
    captured_at: float; expires_at: float; attestation: Literal["operator_attested"]

@dataclass(frozen=True)
class Approval:
    approval_id: str; operation: Literal["migrate","cleanup"]; operator_uid: int; operator_login: str
    controller_id: str; profile: str; sources: tuple[SourceContinuity, ...]
    map_sha256: str; destination_sha256: str; authority_digest: str
    evidence_sha256: str | None; evidence_expires_at: float | None
    created_at: float; expires_at: float; surface: Literal["cli_tty"]

@dataclass(frozen=True)
class CreateOutcome:
    name: str; state: NameState; reason: str | None     # created|verified|conflict|unresolved|acceptance_unknown|failed

@dataclass(frozen=True)
class RevocationOutcome:
    status: Literal["revoked","cleanup_unknown","not_applicable"]; token_expires_at: float | None; next_action: str | None

@dataclass(frozen=True)
class ScopedRunRequest:
    profile: str; keys: tuple[str, ...]; argv: tuple[str, ...]; timeout_seconds: int; max_output_bytes: int
```

## [A] `OwnershipContext` in `ownership/models.py`
```python
@dataclass(frozen=True)
class OwnershipContext:
    config: OwnershipConfig; registry: SourceRegistry; project_root: Path; runtime_root: Path
    controller_id: str; read_only_broker: object | None; credential_store: object | None
# sandbox/secrets/context.py (lazy imports for C-owned runtime state)
def build_ownership_context(project_dir: object) -> OwnershipContext: ...
```
OwnershipConfig additionally provides `as_normalized() -> dict`, reconstructing
the complete canonical metadata block for digest validation and worker grants.

## [A] config / classify / continuity / source_view
```python
# ownership/config.py
def normalize_ownership(project_block: Mapping | None, machine_block: Mapping | None, sources: Mapping, root: object) -> dict | None
# ownership/classify.py
def classify(config: OwnershipConfig, profile: str, source_names: Mapping[str, tuple[str, ...]],
             existence: Mapping[str, Existence] | None = None,
             *, proven_removed: frozenset[str] = frozenset()) -> ClassificationReport
#   source_names: alias -> names present in that registered source (names only)
#   proven_removed: names durably proven removed in cleanup progress, classified as removed directly
#   unmapped = names present in any source referenced by the profile's entries that no entry maps
#   other_domain: entry of this profile whose source key/target scope conflicts with another domain; a bare equal name in isolated projects is allowed
# ownership/continuity.py
def capture(alias: str, info: os.stat_result, names: Iterable[str], *, now_ns: int) -> SourceContinuity
# ownership/source_view.py
def read_names(registry: SourceRegistry, alias: str) -> tuple[SourceContinuity, tuple[str, ...]]
def read_values(registry: SourceRegistry, alias: str, keys: Sequence[str], expected: SourceContinuity) -> dict[str, bytearray]
#   fstat before/after on the same fd; mismatch -> source_changed; unreliable -> continuity_unreliable
#   Initial source reads are file/dotenv only; other kinds/formats -> source_unsupported.
# sandbox/secrets/sources.py
def read_with_stat(self, alias: str) -> tuple[SafeSource, os.stat_result]: ...
#   SourceRegistry generic descriptor-safe snapshot: final stat from the same fd.
#   Existing read(alias) behavior stays unchanged; source_view never reopens raw paths.
# sandbox/secrets/authority.py  (see config-schema.md)
def ownership_digest(profile_name: str, ownership: Mapping, capability: Capability, *, project_root: object) -> str
def validate_ownership_grant(grant: Grant) -> None  # recompute digest from complete authority; validate fixed destination/client/scope/subset before native-store access
```

## [B] manifest / broker / evidence / locator
```python
# ownership/manifest.py
OWNERSHIP_MANIFEST: Mapping[str, Mapping]  # {"infisical-v4": {"capabilities": ("read","create","revoke"), "surfaces": ("cli",), "contract_confirmed": <bool>}}
def load_ownership_adapter(name: str = "infisical-v4") -> "OwnershipAdapter"   # raises provider_contract_unconfirmed when create requested and not confirmed

class OwnershipAdapter(Protocol):
    def validate_create_inputs(self, grant: Grant, values: Mapping[str, bytearray]) -> None: ...  # pure, no I/O
    def open_session(self, grant: Grant, *, deadline: float) -> "OwnershipSession": ...

class OwnershipSession(Protocol):           # one worker process, one login, context manager
    token_expires_at: float
    owners: Mapping[str, str]  # sanitized controller markers from the latest exists result
    def __enter__(self) -> "OwnershipSession": ...
    def __exit__(self, *exc) -> None: ...   # always attempts revoke, then terminate_process_group
    def exists(self, names: Sequence[str]) -> dict[str, Existence]: ...            # value-free; names ⊆ grant.names
    def policy_requires_approval(self) -> bool | None: ...                         # None = not detectable
    def create(self, name: str, value: bytearray, *, comment: str,
               before_send: Callable[[], None]) -> CreateOutcome: ...              # before_send journals 'attempted'+fsync; raise aborts
    def readback_equals(self, name: str, value: bytearray) -> bool | None: ...     # read or create grant; create grant only after a journaled attempt; cleanup uses read grant
    def read(self, names: Sequence[str]) -> dict[str, bytearray]: ...              # capability "read" only
    def revocation(self) -> RevocationOutcome: ...                                 # valid after __exit__

def validate_bootstrap_input(raw: bytearray, gateway: Literal["none","cloudflare-access"]) -> None: ...  # broker.py; pure fixed-code bundle validation, no I/O

class BrokerOwnershipAdapter:               # production implementation (broker.py)
    def __init__(self, *, process_factory=subprocess.Popen, clock=time.monotonic) -> None: ...

# ownership/evidence.py
V2_SELECTION_QUERY: str  # exact source-cited Q2 in storage-evidence.md; never a None placeholder
def admit_evidence(path: Path, destination: Destination, *, now: float) -> StorageAdmission
# sandbox/secrets/credentials/base.py
def locator_for_ownership(grant: Grant) -> CredentialLocator   # service namespace "sandbox-ownership", account = f"{grant.profile}:{grant.capability}:{grant.authority_digest}"
```

## [C] journal / approval / cleanup_progress / orchestration / CLI
```python
# ownership/journal.py
def load_controller_id(root: Path) -> str: ...
def new_qualified_id(prefix: Literal["ap","run"], controller_id: str) -> str: ...
class Journal:
    @classmethod
    def open(cls, root: Path, profile: str, destination_sha256: str, *, controller_id: str, authority_digest: str) -> "Journal"  # flock; journal_locked / journal_foreign_controller
    def states(self) -> dict[str, NameState]: ...
    def append(self, run_id: str, approval_id: str, name: str, state: NameState, reason: str | None = None) -> None: ...  # write+fsync file+dir
    def close(self) -> None: ...
# ownership/approval.py
class ApprovalStore:
    def __init__(self, root: Path) -> None: ...
    def grant_interactive(self, *, operation, profile, config: OwnershipConfig, registry, destination_sha256: str,
                          authority_digest: str, admission: StorageAdmission | None, minutes: int,
                          tty_in: TextIO, tty_out: TextIO) -> Approval: ...   # approval_human_only unless both TTY + foreground
    def load(self, approval_id: str) -> Approval: ...
    def bind_token_expiry(self, approval: Approval, expires_at: float) -> float: ...  # atomic sidecar min(existing, expires_at, approval.expires_at)
    def require_current(self, approval: Approval, *, operation, profile, config, continuities, destination_sha256,
                        authority_digest, now: float) -> None: ...  # approval_expired / approval_mismatch / source_changed / authority_mismatch
# ownership/cleanup_progress.py
class CleanupProgress:
    @classmethod
    def load_or_recover(cls, root: Path, profile: str, destination_sha256: str, *,
                        controller_id: str, authority_digest: str, map_sha256: str,
                        registry: SourceRegistry, current_continuities: Mapping[str, SourceContinuity]) -> "CleanupProgress": ...
    def proven_removed_names(self) -> frozenset[str]: ...
    def prepare(self, name: str, key: str, source_alias: str, staged: SourceContinuity, approval_id: str) -> None: ...
    def checkpoint_removed(self, name: str, key: str, source_alias: str, dev: int, ino: int, approval_id: str) -> None: ...
# ownership/migrate.py
def revocation_outcome(session: OwnershipSession) -> RevocationOutcome: ...
def revocation_status(session: OwnershipSession) -> str: ...
def run_migration(service_ctx: "OwnershipContext", profile: str, approval_id: str | None, *, confirm: bool,
                  timeout_seconds: int, adapter: OwnershipAdapter | None = None) -> dict   # CLI JSON payload
# ownership/cleanup.py
def run_cleanup(service_ctx: "OwnershipContext", profile: str, approval_id: str | None, *, confirm: bool,
                adapter: OwnershipAdapter | None = None) -> dict  # cleanup opens read grant restricted to freshly eligible journaled names; never create capability
# ownership/scoped_run.py
def resolve_scoped_bindings(service_ctx: "OwnershipContext", request: ScopedRunRequest,
                            adapter: OwnershipAdapter | None = None) -> dict[str, str]  # consumer -> value, for runner.run_with_secrets
# sandbox/secrets/writer.py
def remove_keys(path: Path, keys: Sequence[str], *, expected: SourceContinuity,
                before_replace: Callable[[SourceContinuity], None] | None = None,
                deadline: float | None = None) -> SourceContinuity  # dotenv only, atomic replace, no opaque_revision, checks deadline before/after callback and recaptures nofollow stat right before os.replace
# sandbox/commands/secrets.py
def cmd_secrets(cfg, args, *, ownership_adapter: OwnershipAdapter | None = None) -> int  # keyword only for in-process tests
```

## Migration algorithm (C, normative order)
1. Load config; `ownership_not_configured`/`profile_unknown`. 2. `read_names` per source alias → classify; blocking → exit 2, 0 writes. 3. Without `--confirm`: print plan (existence `unchecked`), exit 0. 4. Load approval; `require_current` (continuity, map, destination, authority, expiry). 5. `admit_evidence` (stored path bound by sha256). 6. `load_ownership_adapter(...)` capability check (contract confirmed). 7. `Journal.open` (lock). 8. `open_session(Grant(create), deadline=min(deadline, persisted_exp))`: sidecar token expiry read before worker launch to clamp worker session deadline. Inside the active session context immediately bind worker token expiry to approval (`store.bind_token_expiry`) and check `store.require_current`; if binding fails, revoke and refuse. `policy_requires_approval() is True` → refuse; `None` allowed (response-level detection still applies). Bounded to `min(deadline, effective_exp, token_exp)`. 9. `exists(all names)`: any `UNKNOWN` → `existence_unknown`; on a fresh journal any `PRESENT` → `pre_existing` refuse whole batch (FR-013); on resume, reconcile journaled names first (FR-029/030), enforcing effective clock before `exists` and each `readback_equals` to preserve prior receipts on expiry, and treat other `PRESENT` names as conflict stop. 10. `read_values` (continuity re-checked), then `adapter.validate_create_inputs` on the ENTIRE batch before any POST. Reject non-lossless UTF-8/provider inputs, including every ECMAScript TrimString boundary character that the supported provider would trim; do not normalize values. 11. Per name: in `before_send`, re-read all aliases in `approval.sources`, call `store.require_current` across all sources and destination/authority digests, re-check clock, journal `attempted` + fsync, then release `GO`. Stop on conflict/unresolved/acceptance_unknown; else `readback_equals` → `verified`/`unresolved`. Deadline/approval/token expiry checked before each name (FR-037). 12. Exit session (revoke), wipe values, write CompletionRecord. If completion record writing fails, report `persistence_failed` on the result body while preserving operation state and revocation guidance.

## Integration corrections
- A also owns the generic `SourceRegistry.read_with_stat` addition in sources.py.
- Gateway defaults preserve existing/new direct-source callers. B implements the
  explicit protected credential bundle only for gateway-enabled grants.
- `create` validates its one value defensively; C must also call the batch input
  validator before the first write so a bad final entry cannot cause a partial
  migration. The exact 25 ECMAScript TrimString code points are required.
- All public errors are fixed safe messages/codes; never interpolate payloads,
  provider bodies, exceptions, byte lengths or credential metadata values.
- R5: Cleanup source atomicity recaptures nofollow source metadata immediately
  before destructive `os.replace` (after the `before_replace` callback),
  verifying continuity against expected values (including reliability) and
  refusing with `source_changed` if drifted, preserving ordinary edits.
  Checks deadline guards upon entry, after prepare fsync, and immediately
  before `os.replace`. In `cleanup.py`, `before_replace` callback verifies
  that current continuities across all approved sources match expected
  continuities across previous removals.
- R6: Bounded reconciliation and migration operations read existing persisted
  expiry from the approval sidecar before opening workers to clamp worker session
  deadlines (`worker_deadline = min(deadline, persisted_exp)`). Bounds protected
  operations in worker and parent to `min(deadline, effective_exp, token_exp)`.
  Checks effective clock after `exists` before each `readback_equals` and before
  `exists`, preserving prior journal receipts; expired operations require fresh
  human approval (`approval_expired`).
- R7: Multi-session follow-up in `_finish` formats derived token follow-up from
  uncertain (`cleanup_unknown`) outcomes only, taking the latest expiry among
  uncertain finite expiries, or `expiry unknown` if any uncertain session has an
  unknown expiry, never using revoked token expiries or approval deadlines as live
  token bounds, while preserving primary operation errors.
- R8: CLI ownership dispatch catches audit outcome failures, preserving the
  primary payload, names, run ID, and revocation next action, attaching
  `audit="audit_unavailable"` / `audit_error="audit_unavailable"`, converting
  successful operations to failed `audit_unavailable` (exit 2) while preserving
  failure codes for failed operations. In `_finish`, completion record failures
  are caught and recorded as `persistence="persistence_failed"` and
  `completion_record="persistence_failed"` on the result payload without throwing
  or re-executing `_finish`.

Grant validation is mandatory in the parent AND worker before any native-store
lookup or HTTP. The worker recomputes authority_digest from grant.ownership and
canonical project_root, checks profile/capability/client/destination/gateway and
names subset against that authority, then derives the locator. A caller-supplied
digest by itself is not authority. Modifying endpoint, project, client or names
while retaining a prior digest must fail before credential access.
