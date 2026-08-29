# Product Requirements Draft: Remote Host Swap and Memory Monitor Commands

**Status**: Refined

**Created**: 2026-08-29

**Last Refined**: 2026-08-29

**Input**: "Make the verified remote swap lifecycle and aggregate memory monitoring reusable Sandbox CLI commands."

**Drafting Model**: `gpt-5.6-luna` Max (actual orchestrator fallback)

**Final Validation**: PASS — gpt-5.6-sol High

**Validated On**: 2026-08-29

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Sandbox operators can observe aggregate RAM through remote diagnostics, but they
cannot inspect, enable, disable, or monitor host swap through a supported Sandbox
command. The current escape hatch requires reconstructing privileged host commands,
persistence, rollback, scheduling, safe log fields, and rotation by hand. That is
error-prone, difficult to audit, and contrary to the product goal of turning repeated
host operations into deterministic capabilities.

The configured remote recently reached 96.95% RAM use while running bursty container,
build, and CI workloads. A manually reviewed setup added a 4 GiB emergency swap buffer,
low swap preference, and a five-minute aggregate monitor. The product now needs to make
that verified operator outcome repeatable without requiring raw SSH.

## Users and Desired Outcomes

- **Sandbox operator**: Determine whether a registered remote has usable swap, how much
  is active, whether it is configured for boot or separately reboot-verified, and whether
  monitoring is healthy.
- **Sandbox operator**: Safely enable or remove a bounded emergency swap buffer through
  an explicit reviewed action with truthful rollback evidence.
- **Developer or incident responder**: Read bounded, secret-free memory-pressure history
  without learning process arguments, environment values, or private paths.
- **Automation client**: Receive stable structured results that distinguish planned,
  applied, already-current, refused, partial, failed, and rollback outcomes.

## Goals

- Replace the manual privileged swap setup with read-only planning plus an explicitly
  confirmed, idempotent remote lifecycle.
- Make the verified default policy reusable: a 4 GiB emergency buffer, global host
  `vm.swappiness` 15, aggregate sampling every five minutes, and bounded weekly history.
- Expose bounded swap and monitor status and bounded recent log reads without mutation.
- Fail closed on unsupported platforms, insufficient disk or RAM, ambiguous ownership,
  concurrent changes, unsafe existing files, remote revision mismatch, or incomplete
  evidence.
- Verify restoration when an apply or removal cannot finish; otherwise retain explicit
  incomplete-rollback evidence and block unrelated mutation.

## Non-Goals

- Automatically enabling swap because RAM use crosses a threshold.
- Treating swap as a replacement for right-sizing RAM or constraining runaway jobs.
- Recording per-process names, command lines, arguments, environments, paths, or secrets.
- Installing an autonomous swap-sizing daemon or introducing a process- or cgroup-killing
  OOM policy.
- Managing swap inside individual containers or overriding their memory limits.
- Supporting non-Linux hosts, unregistered hosts, or arbitrary swap partitions in the
  first version.
- Rebooting a remote or claiming reboot persistence without a separately authorized
  reboot acceptance run.
- Exposing an unrestricted host-command or file-writing surface.

## Product Scenarios

### Scenario 1 — Inspect before changing anything

- **Starting state**: An operator has a registered reachable Linux remote.
- **User action**: Request swap and memory-monitor status.
- **Expected outcome**: Sandbox reports aggregate RAM, swap capacity and use, persistent
  configuration state, configured preference, monitoring state, log freshness, and any
  unknown evidence without changing the host. It distinguishes host swap availability
  from container eligibility when cgroup swap-limit evidence is available.

### Scenario 2 — Review and enable the safe default

- **Starting state**: The remote has no active or persistent swap and passes safety checks.
- **User action**: Request an enable plan, review it, then confirm the exact plan.
- **Expected outcome**: Sandbox activates the bounded buffer and aggregate monitor,
  verifies every required state, records a secret-free receipt, and reports the next
  scheduled sample. Repeating the same confirmed intent returns already-current.

### Scenario 3 — Read pressure history

- **Starting state**: Monitoring is active and has retained samples.
- **User action**: Request a bounded recent log window.
- **Expected outcome**: Sandbox returns only timestamped aggregate RAM, swap, and memory
  pressure fields, with completeness, range, and truncation evidence.

### Scenario 4 — Refuse an unsafe enable

- **Starting state**: Disk capacity is too low, a conflicting swap file exists, ownership
  is ambiguous, or the remote changes after planning.
