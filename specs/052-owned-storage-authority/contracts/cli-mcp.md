# CLI and MCP Contract: Owned Storage Authority

The owned-storage CLI/MCP group consists of thin adapters over the same
application service and safe projector. They never connect to the private
repository, open authority paths, perform filesystem cleanup, interpret service
internals, or change support tier. The separately protected `remote service`
review lifecycle is defined in `capability-evidence-v1.md`; it is not an MCP or
ordinary project operation. Every remote call verifies the installed
protocol/revision before relying on new fields.

## CLI operations

### Capability and status

```text
sb storage authority capability --remote NAME [--project-identity ID] [--json]
sb storage authority status --remote NAME --project-identity ID
                            [--kind generation|materialization|artifact]
                            [--limit N] [--cursor CURSOR] [--json]
```

Both are read-only. `capability` follows `capability-evidence-v1.md`. `status`
returns bounded authority objects plus safe legacy projections, with default
limit 100 and maximum 500. Neither command installs, repairs, qualifies,
promotes, opts in, reclaims, or reads source content.

### Disposable qualification acceptance

```text
sb storage authority acceptance --remote NAME --project-identity ID
                                --fixture-id ID --request-id ID
                                --confirm [--json]
```

This is a fixed, separately authorized live-proof harness, not a general
mutation API. After verifying explicit confirmation, the registered disposable
fixture, exact clean source/installed revision, and supervised controller, the
supported lifecycle mints one sealed admission with a fixed operation budget,
deadline, and evidence candidate. The command accepts no path, test selector,
policy mode, object, operation list, force flag, or admission override. It
drives the fixed acceptance matrix through the ordinary application/storage
path, closes the admission with cleanup evidence, and returns a bounded proof
candidate.

Possession of a fixture or request ID is not authorization. Missing, mismatched,
expired, exhausted, replay-conflicting, drifted, or incompletely closed evidence
refuses the run and cannot change support, `adoptable`, or normal project
policy. This lifecycle/acceptance seam is deliberately not an MCP tool.

### Future-object policy and rollback

```text
sb storage authority policy --remote NAME --project-identity ID
                            --mode legacy|future --request-id ID
                            --confirm [--json]
```

- `future` requires current proven/adoptable capability and affects later
  objects only.
- `legacy` is the rollback control for later creation. It does not adopt, move,
  copy back, rewrite, or delete an existing object.
- Confirmation and a replay-safe request ID are mandatory.
- Reusing a request ID with a different canonical transition fails
  `request_id_conflict`.

### Reclamation preview and execution

```text
sb storage authority preview --remote NAME --project-identity ID
                             [--kind generation|materialization|artifact]
                             [--limit N] [--cursor CURSOR] [--json]

sb storage authority reclaim --remote NAME --project-identity ID
                             --preview-id ID --object-id ID
                             --request-id ID --confirm [--json]
```

Preview is read-only and never changes eligibility. Each execution considers
one supplied exact object that was eligible in the complete unexpired
preview. The command cannot accept a path, label-only target, age expression,
glob, broad `--all`, storage-root selector, or force/ignore-evidence option.
Omitting `--object-id` is invalid; a caller cannot turn the preview into an
unreviewed bulk deletion. Multiple objects require distinct request IDs and
one independently replayable cleanup operation per object.

### Operation reconciliation

```text
sb storage authority reconcile --remote NAME --project-identity ID
                               --request-id ID --operation policy|publish|materialize|cleanup
                               [--json]
```

Read-only or safe continuation of the original recorded operation. It never
accepts a second target/request identity and never resends source bytes by
itself.

## Existing command effects

The following existing operations keep their syntax and default outcomes:

- `sync start`, `sync once`, `sync status`, `sync stop`, and exact replay;
- `ci run`, job status/result/retry/cleanup, and workspace status/list;
- resource status/preview and all legacy compatibility commands.

When the exact remote/project policy is `future` and capability is currently
proven, newly created sync generations and eligible new CI materializations use
the authority automatically. Their existing envelopes gain this additive
block:

```json
{
  "storage_authority": {
    "status": "accepted|active|retained|completed|unknown|indeterminate",
    "object_id": "object_opaque-or-null",
    "operation_id": "operation_opaque-or-null",
    "evidence_digest": "sha256:...|null",
    "support_tier": "proven",
    "future_policy_generation": 7
  }
}
```

Legacy objects omit the block or use the fixed path-free projection
`{"status":"legacy_not_owned"}` where status comparison needs it. No existing
accepted generation, job result, cleanup policy, exit code, or legacy replay
meaning changes.

If policy is `future` but capability is unavailable, unproven, unsupported,
drifted, or revision-skewed, creation is refused before authority-dependent
mutation. It never silently falls back while claiming an immutable/owned
object. Existing legacy operations remain available when they do not request or
depend on future authority policy.

## MCP tool group

The explicit `owned-storage` tool group registers:

| Tool | Mutation | Inputs |
|---|---|---|
| `owned_storage_capability` | No | `remote`, optional bound `project_identity`. |
| `owned_storage_status` | No | `remote`, `project_identity`, optional kind, limit, cursor. |
| `owned_storage_policy` | Yes | `remote`, `project_identity`, `mode`, `request_id`, `confirm=true`. |
| `owned_storage_preview` | No | `remote`, `project_identity`, optional kind, limit, cursor. |
| `owned_storage_reclaim` | Yes | `remote`, `project_identity`, `preview_id`, one `object_id`, `request_id`, `confirm=true`. |
| `owned_storage_reconcile` | No/safe continuation | `remote`, `project_identity`, `request_id`, operation enum. |

