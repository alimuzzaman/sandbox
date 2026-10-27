# Data model: secret profiles and ownership (Spec 069)

Non-secret metadata only. All structures normalize, validate, and serialize as
canonical JSON (`separators=(',', ':'), sort_keys=True, ensure_ascii=True`) for hashing.

## Domain
`Domain = Literal["office", "personal"]`
Accepted values: `"office" | "personal"`. Refuse any other value (such as `"production"`, `"staging"`, `"development"`, `"test"`, or `"local"`).
Profiles of different domains must not share `projectId` or client identities (`readClientId`, `createClientId`).

Note on `"undecided"`: `"undecided"` is reserved and NEVER permitted as a configured profile name or domain. It is permitted only as an entry-level profile placeholder in `MapEntry` (`profile: "undecided"`) during incremental source classification.

## Destination
Dataclass: `Destination` (normalized JSON object with exact keys):
- `endpoint`: canonical HTTPS origin only (`https://<hostname>[:<port>]` without path, trailing slash, query, or fragment; HTTP is strictly refused)
- `projectId`: non-empty provider project identifier matching `^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$`
- `environment`: non-empty environment identifier matching `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`
- `path`: canonical absolute provider path starting with `/` (defaults to `"/"`, matching provider path syntax; no relative segments `.` or `..`, no trailing slash unless root)
- `readClientId`: optional bounded provider identifier for read operations (`str | None`)
- `createClientId`: optional bounded provider identifier for create operations (`str | None`). When both `readClientId` and `createClientId` are specified, they must differ
- `timeoutSeconds`: integer between 1 and 120 (defaults to 30)
- `gateway`: `Literal["none", "cloudflare-access"]` (defaults to `"none"`)

Destination SHA256: `sha256(canonical_json(destination.as_normalized()))`.

## Profile
Dataclass: `OwnershipProfile` (normalized JSON representation):
- `name`: valid profile name slug `^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`. The name `"undecided"` is reserved and refused.
- `domain`: `Domain` (`"office" | "personal"`)
- `destination`: `Destination`

Normalized JSON schema:
```json
{
  "domain": "office",
  "destination": {
    "endpoint": "https://manager.example.org",
    "projectId": "proj-123",
    "environment": "prod",
    "path": "/",
    "readClientId": "read-client-id",
    "createClientId": "create-client-id",
    "timeoutSeconds": 30,
    "gateway": "none"
  }
}
```
No optional `description` field exists in the normalized schema; only exact keys `{"domain", "destination"}` are accepted.

## MapEntry
Dataclass: `MapEntry`:
- `source`: registered source alias or `"personal"` (unregistered aliases fail structural normalization)
- `key`: key selector in that source (matching source format rules)
- `profile`: profile name matching a defined profile in `profiles`, or `"undecided"` for unassigned entries
- `name`: destination secret environment name `^[A-Za-z_][A-Za-z0-9_]{0,127}$`
- `consumer`: child consumer environment variable name `^[A-Za-z_][A-Za-z0-9_]{0,127}$` (exact portable uppercase identifier, checked against dangerous variables such as `LD_*`, `DYLD_*`, `BASH_ENV`, etc.)
- `origin`: `Origin = Literal["source", "destination"]`. If `origin == "destination"`, the profile must have `readClientId` configured.

Normalized JSON schema:
```json
{
  "source": "personal-file",
  "key": "OPENAI_API_KEY",
  "profile": "personal",
  "name": "OPENAI_API_KEY",
  "consumer": "OPENAI_API_KEY",
  "origin": "source"
}
```

## OwnershipConfig
Dataclass: `OwnershipConfig`:
- `profiles`: `Mapping[str, OwnershipProfile]` (sorted by profile name)
- `entries`: `tuple[MapEntry, ...]` (sorted by `(source, key, profile, name, consumer, origin)`)
- `map_sha256`: `sha256(canonical_json(normalized_ownership_block))` — computed over the full canonical normalized ownership block (`{"profiles": ..., "entries": ...}`), NOT merely entries.

