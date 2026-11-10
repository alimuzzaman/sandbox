# Feature Specification: Oldest-first durable-job admission

**Feature Branch**: `fix/job-queue-fifo`

**Created**: 2026-11-11

**Status**: Draft

**Input**: User description: "Spec number 083, short name job-queue-fifo. Oldest-first (FIFO) durable-job admission on a Sandbox host. Today a queued durable job tries admission only when its own status is read, so after a host slot frees whoever polls first wins and older jobs starve; Sandbox feedback 498136c2 recorded four guided-setup jobs waiting about 35 minutes. Also two running jobs whose supervisor process was gone (health=stuck, cancel_on_stall 0) held both slots for about 16 minutes. Required behaviour: (1) admission to host capacity slots is oldest-first by acceptance order across the host, driven by a slot being released and by a periodic drain in the long-lived host service, not by which client polls; (2) per-workspace exclusivity and parallel-safe/sync-generation sharing rules are unchanged, and an older job that cannot start because its own workspace, dependency or sync generation is not ready does not block younger jobs that can start; (3) a status read never admits a job out of order; (4) a running job whose supervisor and process are proved gone (same proof reconciliation uses, never stale heartbeats alone) ends and releases its slot by default, regardless of cancel_on_stall; (5) admission stays serialized so concurrent drains, status reads and submissions never start a job twice or exceed max_parallel; (6) queue position reported to users matches real admission order. Prefer no change to controller-facing payload keys; if any change, bump the control protocol. Out of scope: per-project fairness, priorities, preemption."

## Background and evidence

- A Sandbox host runs durable jobs (tests, builds, CI cells) in a bounded number of
  host slots. The slot limit is the host's own `jobs.scheduler.max_parallel`
  (spec-less Part 1 of this work, live since `44bcdba`; xspeed-dev runs 4).
- Today a queued job is offered a slot only when someone submits it or reads its
  status. On xspeed-dev on 2026-11-10, jobs accepted between 14:49 and 14:58 started
  only between 15:23 and 15:29. A job accepted at 15:19 started at 15:23:48, ahead of
  all of them, because its owner polled more often.
- The two jobs that held both slots from about 14:48 to 15:23 (`06a20412`,
  `e89a9e79`) were **not** process-gone. Their supervisors kept heartbeating until a
  manual cancel, and they reported a live owned descendant, `stalled: true` and no
  output after 15:06. They ran with `cancel_on_stall` false, the built-in default for
  every execution profile. The feedback text ("no process on the host") is
  contradicted by the recorded evidence.

## Clarifications

### Session 2026-11-11

- Q: Does an older parallel-safe job that could share a leased workspace block younger jobs? → A: Yes. "Could start right now" uses the same workspace-sharing rule as admission itself, so FR-002 holds for parallel-safe jobs too.
- Q: May the periodic drain apply every existing reconciliation outcome, including `missing_supervisor_identity` for a running row with no durable supervisor identity? → A: Yes. The drain reuses startup reconciliation unchanged; that rule already runs on every job command today, so only its frequency changes.
- Q: May read commands (status, list, MCP job tools) reconcile and drain? → A: Yes. They already reconcile on start; they now also start queued jobs oldest-first. A read never starts a job out of order.
- Q: When does a job waiting on a dependency become a candidate? → A: Once a drain or status read has observed its dependencies complete. Every drain pass re-checks and records the reason of each queued job it visits.
- Q: Must both the supervisor and the child be proved gone before the drain ends a running job? → A: No. The drain keeps today's reconciliation rule exactly (FR-010): a proved-gone supervisor is enough, as it already is on every job command. Requiring the child to be gone too would change existing reconciliation and is out of scope.
- Q: Does the 2026-11-10 slot-holding incident get fixed by this feature? → A: No. Those jobs were live but silent, not process-gone (see Background). The default for `cancel_on_stall` stays an open decision for the repository owner; the feature description's "supervisor process was gone" is corrected by the recorded evidence.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Older queued jobs start first (Priority: P1)

An agent or developer submits a job to a busy host. It waits in the queue, and when a
slot frees it starts before any job accepted after it, whether or not its owner is
polling at that moment.

**Why this priority**: This is the reported failure. Guided-setup runs waited about
35 minutes while younger jobs took the freed slots.

**Independent Test**: On a host with one slot, run a job that holds the slot, queue
job A and then job B, never read A's status, and repeatedly read B's. When the slot
frees, A starts first.

**Acceptance Scenarios**:

1. **Given** a full host and queued jobs A (older) and B (younger), both able to start,
   **When** a running job finishes, **Then** A takes the freed slot and B stays queued.