- **User action**: Attempt to enable the feature.
- **Expected outcome**: Sandbox changes nothing and returns a typed refusal with a safe
  recovery hint. It never adopts or overwrites unknown host state.

### Scenario 5 — Disable safely

- **Starting state**: Sandbox owns the active swap and monitor configuration.
- **User action**: Review and confirm removal.
- **Expected outcome**: Sandbox first proves current RAM can absorb swapped pages, then
  removes only the owned configuration. If safe removal cannot be proven, it refuses and
  leaves the working setup intact.

### Scenario 6 — Recover from a partial operation

- **Starting state**: A protected operation is interrupted or one verification step fails.
- **User action**: Re-run status or the same plan identity.
- **Expected outcome**: Sandbox reports the exact partial state and either completes the
  same replay-safe intent or verifies restoration of the prior state. It reports
  `rollback_complete` only after that proof; otherwise it retains `rollback_incomplete`,
  blocks unrelated mutation, and never reports success from ambiguous output.

## Proposed Product Behavior

- Status, planning, and bounded log reads are read-only and require no confirmation.
- Enable and disable are protected operations. They require a current reviewed plan and
  explicit confirmation; confirmation without a matching plan is refused.
- The default enable policy matches the accepted live setup: 4 GiB, global host
  `vm.swappiness` 15, five-minute aggregate sampling, and current history plus eight
  weekly historical files. Explicit size overrides are limited to 1–8 GiB, must not
  exceed 50% of physical RAM or 10% of filesystem capacity, and must leave at least the
  larger of 10 GiB or 15% of filesystem capacity free. Every selected value and bound
  appears in the plan.
- Sandbox owns only configurations it created and can prove through an owner-safe receipt.
  Pre-existing or modified files remain visible but are never silently adopted.
- Logs contain aggregate counters only. Human and structured output are bounded and state
  when samples are missing, stale, malformed, or truncated.
- Status and log reads observe unmanaged swap, but enable and disable refuse when any
  active or persistent swap lacks a matching Sandbox ownership receipt.
- Removal is conservative: active swap is not removed unless available RAM exceeds
  current swap use plus the larger of 1 GiB or 10% of physical RAM and all owned
  persistence, preference, and monitoring state can be reconciled without drift.
- Monitoring is fresh through two configured intervals plus one minute. It warns after at
  least 512 MiB of swap remains used for three consecutive samples and reports memory
  pressure separately so cold-page use is not mislabeled as active thrashing.
- Retained monitor history is limited to the current file plus eight weekly historical
  files and 32 MiB total; rotation removes the oldest history before exceeding either
  bound.
- A remote implementation or protocol mismatch returns an actionable typed error rather
  than falling back to direct SSH.

## Constraints and Dependencies

- The existing registered-remote authority, authenticated control transport, revision
  evidence, and protected-operation confirmation model remain authoritative.
- Public command and structured-output changes require matching command manifests,
  documentation, tests, and remote revision evidence.
- Host mutations must be idempotent, ownership-scoped, race-aware, and rollback-capable.
- The feature must preserve enough free disk for host operation and must not allocate an
  unbounded percentage of capacity.
- Monitoring must remain lightweight under normal and memory-pressure conditions.
- Swap lifecycle uses already-present maintained Linux platform facilities; autonomous
  swap managers, OOM killers, and metrics exporters are not new mandatory dependencies.
- Host swap status must not be represented as proof that every container may use swap;
  per-cgroup limits remain observational and outside this feature's mutation authority.
- Live proof requires an authorized disposable or explicitly approved remote; local fakes
  prove contracts but not host behavior or reboot persistence.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Initial scope | Registered remote Linux hosts only | The request follows a real remote operation; arbitrary local host mutation would broaden authority. | User request and remote-only clarification |
