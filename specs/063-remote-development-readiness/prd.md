# Product Requirements Draft: Remote Development Execution Readiness

**Status**: Validated

**Created**: 2026-10-08

**Last Refined**: 2026-10-09

**Input**: "A project's declared remote for tests, exec and CI must be usable, or the project must be told exactly why and what maintenance would fix it, before a job is submitted: network capacity that never needs a daemon restart on a host serving production, one read-only readiness check per declared remote, typed refusal for a retired remote, and no silent fallback to local"

**Drafting Configuration**: Claude Fable 5.1 root drafting under delegated product authority (user, 2026-10-08); evidence from the feedback backlog, `docs/remote-hosting.md` (Docker network capacity admission), `docs/remote-job-runtime.md` (workspace ownership repair), and `origin/latest` commit `4ece30f`. Revised 2026-10-09 by Claude Opus 5.5 root (speckit-refine) applying an independent Opus readiness review (verdict `REOPEN`) and Fable decisions delegated by the user; cited code re-verified read-only on `origin/latest`.

**Final Validation**: `PASS` — validation configuration: independent Opus 5.5 reviewer, read-only

**Validated On**: 2026-10-09

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
  `docker_network_capacity_unavailable` (evidence reason
  `missing_pool_evidence`). The migrated host's daemon configuration declares
  no address pools, so the capacity admission has no pool evidence to count.
  This is missing evidence, not exhausted capacity: Docker's built-in default
  pools still allocate networks on that host, but the admission cannot measure
  them and correctly refuses. The pool plan reports `current_pool_count 0`,
  `status planned`, `restart_required true` and 64 running containers, and its
  only remedy is a daemon restart, which would restart Lenzora production and
  development, so no agent can apply it and the operator has not either
  (feedback `cef740dd`, high; `5598f2d0`).
- A project's runtime target declaration still names `scaleway-sandbox`, which
  `sb remote list` no longer knows. The submission fails with the existing
  `unknown_remote` refusal, which points at `sb remote list` but neither lists
  the registered remotes nor says whether the name came from the caller or the
  project declaration (feedback `cebec97a`). Sandbox did not fall back to a
  local run; the agent chose serialized local checks itself.
- Before the retirement, remote workspace materialization failed `EACCES` on
  a root-owned `node_modules` and `ensure --remote` reported ready while `exec
  --remote` found no instance (feedback `b7451117`; the ownership repair and
  the ensure/exec handoff have since shipped, but nothing checks them before
  a job is submitted).
- `exec --remote` returned exit 0 with a job id while the job still ran
  (feedback resolved by `4ece30f`).

Every one of these was discovered by submitting a job and reading its refusal,
then by falling back to serialized local runs. The capacity admission is
correct to refuse: it must not stage a tree onto a host whose capacity it
cannot prove. What is wrong is that the only way to supply that proof is a
production-affecting restart, and that nothing tells a project ahead of time
that its declared remote cannot serve it. `sb doctor` already has a "Remote
targets" section, but it checks registered remotes only, not the remotes a
project declares. The cost is that remote development execution, the
capability spec 032 and 033 spent 240 tasks on, is unavailable on the one
remote the operator runs, and agents silently do less testing.

## Users and Desired Outcomes

- **Agent running a project's tests remotely**: a submission to the selected
  remote is accepted, or refused before any transfer with the exact readiness
  gap and the maintenance that would close it; never with a generic capacity
  code and no path.
- **Operator of a host that serves production**: can restore development
  network capacity without restarting the daemon and without touching hosted
  targets, and can see when a daemon-level change would be required and
  exactly which hosted targets and how many other running containers it
  would restart.
- **Project owner whose declared remote was retired**: is told the declared
  name is unregistered, which registered remotes exist, and how to re-point
  the declaration; nothing runs locally by surprise.
- **Agent deciding where to run**: gets one read-only readiness answer per
  remote (registered, reachable, runtime compatible, network capacity,
  workspace ownership, instance handoff), sees how that remote was selected,
  and chooses local explicitly when the remote is not ready.
- **Reviewer**: can see, per remote, the Sandbox-owned network ranges, what is
  allocated to which workspace or job, and when capacity was last proven.

## Goals

- Development network capacity on a remote never depends on a Docker daemon
  restart while the host serves hosted targets. Sandbox gives the networks it
  creates explicit subnets from a Sandbox-owned range recorded for the remote;
  configured daemon address pools are an optional second source, not a
  prerequisite.
