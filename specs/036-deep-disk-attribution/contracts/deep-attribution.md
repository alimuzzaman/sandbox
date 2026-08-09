# Interface Contract: Deep Disk Attribution

## CLI

```text
sb resources status [--remote NAME] --deep [--budget SECONDS] [--cancelled] [--json]
```

- `--deep` is read-only and implies existing thorough measurement.
- The default budget remains explicit in the implementation and is bounded by
  the existing 3,600-second maximum.
- `--deep` is valid only for `status`; plan and cleanup contracts are unchanged.
- `--cancelled` is valid only for `status` and expresses a pre-cancelled
  non-interactive request for automation tests; it has no cleanup effect.
- Human output shows capacity, both drift dimensions, selected capabilities,
  coverage/limitations, safe mount topology, and ranked findings.

## MCP

```text
resource_status(
  remote: string | null = null,
  thorough: boolean = false,
  deep: boolean = false,
  budget_seconds: number = 15,
  cancelled: boolean = false
) -> ResourceEnvelope
```

`deep=true` implies thorough behavior. Existing callers omitting `deep` receive
the prior response shape. `cancelled=true` is the matching MCP cancellation
test seam; a pre-cancelled supporting provider returns structured cancelled
status/evidence, while a legacy provider returns `request_cancelled` without
starting collection.

## Additive status data

The existing schema-version-1 envelope and `data` fields remain unchanged.
Deep responses add:

```json
{
  "mode": "deep",
  "deep_attribution": {
    "status": "partial",
    "capacity_scope_id": "opaque-id",
    "filesystems": [],
    "findings": [],
    "capabilities": [],
    "coverage": [],
    "reconciliation": {
      "used_bytes": 162906124288,
      "directory_allocated_bytes": 90000000000,
      "deleted_open_bytes": 8000000000,
      "observable_overhead_bytes": 0,
      "overlapping_logical_bytes": 20000000000,
      "accounted_bytes": 98000000000,
      "residual_unexplained_bytes": 64906124288,
      "overage_bytes": 0,
      "drift_bytes": 0,
      "drift_material": false,
      "capacity_drift_bytes": 0,
      "attributed_drift_bytes": 0,
      "capacity_drift_material": false,
      "attributed_drift_material": false
    }
  }
}
```

All byte fields are raw non-negative integers. `overlapping_logical_bytes` is
never included in `accounted_bytes`. The enclosing status `data` also has an
opaque `capacity_scope_id`; deep reconciliation totals are used for the outer
summary only when its scope identity and used-capacity snapshot match.

## Filesystem record

```json
{
  "filesystem_id": "opaque-id",
  "display_name": "root filesystem",
  "filesystem_type": "ext4",
  "total_bytes": 206900281344,
  "used_bytes": 162906124288,
  "available_bytes": 43977379840,
  "writable": true,
  "selected": true,
  "selection_reason": "root",
  "status": "partial",
  "observed_allocated_bytes": 98000000000,
  "hardlink_deduplication": "confirmed",
  "mount_id": "opaque-id",
  "parent_mount_id": null,
  "capacity_scope_id": "opaque-id",
  "mount_flags": ["local", "read_write"],
  "limitations": ["copy_on_write_unknown"]
}
```

Raw device sources and mount options are excluded.

## Finding record

```json
{
  "finding_id": "opaque-id",
  "kind": "deleted_open",
  "display_name": "process 1234",
  "filesystem_id": "opaque-id",
  "owner": {"kind": "process", "id": "1234"},
  "observed_bytes": 8000000000,
  "capacity_accounted": true,
  "overlap": "none",
  "activity": "active",
  "guidance": "manual",
  "evidence": ["zero_link_count", "regular_file"],
  "limitations": []
}
```

Paths, file names, file contents, process arguments, environment values, raw
mount options, managed-root locators, device sources, and credential-like text
are not contract fields.

Docker findings add `unique_bytes`, `shared_bytes`, and
`potentially_reclaimable_bytes`. They distinguish logical engine detail from
capacity attribution: images retain shared-layer data; containers and volumes
report activity; build-cache detail reports engine reclaimability. All are
diagnostic and `capacity_accounted: false` to avoid double counting a measured
Docker root.

## Partial semantics

- One unavailable collector does not fail the status operation when capacity
  and other evidence are available.
- Every incomplete category receives a coverage record with a stable reason.
- Deep status is `partial` when any selected filesystem or required diagnostic
  is incomplete, including unavailable privilege, excluded nested topology,
  or a capacity-scope mismatch.
- Timeouts and disconnects preserve completed evidence when the transport
  returned a valid partial payload; a total transport failure retains the
  unavailable result rather than fabricating partial evidence.
- Parseable directory output produced before timeout is retained as partial
  evidence. Requests are bounded to the supplied budget plus five seconds.
- The residual remains unknown under partial coverage and may include metadata,
  snapshots, copy-on-write/shared allocation, hard links, sparse behavior, or
  capacity/attributed drift. It is not a deletion candidate.
- No deep finding is automatically reclaimable and no new cleanup code is
  introduced.
