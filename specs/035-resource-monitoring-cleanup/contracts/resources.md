# Interface Contract: Resource Monitoring and Safe Cleanup

## CLI

### Fast or thorough status

```text
sb resources status [--remote NAME] [--thorough] [--budget SECONDS] [--json]
```

- No `--remote` means the current local machine.
- A remote name must already be configured and resolve to one exact host.
- Default mode is fast and read-only.
- `--thorough` enables expensive providers within the overall budget.
- Human output ranks capacity, categories, owners, and incomplete measurements.
- JSON output follows the common envelope below.

### Cleanup plan

```text
sb resources plan --scope cache|stale [--remote NAME]
                  [--thorough] [--budget SECONDS] [--json]
```

- Always read-only.
- `cache` includes only positively owned disposable cache.
- `stale` uses the stronger persistent-resource ownership and non-use gates.
- Returns a plan ID, expiry, candidates, exclusions, and estimated bytes.

### Cleanup apply

```text
sb resources cleanup --plan-id ID [--remote NAME] --confirm [--json]
```

- `--confirm` is mandatory.
- The selected target must match the stored plan.
- Expired, completed, replayed, mismatched, or indeterminate plans are refused.
- Candidate IDs/paths cannot be supplied or expanded on the apply command.
- Each candidate is revalidated before exact removal.

CLI failures emit a structured JSON envelope before returning nonzero when
`--json` is selected. Human failures show the stable error code and safe
message.

## MCP tools

The explicit `resources` tool group exposes:

```text
resource_status(
  remote: string | null = null,
  thorough: boolean = false,
  budget_seconds: number = 15
) -> ResourceEnvelope

resource_cleanup_plan(
  scope: "cache" | "stale",
  remote: string | null = null,
  thorough: boolean = true,
  budget_seconds: number = 60
) -> ResourceEnvelope

resource_cleanup_apply(
  plan_id: string,
  remote: string | null = null,
  confirm: boolean = false
) -> ResourceEnvelope
```

MCP and CLI requests with equivalent inputs must return equivalent target,
classification, plan, confirmation, partial-result, and outcome semantics.

## Common structured envelope

```json
{
  "schema_version": 1,
  "ok": true,
  "action": "status",
  "status": "complete",
  "target": {
    "kind": "local",
    "name": "local",
    "identity": "non-secret-stable-id"
  },
  "data": {},
  "error": null
}
```

`action` is one of `status`, `plan`, or `cleanup`.

`status` is one of:

- `complete`, `partial`, or `failed` for status;
- `planned` or `failed` for plan;
- `completed`, `partial`, `indeterminate`, `refused`, or `failed` for cleanup.

An error object has stable shape:

```json
{
  "code": "confirmation_required",
  "message": "resource cleanup requires explicit confirmation",
  "retryable": false
}
```

Raw exception representations, command lines, credentials, file contents, and
sensitive mount options are not contract fields.

## Status data

```json
{
  "scan": {
    "scan_id": "opaque-id",
    "mode": "fast",
    "started_at": "2026-07-28T00:00:00Z",
    "completed_at": "2026-07-28T00:00:05Z",
    "budget_seconds": 15,
    "completeness": "partial",
    "confidence": "medium"
  },
  "capacity": {
    "total_bytes": 206900281344,
    "used_bytes": 169314246656,
    "available_bytes": 37569257472,
    "reserved_bytes": 16777216
  },
  "summary": {
    "attributed_bytes": 80246202368,
    "unknown_bytes": 89068044288,
    "reclaimable_bytes": 80246202368
  },
  "resources": [],
  "category_outcomes": [],
  "drift": null
}
```

Every resource contains:

- stable `resource_id`, `kind`, and safe `display_name`;
- owner kind/ID or explicit ownership gap;
- lifecycle classification;
- size state and raw bytes when measured;
- reclaimable bytes;
- whether the observation contributes to host-capacity attribution;
- evidence quality, references, and bounded errors.

