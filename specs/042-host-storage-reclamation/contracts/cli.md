# Contract: CLI surface

All commands accept `--remote <name>` to target a configured remote and omit it for the local
target. All accept `--json` for a machine-readable envelope with the existing
`{schema_version, ok, action, status, target, data, error}` shape.

## `sb resources status [--remote R] [--fast|--refresh] [--deep]`

Unchanged behaviour plus a `data.reclaim` block:

```jsonc
"reclaim": {
  "deployment_root": "/home/alim/sandbox/deploy-src",
  "classes": [
    {"class": "ORPHAN", "count": 108, "bytes": 33070006272, "measured": 108,
     "unmeasured": 0}
  ],
  "entries": [
    {"name": "…-workspace-8fd1", "path": "…", "class": "ORPHAN",
     "size_bytes": 331000, "size_state": "measured",
     "modified_at": "2026-08-05T…Z", "age_seconds": 900000,
     "lease": {"state": "expired", "expires_at": "…"},
     "reason": "orphan_workspace", "evidence": ["no_container", "no_registry"]}
  ],
  "volumes": {"eligible": 32, "eligible_bytes": …, "protected": 39},
  "drift": {"indexed_absent": 8, "present_unindexed": 104,
            "indexed_absent_names": [...], "present_unindexed_names": [...]},
  "truncated": false, "unmeasured_count": 0,
  "capacity_pressure": {"level": "warning", "free_ratio": 0.11, …}
}
```

Human output prints one line per class with count and bytes, the drift counts, the pressure
warning, and — when anything was bounded — the measured/unmeasured split. It never prints a
bounded total as if it were complete.

## `sb resources plan --remote R --tier safe|tmp|all [--json]`

Side-effect free. Emits a plan id and, per candidate: path/identifier, bytes, mtime, class,
tier, reason. Emits per-tier totals and a `skipped` list with reasons.

```
resources plan: planned (scaleway-sandbox)
  plan: 6a0e…  tier: safe  expires: 2026-08-16T12:15:00Z
  candidates: 140; estimated 33.1 GiB
    3.0 GiB  worktree  ORPHAN  …/x-workspace-8fd1  [orphan_workspace] mtime=2026-08-05
    1.5 GiB  volume    ORPHAN  sandbox-x-workspace-8fd1_lenzora-node-modules
                                                   [workspace_scoped_volume]
  skipped: 71
    volume   lenzora-postgres-data            [volume_not_workspace_scoped]
    worktree hosts                            [hosted_site]
  tier totals: safe 33.1 GiB | tmp 36.0 GiB | all 79.3 GiB
```

`--scope cache|stale` remains valid and unchanged. `--tier` and `--scope` are mutually
exclusive.

## `sb resources cleanup --remote R --tier <t> --confirm [--plan-id ID]`

Requires `--confirm`. Without `--plan-id`, a fresh plan is created for the tier and executed
in the same invocation (the one-click path); the plan id is still emitted and stored so the
run is auditable and resumable. With `--plan-id`, exactly that reviewed candidate set is
executed.

Reports per-candidate outcomes (`removed`, `already_absent`, `skipped`, `failed`,
`timed_out`), the manifest path, reclaimed bytes, capacity before/after, and the reconciled
index/registry counts. Re-running a completed plan id is refused with `plan_already_used`;
re-running the same tier is safe and reports `already_absent` for anything gone.

## `sb workspace release <name> [--remote R]`

Marks the workspace immediately reclaimable. Idempotent. Refuses an unknown name with
`workspace_not_found` without changing anything.

## `sb workspace ttl <name> --ttl 2h|14d [--remote R]`

Sets or extends the workspace's expiry, clearing any released marker.

## `sb workspace reap [--remote R] [--dry-run] [--ttl 7d] [--confirm]`

Reclaims expired, not-in-use workspaces and expired one-shot base deployments. `--dry-run`
changes nothing and lists what would be reclaimed. A real run requires `--confirm` and writes
the same deletion manifest as `resources cleanup`.

## Exit codes

`0` on success. `1` on any `ok:false` envelope. A partial or indeterminate cleanup exits `1`
with the outcome payload intact.
