# Contract: reclaim policy

Normative rules implemented by `sandbox/resources/reclaim.py`. The module is pure: it takes
evidence dictionaries and returns decisions. It performs no I/O, so every rule below is unit
testable without a host.

## 1. Lifecycle classes

Every entry of the managed deployment root is assigned exactly one class, evaluated in this
order. The first match wins, and the matching rule's name is recorded as `reason`.

| Order | Class | Condition | Reason |
|---|---|---|---|
| 1 | `PROTECTED` | the entry is the hosted-sites subtree, or its name/path belongs to a registered hosted site | `hosted_site` |
| 2 | `PROTECTED` | an always-protect rule matches (see §2) | rule name |
| 3 | `LIVE` | a **running** container binds the entry's path, or an active (non-terminal) job binds it | `live_container_bind` / `active_job` |
| 4 | `STOPPED` | a container exists for the entry but is not running | `stopped_container` |
| 5 | `REGONLY` | the workspace index references the entry but no container exists (instance-registry entries already matched rule 2 and are PROTECTED) | `registry_only` |
| 6 | `BASE` | the entry name has no workspace marker (`-workspace-`, `.workspace-`) | `base_deployment` |
| 7 | `ORPHAN` | none of the above: a workspace directory with no container, no registry entry, and no index record | `orphan_workspace` |

`UNKNOWN` is returned only when the *container inventory* itself is unavailable — which is
deliberately a different question from "was every directory measured". An `UNKNOWN` entry is
never a candidate at any tier. A directory whose size could not be measured keeps its class
and is skipped with `size_unmeasured`.

## 2. Always-protected rules (deny-by-default)

Checked at classification time, again at plan time, and once more immediately before removal.
Each rule has a stable name that is reported as the skip reason.

| Rule | Applies to | Effect |
|---|---|---|
| `hosted_site` | the `hosts` entry of the deployment root, anything beneath it, and any entry whose name matches a registered hosted site | never a candidate, any tier |
| `managed_root` | the deployment root, the runtime root, and the sandbox home themselves | never removable |
| `path_escape` | any path that does not resolve strictly inside the deployment root or the runtime root | never removable |
| `instance_registry` | an entry referenced by a registered instance | never a candidate |
| `active_job` | an entry bound by a non-terminal or retained job | never a candidate |
| `volume_not_workspace_scoped` | any volume whose name does not match §3 | never a candidate, any tier |
| `symlink` | an entry that is itself a symlink | never removable through this path |

## 3. Volume eligibility (FR-021)

A volume is eligible **only** when all of the following hold:

1. Its name matches `^sandbox-(?P<workspace>.+)_[A-Za-z0-9.-]*node[-_]?modules$` (the suffix class excludes `_` so the greedy capture stops at the final separator) — that is,
   a Compose-project-scoped volume whose logical name ends in a `node_modules` spelling.
2. The captured `<workspace>` contains a workspace marker (`-workspace-` or `.workspace-`).
3. The deployment entry named `<workspace>` is itself a candidate in the same plan, or is
   absent from disk entirely. Matching is **prefix-aware in both directions**, because
   Compose truncates long project names: `sandbox-lenzora-workspace-37a8ee_…` belongs to
   the live directory `lenzora-workspace-37a8eec1ce1968`. If any *retained* entry matches
   the captured segment as a prefix, the volume is protected
   (`owning_workspace_retained`). Exact matching produced this exact false positive on the
   real remote.
4. No running container mounts the volume.
5. The container inventory and deployment/workspace listing are complete. If
   the engine probe is partial or unavailable, every volume is withheld with
   `container_inventory_unavailable`; if the deployment listing is partial,
   unavailable, or truncated, every volume is withheld with
   `deployment_inventory_unavailable`. Incomplete evidence can never authorize
   a volume deletion.

Everything else is protected with reason `volume_not_workspace_scoped`, including volumes the
engine reports as unused. The four volumes that motivated this rule —
`lenzora-postgres-data`, `sandbox-amarsonar-bangla-public_wordpress-db`, `wordpress-uploads`,
`lenzora-storage` — must be rejected by rule 1 or 2 in tests.

## 4. Liveness (FR-024)

`in_use(entry, now)` is true when **any** of:

- an active (non-terminal, or `retain`-policy, or `retained`-state) job binds the entry;
- an unexpired lease exists for the entry and it has not been released;
- the entry's modification time is newer than `activity_window` (default: the retention
  window, 7 days).

The existence of a process, or of a *running* container, is **not** sufficient. A running
container contributes the `LIVE` class — which keeps the entry out of the `safe` tier — but a
released entry, or an entry whose lease has expired, is reclaimable at the `all` tier even
while a container is running, and the container is stopped and removed first. This is the
rule that would have released the nine idle keepalive workspaces holding 28.8 GiB.

## 5. Tiers (FR-010, strictly nested)

| Tier | Adds |
|---|---|
| `safe` | `ORPHAN` entries; released entries with no running container; expired `REGONLY` entries; workspace-scoped volumes of those entries (§3) |
| `tmp` | everything in `safe`, plus disposable scratch under the runtime root (`.drive-volume-fallbacks-*`) |
| `all` | everything in `tmp`, plus `STOPPED` entries past their retention window, `LIVE` entries whose window expired or that were released while a container runs, and one-shot `BASE` entries past their retention window |

Expired job artifacts, the download cache, images, containers, networks, and build cache stay
with the pre-existing `--scope cache|stale` path; tiers deliberately do not duplicate them.

`LIVE` entries that are neither released nor expired are never candidates at any tier.
`PROTECTED` entries are never candidates at any tier. `tier_candidates(inventory, "safe") ⊆
tier_candidates(inventory, "tmp") ⊆ tier_candidates(inventory, "all")` is an invariant
asserted by tests.

## 6. Growth exclusion (FR-025)

`growth_excluded(planned, observed)` returns a reason when either:

- `observed.mtime > planned.mtime` → `candidate_modified_since_plan`, or
- `observed.size_bytes > planned.size_bytes` **and** `observed.mtime != planned.mtime` →
  `candidate_growing`.

A size difference with an unchanged mtime is **not** an exclusion: that is a measurement
race, which is what actually happened during the manual audit. Every exclusion is recorded in
the plan/run output with its reason.

## 7. Retention leases (FR-027…FR-031)

A lease record is `{name, expires_at, released, released_at, updated_at, note}`.

- `release(name)` sets `released = true`; the entry becomes immediately reclaimable
  (`lease_released`) regardless of age.
- `ttl(name, duration)` sets `expires_at = now + duration`; durations are `<int><unit>` with
  units `m`, `h`, `d` (`2h`, `14d`). A TTL request clears `released`.
- With no lease, the effective expiry is `mtime + default_window` where `default_window` is
  7 days for workspaces and 7 days for one-shot base deployments.
- `lease_state(...)` returns one of `released`, `expired`, `active`, with the effective
  expiry timestamp, so status and plan can report the reason.

## 8. Disk capacity pressure (FR-032…FR-034)

`disk_capacity_pressure(capacity, *, warn_ratio=0.15, critical_ratio=0.05,
auto_tier=None)` returns:

```json
{
  "level": "normal|warning|critical",
  "free_ratio": 0.043,
  "free_bytes": 8323072,
  "warn_ratio": 0.15,
  "critical_ratio": 0.05,
  "threshold_crossed": "critical_ratio",
  "auto_tier": null,
  "guidance": "…"
}
```

`auto_tier` is non-null only when the host is configured for automatic reclamation, and may
only ever be `safe`. Automatic reclamation is off by default and every automatic run is
recorded in the manifest with `trigger: "threshold"`.

## 9. Deletion manifest (FR-013, FR-014)

One JSON object per line, appended and flushed before the corresponding removal is attempted:

```json
{"schema":1,"run_id":"…","seq":3,"phase":"intent","path":"…","kind":"worktree",
 "bytes":3221225472,"class":"ORPHAN","tier":"safe","reason":"orphan_workspace",
 "at":"2026-08-16T12:00:00Z","trigger":"manual"}
{"schema":1,"run_id":"…","seq":3,"phase":"outcome","path":"…","status":"removed",
 "reason":"removed","elevated":false,"verified_absent":true,
 "at":"2026-08-16T12:00:04Z"}
```

`phase: "intent"` is durable before the removal starts; `phase: "outcome"` follows it. A run
whose outcome line is missing for a given `seq` is exactly the resumable case: the path is
re-checked on the next run and reported `already_absent` if it is gone.