The group contains no publish/upload tool, arbitrary command, arbitrary path,
raw service call, support promotion, service install/restart, data purge,
legacy adoption, resolver cleanup, or force cleanup. Existing sync and CI MCP
tools reach publication/materialization through their application services and
the durable future policy.

MCP service factories bind the configured project root when one exists and
resolve project/remote identity through existing application services. They do
not read registry JSON or service state directly. Mutating tools require exact
project authorization, confirmation, and replay-safe request identity.

## Status page envelope

```json
{
  "ok": true,
  "status": "complete|partial",
  "remote_identity": "opaque",
  "project_identity": "opaque",
  "policy": {"mode": "legacy|future", "generation": 7},
  "capability": {
    "support_tier": "implemented_unproven|proven|unsupported|unavailable|drifted",
    "adoptable": false,
    "service_revision": "opaque",
    "evidence_id": null
  },
  "objects": [
    {
      "object_id": "object_opaque",
      "kind": "sync_generation|ci_materialization|retained_artifact",
      "lifecycle": "accepted|active|retained|eligible|removed|indeterminate",
      "scope": {
        "relationship_id": "rel_opaque-or-null",
        "workspace_id": "opaque-or-null",
        "job_id": "opaque-or-null"
      },
      "evidence_digest": "sha256:...",
      "known_bytes": 12345,
      "reason_code": "current_generation"
    }
  ],
  "legacy": [
    {
      "legacy_identity": "opaque",
      "kind": "sync_generation|workspace",
      "authority_status": "legacy_not_owned",
      "eligibility": "not_authority_candidate"
    }
  ],
  "complete": true,
  "next_cursor": null,
  "counts": {"returned": 1, "authority": 1, "legacy": 0},
  "observed_at": "2026-08-31T12:00:00Z"
}
```

`complete:false` requires a safe reason/code and grants no cleanup authority.
Cursor is opaque, exclusive, scope-bound, and expiry-bound. A cursor for a
different remote/project/filter fails.

## Preview envelope

```json
{
  "ok": true,
  "status": "previewed",
  "preview_id": "preview_opaque",
  "remote_identity": "opaque",
  "project_identity": "opaque",
  "inventory_generation": 19,
  "policy_generation": 7,
  "candidates": [
    {
      "object_id": "object_opaque",
      "kind": "sync_generation",
      "decision": "eligible|protected",
      "reason_code": "retention_elapsed|current_generation|reference_active|evidence_incomplete",
      "estimated_bytes": 12345,
      "evidence_digest": "sha256:..."
    }
  ],
  "estimated_reclaimable_bytes": 12345,
  "unknown_byte_candidates": 0,
  "complete": true,
  "expires_at": "2026-08-31T12:15:00Z"
}
```

Unknown bytes are `null` per candidate and excluded from totals. A partial or
timed-out preview is `complete:false` and cannot be used for reclaim.

## Reclaim envelope

```json
{
  "ok": true,
  "status": "completed|already_completed|retained|refused",
  "preview_id": "preview_opaque",
  "request_id": "request_opaque",
  "object_id": "object_opaque",
  "cleanup_id": "cleanup_opaque",
  "reason_code": "removed|reference_active|object_identity_drift",
  "observed_reclaimed_bytes": 12345,
  "complete": true
}
```

Top-level `ok:true` means the one bounded request was decoded and its outcome is
truthfully reported; it does not turn retained/refused into successful removal.
Failed or indeterminate effects use the failure envelope. CLI exit is 0 only
for completed/already-completed, 1 for retained/refused, and otherwise follows
the failure table below. The terminal job result is not part of this mutable
outcome.

## Stable errors and exit behavior

All failures contain `ok:false`, safe `code`, bounded `message`, request/object
identity where safe, and `retryable`. Core codes are those in
`authority-service-v1.md`.

CLI exit codes extend existing meanings without changing them:

| Code | Meaning |
|---:|---|
| 0 | Read-only operation completed truthfully; policy transition completed; or the one requested cleanup completed/already completed. |
| 1 | A valid cleanup request was evaluated but the object was retained or refused; no removal is claimed. |
| 2 | Invalid input, missing confirmation, request conflict, preview mismatch/expiry, or unauthorized scope. |
| 5 | Remote/service unreachable, capability unavailable, or observation incomplete. |
| 6 | Storage integrity, publication, identity, cleanup failure, or indeterminate effect. |

Unsupported/unproven authority mutation uses code 5 unless input/scope itself is
invalid. Existing sync/job exit rules remain authoritative for those commands.

## Disclosure and bounds

CLI text, CLI JSON, MCP responses, tool errors, progress metadata, and retained
operation evidence use one allowlist plus the shared redaction service. Allowed
fields are opaque identities, digests, counts, lifecycle/policy/outcome/reason
codes, safe timestamps, capability tier/revision/evidence ID, and aggregate
known bytes.

They never contain source content or entry names, credentials, argv/environment,
raw UID/GID/PID, process/unit/socket details, filesystem paths, mount locators,
host configuration, remote SSH details, resolver/network state, or unrelated
project state. No raw traceback or service journal bypass is permitted.