- Docker's built-in default pools never count as capacity evidence. A remote
  with neither configured pools nor a Sandbox-owned range keeps refusing, and
  the refusal reads "no pool evidence, not exhausted capacity" and names the
  range remedy.
- A daemon-level pool change stays a maintenance operation: it is applied only
  with a confirmation bound to the exact list of hosted targets and the count
  of other running containers the plan says it would restart, and is refused
  if either changed since the plan. Without a confirmation it only plans.
- One read-only readiness check per remote covers registration, reachability,
  runtime compatibility, network capacity, workspace ownership repairability,
  and the ensure-to-exec instance handoff. It runs on request, in `sb doctor`
  for every remote the focused project declares, and before every remote
  submission, with the same result shape.
- A remote name, declared or passed explicitly, that is not registered yields
  a typed refusal listing registered remotes and where the name came from;
  re-pointing is an explicit change to the project declaration.
- Fallback to local execution is the caller's decision, never automatic; a
  refused remote submission never starts a local run.
- Capacity, ranges and allocations are inspectable per remote, bounded and
  secret-free, and every allocation is attributable to a workspace or job.

## Non-Goals

- Changing the capacity admission's fail-closed rule or letting it retry or
  delete networks. It keeps refusing on missing evidence, and the readiness
  check never replaces the admission at submission time.
- Counting Docker's built-in default pools as evidence.
- Assigning a Sandbox-owned range automatically at registration, provision or
  readiness time; assignment is always an explicit operator action.
- A maintenance scheduler or time-window concept for daemon restarts.
- Host-wide resource governance (CPU, memory, disk admission): feature 047.
- Hosting targets' own networks. Hosted Compose projects keep their current
  network behavior; this feature covers networks Sandbox creates for
  development workspaces, jobs, previews and CI cells.
- Multi-remote scheduling or choosing among several remotes automatically. The
  existing single-configured-remote inference stays as it is.
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
  measured usable count and the time it was proven.

### Scenario 2 — Readiness before submission

- **Starting state**: A project declares `xcloud-london` as its remote target.
- **User action**: The agent runs the readiness check for the project.
- **Expected outcome**: One bounded result naming the remote and its
  `remote_selection` (`explicit` or `profile` here), with a
  row per readiness aspect (registration, reachability, runtime compatibility,
  capacity, ownership repair, instance handoff), each `ready`, `not_ready`
  with a reason, `unknown` with the probe state, or `not_applicable` with the
  reason, and for each `not_ready` or `unknown` row the supported command or
  declaration change that would resolve or prove it. Only `not_ready` rows
  make the remote not ready for submission; `unknown` and `not_applicable`
  rows are reported but never refuse on their own. The result states the
  window within which a submission may reuse it.

### Scenario 3 — Declared remote retired (negative)

- **Starting state**: The project declares `scaleway-sandbox`; only
  `xcloud-london` is registered.
- **User action**: `sb test fast`, `sb test fast --remote scaleway-sandbox`,
  or the readiness check.
- **Expected outcome**: The `unknown_remote` refusal names the unregistered
  name, whether it came from the caller or the project declaration, the
  registered remotes, and the supported way to re-point the declaration. No
  transfer, no local run, exit non-zero.

### Scenario 4 — Daemon-level change while production runs (negative)

- **Starting state**: As in Scenario 1, and the operator asks the pool plan to
  configure daemon address pools.
- **User action**: Run the pool command without a confirmation, then apply it
  with a confirmation bound to an earlier plan after a hosted target was
  added.
- **Expected outcome**: The plan lists every hosted target the restart would
  restart, the count of other running containers it would also restart
  (development workspaces, jobs, and containers Sandbox does not own), and a
  digest covering both. The unconfirmed run only plans: it reports status
  `planned`, exits 0, and restarts nothing. The apply confirmed against a
  stale plan is refused because the list or count changed since the plan.
  Both results offer the Sandbox-owned range as the no-restart alternative. No
  daemon restart happens.

### Scenario 5 — Range exhausted (negative)

- **Starting state**: The Sandbox-owned range is fully allocated to live
  workspaces and jobs.
- **User action**: A job is submitted.
- **Expected outcome**: Refused with `docker_network_subnet_exhausted` and an
  allocation table giving, per allocation, an opaque owner id, owner kind
  (workspace or job), the owning workspace and age, plus the supported release commands
  (`workspace release`, `workspace reap`). Subnets appear only in the
  per-remote range listing, not in the refusal. Nothing is deleted.

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
  uses it. The readiness check's handoff row is `ready` only when a recorded
  ensure-to-exec round trip exists on that remote at its installed runtime
  revision; otherwise it is `unknown` and names the command that proves it.
  An `unknown` handoff row never refuses `ensure --remote` or the first job
  on that remote.