Normalized JSON representation:
```json
{
  "profiles": {
    "office": {
      "domain": "office",
      "destination": {
        "endpoint": "https://manager.example.org",
        "projectId": "office-project",
        "environment": "prod",
        "path": "/",
        "readClientId": "office-read-client",
        "createClientId": "office-create-client",
        "timeoutSeconds": 30,
        "gateway": "none"
      }
    }
  },
  "entries": [
    {
      "source": "office-file",
      "key": "OPENAI_API_KEY",
      "profile": "office",
      "name": "OPENAI_API_KEY",
      "consumer": "OPENAI_API_KEY",
      "origin": "source"
    }
  ]
}
```

## SourceContinuity
Dataclass representing safe nofollow stat + names observation:
- `alias`: str (registered source alias)
- `dev`: int
- `ino`: int
- `size`: int
- `mtime_ns`: int
- `ctime_ns`: int
- `nlink`: int
- `uid`: int
- `mode`: int
- `names_sha256`: str (`sha256(canonical_json(tuple(sorted(present_keys))))`)
- `reliable`: bool (`False` if mounted on unreliable filesystem or stat attributes missing)
- `unreliable_reason`: `str | None`

Continuity equality (`same_as`): compares `alias`, `dev`, `ino`, `size`, `mtime_ns`, `ctime_ns`, `nlink`, `uid`, `mode`, and `names_sha256`. Does not compare `reliable` or `unreliable_reason`.

## Approval
Canonical record serialized to `approvals/<approval_id>.json`:
- `approval_id`: `ap_<controller_id>_<hex16>`
- `operation`: `Literal["migrate", "cleanup"]` ONLY (`"run"` is never an approval operation)
- `operator_uid`: int (OS effective user ID)
- `operator_login`: str (login name or string UID)
- `controller_id`: 32 hex chars matching the local controller identity
- `profile`: str (profile name)
- `sources`: `tuple[SourceContinuity, ...]` (serialized as a JSON list of `SourceContinuity` objects, sorted by alias, one per distinct source alias in profile)
- `map_sha256`: str (SHA256 of the full normalized ownership block)
- `destination_sha256`: str (SHA256 of canonical destination descriptor)
- `authority_digest`: str (`o1-...` digest)
- `evidence_sha256`: `str | None` (required for migration approvals, optional for cleanup)
- `evidence_expires_at`: `float | None` (bounded lifetime from admitted evidence)
- `created_at`: float (epoch timestamp when approved)
- `expires_at`: float (epoch timestamp; must satisfy `0 < expires_at - created_at <= 3600`)
- `surface`: `Literal["cli_tty"]`

Approval Consumption:
Approval records do NOT contain an inline `consumed` boolean or timestamp. Instead, approval consumption is tracked via a separate marker file at `approvals/<approval_id>.consumed` containing `b"consumed\n"`. `ApprovalStore.consume` creates this marker exclusively; if the file already exists, subsequent cleanup attempts with that approval refuse with `approval_mismatch` ("approval was already used").

Approval Authorization:
Approvals require an operator at an interactive foreground TTY (`sb secrets ownership approve`). No hand-authored, synthetic, or agent-generated approval records are permitted for operational use.

## TokenExpirySidecar (`approvals/<approval_id>.token_expiry.json`)
Bound atomically upon opening the first worker session. Expiry checked via `ApprovalStore.require_current` on each operation.
Exact fields:
- `version`: int (1)
- `approval_id`: str (`ap_<controller_id>_<hex16>`)
- `controller_id`: str (32 hex chars)
- `operator_uid`: int
- `operator_login`: str
- `profile`: str
- `operation`: str (`"migrate"` or `"cleanup"`)
- `map_sha256`: str (64 hex chars)
- `destination_sha256`: str (64 hex chars)
- `authority_digest`: str (`o1-...`)
- `token_expires_at`: float (observed provider token expiry)
- `effective_expires_at`: float (`min(existing_effective, float(token_expires_at), float(approval.expires_at))`)
- `bound_at`: float (epoch timestamp of binding)

## CleanupProgressSidecar (`journals/<profile>/<destination_sha256>/cleanup.json`)
Durable per-name progress tracking for source cleanup:
- `cleanup`: 1 (schema version, integer)
- `controller_id`: 32 hex characters matching controller ID
- `profile`: profile name
- `destination_sha256`: 64 hex characters
- `map_sha256`: 64 hex characters
- `authority_digest`: `o1-...` (67 characters)
- `items`: `{name: {name: str, key: str, source_alias: str, state: "prepared" | "removed", approval_id: str, dev: int, ino: int, names_sha256: str, updated_at: float}}`