2. **Given** the same queue, **When** only B's owner reads status after the slot frees,
   **Then** B stays queued, A starts, and B's reported position becomes 1.
3. **Given** a full host and no client reading any status, **When** a running job
   finishes, **Then** the oldest startable queued job starts within the drain interval.
4. **Given** two slots free at once and three queued jobs, **When** the queue drains,
   **Then** the two oldest startable jobs start and the third stays queued.

---

### User Story 2 - A blocked older job does not hold up others (Priority: P1)

An older queued job cannot start for a reason of its own: its workspace is busy, a
dependency is unfinished, or its sync generation is not ready. Younger jobs that could
start on a free slot still start.

**Why this priority**: Strict first-in-first-out with head-of-line blocking would make
the starvation worse. Workspace exclusivity must stay exactly as it is.

**Independent Test**: With one free slot, queue an older job for workspace W while W
is busy and a younger job for workspace X. The younger job starts; the older one stays
queued with its workspace reason.

**Acceptance Scenarios**:

1. **Given** an older job queued on a busy workspace and a younger job on a free
   workspace, **When** a slot is free, **Then** the younger job starts and the older job
   stays queued with a workspace reason.
2. **Given** two queued jobs for the same exclusive workspace, **When** that workspace
   frees and a slot is free, **Then** the older of the two starts.
3. **Given** parallel-safe jobs that may share one sync generation, **When** slots are
   free, **Then** the existing sharing rules decide whether they run together, in
   acceptance order.
4. **Given** an older job waiting on an unfinished dependency, **When** a slot is free,
   **Then** a younger startable job starts.

---

### User Story 3 - A job whose owner is proved gone gives back its slot (Priority: P2)

A running job's supervisor and process are proved gone (for example after a crash).
The host ends that job and frees its slot without anyone running a cancel or a
reconcile command, and the next queued job starts.

**Why this priority**: A slot held by a dead job blocks the whole queue. The existing
proof already runs on each local command and service start; this makes it happen on
the host's own schedule too.

**Independent Test**: Record a running job whose supervisor identity is proved
absent, hold no client activity, and confirm it ends as interrupted with its slot
released, and that a queued job starts, within the drain interval.

**Acceptance Scenarios**:

1. **Given** a running job whose recorded supervisor is proved absent, **When** the
   periodic drain runs, **Then** the job ends as interrupted with reason
   `supervisor_lost` and its slot is released.
2. **Given** a running job with only a stale heartbeat and no proof of absence,
   **When** the drain runs, **Then** the job keeps its slot.
3. **Given** a running job whose child is alive but silent past its stall threshold and
   which has `cancel_on_stall` false, **When** the drain runs, **Then** it keeps its
   slot (unchanged behaviour; see Assumptions).

---

### User Story 4 - Queue position tells the truth (Priority: P3)

A user reading a queued job's status or the job list sees a position and blockers that
match the order in which jobs will actually start.

**Why this priority**: Users decide whether to wait or cancel from this number.

**Independent Test**: Queue three jobs on a full host; positions read 1, 2, 3 in
acceptance order across projects, and they start in that order.

**Acceptance Scenarios**:

1. **Given** queued jobs from two projects on one host, **When** their statuses are
   read, **Then** positions follow host-wide acceptance order, not per-project order.
2. **Given** an older job blocked on its own workspace, **When** a younger job's
   status is read, **Then** the younger job's position does not count that blocked job.

### Edge Cases

- Concurrent drains (the service tick, a status read and a new submission at the same
  moment) must start each job once and never exceed the slot limit.
- A drain that finds its candidate already started, cancelled or terminal by another
  actor skips it and continues.
- A job whose launch fails during a drain is ended as today (`supervisor_launch_failed`)
  and its slot is released; the drain continues with the next job.
- An invalid host `jobs.scheduler` policy (Part 1) stops all admission, and queued jobs
  keep the policy error code as their reason.
- The host resource floor (free memory or disk) is below its minimum: nothing is
  admitted; the drain stops without reordering the queue.
- A matrix or plan parent has no process of its own and is never admitted to a slot or
  swept as lost.
- Lowering `max_parallel` below the slots in use admits nothing until enough jobs end.
- A queued job past its deadline plus queue grace is expired by the existing rule and
  never admitted.
- Two jobs with the same acceptance timestamp are ordered by job id.
- A pending sync launch that is mid-handoff keeps its existing grace and ownership
  rules.
- The host service is not running (CLI-only host): admission still happens on slot
  release and on each command run, and order is still oldest-first.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The host MUST offer a free slot to queued jobs in acceptance order
  (acceptance time, then job id) across all projects and workspaces on that host.