### Scenario 8 — Caller chooses local explicitly

- **Starting state**: Readiness for the declared remote is `not_ready`.
- **User action**: The agent runs the tests with an explicit local selector.
- **Expected outcome**: The local run proceeds, its result reports
  `remote_selection` `explicit` (the local selector), and it states that the
  declared remote was not ready, which row and why, so the evidence is not
  mistaken for a remote result.

### Scenario 9 — Old runtime without range support (negative)

- **Starting state**: A remote's installed runtime predates Sandbox-owned
  ranges.
- **User action**: Assign a range.
- **Expected outcome**: A typed limitation naming the migrate that is
  required; nothing is recorded. The daemon-pool path remains the documented
  alternative with its restart cost and affected targets stated.

### Scenario 10 — Range overlaps a host route (negative)

- **Starting state**: The proposed range overlaps a route on the host, an
  existing Docker network, Docker's built-in default address pools, or the
  Tailscale address space (`100.64.0.0/10`).
- **User action**: Assign the range.
- **Expected outcome**: Refused at assignment with the overlapping network or
  route class named; nothing is recorded. When inventory is partial, the
  assignment is refused as `unknown` rather than accepted.

### Scenario 11 — Concurrent allocation for the last subnet (negative)

- **Starting state**: One subnet remains free in the range.
- **User action**: Two submissions arrive at the same time.
- **Expected outcome**: Exactly one receives the subnet; the other is refused
  with `docker_network_subnet_exhausted`. No subnet is allocated twice.

### Scenario 12 — Orphaned allocation after a killed job (negative)

- **Starting state**: A job was killed before releasing its network.
- **User action**: Inspect the range, then submit.
- **Expected outcome**: The allocation stays attributed to the dead job and
  its workspace and counts as used until `workspace release`, `workspace reap`, or
  the workspace's retention expiry (default 7 days) frees it. It is never
  silently reused.

### Scenario 13 — Compose file with extra or external networks

- **Starting state**: A job's Compose file declares several networks, one of
  them external.
- **User action**: Submit.
- **Expected outcome**: Each network Sandbox creates receives a subnet from
  the range and counts toward the required capacity; the external network is
  not allocated and is reported as outside the range. If the required count
  exceeds usable capacity, the submission is refused before transfer.

### Scenario 14 — Readiness on a remote with no instance

- **Starting state**: The remote is registered and reachable but has no
  provisioned instance for the project.
- **User action**: Readiness check.
- **Expected outcome**: The ownership-repair row is `not_applicable` with that
  reason, and the handoff row is `unknown` with the `ensure --remote` command
  that would prove it. No row is `not_ready` on that account, so
  `ensure --remote` is accepted and creates the instance.

### Scenario 15 — Two registered remotes, none declared (negative)

- **Starting state**: Two remotes are registered and provisioned; the project
  declares no remote and the caller passes no `--remote`.
- **User action**: Readiness check, then a remote submission.
- **Expected outcome**: The registration row is `not_ready` with
  `ambiguous_remote` and lists the candidate remotes and the supported ways to
  choose one (`--remote NAME` or a project declaration). The submission is
  refused the same way; nothing is transferred and nothing runs locally. A
  registered but unprovisioned remote is likewise `not_ready` with
  `remote_not_provisioned`.

## Proposed Product Behavior

- A remote record may hold one or more Sandbox-owned development ranges with
  a per-network subnet size. Networks Sandbox creates for workspaces, jobs,
  previews and CI cells get explicit subnets allocated from those ranges and
  recorded before creation against their owner kind (workspace or job) and
  owning workspace. Allocations owned by a job are freed with that job's
  workspace, by `workspace release`, `workspace reap`, or retention expiry.
- Range assignment is explicit only. Provision and the readiness check, when
  no range is assigned, propose a range that overlaps no observed network or
  route and lies outside Docker's built-in default address pools, and print
  the exact assign command; they never assign it.
- The capacity admission counts usable subnets as the union of configured and
  proven daemon-pool capacity and unallocated Sandbox-owned range capacity.
  Docker's built-in default pools are not evidence. Its evidence rules are
  otherwise unchanged; `missing_pool_evidence` stays a refusal, worded as
  missing evidence rather than exhausted capacity, naming the range remedy.
