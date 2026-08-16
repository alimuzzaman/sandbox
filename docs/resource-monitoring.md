# Resource Monitoring and Safe Cleanup

`sb resources` reports host storage, builds reviewable cleanup plans, and
applies only the exact candidates in a current confirmed plan. It is global:
run it from any directory, with no instance boot required.

## Inspect storage

Use fast status for capacity and cheap inventory:

```sh
./sb resources status --json
./sb resources status --remote scaleway-sandbox --json
```

Use a bounded thorough scan when worktrees, private engine volumes, backups, or
other slow categories need measurement:

```sh
./sb resources status --thorough --budget 60 --json
./sb resources status --remote scaleway-sandbox --thorough --budget 300 --json
```

Use deep attribution when the capacity-level unknown bucket remains large:

```sh
./sb resources status --deep --budget 120 --json
./sb resources status --remote scaleway-sandbox --deep --budget 600 --json
```

## One command for the whole host

On a large or nearly full host, a directory walk cannot finish inside an
interactive budget. Deep mode therefore keeps a **cached host directory index**
on the host itself, under `$SANDBOX_HOME/runtime/resources/directory-index.json`.

```sh
# rebuild the index and report the whole host in one command
./sb resources status --remote scaleway-sandbox --refresh --json

# always-available report: capacity plus the cached index, no disk walk
./sb resources status --remote scaleway-sandbox --fast
```

- `--refresh` walks each selected filesystem to depth 6, keeping every row at
  or above 32 MiB plus every row under a managed root (`$SANDBOX_HOME`,
  `deploy-src`, `runtime`, the containerd store) at any size, then stores the
  result. Default budget 900s.
- `--fast` never walks and never inventories the engine. It answers from the
  cache, or says `directory_index.source = cache_missing` and tells the
  operator to run `--refresh`. Default budget 10s.
- Plain `--deep` reuses a cached index younger than 6 hours and otherwise walks
  within its own budget, writing whatever it completed. A truncated walk is
  never allowed to replace a complete one.
- Every report states the index provenance: `source` (`scan`, `cache`,
  `cache_missing`, `not_measured`), `complete`, `stale`, `age_seconds`,
  `depth`, and `minimum_row_bytes`.

Because the index already knows the size of every managed path, deep mode also
reports host filesystem roots, Docker storage roots, the **containerd content
store** (`/var/lib/containerd`, which `docker system df` never reports), and
per-workspace `deploy-src` sizes without paying for one `du` per path.

Sandbox-managed directories are named by their path relative to the managed
root (`Sandbox home/deploy-src/<workspace>`); directories outside a managed root
stay anonymized as `entry N`.

## Never go blind

Capacity is published by the probe before any bounded work starts, and the
transport keeps the richest record it received. A probe that is killed
mid-measurement therefore still reports capacity as a `partial` result with
`remote_probe: probe_incomplete_capacity_only`, instead of failing the whole
command with `measurement_unavailable`. A probe that raises reports the phase
it failed in (`probe_failed_in_<phase>`).

Human output leads with the unattributed share of used capacity, and shouts it
when it is 10% or more. Category outcomes that are not complete carry
`measured_bytes`, `measured_count`, and `unmeasured_count`, so a partial
category reports what it did measure rather than looking like an empty one.

Elevated measurement commands are bounded with `timeout` inside `sudo`: an
unprivileged probe cannot signal a root child, and killing only the direct
`sudo` process leaves the real worker holding the pipe and overruns the budget.

Deep mode implies `--thorough` and is status-only. It inventories mount
topology before walking, measures only the root, Sandbox-home, Docker-data,
and typed managed-root capacity scopes, and uses one-filesystem scanner mode.
Same-device nested-mount limitations remain explicit coverage rather than an
unconditional exclusion guarantee. `capacity_scope_id` identifies the capacity
boundary used for reconciliation: bind/nested mounts sharing a scope are
measured and counted once. Public records expose opaque mount and scope identities plus safe mount
flags, never mount sources or managed-root paths.

Typed managed roots come only from existing read-only typed repositories. They
select a containing filesystem but do not disclose their path, registry key, or
other locator in a finding. Virtual, unrelated, unavailable, and duplicate
mounts remain explicit coverage records rather than becoming scan targets.

Sandbox uses an already installed `gdu` for directory ranking when available
and falls back to standard allocated-block `du` when `gdu` is unavailable or
fails before yielding usable partial output; the capability record reports that
fallback. It never installs host packages during a scan. Existing passwordless,
non-interactive `sudo -n` may improve visibility, but missing privilege is
reported as partial coverage instead of prompting.
On inode-dense hosts using the standard fallback, use a larger finite budget;
completed entries and parseable timeout output are retained if the scan still
times out. A bounded local or remote request is expected to return within its
budget plus five seconds; this is a contract target, not live-proof evidence.

A scan reports raw total, used, available, attributed, unknown, and estimated
reclaimable bytes. Each resource includes an owner or ownership gap, lifecycle
classification, measurement state, and decision evidence. Timed-out and
unavailable measurements remain unknown; they are never reported as zero.
Thorough human output prints the category currently being measured.

Remote thorough scans also read the remote instance registry and durable-job
registry through their typed read-only repositories. They use that evidence to
distinguish active or retained workspaces from exact stale candidates. Host filesystem
roots and Docker storage roots are reported separately from nested
workspace/volume/image/build-cache detail so nested bytes do not inflate
capacity attribution.