- **FR-002**: A queued job MUST NOT be admitted while an older queued job that could
  start right now is waiting. "Could start right now" (a *candidate*) means: not an
  aggregate parent; its recorded wait is for a slot or its workspace, not for a
  dependency, sync generation or policy; and the workspace-sharing rule used by
  admission would let it take its workspace now. The single definition lives in
  `contracts/admission.md`.
- **FR-003**: An older queued job that cannot start for a reason of its own (workspace,
  dependency, sync generation) MUST NOT prevent a younger startable job from taking a
  free slot.
- **FR-004**: Workspace exclusivity, parallel-safe sharing and sync-generation
  sharing rules MUST behave exactly as before for any pair of jobs.
- **FR-005**: Releasing a slot MUST trigger an attempt to admit queued jobs, on the
  same host, without waiting for a client to read status. This covers job completion,
  cancellation, launch failure, interruption and reconciliation.
- **FR-006**: The long-lived host service MUST also attempt admission on a fixed
  interval (default 20 seconds) as a backstop when no release event reaches it.
- **FR-007**: Reading a queued job's status MUST NOT admit that job ahead of an older
  candidate. Outside a drain, a status read of a queued job runs the oldest-first drain
  instead of admitting that job directly. Read commands may reconcile and drain.
- **FR-008**: Admission MUST stay serialized so that no job starts twice and the
  number of held slots never exceeds the host's current `max_parallel`, under any
  interleaving of drains, status reads, submissions and cancellations, including from
  separate processes on the same host.
- **FR-009**: A drain MUST be safe to re-run at any time, and a drain interrupted part
  way MUST leave every job in a valid state.
- **FR-010**: The periodic drain MUST also run today's startup reconciliation unchanged.
  That rule ends a running job, and releases its slot, when its recorded supervisor is
  proved gone (identity mismatch or proved absence), when its running row has no durable
  supervisor identity, or when its recorded child PID now belongs to a different live
  process. A child that is merely absent while its verified supervisor lives is left to
  that supervisor. A stale heartbeat alone MUST NOT end a job.
- **FR-011**: A queued job's reported position MUST count only older queued jobs that
  could start right now, host-wide, so it matches the order in which jobs start.
- **FR-012**: When no slot is free, the drain MUST leave queued jobs queued with their
  current reason and touch nothing else.
- **FR-013**: The change SHOULD NOT alter controller-facing payload keys. If any key
  changes, the control protocol version MUST be bumped and the recorded shapes updated.
- **FR-015**: A job already admitted by one caller MUST NOT be admitted, failed or
  released by a second caller that raced it; the second caller leaves it alone.
- **FR-014**: Existing reasons a job waits (`workspace_or_capacity_busy`, dependency,
  sync generation, policy error codes) MUST keep their current values.

### Key Entities

- **Queued job**: a durable job in the `queued` lifecycle, with acceptance time, job id,
  target, project, workspace, workspace mode, dependencies and optional sync generation.
- **Host slot**: one unit of the host's `max_parallel` capacity, held by at most one
  active job at a time.
- **Drain**: one pass that offers free slots to queued jobs oldest-first, starting each
  admitted job, and stops when no slot is free or no queued job can start.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a replay of the 2026-11-10 pattern (a full host, older jobs whose owners
  do not poll, younger jobs whose owners poll often), every older startable job starts
  before every younger one: 0 out-of-order starts.
- **SC-002**: With no client activity, a freed slot is taken by the oldest startable
  queued job within 30 seconds on a host whose service is running.
- **SC-003**: Across 200 randomized interleavings of drains, status reads, submissions
  and cancellations, no job starts twice and held slots never exceed the limit.
- **SC-004**: A job whose supervisor is proved gone frees its slot within 30 seconds
  on a host whose service is running, with no command run by anyone.
- **SC-005**: Reported queue positions match start order in every ordering test.
- **SC-006**: Existing workspace, sync-generation, dependency and cancellation tests
  pass unchanged.

## Assumptions

- Host-wide oldest-first is the fairness rule. Per-project fairness, priorities,
  reserved lanes and preemption are out of scope (the feedback's "optional priority or
  reserved-lane flag" is deferred).
- The drain interval default is 20 seconds and is not user-configurable in this
  feature.
- A live child that is silent past its stall threshold keeps its slot unless the job
  was submitted with `cancel_on_stall`. This is today's behaviour, and the incident's
  slot-holders were in exactly this state. Changing the default for every profile is a
  separate owner decision, recorded as an open question in the plan; callers such as
  guided setup should pass `--cancel-on-stall`.
- Admission stays inside the one host's job registry; there is no cross-host queue.
- Changes reach a remote host only after `sb remote service migrate`.