- The daemon-pool plan distinguishes "configure pools" (daemon restart) from
  "assign a range" (no restart), lists exactly the hosted targets in the
  hosting inventory at plan time that a restart would affect plus the count
  of other running containers it would restart, and shows a digest covering
  both. Without a confirmation the command only plans (status `planned`,
  exit 0, no restart). Applying requires a confirmation bound to that digest
  and is refused if the list or count changed since the plan.
- A readiness check is one read-only command and one MCP tool sharing a result
  shape. `sb doctor`'s existing "Remote targets" section is extended to cover
  every remote the focused project declares, including declared but
  unregistered names. Every remote submission runs the same check first and
  reports the first `not_ready` row as its refusal; only `not_ready` rows
  refuse, while `unknown` and `not_applicable` rows are reported and never
  refuse on their own, so `ensure --remote` and the first job on a remote are
  never refused because handoff or ownership is `unknown`. The capacity
  admission still runs at submission. A readiness proof may be reused by a
  submission within a short bounded window stated in the result, never
  across a migrate or installed runtime revision change.
- The registration row is `not_ready` with the existing `unknown_remote`,
  `ambiguous_remote` (listing the candidate remotes) or
  `remote_not_provisioned` refusal when target selection fails that way.
- Every readiness and submission result surfaces the `remote_selection` that
  target selection already computes (`explicit`, `profile`,
  `single-configured`, or `local` when nothing selects a remote). The
  existing single-configured-remote inference is unchanged.
- Until feature 061 ships, the runtime compatibility row reports today's
  exact revision check; afterwards it reports 061's verdict.
- An unregistered remote name stays the `unknown_remote` refusal, extended
  with the registered remotes and the source of the name; no command infers
  a replacement remote.
- Local fallback is an explicit selector; a refused remote submission exits
  non-zero and runs nothing locally.
- Ranges, allocations, and last-proven capacity are listed per remote,
  bounded, attributable and secret-free.

## Constraints and Dependencies

- The Docker network capacity admission (`docs/remote-hosting.md`, "Docker
  network capacity admission") and its fail-closed codes are the baseline;
  this feature adds a capacity source and does not relax the admission. Its
  refusal envelope forwards no subnets, paths or probe output, and this
  feature keeps that property.
- Spec 032 (remote job runtime) owns workspace materialization, ownership
  repair, job acceptance and workspace retention; spec 033 owns agent-aware
  sync. The readiness check consumes their supported read-only probes.
- Feature 061 supplies the runtime compatibility verdict; feature 060 supplies
  the hosted-target inventory that a restart plan must name. Until 060 exists,
  the restart plan uses the current hosting inventory.
- Remote-side range allocation takes effect only after a migrate to a runtime
  that supports it (Scenario 9).
- Hosted Compose projects on the same daemon may use daemon-default or
  declared subnets; Sandbox-owned ranges must not overlap any network or route
  the inventory can observe, and an overlap is a refusal at range assignment.
- A project selects a remote through its runtime target declaration (default
  runtime and named remote) or an explicit `--remote`; re-pointing a declared
  remote is a declaration change the project owner makes, with the merge order
  (user-global, project, override) unchanged.
- Constitution and module boundaries: ranges and allocations are new state
  registered through the project's explicit state contracts.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Capacity source | Sandbox-owned ranges with explicit per-network subnets; configured daemon pools optional | Daemon pool changes need a restart that stops production; explicit subnets need none | Fable decision (delegated by user), 2026-10-09 |
| Built-in default pools | Not capacity evidence; `missing_pool_evidence` stays a refusal reading "no pool evidence, not exhausted capacity" and names the range remedy | The admission cannot measure built-in pools, and fail-closed on missing evidence is the baseline | Fable decision (delegated by user), 2026-10-09 |
| Daemon restart | No scheduler. Applied only with `--confirm` bound to the digest of the affected-target list shown in the plan; refused if that list changed since the plan | A restart on a production host is a maintenance event; binding the confirmation to the list prevents restarting a target the operator never saw | Fable decision (delegated by user), 2026-10-09 |
| Range assignment | Explicit only; provision and readiness propose a non-overlapping range and print the exact assign command | Assignment claims address space on a shared host and needs an operator decision | Fable decision (delegated by user), 2026-10-09 |
| Remote selection | Keep single-remote inference; every result carries `remote_selection`: `explicit`, `profile`, or `single-configured` | Inference already works; reporting its source makes it visible | Fable decision (delegated by user), 2026-10-09 |
| Readiness surface | One read-only check shared by CLI, MCP, doctor and pre-submission | Discovering unreadiness by submitting is the cost this feature removes | Fable decision (delegated by user), 2026-10-09 |
| Retired declared remote | Typed refusal with registered alternatives; re-pointing is an explicit declaration change | Inferring a replacement remote routes work somewhere the project did not declare | Fable decision (delegated by user), 2026-10-09 |
| Local fallback | Explicit only; refused remote submission runs nothing locally | Silent fallback hides that remote testing is broken and mislabels evidence | Fable decision (delegated by user), 2026-10-09 |
| Scope of networks | Sandbox-created development networks only; hosted projects unchanged | Hosting networks are owned by the hosted Compose declaration | Fable decision (delegated by user), 2026-10-09 |

## Open Questions

- None.

## Acceptance Outcomes

- On a remote with zero daemon address pools and running hosted targets,
  assigning a range and submitting a remote test job completes with zero
  daemon restarts and zero hosted container restarts, verified by container
  start times before and after.
- On a remote with neither configured pools nor an assigned range, 100% of
  submissions are refused before transfer with the missing-evidence wording
  and the range remedy, and the readiness check proposes a non-overlapping
  range with the exact assign command.
- The readiness check returns within 30 seconds, with a hard stop at 60
  seconds that reports unfinished rows as `unknown`, and every aspect row
  populated; a submission to a `not_ready` remote is refused with the same
  row as its reason and transfers zero bytes of source.
- An unregistered remote name is refused with the registered remote list and
  the name's source on every submission path (`test`/`run_tests`,
  `e2e`/`run_e2e`, `ci`/`ci_run`, `exec --remote`, `job-start`,
  `ensure --remote`) and the readiness check, and starts zero local runs.