An unavailable or timed-out size is `null`, never zero.
Nested detail can overlap a measured host root and therefore sets
`capacity_accounted: false`; it remains ranked without inflating attributed
host bytes.

## Plan data

```json
{
  "plan_id": "opaque-id",
  "scope": "stale",
  "created_at": "2026-07-28T00:00:00Z",
  "expires_at": "2026-07-28T00:15:00Z",
  "requires_confirmation": true,
  "estimated_reclaimable_bytes": 80246202368,
  "candidates": [],
  "exclusions": []
}
```

Candidate output contains safe IDs and evidence summaries, not internal delete
locators.

## Cleanup data

```json
{
  "run_id": "opaque-id",
  "plan_id": "opaque-id",
  "planned_bytes": 80246202368,
  "observed_reclaimed_bytes": 80000000000,
  "capacity_before": {},
  "capacity_after": {},
  "outcomes": [
    {
      "resource_id": "opaque-resource-id",
      "status": "skipped",
      "reason": "became_active",
      "observed_bytes": 3135596000,
      "evidence_changed": true
    }
  ],
  "drift": {}
}
```

Every candidate receives exactly one terminal outcome when the run is
determinate. An ambiguous remote timeout marks the run and plan indeterminate;
the client is told to rescan rather than replay automatically.

## Stable refusal/error codes

- `unknown_remote`
- `remote_unreachable`
- `target_identity_changed`
- `invalid_scope`
- `invalid_budget`
- `measurement_unavailable`
- `partial_measurement`
- `confirmation_required`
- `plan_not_found`
- `plan_expired`
- `plan_target_mismatch`
- `plan_already_used`
- `plan_indeterminate`
- `candidate_became_active`
- `candidate_evidence_changed`
- `cleanup_failed`
- `cleanup_timed_out`

Additional codes may be added compatibly. Existing meanings cannot be silently
redefined.

## Convergence amendment — 2026-08-13: network lifecycle and parser boundary

The following additive fields and error semantics close feedback `a813480b`,
`bf05eeb9`, `0fac3b07`, `822b9323`, `78aaf583`, and consumer feedback
`6bc4c6d5`.

### Network observation

When the selected provider can observe networks, `status` MAY add a bounded
`networks` collection. Each item has:

```json
{
  "network_id": "opaque-id",
  "display_name": "safe-name",
  "owner": {"kind": "sandbox|foreign|unknown", "id": "opaque-id"},
  "lifecycle": "active|idle|orphaned|indeterminate",
  "active_references": {"containers": 1, "leases": 1, "jobs": 0},
  "allocation": {"state": "allocated|available|exhausted|unknown", "pool": "safe-id"},
  "capacity_accounted": false,
  "cleanup_eligible": false,
  "evidence": [{"kind": "bounded-reference", "quality": "high"}]
}
```

`cleanup_eligible` is true only for positively Sandbox-owned, inactive,
revalidated networks with no active container, lease, or job reference. Active,
foreign, unknown, and indeterminate values are exclusions, not candidates.
Network allocation and release are idempotent against the same owner/workspace
identity; a failed release remains an explicit lifecycle outcome.

### Capacity and remote observation

Address-pool exhaustion uses stable code `network_pool_exhausted` and reports
bounded counts/identities, not raw daemon traces. A collision uses
`network_allocation_conflict`. Remote timeout/unreachable observation uses the
existing `remote_unreachable` or `measurement_unavailable` code with
`status:"partial"` and a category outcome; it never produces an empty-success
or deletion-ready result. Automatic network deletion and broad prune are outside
this contract.

### Job-list consumer rule

Resource monitoring consumes the Spec 032 job-list service/parser directly. The
wire shape remains the top-level page (`jobs`, cursor, and bounded counts), not a
`.data` envelope. A malformed or nested response is a parser error and leaves
network lifecycle state `indeterminate`; the resource consumer must not invent a
second decoder.
