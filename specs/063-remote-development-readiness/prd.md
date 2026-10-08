# Product Requirements Draft: Remote Development Execution Readiness

**Status**: Refined

**Created**: 2026-10-08

**Last Refined**: 2026-10-09

**Input**: "A project's declared remote for tests, exec and CI must be usable, or the project must be told exactly why and what maintenance would fix it, before a job is submitted: network capacity that never needs a daemon restart on a host serving production, one read-only readiness check per declared remote, typed refusal for a retired remote, and no silent fallback to local"

**Drafting Configuration**: Claude Fable 5.1 root drafting under delegated product authority (user, 2026-10-08); evidence from the feedback backlog, `docs/remote-hosting.md` (Docker network capacity admission), `docs/remote-job-runtime.md` (workspace ownership repair), and `origin/latest` commit `4ece30f`. No independent readiness review has run.

**Final Validation**: `PENDING` — independent readiness review

**Validated On**: N/A

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Sandbox sells two things on a remote: hosting a project's targets, and running
the project's development work (tests, type checks, E2E, CI, exec) in
disposable workspaces next to them. The first is in daily use. The second
stopped working for every project on 2026-10-06 when `scaleway-sandbox` was
retired and `xcloud-london` took its hosting, and it has not come back.

- `xcloud-london` rejects every test and exec job with
  `docker_network_capacity_unavailable`: the migrated host has no Docker
  address pools configured (`current_pool_count 0`, `status planned`,
  `restart_required true`) and 64 running containers. The only remedy the
  pool plan offers is a daemon restart, which would restart Lenzora production
  and development, so no agent can apply it, and the operator has not either
  (feedback `cef740dd`, high; `5598f2d0`).
- The project's declared test remote is still `scaleway-sandbox` in
  `sandbox.config.json`, which `sb remote list` no longer knows; the
  submission fails with `unknown_remote` and there is no remote test target at
  all (feedback `cebec97a`).
- Before the retirement, remote workspace materialization failed `EACCES` on
  a root-owned `node_modules` and `ensure --remote` reported ready while `exec
  --remote` found no instance (feedback `b7451117`; the ownership repair and
  the ensure/exec handoff have since shipped, but nothing checks them before
  a job is submitted).
- `exec --remote` returned exit 0 with a job id while the job still ran
  (feedback resolved by `4ece30f`).

Every one of these was discovered by submitting a job and reading its refusal,
then by falling back to serialized local runs. The capacity admission is
correct to refuse: it must not stage a tree onto a host that cannot give it a
network. What is wrong is that the only way to restore capacity is a
production-affecting restart, and that nothing tells a project ahead of time
that its declared remote cannot serve it. The cost is that remote development
execution, the capability spec 032 and 033 spent 240 tasks on, is unavailable
on the one remote the operator runs, and agents silently do less testing.

## Users and Desired Outcomes

- **Agent running a project's tests remotely**: a submission to the declared
  remote is accepted, or refused before any transfer with the exact readiness
  gap and the maintenance that would close it; never with a generic capacity
  code and no path.
- **Operator of a host that serves production**: can restore development
  network capacity without restarting the daemon and without touching hosted
  targets, and can see when a daemon-level change would be required and what
  it would affect.
- **Project owner whose declared remote was retired**: is told the declared
  name is unregistered, which registered remotes exist, and how to re-point
  the declaration; nothing runs locally by surprise.
- **Agent deciding where to run**: gets one read-only readiness answer per
  declared remote (registered, reachable, runtime compatible, network
  capacity, workspace ownership, instance handoff) and chooses local
  explicitly when the remote is not ready.
- **Reviewer**: can see, per remote, the Sandbox-owned network ranges, what is
  allocated to which workspace, and when capacity was last proven.

## Goals

- Development network capacity on a remote never depends on a Docker daemon
  restart while the host serves hosted targets. Sandbox allocates explicit
  subnets for the networks it creates from a Sandbox-owned range recorded in
  the remote record; daemon default address pools are an optimization, not a
  prerequisite.
- A daemon-level pool change remains a planned maintenance operation: it is
  refused while hosted targets run unless explicitly scheduled with
  confirmation and a plan that names every target it would restart.
