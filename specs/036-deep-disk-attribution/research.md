# Research: Deep Disk Attribution

## Decision 1: Add an explicit deep status mode

**Decision**: Add `--deep` to `sb resources status` and `deep` to the existing
MCP status tool. Deep implies thorough measurement but remains a status
operation; plans and cleanup reject or ignore no new inputs.

**Rationale**: Root filesystem traversal and process-file inspection are
materially more expensive than the existing thorough managed-resource scan.
Making the mode explicit preserves current latency and compatibility.

**Alternatives considered**:

- Make every thorough scan deep. Rejected because existing callers rely on
  bounded managed-resource attribution and would incur unexpected host I/O.
- Add a separate top-level command. Rejected because it would split one
  capacity reconciliation across multiple product surfaces.

## Decision 2: Prefer installed gdu with a standard du fallback

**Decision**: Detect `gdu` without modifying the host. When available, invoke
its non-interactive, no-progress, raw-byte, no-cross-filesystem, no-delete,
no-shell, and no-file-view mode and parse only bounded top-level results.
Otherwise run the platform's standard allocated-block `du` form at depth one.
Record the selected capability and limitations.

**Rationale**: Official gdu behavior includes parallel scans, raw-number
non-interactive output, hard-link deduplication, and explicit safety flags. A
local isolated probe of gdu 5.36.1 confirmed a stable numeric-first
non-interactive line form and one-filesystem behavior. Standard `du` keeps the
feature functional without package installation.

**Alternatives considered**:

- Export gdu's full JSON tree. Rejected because it builds and returns an
  unbounded tree for a host-wide scan.
- Download or install gdu during status. Rejected because monitoring must not
  mutate package or executable state.
- Require ncdu/duc databases. Rejected because they add interactive or
  persistent indexing state that this bounded status feature does not need.

## Decision 3: Discover mounts before walking directories

**Decision**: On Linux, parse the current mount namespace from the kernel's
mount metadata and obtain per-mount capacity with standard filesystem calls.
Use platform `df`/mount output only as a fallback. Inventory writable local
filesystems, exclude virtual/pseudo filesystems from deep walking, and select
the root, Sandbox-home, container-data, and other known managed filesystems.

**Rationale**: A directory walk must not silently cross nested mounts, and a
root-only `df` cannot explain data on separate filesystems. Kernel mount
metadata is read-only, local to the target namespace, and does not require a
new dependency.

**Alternatives considered**:

- Assume Docker is under `/var/lib/docker`. Rejected because storage roots and
  drivers are configurable.
- Scan every mount. Rejected because unrelated network/removable filesystems
  can create unbounded I/O and privacy exposure.

## Decision 4: Detect deleted-open files with lsof field output

**Decision**: When installed, run `lsof +L1` in parseable field mode, preferring
existing `sudo -n` visibility and falling back to the current user. Accept only
regular-file records selected by zero-link semantics, deduplicate by device and
inode when available, aggregate by filesystem and minimized process identity,
and never return names or command arguments. Mark platforms without trustworthy
link-count semantics partial.

**Rationale**: The lsof project documents `+L1` specifically for unlinked open
files and its field output as cross-dialect automation input. These blocks are
present in `df` but absent from directory walks.

**Alternatives considered**:

- Walk `/proc/*/fd` directly. Rejected as the primary path because permission,
  namespace, and deleted-name handling are less portable; it may be a Linux
  fallback only if tests prove equivalent safe aggregation.
- Report deleted paths. Rejected because paths can contain secrets and are not
  needed for safe owner-level guidance.

## Decision 5: Use Docker's structured detailed accounting

**Decision**: Invoke `docker system df -v --format json` read-only and parse the
single structured object containing image, container, volume, and build-cache
details. Preserve unique/shared/reclaimable values as logical diagnostics and
keep them `capacity_accounted: false`.

**Rationale**: Observed Docker CLI behavior provides explicit `UniqueSize` and
`SharedSize` fields and retains activity information. The engine data-root
directory scan remains the capacity-accounted observation; detailed engine
values explain it without double counting.

**Alternatives considered**:

- Add detailed image sizes from existing image inspection. Rejected because
  image `Size` repeats shared layers.
- Parse the human table. Rejected because headings and localized formatting are
  not a stable automation contract.

## Decision 6: Reconcile observed allocation conservatively

**Decision**: Capacity attribution is the sum of selected filesystem-root
observed allocation plus deleted-open bytes not already directory-visible,
capped at used capacity. Container details and nested findings remain
overlapping diagnostics. The result reports overage, residual, capability
limits, and live drift instead of forcing equality.

**Rationale**: Hard links, sparse files, reflinks, compression, snapshots, and
copy-on-write can prevent path totals from equaling physical allocation.
Feature 035 already demonstrated that logical Buildx reclamation and physical
capacity changes differ.

**Alternatives considered**:

- Label all directory totals physical. Rejected as falsely precise.
- Convert the residual to filesystem overhead. Rejected because incomplete
  permissions or timed-out subtrees are equally plausible.

## Decision 7: Keep the remote probe compact and self-contained

**Decision**: Extend the current bounded remote resource program with deep
collectors and normalize its response through the same local model validators.
Do not upload, install, or update remote runtime code as part of status.

**Rationale**: Existing named-remote monitoring deliberately avoids deployment.
A self-contained probe works against provisioned remotes whose checked-out
Sandbox version may lag the caller.

**Alternatives considered**:

- Import only the new collector module from the remote checkout. Rejected
  because a lagging remote would make read-only status unavailable.
- Start multiple SSH sessions. Rejected because disconnect handling and partial
  evidence are simpler within the existing single bounded operation.