| Default buffer | 4 GiB | Matches the reviewed live setup and is bounded for the observed 12 GiB host. | User-approved live operation |
| Default preference | Global host `vm.swappiness` 15, not swap-area priority 15 | Matches the accepted live configuration and keeps disk swap relatively unattractive; swap-area priority remains observed state. | User-approved live operation plus Linux control semantics |
| Monitoring cadence | Every five minutes | Captures bursts without high logging overhead. | User-approved live operation |
| Retention | Current log plus eight weekly historical files, capped at 32 MiB total | Keeps both age and byte history bounded. | Accepted live retention plus conservative operator bound |
| Mutation authority | Reviewed plan plus explicit confirmation | Host persistence and swap removal are consequential operations. | Repository policy |
| Existing state | Observe but never adopt or overwrite ambiguous files | Prevents ownership confusion and destructive takeover. | Repository policy |
| Log privacy | Aggregate counters only | Meets the need without exposing process or secret surfaces. | User-approved monitor design and repository policy |
| First-version swap type | One Sandbox-owned swap file | Keeps ownership and rollback deterministic; partitions and arbitrary paths remain out of scope. | Bounded product assumption |
| Monitor placement and Spec 043 coexistence | Separate remote-host memory monitor exposed within the existing resources command family | Keeps host memory sampling distinct from controller-side storage monitoring while preserving one resource-operations surface. | Existing Spec 043 boundary plus accepted live host monitor |
| Pre-existing swap | Status observes it; enable and disable refuse when any active or persistent swap is not receipt-owned by Sandbox | Avoids changing global preference or capacity around state Sandbox does not own. | Repository ownership policy and Sol High review |
| Enable safety bounds | 1–8 GiB; no more than 50% of RAM or 10% of filesystem capacity; preserve at least the larger of 10 GiB or 15% filesystem free | Makes allocation eligibility measurable while keeping the accepted 4 GiB default valid on the observed host. | Conservative operator policy reviewed against the live host |
| Disable headroom | Available RAM must exceed current swap use plus the larger of 1 GiB or 10% of RAM | Prevents swap removal when reclaimed pages could consume recovery headroom; an apply-time race still fails closed. | Linux removal semantics plus conservative operator policy |
| Monitor health and warning | Fresh within two intervals plus one minute; warn after at least 512 MiB is used for three consecutive samples; report pressure separately | Distinguishes a missed sample and one short page-out from sustained emergency-buffer use or active pressure. | Accepted cadence plus conservative observability policy |
| Required platform tools | Maintained native Linux swap, service, scheduling, and aggregate kernel telemetry facilities; no third-party manager | The target already provides the required primitives, while autonomous managers and OOM killers broaden dependency and authority. | Sol High open-source tool review |

## Open Questions

- None.

## Acceptance Outcomes

- An operator can obtain complete swap and monitor status from a healthy registered remote
  in one command without host mutation or direct SSH.
- An unconfirmed enable or disable changes zero host files, services, settings, or swap
  state and returns a stable protected-operation result.
- A confirmed safe-default enable reaches a verified active and persistent state, creates
  a fresh aggregate sample, and is idempotent on immediate replay.
- Enable refuses at every size, RAM-percentage, filesystem-percentage, and post-allocation
  reserve boundary without changing host state; valid values are shown in the reviewed
  plan before confirmation.
- Any interruption leaves either verified restored state or explicit partial-state
  evidence that the same replay-safe operation can reconcile.
- Every supported bounded log read omits process identity, arguments, environment values,
  arbitrary paths, and secret-like fields.
- Monitor execution completes within five seconds under normal host conditions, becomes
  stale only after two intervals plus one minute, warns after three consecutive samples at
  or above 512 MiB swap use, and retains no more than eight weekly historical files or
  32 MiB total.
- Disable refuses when available RAM does not exceed swap use plus the larger of 1 GiB or
  10% of RAM, and removes only a fully proven Sandbox-owned configuration when it succeeds.
- Status observes but never adopts unmanaged swap and never claims host swap availability
  proves an individual container can use it.
- An authorized live Linux acceptance demonstrates the documented status, refusal,
  application, replay, logging, and cleanup outcomes before release.

## Risks and Assumptions

- **Risk**: Disk-backed swap can hide sustained RAM shortage and increase latency. The
  product must surface sustained use as an operator warning rather than calling it healthy.
- **Risk**: Removing active swap can trigger severe reclaim or OOM. Removal must fail
  closed when headroom is not proven.
- **Risk**: Host files may be concurrently changed outside Sandbox. Plan identity,
  ownership, and pre-apply observations must detect drift.
- **Risk**: An older remote service may not understand the new operation. Revision mismatch
  must be explicit and must not trigger an SSH fallback.
- **Assumption**: The target exposes ordinary Linux swap, memory-pressure, service, and log
  facilities required for complete evidence; missing facilities produce unsupported or
  partial results rather than guessed success.
- **Assumption**: The default 4 GiB policy remains an emergency buffer for the observed
  class of host, not a universal RAM-sizing rule; operators review overrides per host.

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

**Readiness**: READY FOR SPECKIT

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