- One read-only readiness check per declared remote covers registration,
  reachability, runtime compatibility (feature 061's verdict), network
  capacity, workspace ownership repairability, and the ensure-to-exec instance
  handoff. It runs on request, in `sb doctor`, and before every remote
  submission, with the same result shape.
- A declaration that names an unregistered remote yields a typed refusal
  listing registered remotes; re-pointing is an explicit, supported change to
  the project declaration.
- Fallback to local execution is the caller's decision, never automatic; a
  refused remote submission never starts a local run.
- Capacity, ranges and allocations are inspectable per remote, bounded and
  secret-free, and every allocation is attributable to a workspace or job.

## Non-Goals

- Changing the capacity admission's fail-closed rule or letting it retry or
  delete networks. It keeps refusing on missing evidence.
- Host-wide resource governance (CPU, memory, disk admission): feature 047.
- Hosting targets' own networks. Hosted Compose projects keep their current
  network behavior; this feature covers networks Sandbox creates for
  development workspaces, jobs, previews and CI cells.
- Multi-remote scheduling or picking a remote automatically for a project.
- Remote runtime compatibility rules (feature 061) and per-target hosting
  locks (feature 060); this feature consumes their verdicts.
- Replacing the retired remote's registration or re-provisioning a new remote.

## Product Scenarios

### Scenario 1 — Capacity without a daemon restart

- **Starting state**: `xcloud-london` has no daemon address pools, 64 running
  containers, and hosted production targets.
- **User action**: The operator assigns a Sandbox-owned development range to
  the remote with confirmation, then an agent submits a remote test job.
- **Expected outcome**: The range is recorded, no daemon restart happens, no
  hosted container restarts, and the job is accepted with a subnet allocated
  from the range. The readiness check reports capacity as proven with the
  measured usable count.

### Scenario 2 — Readiness before submission

- **Starting state**: A project declares `xcloud-london` as its test remote.
- **User action**: The agent runs the readiness check for the project's
  declared remote.
- **Expected outcome**: One bounded result with a row per readiness aspect
  (registration, reachability, runtime compatibility, capacity, ownership
  repair, instance handoff), each `ready`, `not_ready` with a reason, or
  `unknown` with the probe state, and for each `not_ready` row the supported
  maintenance command or declaration change.

### Scenario 3 — Declared remote retired (negative)

- **Starting state**: The project declares `scaleway-sandbox`; only
  `xcloud-london` is registered.
- **User action**: `sb test fast --remote scaleway-sandbox` or the readiness
  check.
- **Expected outcome**: A typed `declared_remote_unregistered` refusal naming
  the declared name and the registered remotes, and the supported way to
  re-point the declaration. No transfer, no local run.

### Scenario 4 — Daemon-level change refused while production runs (negative)

- **Starting state**: As in Scenario 1, and the operator asks the pool plan to
  configure daemon default pools.
- **User action**: Apply the plan.
- **Expected outcome**: Refused with the list of hosted targets the restart
  would affect and the scheduling command that would make it an explicit
  maintenance window. The Sandbox-owned range remedy is offered as the
  no-restart alternative.

### Scenario 5 — Range exhausted (negative)

- **Starting state**: The Sandbox-owned range is fully allocated to live
  workspaces.
- **User action**: A job is submitted.
- **Expected outcome**: Refused with `docker_network_subnet_exhausted`, the
  allocation table (workspace or job per subnet, age), and the supported
  release commands (`workspace release`, `workspace reap`). Nothing is deleted.

### Scenario 6 — Ownership repair is verified ahead

- **Starting state**: A previous job left root-owned files in a workspace's
  bind sources.
- **User action**: Readiness check, then submission.
- **Expected outcome**: Readiness reports ownership repair as `ready` after a
  read-only check that the repair path is available; the submission's
  materialization repairs or refuses with the exact entry and owner, as today,
  never a bare transport error.

### Scenario 7 — Ensure then exec on a remote

- **Starting state**: No remote instance for the project.
- **User action**: `sb ensure --remote NAME` then `sb exec --remote NAME`.
- **Expected outcome**: Ensure returns the instance record it created; exec
  uses it. The readiness check's handoff row is `ready` only if this round
  trip is proven on that remote.

### Scenario 8 — Caller chooses local explicitly

- **Starting state**: Readiness for the declared remote is `not_ready`.
- **User action**: The agent runs the tests with an explicit local selector.
- **Expected outcome**: The local run proceeds and its output states that the
  declared remote was not ready and why, so the evidence is not mistaken for
  a remote result.

### Scenario 9 — Old runtime without range support (negative)

- **Starting state**: A remote's installed runtime predates Sandbox-owned
  ranges.
- **User action**: Assign a range.
- **Expected outcome**: A typed limitation naming the migrate that is
  required; the daemon-pool path remains the documented alternative with its
  restart cost stated.

## Proposed Product Behavior

- A remote record may hold one or more Sandbox-owned development ranges with
  a per-network subnet size. Networks Sandbox creates for workspaces, jobs,
  previews and CI cells are created with explicit subnets allocated from those
  ranges and recorded against the owner. Allocation is exact and
  manifest-first in the style of host storage reclamation; release follows
  the owning workspace or job.
- The capacity admission counts usable subnets as the union of daemon-pool
  capacity (when configured and proven) and unallocated Sandbox-owned range
  capacity; its evidence rules are unchanged.
- The daemon-pool plan distinguishes "configure pools" (restart) from "assign
  a range" (no restart), states the hosted targets a restart would affect, and
  refuses a restart while any hosted target runs unless scheduled as a
  confirmed maintenance window.
- A readiness check for a declared remote is one read-only command and one
  MCP tool sharing a result shape; `sb doctor` includes it for every declared
  remote of the focused project; every remote submission runs the same check
  first and reports the first `not_ready` row as its refusal.
- A declared remote that is not registered is a typed refusal that lists
  registered remotes and the declaration change; no command infers a
  replacement remote.
- Local fallback is an explicit selector; a refused remote submission exits
  non-zero and runs nothing locally.
- Ranges, allocations, and last-proven capacity are listed per remote,
  bounded, attributable and secret-free.

## Constraints and Dependencies

- The Docker network capacity admission (`docs/remote-hosting.md`, "Docker
  network capacity admission") and its fail-closed codes are the baseline;
  this feature adds a capacity source and does not relax the admission.
- Spec 032 (remote job runtime) owns workspace materialization, ownership
  repair and job acceptance; spec 033 owns agent-aware sync. The readiness
  check consumes their supported read-only probes.
- Feature 061 supplies the runtime compatibility verdict; feature 060 supplies
  the hosted-target inventory that a restart plan must name. Until 060 exists,
  the restart plan uses the current hosting inventory.
- Remote-side range allocation takes effect only after a migrate to a runtime
  that supports it (Scenario 9).
- Hosted Compose projects on the same daemon may use daemon-default or
  declared subnets; Sandbox-owned ranges must not overlap any network the
  inventory can observe, and an overlap is a refusal at range assignment.
- Project declarations live in `sandbox.config.json`; re-pointing a declared
  remote is a declaration change the project owner makes, with the merge
  order (user-global, project, override) unchanged.
- Constitution and module boundaries: ranges and allocations are new state
  registered through explicit manifests; the readiness check is a shared
  service, with adapters owning runtime policy.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Capacity source | Sandbox-owned ranges with explicit per-network subnets; daemon pools optional | Daemon pool changes need a restart that stops production; explicit subnets need none | Fable decision (delegated by user), 2026-10-09 |
| Daemon restart | Refused while hosted targets run unless scheduled as a confirmed maintenance window naming affected targets | A restart on a production host is a maintenance event, not a capacity fix | Fable decision (delegated by user), 2026-10-09 |
| Readiness surface | One read-only check shared by CLI, MCP, doctor and pre-submission | Discovering unreadiness by submitting is the cost this feature removes | Fable decision (delegated by user), 2026-10-09 |
| Retired declared remote | Typed refusal with registered alternatives; re-pointing is an explicit declaration change | Inferring a replacement remote routes work somewhere the project did not declare | Fable decision (delegated by user), 2026-10-09 |
| Local fallback | Explicit only; refused remote submission runs nothing locally | Silent fallback hides that remote testing is broken and mislabels evidence | Fable decision (delegated by user), 2026-10-09 |
| Scope of networks | Sandbox-created development networks only; hosted projects unchanged | Hosting networks are owned by the hosted Compose declaration | Fable decision (delegated by user), 2026-10-09 |

## Open Questions

- None blocking. The independent readiness review should confirm whether a
  default Sandbox-owned range should be assigned at remote registration (so
  new remotes are ready without a second step) or only on explicit assignment.

## Acceptance Outcomes

- On a remote with zero daemon address pools and running hosted targets,
  assigning a range and submitting a remote test job completes with zero
  daemon restarts and zero hosted container restarts, verified by container
  start times before and after.
- The readiness check for a declared remote returns within its bound with
  every aspect row populated; a submission to a `not_ready` remote is refused
  with the same row as its reason and transfers zero bytes of source.
- A declaration naming an unregistered remote is refused with the registered
  remote list in 100% of submission paths (test, exec, E2E, CI) and the
  readiness check, and starts zero local runs.
- Applying a daemon-pool restart plan while a hosted target runs is refused
  and names every affected target; the same plan under a confirmed
  maintenance window proceeds.
- With a range fully allocated, a submission is refused with the allocation
  table and no network is deleted; after `workspace release` of one
  allocation, the next submission is accepted.
- Ranges and allocations listed for a remote contain no secret-shaped values
  and are bounded in count and bytes.

## Risks and Assumptions

- **Risk**: Explicit subnets collide with networks the inventory cannot
  observe (foreign networks created outside Sandbox). Mitigation: range
  assignment refuses on any observed overlap and reports partial inventory as
  unknown; a collision at network creation is a typed refusal, never a
  retry.
- **Risk**: Readiness checks add latency to every submission. Mitigation:
  bounded, read-only, with a short-lived proof the submission can reuse.
- **Risk**: A maintenance-window restart on a production host still restarts
  production. Mitigation: it is explicit, confirmed, and names targets; this
  feature does not make it safer, only visible and non-default.
- **Assumption**: The Docker daemon on the operator's remotes accepts explicit
  `--subnet` network creation without daemon pool configuration; this is
  standard Docker behavior.
- **Assumption**: The operator will assign a range on `xcloud-london` once the
  capability exists; until then remote development stays unavailable there.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred (delegated
      decisions, recorded above; user may overturn).
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [ ] The latest independent readiness review verdict is `PASS`.

**Readiness**: `NOT READY`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
