# Feature Specification: Remote Development Execution Readiness

**Feature Branch**: `063-remote-development-readiness`

**Created**: 2026-10-09

**Status**: Draft

**Input**: `specs/063-remote-development-readiness/prd.md` (READY FOR SPECKIT; independent PASS 2026-10-09; feedback cef740dd, 5598f2d0, cebec97a, b7451117)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Development network capacity without a daemon restart (Priority: P1)

The operator of a remote that serves production assigns a Sandbox-owned
development address range to the remote. From then on, networks Sandbox
creates for workspaces, jobs, previews and CI cells get explicit subnets from
that range, and remote tests and exec are accepted without any Docker daemon
restart and without touching hosted targets.

**Why this priority**: Remote development execution has been unavailable on
the operator's only remote since 2026-10-06; this is the only path back that
does not restart production.

**Independent Test**: On a test remote with no daemon address pools and
running hosted containers, assign a range with confirmation, submit a remote
test job, and compare hosted container start times before and after.

**Acceptance Scenarios**:

1. **Given** a remote with no daemon address pools and running hosted targets,
   **When** the operator assigns a non-overlapping Sandbox-owned range with
   confirmation and an agent submits a remote test job, **Then** the range is
   recorded, zero daemon restarts and zero hosted container restarts happen,
   and the job is accepted with a subnet allocated from the range.
2. **Given** a remote with neither configured pools nor an assigned range,
   **When** a job is submitted, **Then** it is refused before transfer with
   wording that says "no pool evidence, not exhausted capacity" and names the
   range remedy; Docker's built-in default pools never count as evidence.
3. **Given** a proposed range that overlaps an observed Docker network, a host
   route, Docker's built-in default address pools or `100.64.0.0/10`, **When**
   the operator assigns it, **Then** the assignment is refused naming the
   overlapping class, and nothing is recorded; with partial inventory it is
   refused as `unknown`.
4. **Given** a remote whose installed runtime predates ranges, **When** the
   operator assigns a range, **Then** a typed limitation names the required
   migrate and nothing is recorded.
5. **Given** an assigned range with one free subnet, **When** two submissions
   arrive at once, **Then** exactly one is accepted and the other is refused
   `docker_network_subnet_exhausted`; no subnet is allocated twice.
6. **Given** a fully allocated range, **When** a job is submitted, **Then** it
   is refused `docker_network_subnet_exhausted` with an allocation table
   (opaque owner id, owner kind, owning workspace, age) and the release
   commands, no subnets in the refusal, and nothing deleted; after
   `workspace release` of one allocation the next submission is accepted.
7. **Given** a job's Compose file declaring N Sandbox-created networks and one
   external network, **When** it is submitted, **Then** each created network
   gets a subnet from the range, the external network is not allocated and is
   reported as outside the range, and the submission is refused before
   transfer if usable capacity is below N.
8. **Given** a job killed before releasing its network, **When** the range is
   inspected, **Then** the allocation stays attributed to that job and its
   workspace and counts as used until `workspace release`, `workspace reap`,
   or the workspace's retention expiry; it is never silently reused.

---

### User Story 2 - One readiness answer before submitting (Priority: P1)

An agent asks once whether a project's remote can serve it, and gets a
bounded, read-only answer per readiness aspect, with the command or
declaration change that would fix each gap. Every remote submission runs the
same check first and refuses with the first failing row.

**Why this priority**: Every recent failure was discovered only by submitting
a job and reading its refusal; agents then silently tested less.

**Independent Test**: Run the readiness check against a ready remote, an
unreachable one, a remote with no instance, and a remote with no capacity
evidence; compare each row with the expected state and submit to each.

**Acceptance Scenarios**:

1. **Given** a project that declares a remote, **When** the readiness check
   runs, **Then** one result names the remote and its `remote_selection`, and
   has one row each for registration, reachability, runtime compatibility,
   capacity, ownership repair and instance handoff, each `ready`, `not_ready`
   with a reason, `unknown` with the probe state, or `not_applicable` with a
   reason, plus the resolving command or declaration change for every
   `not_ready` or `unknown` row and the window within which a submission may
   reuse the result.
2. **Given** a remote whose readiness has a `not_ready` row, **When** any
   remote submission runs, **Then** it is refused with that row as its reason
   and transfers zero bytes of source.
