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