Records staged intent before `remove_keys` replacement, and checkpoints removal after replacement. Recovers crash state without adopting untracked removals or falsely failing on missing keys.

## MigrationJournal
Header: `{"journal": 1, "journal_id": str, "controller_id": str, "profile": str, "destination_sha256": str, "authority_digest": str, "created_at": float}`.
Records: `{"seq": int, "at": float, "run_id": str, "approval_id": str, "name": str, "state": str, "reason": str | None}`.

Name state machine (FR-026..FR-030):
```
pending ──journal(attempted)+fsync──▶ attempted ──POST──▶ created ──readback eq──▶ verified
   │                                     │  ├─ approval body ──▶ unresolved (stop)
   │                                     │  ├─ 400 already exists ──▶ conflict (stop)
   │                                     │  └─ timeout/5xx/malformed ──▶ acceptance_unknown (stop)
   ├─ initial preflight exists ──▶ pre_existing (whole batch refused, 0 writes)
   └─ pre-send transport failure ──▶ failed
created ──readback neq/missing──▶ unresolved
resume: acceptance_unknown|attempted ──exact check──▶ match: reconciled | absent: pending (one create) | mismatch: unresolved
resume: verified|reconciled ──revalidate metadata──▶ unchanged (never recreated) | missing/changed: unresolved
```
Terminal for cleanup eligibility: `verified`, `reconciled` only.

## CompletionRecord (`runs/<run_id>.json`)
Persisted to `runs/<run_id>.json` upon operation finish:
- `run_id`: str (`run_<controller_id>_<hex16>`)
- `operation`: str (`"migrate"` or `"cleanup"`)
- `profile`: str
- `approval_id`: `str | None`
- `started_at`: float
- `ended_at`: float
- `end_reason`: str
- `counts`: `dict[str, int]` mapping state to count
- `names`: `dict[str, list[str]]` mapping state to list of names
- `revocation`: `Literal["revoked", "cleanup_unknown", "not_applicable"]`
- `next_action`: `str | None`
- `storage_attestation`: `Literal["operator_attested"]`

On `cleanup_unknown`, `next_action` details token expiry time (`expires at <ts>` using the latest uncertain finite expiry, or `expiry unknown` if any uncertain session has an unknown expiry) and notes that native bootstrap entries and Cloudflare credentials remain operator-managed.
There is no persisted `token_expires_at` field in this record. Token expiry lives
in the approval's token-expiry sidecar and in trusted `next_action` guidance.
If writing the completion record encounters an error, the payload attaches `persistence="persistence_failed"` and `completion_record="persistence_failed"` while preserving known operation state and revocation guidance without re-throwing.
These failure indicators are returned payload fields, not fields of a successfully
written completion record. A previously successful operation then fails with
`provider_failed` and exit 4, preserving its prior code in `operation_code`.

## ScopedRunRequest
Dataclass:
- `profile`: str
- `keys`: `tuple[str, ...]` (non-empty tuple of map `name`s in that profile)
- `argv`: `tuple[str, ...]` (non-empty direct child command and arguments)
- `timeout_seconds`: int (bounded child timeout)
- `max_output_bytes`: int (bounded captured output)

All five fields are required; the frozen dataclass supplies no defaults.

Profile alone grants nothing (FR-043); explicit keys must be selected.

## ControllerIdentity
`controller_id` = random 128-bit hex created once at `$SANDBOX_HOME/runtime/secret-ownership/controller-id` (mode 0600, owner euid). Not machine-derived; a copied `$SANDBOX_HOME` is detected only by journal header + destination comment mismatch, not claimed beyond that.

## Integration clarifications
Entry source aliases must already be registered (unregistered configured aliases
are structural normalization errors). Unsupported registered source kinds and
formats classify `source_unsupported` in v1. Runtime CLI `source_unregistered` is
the public refusal corresponding to BlockReason `unregistered_source`.
Cleanup final verification opens a read grant, never a create grant. Grant
metadata includes the complete canonical ownership block and project root so
the worker can independently recompute the authority digest before using native
credentials. Controller-qualified IDs carry owner guidance but no authority.