3. **Given** a reachable remote with no instance for the project, **When** the
   readiness check runs, **Then** ownership repair is `not_applicable`,
   handoff is `unknown` naming the `ensure --remote` command, and
   `ensure --remote` is accepted and creates the instance.
4. **Given** a remote with a recorded ensure-to-exec round trip at its
   installed runtime revision, **When** the readiness check runs, **Then**
   handoff is `ready`; without one it is `unknown`, which never refuses.
5. **Given** a workspace with root-owned files left by an earlier job,
   **When** readiness then a submission run, **Then** ownership repair is
   `ready` after a read-only check that the repair path exists, and the
   submission repairs or refuses with the exact entry and owner.
6. **Given** `sb doctor` in a focused project, **When** it runs, **Then** its
   "Remote targets" section covers every remote the project declares,
   including declared but unregistered names, with the same rows.
7. **Given** a readiness proof, **When** a migrate or installed runtime
   revision change happens, **Then** no later submission reuses that proof.

---

### User Story 3 - Retired or ambiguous remote is told, not guessed (Priority: P2)

A project whose declared remote is no longer registered, or which has two
registered remotes and no choice, gets a typed refusal naming what is
registered, where the name came from, and how to choose. Nothing runs locally
by surprise; local runs are an explicit choice and say why they happened.

**Why this priority**: The retired remote and silent local fallback mislabel
evidence; fixing the refusal is small once the readiness row exists.

**Independent Test**: Declare an unregistered remote and submit through every
submission path; then register two remotes with no declaration; then run
locally with an explicit selector after a `not_ready` result.

**Acceptance Scenarios**:

1. **Given** a project declaring an unregistered remote, **When** any of
   `test`/`run_tests`, `e2e`/`run_e2e`, `ci`/`ci_run`, `exec --remote`,
   `job-start`, `ensure --remote` or the readiness check runs, **Then** it is
   refused `unknown_remote` naming the name, whether it came from the caller
   or the project declaration, the registered remotes, and the supported way
   to re-point the declaration; it exits non-zero and starts no local run.
2. **Given** two registered, provisioned remotes and no declaration or
   `--remote`, **When** readiness then a submission run, **Then** the
   registration row is `not_ready` with `ambiguous_remote` listing both and
   how to choose, and the submission is refused with zero bytes transferred.
3. **Given** a registered but unprovisioned remote, **When** readiness runs,
   **Then** the registration row is `not_ready` with
   `remote_not_provisioned`.
4. **Given** a `not_ready` declared remote, **When** the agent runs tests with
   an explicit local selector, **Then** the local run proceeds, reports
   `remote_selection` `explicit`, and states the declared remote, the failing
   row and its reason.

---

### User Story 4 - Daemon-pool change stays a visible maintenance step (Priority: P3)

When an operator does want daemon address pools, the plan names every hosted
target and the count of other running containers a restart would restart, and
applying it needs a confirmation bound to exactly that plan.

**Why this priority**: The no-restart range makes this path optional; it must
only stop being the sole and unexplained remedy.

**Independent Test**: Run the pool plan unconfirmed, add a hosted target,
apply with the old confirmation, then apply with a fresh one, against a test
host.

**Acceptance Scenarios**:

1. **Given** hosted targets on the remote, **When** the pool command runs
   without confirmation, **Then** it reports status `planned`, exits 0,
   restarts nothing, and lists every hosted target in the inventory, the count
   of other running containers, a digest covering both, and the range as the
   no-restart alternative.
2. **Given** that plan, **When** a hosted target is added or the container
   count changes and the operator applies with the old confirmation, **Then**
   it is refused with zero daemon restarts.
3. **Given** a confirmation matching the current plan, **When** the operator
   applies, **Then** the change proceeds.

---

### Edge Cases

- Readiness probes that do not finish by 60 seconds are reported `unknown`;
  the result still returns with every row populated.
- `unknown` and `not_applicable` rows never refuse a submission on their own;
  only `not_ready` rows refuse.
- Provision and readiness propose a range when none is assigned, but never
  assign it.
- A collision at network creation with a network the inventory could not
  observe is a typed refusal, never a retry.