- Every readiness and submission result reports `remote_selection`.
- With two registered remotes and no declaration or `--remote`, the readiness
  registration row is `not_ready` with `ambiguous_remote` listing both, and
  the submission is refused with zero bytes transferred (Scenario 15).
- On a reachable remote with no instance for the project, readiness reports
  ownership repair `not_applicable` and handoff `unknown` naming the
  `ensure --remote` command, and `ensure --remote` is accepted (Scenario 14).
- A job whose Compose file declares N Sandbox-created networks plus one
  external network is refused before transfer when usable capacity is below
  N; the external network is never allocated (Scenario 13).
- Every readiness result states its reuse window, and no submission reuses a
  proof taken before a migrate or installed runtime revision change.
- A daemon-pool restart plan names exactly the hosted targets in the
  inventory at plan time and the count of other running containers; running
  it unconfirmed returns status `planned` with exit 0 and zero daemon
  restarts; applying it confirmed against a list or count that has since
  changed is refused with zero daemon restarts; applying it with a
  confirmation matching the current plan proceeds.
- With a range fully allocated, a submission is refused with the allocation
  table and no network is deleted; after `workspace release` of one
  allocation, the next submission is accepted. Two simultaneous submissions
  for one remaining subnet yield exactly one acceptance.
- A range overlapping an observed network, host route, Docker's built-in
  default address pools or `100.64.0.0/10` is refused at assignment in 100% of
  cases and nothing is recorded; every proposed range lies outside all of
  them.
- A local run chosen after a `not_ready` readiness result states the declared
  remote, the failing row and its reason in its result (Scenario 8).
- Assigning a range on a runtime without range support returns the typed
  limitation naming the required migrate and records nothing (Scenario 9).
- Ranges and allocations listed for a remote contain no secret-shaped values
  and are bounded in count and bytes.

## Risks and Assumptions

- **Risk**: Explicit subnets collide with networks the inventory cannot
  observe (foreign networks created outside Sandbox). Mitigation: range
  assignment refuses on any observed overlap and reports partial inventory as
  unknown; a collision at network creation is a typed refusal, never a
  retry.
- **Risk**: Readiness checks add latency to every submission. Mitigation:
  bounded, read-only, with a short-lived proof the submission can reuse that
  never survives a migrate.
- **Risk**: A confirmed daemon restart on a production host still restarts
  production. Mitigation: it is explicit, confirmed against the exact target
  list, and names targets; this feature does not make it safer, only visible
  and non-default.
- **Risk**: Orphaned allocations from killed jobs hold capacity until release
  or retention expiry. Mitigation: they are attributed and listed, and the
  exhaustion refusal names the release commands.
- **Assumption**: The Docker daemon on the operator's remotes accepts
  networks created with an explicit subnet without daemon pool configuration;
  this is standard Docker behavior.
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
- [x] The latest independent readiness review verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
