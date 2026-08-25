# Resource Monitoring and Safe Cleanup

`sb resources` reports host storage, builds reviewable cleanup plans, and
applies only the exact candidates in a current confirmed plan. It is global:
run it from any directory, with no instance boot required.

## Run a storage-pressure monitor

`monitor` is the bounded, cache-only pressure pass. It resolves the configured
storage-monitor policy before constructing a host-facing service, then records
the capacity level, thresholds, automatic-cleanup decision, and retention
reap evidence for the selected target:

```sh
./sb resources monitor --json
./sb resources monitor --remote scaleway-sandbox --scheduled --json
```

The default budget is 900 seconds. `--scheduled` changes only the recorded
trigger; it grants no extra authority. `--dry-run` forces observation-only
cleanup and reaping, so it cannot delete resources, but the monitor still
writes its local last-run record and a dry reaping pass may persist a review
plan. Automatic reclamation and real reaping remain off unless their separate
policy switches are explicitly enabled. Warning and normal runs exit zero;
critical, unknown, policy-refused, and action-failed runs exit one so a caller
can surface them without parsing human output.

For a warning or critical result, review the exact next command printed by the
monitor (`resources plan --tier safe` for a warning and the confirmation-gated
safe cleanup command for a critical result). A policy or target refusal is
local and occurs before any host probe or service construction.

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

- `--refresh` walks each selected filesystem to depth 4, keeping every row at
  or above 32 MiB plus every row under a managed root (`$SANDBOX_HOME`,
  `deploy-src`, `runtime`, the containerd store) at any size, then stores the
  result. Default budget 900s, of which the walk keeps 90%. A large or busy
  host needs more: a 190 GB, inode-dense host took over 17 minutes, so give it
  `--budget 1800` and expect `complete: false` if it still runs out. An
  incomplete index is still used, still reported as incomplete, and is never
  allowed to replace a complete one.
- `--fast` never walks and never inventories the engine. It answers from the
  cache, or says `directory_index.source = cache_missing` and tells the
  operator to run `--refresh`. Default budget 10s.
- Detached host-local workers honor the same `--refresh`/`--fast` cache mode.
  A refresh does not spend its budget repeating one `du` per managed path
  before the filesystem walk; the completed or partial frontier is persisted
  atomically. After the walk, one bounded multi-path `du -s` pass resolves
  worktree, runtime, and Docker-volume resource IDs; subsequent cache reads
  replay those measurements with their provenance.
- Plain `--deep` reuses a cached index younger than 6 hours and otherwise walks
  within its own budget, writing whatever it completed. A truncated walk is
  never allowed to replace a complete one.
- Every report states the index provenance: `source` (`scan`, `cache`,
  `cache_missing`, `not_measured`), `complete`, `stale`, `age_seconds`,
  `depth`, and `minimum_row_bytes`.

## Run a detached deep scan

For a scan that can outlive the interactive command, submit it to the durable
job supervisor. The worker runs the host-local resource adapter on the selected
machine, so a remote scan does not open a second SSH probe back to itself:

```sh
./sb resources status --remote scaleway-sandbox --deep --refresh --budget 1800 \
  --detach --request-id storage-refresh-20260823 --json
```

The response is an acceptance receipt, not a completed measurement. Keep the
returned `job_id` and poll the retained status and JSONL progress/result output:

```sh
./sb job-status JOB_ID --remote scaleway-sandbox --json
./sb job-output JOB_ID --remote scaleway-sandbox --stream combined \
  --wait-seconds 20 --json
```

Use the same request ID to replay an uncertain acceptance; it returns the
original durable job instead of starting a duplicate. The probe budget remains
finite, while the durable supervisor adds a bounded grace period for startup
and result publication. Treat `partial`, `timed_out`, `failed`, or missing
output as incomplete evidence and inspect the retained category coverage before
interpreting any residual.

The indexed result reports host filesystem roots, Docker storage roots, and
per-resource worktree, runtime, and Docker-volume sizes. The resource rows
carry `directory_index` evidence so a later cache-only read can reproduce the
same logical domain totals without another full inode walk.

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

Remote Docker IDs are inspected in bounded batches. Rows delivered before a
container race, malformed response, or timeout are retained and the category
is marked `partial` or `timed_out`; an empty or unavailable category never
authorizes cleanup.

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

Remote plans are bound to the host identity emitted by the authenticated probe.
When applying a plan from a fresh client process, Sandbox performs a bounded
cache-only identity probe before comparing the persisted target; a failed probe
refuses the apply as `remote_target_unavailable` rather than using a client-side
fallback identity or touching the host.

## Tiered reclamation of deployment storage

`--scope cache|stale` reclaims engine and cache resources. `--tier safe|tmp|all`
is the separate, deployment-storage path: it classifies every entry of
`$SANDBOX_HOME/deploy-src`, selects candidates by lifecycle class and retention,
and writes a deletion manifest before it removes anything. The two are mutually
exclusive on one invocation.

### Lifecycle classes

Every deployment entry gets exactly one class, first match wins:

| Class | Meaning |
|---|---|
| `PROTECTED` | hosted site, registered instance, active job, symlink, or a path outside the managed roots |
| `LIVE` | a running container binds it |
| `STOPPED` | a container exists for it but is not running |
| `REGONLY` | the workspace index references it, but no container exists |
| `BASE` | no workspace marker in the name — a base deployment target |
| `ORPHAN` | a workspace directory with no container, no registry record, and no index record |
| `UNKNOWN` | the container inventory was unavailable, so no class can be proved |

`status` prints per-class counts and byte totals, index-versus-disk drift in both
directions, and the per-tier candidate totals:

```sh
./sb resources status --remote scaleway-sandbox --deep --budget 180
./sb resources status --remote scaleway-sandbox --fast     # cached index only
```

`--fast` skips the engine inventory, so LIVE/STOPPED cannot be proved and those
entries are reported `UNKNOWN` with reason `container_inventory_unavailable`
and zero candidates at every tier. That is deliberate: a fast report never
authorises a deletion.

### Tiers

| Tier | Adds |
|---|---|
| `safe` | ORPHAN entries, released entries, expired registry-only entries, and their workspace-scoped package volumes |
| `tmp` | `safe` plus disposable runtime scratch (`.drive-volume-fallbacks-*`) |
| `all` | `tmp` plus expired STOPPED workspaces, expired one-shot BASE deployments, and released entries that still have a running container |

Tiers are strictly nested and the nesting is asserted by tests.

### Safety rules enforced in code

- **Volumes are deny-by-default.** Only a name matching
  `sandbox-<workspace>_<name>node-modules`, where `<workspace>` contains a
  workspace marker, is ever eligible — and only when its owning workspace is
  itself a candidate or genuinely absent. Compose truncates long project names,
  so the owning-workspace check is prefix-aware: matching exactly once labelled
  a live workspace's volume "orphaned" on the real remote. Everything else,
  including volumes the engine reports as unused, is refused with
  `volume_not_workspace_scoped`. This is what keeps `lenzora-postgres-data`,
  `sandbox-amarsonar-bangla-public_wordpress-db`, `wordpress-uploads`, and
  `lenzora-storage` out of every plan.
- **Hosted sites are untouchable.** `deploy-src/hosts/**` and any entry naming a
  site registered under `$SANDBOX_HOME/runtime/hosts/` are refused at every
  tier, and again host-side immediately before removal.
- **In use means activity, not a process.** An entry is in use when an active
  job binds it, an unexpired lease covers it, or its modification time is inside
  the retention window. A running container produces the `LIVE` class — which
  keeps it out of `safe` — but an idle keepalive container cannot outvote an
  explicit release or an expired window.
- **Root-owned trees.** Removal escalates once through bounded
  `sudo -n timeout -k 1 N rm -rf --` and then re-stats the path. If anything
  remains, the outcome is `failed` with `partial_removal_detected` and its bytes
  are excluded from the reclaimed total. A partial delete is never success.
- **Growth exclusion.** The plan records `(size, mtime)`; the apply refuses a
  candidate whose mtime advanced (`candidate_modified_since_plan`). A size
  difference with an unchanged mtime is a measurement race, not growth, and is
  not an exclusion.

### Plan and apply

```sh
./sb resources plan    --remote scaleway-sandbox --tier safe
./sb resources cleanup --remote scaleway-sandbox --tier safe --confirm
./sb resources cleanup --remote scaleway-sandbox --tier safe --plan-id ID --confirm
```

`plan` has no side effects on the target host and lists, per candidate, the
path, bytes, mtime, class, tier, and reason — plus everything it skipped and
why. `cleanup` without `--plan-id` creates and executes a plan in one call (the
one-click path) and still stores the plan id.

Execution is resumable and idempotent. An interrupted run leaves its plan
`in_progress`; re-running the same plan id resumes it and reports `resumed:
true`. A completed plan id is refused (`plan_already_used`); re-running the same
*tier* is always safe and reports `already_absent` for anything already gone.

### Deletion manifest

Before each removal the host appends an `intent` record, and after it an
`outcome` record, to
`$SANDBOX_HOME/runtime/resources/deletions/<run_id>.jsonl` (mode `0600`,
append-only, `fsync` per record). No temporary file is used, so the manifest
works on a filesystem with zero free bytes; if the manifest cannot be created,
the whole run is refused with `manifest_unavailable` rather than deleting
unrecorded. Each intent names the path, bytes, class, tier, reason, trigger and
time, which is what makes "what happened to X" answerable afterwards.

After removal the host reconciles: registry records whose root is gone are
dropped through the typed repository, feature-owned lease files are removed, and
workspace index records are dropped through `sb workspace destroy
--workspace-id`. A host whose runtime predates that command reports
`index_pending` with status `partial` instead of implying the index is clean.

### Retention

```sh
./sb workspace release <name> --remote R          # done with it, reclaim now
./sb workspace ttl <name> --ttl 14d --remote R    # keep it longer
./sb workspace reap --remote R --dry-run          # what would expire
./sb workspace reap --remote R --confirm          # reclaim it
```

The default retention window is **7 days** for workspaces and 7 days for
one-shot base deployments. Leases live in
`$SANDBOX_HOME/runtime/resources/leases/<name>.json` (mode `0600`); the name
grammar is path-free. `reap` is a dry run unless `--confirm` is passed, and it
never touches disposable runtime scratch — that stays with `--tier tmp`.

### Threshold alerting

`status` classifies free-space pressure: `warning` below 15% free, `critical`
below 5%. Automatic reclamation is **off by default** and, when enabled, may
only ever run the `safe` tier; every automatic run is recorded in the manifest
with `trigger: "threshold"`.

### What needs a host runtime sync

`resources status|plan|cleanup` and `workspace release|ttl|reap` ship their probe
program over the connection on every call, so they work against a host running
an older Sandbox runtime. Only the host-executed control commands — `workspace
list|status|create|reset|destroy --remote` — run `sb` on the host and therefore
depend on its `sb-src` copy. Index reconciliation after a cleanup uses that same
command, which is why it degrades to `index_pending` rather than failing.

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