- The capacity admission still runs at submission even after a fresh `ready`
  readiness result; readiness never replaces it.
- Hosted Compose projects' own networks are outside ranges and unchanged.
- Until feature 061 ships, the runtime-compatibility row reports today's exact
  revision check; afterwards it reports 061's verdict.
- The refusal envelope never forwards subnets, paths or probe output; subnets
  appear only in the per-remote range listing.

## Requirements *(mandatory)*

### Functional Requirements

**Ranges and allocation**

- **FR-001**: A remote record MUST be able to hold one or more Sandbox-owned
  development ranges with a per-network subnet size, assigned only by an
  explicit, confirmed operator action.
- **FR-002**: Range assignment MUST refuse, recording nothing, when the range
  overlaps any observed Docker network, host route, Docker's built-in default
  address pools or `100.64.0.0/10`; partial inventory MUST refuse as
  `unknown`; a runtime without range support MUST return a typed limitation
  naming the required migrate.
- **FR-003**: Provision and the readiness check, when no range is assigned,
  MUST propose a range that satisfies FR-002 and print the exact assign
  command, and MUST NOT assign it.
- **FR-004**: Every network Sandbox creates for workspaces, jobs, previews and
  CI cells MUST receive an explicit subnet from an assigned range, recorded
  before creation against its owner kind (workspace or job) and owning
  workspace. External networks MUST NOT be allocated and MUST be reported as
  outside the range.
- **FR-005**: Allocation MUST be atomic per remote: no subnet is ever
  allocated twice, and concurrent requests for the last subnet yield exactly
  one success.
- **FR-006**: Allocations MUST be freed only by `workspace release`,
  `workspace reap` or the owning workspace's retention expiry (default 7
  days); an orphaned allocation stays attributed and counts as used.
- **FR-007**: Exhaustion MUST refuse with `docker_network_subnet_exhausted`, an
  allocation table (opaque owner id, owner kind, owning workspace, age) and
  the release commands, without subnets and without deleting anything.

**Capacity admission**

- **FR-008**: The capacity admission MUST count usable capacity as the union
  of configured and proven daemon-pool capacity and unallocated range
  capacity; Docker's built-in default pools MUST NOT count. A submission whose
  required network count exceeds usable capacity MUST be refused before
  transfer.
- **FR-009**: `missing_pool_evidence` MUST remain a refusal, worded as missing
  evidence rather than exhausted capacity, naming the range remedy. The
  admission's other fail-closed rules MUST be unchanged, and its refusal
  envelope MUST forward no subnets, paths or probe output.

**Daemon-pool maintenance**

- **FR-010**: The daemon-pool plan MUST distinguish configuring pools (daemon
  restart) from assigning a range (no restart), list every hosted target in
  the hosting inventory at plan time, the count of other running containers a
  restart would restart, and a digest covering both.
- **FR-011**: Without confirmation the pool command MUST only plan (status
  `planned`, exit 0, no restart). Applying MUST require a confirmation bound
  to the plan digest and MUST be refused, with zero restarts, if the target
  list or container count changed since the plan.

**Readiness**

- **FR-012**: The system MUST provide one read-only readiness check as a CLI
  command and an MCP tool with one shared result shape: the remote, its
  `remote_selection`, and rows for registration, reachability, runtime
  compatibility, capacity, ownership repair and instance handoff, each
  `ready`, `not_ready` (reason), `unknown` (probe state) or `not_applicable`
  (reason), with a resolving command or declaration change for every
  `not_ready` or `unknown` row, and the reuse window.
- **FR-013**: The readiness check MUST return within 30 seconds and stop by
  60 seconds, reporting unfinished rows as `unknown`.
- **FR-014**: The registration row MUST be `not_ready` with the existing
  `unknown_remote`, `ambiguous_remote` (listing candidates and how to choose)
  or `remote_not_provisioned` refusal when target selection fails that way.
- **FR-015**: The ownership-repair row MUST be `ready` only after a read-only
  check that the repair path is available, and `not_applicable` when the
  project has no instance on the remote.
- **FR-016**: The handoff row MUST be `ready` only when a recorded
  ensure-to-exec round trip exists on that remote at its installed runtime
  revision, otherwise `unknown` naming the command that proves it.
