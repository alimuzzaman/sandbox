# Product Requirements Draft: Remote Storage Reservation

**Status**: Ready

**Created**: 2026-10-13

**Last Refined**: 2026-10-13

**Input**: "Spec 066 remote storage reservation: protect hosted production on a shared remote from disk exhaustion caused by test workloads (Phase A software controls, Phase B hard isolation gated on approval)."

**Drafting Model**: Claude Opus 5.5 (harness default effort)

**Final Validation**: `PASS` — GPT-6.1-Sol (Codex, reasoning high), round 3

**Validated On**: 2026-10-13

**Artifact Owner**: `speckit.prd.refine`

**Next Stage**: `speckit.specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

A single remote (xcloud-london: one 197 GB root filesystem shared by the
container runtime, test workspaces and hosted production) filled to 100% three
times (2026-10-10, 2026-10-12 06:25 UTC and 15:29 UTC). Test activity alone
caused it: about 45 retained remote test workspaces (2-3 GB of source each),
their idle service stacks (databases idle 11-40 hours, dependency and browser
volumes), and about 9.5 GB of build cache.

Hosted production (`sandbox-host-lenzora-production`) took the damage: its
database could not write its lock file and went through crash recovery, two
workers exhausted their restart allowance and stayed down for about 20 hours,
a monitor exited, dependents went unhealthy, and the runtime needed manual
orphan cleanup. Test submissions meanwhile failed with an opaque
`supervisor_launch_failed` instead of saying the disk was full.

Existing cleanup did not prevent this: a reap run (2026-10-11) removed the
source directories of workspaces it judged live but left their service stacks
running (feedback e31fe787), and reclaim skips any workspace that still has an
`active_job` marker, however stale.

Production uptime on shared remotes must not depend on someone noticing test
debris in time.

## Users and Desired Outcomes

- **Hosted-site owner**: test submissions that would eat into a configured
  free-space floor are refused (an admitted run that outgrows its estimate can
  still overrun it until Phase B isolation), and production
  recovers without manual container surgery once space is back.
- **Developer submitting remote tests or jobs**: a submission that would eat
  into the reserve is refused up front with a clear reason and numbers, not a
  late opaque failure.
- **Remote operator**: finished test workspaces disappear completely (stack,
  data and source) on a predictable schedule, and recovery of stopped services
  is a single safe, previewable command.

## Goals

- **Phase A (software only, no production or daemon change):**
  1. A per-remote storage floor protected by admission refusal (default 30 GB
     on xcloud-london); admitted runs that outgrow their estimate may still
     overrun it.
  2. Admission refuses a remote test or job submission before any transfer when
     current free space, minus the estimates of work already admitted and still
     running, minus this submission's estimate, would drop below the floor,
     reporting free space, in-flight reservations, floor and estimate. This is
     admission protection, not a hard guarantee: a run that outgrows its
     estimate is not stopped mid-flight (hard limits are Phase B).
  3. Cleanup and reap remove a workspace completely: its service stack
     (containers, its own volumes, its networks) and then its source.
  4. Finished workspaces idle beyond a threshold (default 6 hours) become
     eligible for reap even if a stale job marker remains, but only on
     affirmative evidence that no job is running or queued for them; a
     workspace with running, queued or undeterminable job state never is.
  5. An operator command restarts hosted services that stopped after exhausting
     their restart allowance, only while current free space is strictly above
     the floor; preview by default, action only on confirmation, limited to the
     eligible services the preview listed.
- **Phase B (hard isolation, plan only):** a reviewed, reversible rollout plan
  comparing (a) a fixed-size (~100 GB) preallocated filesystem holding test
  workspace source and test volumes, (b) a capped container build cache, and
  (c) moving production data to its own reserved storage. Phase B delivers the
  plan only; every production or container-daemon change it describes requires
  the user's explicit approval and a maintenance window before execution.

## Non-Goals

- Disk usage alerts and notifications (spec 065).
- Changing, moving, restarting or pruning lenzora production containers or
  volumes without the user's go-ahead (Phase A's heal command acts only on the
  operator's explicit confirmation, case by case).
- Global prune operations (build cache prune, volume prune, image prune -a).
- Quotas for hosted sites against each other.
- Local (non-remote) workspaces.

## Product Scenarios

### Scenario 1 — Submission within budget

- **Starting state**: remote has 80 GB free, floor 30 GB, recent workspaces of
  the same repository used about 3 GB.
- **User action**: submits a remote test run.
- **Expected outcome**: accepted and runs as today.

### Scenario 2 — Submission would breach the floor

- **Starting state**: 32 GB free, floor 30 GB, estimate 3 GB.
- **User action**: submits a remote test run.
- **Expected outcome**: refused before any upload with `remote_storage_floor`,
  showing free, floor and estimate, and pointing at reclaim; no remote state
  changes.

### Scenario 3 — Free space unknown

- **Starting state**: the remote's free space cannot be measured.
- **User action**: submits.
- **Expected outcome**: refused as a storage-floor refusal that says the
  measurement failed (fail closed), not accepted blindly.

### Scenario 3b — Concurrent submissions

- **Starting state**: 35 GB free, floor 30 GB, estimate 3 GB each.
- **User action**: two submissions arrive together.
- **Expected outcome**: the first is admitted and its estimate is reserved;
  the second is refused because 35 - 3 - 3 < 30.

### Scenario 4 — Stale finished workspace

- **Starting state**: a workspace whose last job finished 8 hours ago still
  carries a job marker and a running database stack.
- **User action**: scheduled or manual reap.
- **Expected outcome**: when reap is run manually, or by the existing opt-in
  scheduled routine once enabled, the stack, its exclusively owned volumes and
  networks, then source are removed; the deletion record lists each item.

### Scenario 4b — Job state unknown, queued, or starting

- **Starting state**: an idle workspace whose job state cannot be read, has a
  queued job, or receives a new submission while reap is selecting it.
- **Expected outcome**: the workspace is kept; admission and reap never act on
  the same workspace at once.

### Scenario 4c — Shared or foreign resource

- **Starting state**: a volume or network attached to the workspace is also
  used by another project.
- **Expected outcome**: that resource is kept and reported; the rest of the
  workspace's exclusive resources are removed.

### Scenario 4d — Teardown fails or is interrupted

- **Starting state**: stack teardown fails, the run is interrupted, or the
  deletion record cannot be written.
- **Expected outcome**: if the record cannot be written nothing is deleted; if
  teardown fails the source is kept so the workspace can still be identified;
  the result is reported as partial and a later reap completes it safely.

### Scenario 5 — Running job

- **Starting state**: a workspace has a job still executing.
- **User action**: reap.
- **Expected outcome**: untouched regardless of age.

### Scenario 6 — Hosted production stack never matched

- **Starting state**: reap runs on a remote hosting lenzora production.
- **Expected outcome**: no hosted (`sb host`) project, volume or network is
  selected, even if names resemble a test workspace.

### Scenario 7 — Heal after space returns

- **Starting state**: two production workers exited after their restart limit;
  free space is now above the floor.
- **User action**: runs heal (preview), then heal with confirmation.
- **Expected outcome**: preview lists exactly those services and restarts
  nothing; confirmation re-checks free space and eligibility, restarts only
  those services, leaves every other service untouched, and reports their
  resulting state.

### Scenario 8 — Heal while still short of space

- **Starting state**: free space at or below the floor, unmeasurable, or
  dropped below the floor between preview and confirmation.
- **User action**: heal with confirmation.
- **Expected outcome**: refused with free and floor; nothing restarted.

### Scenario 9 — Phase B proposal

- **Starting state**: Phase A is live.
- **User action**: asks for the isolation options.
- **Expected outcome**: receives the options with each step's downtime,
  rollback and approval point; nothing executes until approved.

## Proposed Product Behavior

- The floor is configured per remote, has a default, and is shown wherever the
  remote's readiness or resources are reported.
- The footprint estimate comes from recent workspaces of the same repository on
  that remote; with no history a conservative default applies and the refusal
  says the estimate is a default.
- Admission is checked once per submission before any transfer, alongside the
  existing readiness gate, using a fresh free-space measurement (never a cached
  readiness result) and counting reservations of admitted work still running.
- A run that outgrows its estimate is not killed; the next admission sees the
  real usage.
- Cleanup removes only resources positively owned by the workspace (by its
  project identity), never by name similarity, and records every deletion
  before performing it.
- Idle threshold is configurable. Reap eligibility needs affirmative evidence
  that the workspace has no running or queued job; unknown or conflicting
  evidence keeps the workspace. Only resources exclusively owned by the
  workspace are removed.
- Unattended idle reap runs only through the existing opt-in scheduled
  routine (disabled by default, existing locks honoured); this feature widens
  what that routine may remove, not when it is enabled.
- Teardown order is stack first, then source; source is kept if stack teardown
  fails, and replay is safe.
- Heal touches only hosted services in an exited state after restart
  exhaustion; it never recreates, pulls, rebuilds or deletes anything.
- Phase B deliverable is a plan document with options, downtime, rollback and
  approval point per step; no tooling or system change ships in Phase B.

## Constraints and Dependencies

- Remote changes take effect only after the remote runtime is migrated (gotcha
  23/28 in CLAUDE.md); protocol bumps follow spec 061 rules.
- Readiness gate (spec 063) is the existing admission point.
- Reclaim/reap semantics from specs 044/057 must stay compatible; deletion
  manifests stay the audit trail.
- No raw Docker or SSH workflows for users; everything via `sb`.
- Production data safety: no command in this feature deletes a hosted
  project's volumes.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Floor default | 30 GB on xcloud-london | Lenzora DB + logs headroom | User (lenzora thread) |
| Idle reap threshold | 6 hours default, configurable | Covers overnight runs without 40 h idle stacks | User |
| Unknown free space | Refuse (fail closed) | Protecting prod outranks test convenience; differs from readiness gate, which tolerates `unknown` rows | Accepted assumption (root), revisitable in clarify |
| Concurrent admission | Reserve admitted estimates until the job ends | Prevents two runs jointly breaching the floor | Root, follows from user's floor goal |
| Unattended idle reap | Via existing opt-in routine only | Keeps activation authority with the user | Existing policy (spec 057) |
| Heal mode | Dry run default, `--confirm` to act, floor-gated | Matches destroy/reclaim conventions | User |
| Phase B execution | Plan only; prod/daemon changes need explicit approval + window | Prod risk | User |
| Disk alerts | Out of scope | Owned by spec 065 | User |

## Open Questions

- None blocking. Phase B option choice is deferred to the user after Phase A
  ships, by design.

## Acceptance Outcomes

- With free space minus estimate below the floor, 100% of remote test/job
  submissions are refused before any byte is transferred, with free, floor and
  estimate shown.
- After a successfully completed reap, all exclusively owned workspace
  containers, volumes, networks and source are removed; shared or foreign
  resources are retained and reported; partial or failed teardown follows
  Scenario 4d.
- No workspace with a running job is ever reaped (verified across idle ages).
- Zero hosted-project resources appear in any reap or cleanup selection.
- Heal preview restarts nothing; confirmed heal restarts only the listed
  eligible services when free space is strictly above the floor, and none when
  free space is at/below the floor or unknown.
- Replaying the 2026-10-12 workspace population, admission refuses every
  submission that would have taken projected free space below the floor.
- Workspaces with unknown, queued or running job state, and shared resources,
  survive every reap; a failed or interrupted teardown leaves source in place
  and a later reap completes it.
- With the scheduled routine disabled nothing is reaped unattended; enabled, an
  eligible idle stack is removed.
- Phase B plan lists each step's downtime, rollback and approval point.

## Risks and Assumptions

- **Risk**: estimate too low lets a run push past the floor; mitigated by
  conservative default and floor margin.
- **Risk**: misidentifying ownership deletes a non-test volume; mitigated by
  positive project identity and pre-deletion manifest.
- **Risk**: heal restarts a service that keeps failing for a non-disk reason;
  heal reports resulting state and does not loop.
- **Assumption**: hosted projects and test workspaces carry distinguishable
  project identities on the remote; exclusive ownership is checked per
  resource, not inferred from identity alone.
- **Assumption**: free space of the filesystem holding the container data root
  is the right measure for a single-filesystem remote.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent readiness validation verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
