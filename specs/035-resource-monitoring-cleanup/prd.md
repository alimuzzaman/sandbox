# Product Requirements Draft: Resource Monitoring and Safe Cleanup

**Status**: Ready for Specification

**Created**: 2026-07-28

**Last Refined**: 2026-07-28

**Input**: "Add resource monitoring and cache cleanup tools/commands. Do a thorough check on what is taking so much space."

**Drafting Model**: `GPT-5` current root configuration (fallback; the preferred `gpt-5.6-terra` Medium root was not selected for this turn)

**Final Validation**: `PASS` — `gpt-5.6-sol` High

**Validated On**: 2026-07-28

**Artifact Owner**: `speckit.prd.refine`

**Next Stage**: `speckit.specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Operators can create and remove local or remote Sandbox instances, jobs,
previews, deployment worktrees, caches, and backups, but they cannot obtain one
trustworthy explanation of host storage use or safely reclaim stale resources
through Sandbox. Existing point tools cover only individual areas, and Docker's
own summary can hang or hide data that is mounted in a private namespace.

This leaves operators unable to answer basic operational questions: which
project owns the space, whether the data is active or abandoned, how much can be
reclaimed, and whether cleanup will affect a permanent host. The immediate
remote audit demonstrated the impact: a 206.9 GB host had 169.3 GB in use, with
approximately 45.0 GB in dangling Lenzora dependency volumes and 34.2 GB in
unmounted deployment worktrees. Old backup staging and several smaller cache
classes added further pressure. These resources survived instance cleanup
because their ownership and lifecycle were not reconciled in one place.

## Users and Desired Outcomes

- **Sandbox operator**: Understand total, used, available, and reclaimable
  storage on a local or named remote host, attributed to recognizable owners.
- **Developer or QA worker**: See whether their active project, temporary
  workspace, test run, cache, or backup is responsible for storage growth.
- **Host administrator**: Reclaim only resources proven safe to remove, with a
  reviewable plan and an audit of what changed.
- **Automation client**: Receive the same bounded, structured monitoring and
  cleanup results available to an interactive CLI user.

## Goals

- Provide a host-wide storage report that reconciles filesystem capacity with
  Sandbox worktrees and runtime data, Docker resources, backup staging,
  downloads, jobs, logs, and other material host categories.
- Attribute managed resources to a project, host, instance, workspace, job, or
  backup where evidence permits, and label the remainder as unmanaged or
  unknown rather than guessing.
- Distinguish active/permanent data, inactive but retained data, caches,
  dangling managed resources, and resources that are safe to reclaim.
- Offer a fast bounded status view and an explicit thorough scan for categories
  whose directory traversal is expensive.
- Offer cleanup plans and confirmation-gated execution for safe caches and
  proven stale Sandbox resources.
- Preserve CLI and MCP parity, including structured output suitable for
  automation.
- Make partial, timed-out, unavailable, or low-confidence measurements visible
  so totals cannot be mistaken for complete attribution.

## Non-Goals

- A general-purpose disk cleaner for arbitrary host files or applications not
  owned by Sandbox.
- Automatic deletion based only on age, name patterns, apparent inactivity, or
  low disk space.
- Deleting running containers, mounted volumes, permanent host deployments,
  current backups, user source repositories, or unmanaged data.
- Replacing external host observability, alerting, billing, or capacity
  planning systems.
- Redesigning instance, preview, backup, deployment, or job lifecycles in this
  feature.
- Performing cleanup as part of PRD discovery.

## Product Scenarios

### Scenario 1 — Fast storage status

- **Starting state**: A local or named remote host has multiple active projects
  and may be close to full.
- **User action**: The operator requests resource status.
- **Expected outcome**: Within a bounded wait, the operator sees host capacity,
  the largest attributable categories, active versus reclaimable space, the
  scan timestamp, and any incomplete measurements.

### Scenario 2 — Thorough attribution

- **Starting state**: The fast view shows a large unknown or slow category.
- **User action**: The operator requests a thorough storage scan.
- **Expected outcome**: Sandbox measures expensive categories with visible
  progress and bounded per-category work, reconciles managed resources against
  live use, and ranks the largest owners and reclaimable candidates without
  changing the host.

### Scenario 3 — Review safe cache cleanup

- **Starting state**: Unused images, build data, stopped temporary containers,
  download cache, expired job artifacts, or other disposable caches consume
  space.
- **User action**: The operator requests a cleanup plan.
- **Expected outcome**: The plan names every cleanup class, its scope and
  estimated reclaimable size, explains exclusions, and performs no mutation.

### Scenario 4 — Execute reviewed cache cleanup

- **Starting state**: The operator has reviewed a current cleanup plan.
- **User action**: The operator explicitly confirms that plan.
- **Expected outcome**: Sandbox removes only the listed safe cache resources,
  reports actual reclaimed space and per-item outcomes, and confirms that
  active containers and persistent volumes were preserved.

### Scenario 5 — Remove stale managed resources

- **Starting state**: A thorough scan finds unmounted worktrees or volumes that
  are no longer referenced by any Sandbox registry entry, live container,
  retained job, backup, or permanent host.
- **User action**: The operator requests a stale-resource cleanup plan and then
  separately confirms it.
- **Expected outcome**: Only resources with positive Sandbox ownership and
  non-use evidence are eligible. Ambiguous, unmanaged, mounted, or permanent
  resources remain excluded and are reported for human review.

### Scenario 6 — Accounting is slow or unavailable

- **Starting state**: A storage backend hangs, privileges are insufficient, or
  a remote becomes unreachable.
- **User action**: The operator requests monitoring or cleanup.
- **Expected outcome**: Monitoring returns a partial result with explicit
  errors and confidence. Cleanup refuses to act on unverified categories and
  does not reinterpret missing evidence as safe-to-delete.

## Proposed Product Behavior

- Resource monitoring is read-only by default and supports both the current
  machine and an explicitly named remote.
- The primary view reports host capacity and category totals, followed by
  ranked resources with owner, lifecycle state, age where known, measured size,
  reclaimable estimate, and evidence quality.
- Managed coverage includes Sandbox home deployment copies and runtime state,
  instance and workspace data, Docker volumes and cache classes, job artifacts,
  download caches, snapshots/backups and staging, and material host logs or
  package caches. Unmanaged host categories are visible but never made eligible
  for cleanup.
- Host package caches and logs are monitoring-only unless positive Sandbox
  ownership makes a specific resource eligible under the same cleanup
  safeguards as other managed data.
- Fast monitoring favors responsiveness and may use recent validated
  measurements. Thorough monitoring performs deeper reconciliation, shows
  progress, and records which categories completed, timed out, or changed
  during the scan.
- Cleanup has two intentionally separate scopes:
  - **Safe cache cleanup** covers disposable cache classes that are unused at
    execution time.
  - **Stale managed-resource cleanup** covers persistent-looking worktrees or
    volumes only after positive ownership and non-use checks.
- Every mutating cleanup starts from a fresh plan, requires explicit
  confirmation, revalidates liveness immediately before each deletion, and
  skips anything whose evidence changed.
- Named volumes are never treated as ordinary cache. They can appear only in a
  stale managed-resource plan and only when Sandbox ownership and non-use are
  both proven.
- Monitoring and cleanup results have consistent human-readable and structured
  forms. MCP clients receive the same scope, protections, partial-result
  semantics, and outcome details as CLI users.
- Each cleanup run reports planned bytes, actual bytes reclaimed, skipped and
  failed items, remaining host capacity, and enough non-secret identifiers to
  explain the decision later.

## Constraints and Dependencies

- Sandbox's own registry and lifecycle services remain authoritative for
  managed ownership; live runtime state is authoritative for current use.
- All runtime-touching behavior must be available through Sandbox rather than
  requiring operators to substitute raw Docker or ad hoc SSH commands.
- Remote monitoring depends on the existing named-remote connection,
  provisioned Sandbox runtime, and sufficient non-interactive privileges for
  the requested measurements.
- Storage walks must be bounded and cancellable because dependency trees and
  engine accounting can take minutes or hang.
- The product must tolerate concurrent instance, job, deployment, and backup
  activity; cleanup eligibility must be rechecked at execution time.
- Secret values, credentials, file contents, and sensitive mount options must
  never appear in reports or audit output.
- Existing local and remote instances, permanent hosts, custom deployments, and
  non-Sandbox Docker workloads must remain compatible and protected.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Default action | Monitoring and planning are read-only; mutation requires explicit confirmation | Storage inspection should be safe to run routinely | User request plus destructive-action policy |
| Cleanup scopes | Separate safe caches from stale persistent-looking managed resources | A named volume or worktree must not be implied safe merely because it is unused | Existing safety and ownership policy |
| Persistent volumes | Excluded from ordinary cache cleanup | Application state must survive cache reclamation | User's prior instruction to preserve permanent hosts and active storage |
| Ownership ambiguity | Report but do not delete | Missing or conflicting evidence is not permission | Existing least-privilege policy |
| Remote scope | Require an explicitly named remote | Prevent cleanup from targeting an unintended host | Existing named-remote workflow |
| Scan depth | Fast bounded status plus explicit thorough mode | Operators need both routine visibility and evidence-grade attribution | User explicitly requested monitoring and a thorough check |
| Surfaces | CLI-first with MCP parity and structured output | Matches Sandbox operation policy and automation needs | Repository constitution and agent guide |
| Cleanup reporting | Show plan, actual reclaimed bytes, skips, failures, and final capacity | Operators need verifiable outcomes rather than a successful exit code alone | Operational need demonstrated by the live audit |

## Open Questions

- None.

## Acceptance Outcomes

- On a host with managed resources, a single resource-status request reports
  total, used, available, attributed, unknown, and reclaimable bytes without
  changing host state.
- A thorough scan classifies every discovered managed deployment worktree and
  named volume as active, retained, stale candidate, or unverified; it gives an
  owner or an explicit ownership gap and explicitly reports resources it could
  not measure.
- The report never silently presents a partial sum as the host total; timed-out
  or unavailable categories are named and reduce the displayed confidence.
- A cleanup plan causes zero resource deletions and lists the exact eligible
  scope, exclusions, and estimated bytes.
- Cleanup without explicit confirmation fails safely and reclaims zero bytes.
- Confirmed safe cache cleanup preserves all running containers and all named
  persistent volumes while reporting the actual change in host capacity.
- Confirmed stale-resource cleanup skips a candidate if it becomes mounted,
  referenced, or otherwise active after planning.
- An unmanaged Docker volume or filesystem path is never automatically deleted,
  even when it is old or dangling.
- Equivalent CLI and MCP requests produce materially equivalent scope,
  classifications, safeguards, and structured outcome fields.
- Monitoring and cleanup remain bounded when Docker accounting or a filesystem
  walk hangs, and the operator receives a useful partial result rather than an
  indefinite wait.

## Risks and Assumptions

- **Risk**: Filesystem totals may change during a long scan, so category sums
  may not exactly equal the final capacity reading.
- **Risk**: Private mounts, shared layers, hard links, copy-on-write storage,
  and sparse files can cause double counting or misleading apparent sizes.
- **Risk**: Historical worktrees and volumes may predate reliable ownership
  metadata, forcing conservative classification and less reclamation.
- **Risk**: A remote can disconnect after planning but before cleanup completes,
  producing a partially completed run.
- **Assumption**: Sandbox can reconcile its registry and live runtime state
  without treating names alone as proof of ownership.
- **Assumption**: Operators prefer missed cleanup opportunities over deletion
  of ambiguous or permanent data.
- **Assumption**: Existing remote privileges can perform read-only host and
  engine measurements; missing privileges will be reported rather than
  bypassed.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