Deep reconciliation separates filesystem used capacity, observed allocated
directory blocks, deleted-open allocated blocks, overlapping logical Docker
values, accounted bytes, overage, and the residual unexplained gap. It reports
both capacity drift and attributed-allocation drift; each is material only when
it exceeds the greater of one percent of used capacity or 64 MiB. A
capacity-scope mismatch makes the result partial and prevents it being combined
with the ordinary capacity summary. Capacity and the deep pass read the same
live filesystem moments apart, so a scope match tolerates the same materiality
threshold used for drift rather than requiring exact byte equality. Ranked
directory names outside a managed root are intentionally anonymized.

Deleted-open evidence uses `lsof +L1` field output when available, accepts
only regular zero-link records, deduplicates stable file identity where
available, and maps allocated blocks to a selected filesystem. It aggregates
safe process identity without file names or process arguments. Missing
elevation, inaccessible processes, incomplete allocated-block metadata, and
platform link-count limits are partial coverage—not zero bytes.

Docker image, container, volume, and build-cache detail reports observed,
unique, shared, activity, and potentially reclaimable logical values. These
diagnostics are always non-capacity-accounted: shared layers, writable layers,
volume data, and logical cache values must not be added to an already measured
Docker data-root allocation.

Review `deep_attribution.coverage`, capabilities, and filesystem limitations
before treating the residual as genuinely unlocated. Every discovered
filesystem and category says whether it was complete, partial, not selected,
unavailable, timed out, cancelled, or disconnected, and whether privilege was
sufficient. Coverage errors and excluded nested/virtual/unrelated mounts stay
visible. A nonzero residual after complete coverage can still represent
filesystem metadata, reserved blocks, snapshots, copy-on-write behavior,
sparse-file differences, hard-link allocation, or live capacity/attribution
drift.

`--cancelled` is an explicit non-interactive test seam valid only for
`resources status`; MCP `resource_status(cancelled=true)` exposes the same
pre-cancelled request state. Cancellation is not a cleanup action and any
already returned valid evidence remains structured with a `cancelled` terminal
status. A remote disconnect after a valid payload similarly retains it with a
`disconnected` terminal status; total transport loss returns unavailable rather
than fabricated partial evidence.

Deep findings do not create deletion authority. An exact finding may reference
`existing_cache_scope` or `existing_stale_scope` only when the ordinary
resource inventory independently establishes that same uniquely named
resource as eligible. Deleted-open files and anonymous host directories require
manual operator review; shared or active engine data remains monitoring-only.
Plan and cleanup continue to use the existing ownership, liveness, retention,
confirmation, and exact-target controls.

## Review a cleanup plan

Planning is read-only:

```sh
./sb resources plan --scope cache --thorough --budget 60 --json
./sb resources plan --scope stale --thorough --budget 90 --json
```

`cache` may include positively owned download cache, expired terminal-job
artifacts, stopped temporary containers, unused managed networks, and exact
unused Sandbox-labelled images. A provisioned named remote may also include
immutable build-cache records that its engine explicitly marks reclaimable;
each record must also be an unused private root record and is planned and
revalidated by exact build-cache ID. Buildx logical record sizes can overlap,
so cleanup receipts use the host capacity delta as the authoritative physical
reclamation result. It never
includes named persistent volumes,
host logs, package caches, retained backups, active resources, or unmanaged
data.

`stale` is a separate higher-risk scope. A worktree or named volume is eligible
only with positive Sandbox ownership, a measured size, and complete evidence
that no registry, live container, retained job, backup, permanent host, or
mount protects it. Names and age alone are insufficient. Remote providers
read remote registry/job evidence through typed repositories and exclude persistent resources
when that evidence is missing, invalid, or unavailable.

Plans expire after 15 minutes, bind to one host identity, and store internal
locators only in owner-readable records under
`$SANDBOX_HOME/runtime/resource-plans/`.

## Apply a reviewed plan

Confirmation is mandatory:

```sh
./sb resources cleanup --plan-id PLAN_ID --confirm --json
```

Apply accepts no caller-supplied paths or engine IDs. It loads the stored plan,
checks target identity and expiry, re-observes every candidate, and uses only
exact path or engine removal. If ownership, liveness, retention, or mount
evidence changed, the item is skipped. Broad Docker prune operations are not
used.

Completed, expired, replayed, mismatched, and indeterminate plans are refused.
A remote timeout is not retried automatically because the deletion result may
be ambiguous. Cleanup output itemizes removed, skipped, failed, timed-out, and
already-absent resources, compares capacity before and after, discloses drift,
and writes an owner-readable run receipt.

Do not confirm a plan until its candidates and exclusions have been reviewed.
Use a disposable fixture for mutating validation; monitoring and planning are
safe against permanent hosts.

## MCP parity

The explicit `resources` MCP group exposes:

- `resource_status`
- `resource_cleanup_plan`
- `resource_cleanup_apply`

These adapters use the same service and result envelope as the CLI.
`resource_status(deep=true)` returns the same additive attribution contract as
`status --deep`. `resource_status(fast=true)` and `resource_status(refresh=true)`
match `status --fast` and `status --refresh`; passing both is refused with
`invalid_mode`.
`resource_cleanup_apply` refuses missing confirmation before resolving a
provider.
