# Product Requirements Draft: Scheduled Safe Cleanup on a Remote

**Status**: Ready

**Created**: 2026-10-08

**Last Refined**: 2026-10-08

**Input**: "Feedback 8a3e8c35: a cadence-driven safe-tier cleanup that runs on the remote as a systemd timer installed through the authenticated control plane, bounded, manifested before deletion, opt-in, and never touching PROTECTED/LIVE data."

**Drafting Configuration**: Claude Opus 5.5 root drafting; no delegated drafting.

**Final Validation**: `PASS` — independent read-only review, Claude Fable 5.1

**Validated On**: 2026-10-08

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Remote hosts used for disposable workspaces fill up. An operator today keeps
them healthy by hand: `resources status`, `resources plan --tier safe`,
`workspace reap --dry-run`, then `resources cleanup --plan-id ... --confirm`.
Feedback 8a3e8c35 records one such run on 2026-10-07 that reclaimed 464.8 MB
and found 4.6 GB of orphaned workspace directories that had to be filtered by
hand.

The existing automation does not cover this:

- `sb resources schedule` installs a timer on the machine that runs `sb`, never
  on the monitored remote, and the timer only monitors.
- On macOS the schedule is review-only, because launchd cannot enforce the
  configured timeout. The operator's Mac therefore cannot run it at all.
- `auto_enabled` cleanup acts only at `critical_ratio` (5% free by default), so
  a host at 31% free never gets routine cleanup.

Per-tier reaping with exclusions (`workspace reap --tier`, `--exclude`,
`resources.reclaim_exclude`) shipped in 530fe3a. Safe candidates can therefore
be selected without hand-filtering, but nothing runs that selection on a cadence.

## Users and Desired Outcomes

- **Remote operator**: a remote's safe-tier waste is reclaimed on a regular
  cadence without logging in, and without their own laptop staying awake.
- **Project owner sharing the remote (for example Lenzora)**: their workspaces,
  live instances and protected data are never removed by the routine, and they
  can exclude their project by name.
- **Auditor / later reader**: every automated deletion can be traced to a run,
  a policy, and a manifest written before anything was removed.

## Goals

- One documented, confirmed command enables a recurring safe-tier cleanup on a
  named remote, and one disables it.
- The routine runs on the remote itself, independent of free-space pressure.
- Each run is bounded in time and writes the existing deletion manifest before
  any deletion.
- Excluded, PROTECTED and LIVE resources never appear as candidates.
- The operator can see whether the routine is enabled, when it last ran, what
  it reclaimed, and why a run refused.

## Non-Goals

- Cleanup tiers other than `safe` (`tmp`, `all`) on a schedule.
- Running the routine on the operator's local machine, or making launchd
  enforce timeouts.
- Changing what the `safe` tier classifies as eligible.
- Pressure-triggered cleanup (`auto_enabled` at `critical_ratio`) stays as it
  is.
- Scheduling for hosts that are not registered, provisioned Sandbox remotes.

## Product Scenarios

### Scenario 1 — Enable the routine

- **Starting state**: a provisioned remote whose control-plane runtime matches
  the local revision; no routine is enabled.
- **User action**: the operator runs the enable command with explicit
  confirmation, optionally naming a cadence and exclusions.
- **Expected outcome**: the remote reports the routine enabled, with its
  cadence, timeout, exclusions and next run time. Nothing is deleted at enable
  time.

### Scenario 2 — A routine run reclaims safe waste

- **Starting state**: the routine is enabled; the remote has orphaned
  workspace directories and their workspace-scoped package volumes, plus one
  STOPPED workspace, one excluded project and one live instance.
- **User action**: none; the cadence fires.
- **Expected outcome**: a manifest listing only the eligible safe candidates is
  written first, those candidates are removed, and the run record shows the
  bytes reclaimed. The STOPPED workspace and the live instance are untouched.
  The excluded project appears only as skipped (`excluded_by_request`), never
  as a candidate or in a manifest intent.

### Scenario 3 — Inspect the routine

- **User action**: the operator asks for the routine's status on the remote.
- **Expected outcome**: enabled or disabled, cadence, exclusions, last run
  time, last outcome (reclaimed, nothing to do, refused, timed out), bytes
  reclaimed, the runtime revision the routine runs under, and the manifest
  reference when the run attempted any removal — without opening a shell on the host.

### Scenario 4 — Disable the routine

- **User action**: the operator runs the disable command with confirmation.
- **Expected outcome**: no further runs fire; past run records and manifests
  remain readable.

### Negative scenarios

- **Runtime mismatch**: the remote's installed runtime differs from the local
  revision. Enable refuses and names the mismatch; no timer is changed.
- **A run exceeds its time bound**: it is stopped, records `timed_out`, and
  deletes nothing after the bound. A partly applied run lists what it removed.
  The next scheduled run starts a fresh plan and does not resume the
  interrupted one; anything already removed is reported `already_absent`.