- **FR-017**: The runtime-compatibility row MUST report today's exact revision
  check until feature 061 ships and 061's verdict afterwards.
- **FR-018**: Every remote submission (`test`/`run_tests`, `e2e`/`run_e2e`,
  `ci`/`ci_run`, `exec --remote`, `job-start`, `ensure --remote`) MUST run the
  readiness check first, or reuse a proof within its window, and refuse with
  the first `not_ready` row, transferring zero source bytes. `unknown` and
  `not_applicable` rows MUST NOT refuse on their own. A proof MUST NOT be
  reused across a migrate or installed runtime revision change.
- **FR-019**: `sb doctor`'s "Remote targets" section MUST cover every remote
  the focused project declares, including declared but unregistered names,
  using the readiness rows.

**Selection and fallback**

- **FR-020**: `unknown_remote` MUST name the unregistered name, its source
  (caller or project declaration), the registered remotes, and the supported
  way to re-point the declaration. No command may infer a replacement remote.
- **FR-021**: Every readiness and submission result MUST report
  `remote_selection` (`explicit`, `profile`, `single-configured`, or `local`).
  Single-configured-remote inference MUST be unchanged.
- **FR-022**: A refused remote submission MUST exit non-zero and start no
  local run. Local execution MUST require an explicit selector; a local run
  chosen after a `not_ready` result MUST state the declared remote, the
  failing row and its reason.

**Inspection and docs**

- **FR-023**: Ranges, allocations and last-proven capacity MUST be listable
  per remote, bounded in count and bytes, attributable, and free of
  secret-shaped values.
- **FR-024**: `docs/remote-hosting.md` (capacity admission) and
  `docs/remote-job-runtime.md` MUST describe ranges, readiness and selection.

### Key Entities

- **Development range**: a Sandbox-owned address range on a remote with a
  per-network subnet size, assignment time and assigner.
- **Subnet allocation**: a subnet from a range, its owner kind (workspace or
  job), opaque owner id, owning workspace, and allocation time.
- **Readiness result**: remote, `remote_selection`, six aspect rows, remedies,
  reuse window, runtime revision it was taken at.
- **Restart plan**: hosted targets affected, count of other running
  containers, digest.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a remote with zero daemon address pools and running hosted
  targets, assigning a range and running a remote test job completes with
  zero daemon restarts and zero hosted container restarts.
- **SC-002**: 100% of submissions to a remote with neither configured pools
  nor an assigned range are refused before transfer with the missing-evidence
  wording and the range remedy.
- **SC-003**: The readiness check returns within 30 seconds in normal
  conditions and never later than 60 seconds, with every row populated.
- **SC-004**: 100% of submissions to a `not_ready` remote are refused with the
  same row and zero source bytes transferred, and start zero local runs.
- **SC-005**: An unregistered remote name is refused with the registered list
  and its source on every listed submission path and the readiness check.
- **SC-006**: Two simultaneous submissions for one remaining subnet yield
  exactly one acceptance in 100% of runs of the concurrency test.
- **SC-007**: 100% of range assignments overlapping an observed network, host
  route, built-in default pools or `100.64.0.0/10` are refused, and every
  proposed range lies outside all of them.
- **SC-008**: A daemon-pool apply confirmed against a changed target list or
  container count causes zero daemon restarts.
- **SC-009**: Range and allocation listings contain no secret-shaped values.

## Assumptions

- Docker on the operator's remotes accepts networks created with an explicit
  subnet without daemon pool configuration (standard behavior).
- The operator assigns a range on `xcloud-london` once the capability ships;
  until then remote development stays unavailable there. Live proof on that
  host follows the remote install protocol (migrate, repin, confirm revision
  match) and needs the owner's approval before touching a host that serves
  production.
- Spec 032 owns workspace materialization, ownership repair, job acceptance
  and retention; spec 033 owns sync; this feature consumes their read-only
  probes.
- Feature 060 supplies the hosted-target inventory for the restart plan; until
  it ships, the current hosting inventory is used.
- Out of scope: changing the admission's fail-closed rule, counting built-in
  default pools, automatic range assignment, a restart scheduler, host-wide
  resource governance (047), hosted projects' networks, multi-remote
  scheduling, 060's locks and 061's rules, and re-provisioning a retired
  remote.