- **Overlap**: a run fires while a previous run, an operator-initiated cleanup
  (local or through the control plane), a pressure-triggered automatic
  cleanup, or a host apply is still working on the remote. The new run skips
  and records `skipped: busy`; it never runs concurrently with another cleanup.
- **Inventory incomplete**: the probe cannot classify every resource
  (for example a capacity-only or partial probe). The run deletes nothing and
  records the refusal.
- **Unknown or invalid exclusion**: an exclusion that cannot be parsed refuses
  enable; it is never silently ignored.
- **Remote unreachable from the operator**: the routine keeps running on the
  remote; status reports the last known record once reachable.
- **Non-systemd host**: enable refuses with a typed reason.

## Proposed Product Behavior

- The routine is opt-in per remote and off by default.
- Enabling and disabling are protected operations that require explicit
  confirmation, consistent with existing schedule activation.
- Each run is one `safe`-tier reclamation pass: the same selection
  `workspace reap --tier safe` makes, including workspaces past their TTL.
  There is no separate second pass.
- The routine runs only the `safe` tier, applies the exclusions recorded with
  the routine at enable time plus `resources.reclaim_exclude` from the remote's
  own configuration (the operator's local configuration has no effect on a
  remote run), and reuses the same candidate classification and
  deletion manifest as an operator-run safe cleanup.
- Each run has a configured time bound that the remote's init system enforces.
- Run outcomes are recorded on the remote in a bounded history the operator can
  read through the control plane.
- Exclusions are name globs matched against workspace and deployment entry
  names (for example `lenzora*`), the same grammar as `workspace reap
  --exclude`. Status shows the merged effective list.
- Manifest records from the routine carry a trigger value distinct from
  manual, reap and pressure-triggered runs.

## Constraints and Dependencies

- Remote actions go through the authenticated control plane; no raw SSH path.
- The remote must run the installed runtime revision that contains this
  feature (`sb remote service migrate`). Installing on a remote shared with an
  active deploy is coordinated with its owner first; that coordination is
  operational, not part of the product.
- Deletion safety rules in CLAUDE.md gotcha 23 hold: never `docker volume
  prune`, only Sandbox-owned eligible resources, manifest before deletion.
- PROTECTED and LIVE classes and registry/job evidence used by the classifier
  are authoritative; the routine never overrides them.
- Linux remotes with systemd only.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Where the routine runs | On the remote, under its init system | The operator machine sleeps and macOS cannot enforce timeouts | Feedback 8a3e8c35 ask |
| Tier | `safe` only | Lowest-risk tier; others need review | Feedback 8a3e8c35 ask |
| Default state | Off; explicit confirmed enable | Matches existing schedule activation policy | Existing policy (resource-monitoring.md) |
| Trigger | Cadence, independent of free space | Pressure trigger already exists and never fires at normal fill | Feedback 8a3e8c35 |
| Default cadence | Daily | Matches disposable-workspace churn at low risk | User, 2026-10-08 |
| TTL expiry in a run | Yes, `workspace reap` safe tier runs in the same routine | Covers the orphaned workspace directories the feedback found | User, 2026-10-08 |
| Default time bound | Inherit `schedule_timeout` (30 min) unless overridden at enable | Existing key already governs scheduled runs | Existing policy (resource-monitoring.md) |
| Run history | Last 30 run records kept on the remote | Bounded by count; manifests follow existing manifest rules | User, 2026-10-08 |

## Open Questions

- None.

## Acceptance Outcomes

- One documented enable command, run once with confirmation, results in a
  routine that fires on the remote at the configured cadence with no further
  operator action.
- Across a run on a host seeded with eligible, excluded, PROTECTED and LIVE
  resources, 100% of removed items appear in a manifest written before removal,
  and 0 excluded, PROTECTED or LIVE items appear as candidates, manifest
  intents, or removals.
- A run forced past its time bound is stopped within the bound plus at most
  60 seconds and records `timed_out`.
- Status shows enabled state, cadence, exclusions, last outcome and bytes
  reclaimed in one command, with no shell access to the host.
- After disable, no run fires for two cadence periods, verified with a short
  test cadence, and the host has no remaining timer for the routine.

## Risks and Assumptions

- **Risk**: a classifier mistake becomes an unattended deletion. Mitigated by
  safe-only scope and refusal on incomplete inventory, but not eliminated.
- **Risk**: installing the feature requires a remote runtime migrate, which can
  disrupt other users' deploys pinned to an exact revision.
- **Risk**: mutual exclusion between cleanups is client-side only today; the
  routine needs host-side exclusion shared with operator and
  pressure-triggered cleanups.
- **Assumption**: the routine runs the remote's installed runtime, so a
  classifier fix reaches it only after `sb remote service migrate`.
- **Assumption**: the safe-tier classifier and `reclaim_exclude` from 530fe3a
  are correct for unattended use.
- **Assumption**: target remotes run systemd with user lingering, as the
  control-plane service already requires.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent readiness review verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
