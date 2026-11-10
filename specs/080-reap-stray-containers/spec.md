# Feature Specification: Reclaim Stray Unlabeled Containers Owned by a Workspace

**Feature Branch**: `fix/reap-stray-containers`

**Created**: 2026-11-10

**Status**: Draft

**Input**: User description: "Sandbox deletion-safety fix approved by the owner on 2026-11-10 (feedback 43e3d4e5, spec 077 research R10). `sb resources plan --tier safe` offered a finished-job workspace as LIVE with `stop_containers=[<id>]`; that container was a standalone browser-test container a finished job left behind, not part of any compose project. `sb resources cleanup --tier safe --plan-id <id> --confirm` then failed the candidate with `stack_teardown_incomplete` and kept the checkout, because workspace teardown refuses a checkout that has an unlabeled container bound to it (`bound_container_not_exclusive`). Treat an unlabeled container as owned by a workspace only when every bind and volume source is inside that workspace's checkout and nothing else holds it: not in a compose project, not a hosted target, not a Sandbox instance, and its creating job, when known, is terminal. A container with any bind outside the checkout, any named volume, or any network other than default/bridge stays protected and is reported. The planner shows such containers with evidence, records each in the deletion manifest before stopping or removing it, never removes a container it did not plan, and refuses a planned container that changed since the plan with an evidence-changed code. Teardown stops then removes only the planned containers, then removes the checkout; a partial failure keeps the checkout and says why. Never prune containers or volumes. Also: `cleanup` without `--tier` refused a tier plan id with `plan_kind_mismatch` while the docs example shows the plan-id form without `--tier`; fix the docs or accept it."

## Clarifications

### Session 2026-11-10

Answered in this delegated thread from the owner's brief and its stated
deletion-safety-first default; no interactive answer was available. Each can be
overridden by the owner.

- Q: Does stray reclaim apply to base deployments (checkouts without the workspace
  marker) too? → A: No. Only workspace checkouts own a stack; base deployments keep
  today's behavior.
- Q: Is a bind "inside the checkout" judged on the path as written or after resolving
  links? → A: Both: the source as written and the resolved source must each be the
  checkout or below it.
- Q: Which other container settings protect it? → A: Any mount that is not a bind
  (named or anonymous volume, tmpfs), a restart policy other than none, any Compose,
  stack or Sandbox label, or a `sandbox-` name.
- Q: Should a bound container from another Compose project stop the plan, or only the
  cleanup as today? → A: The plan: the workspace is reported protected and never
  offered.
- Q: What does an older remote runtime do with a plan that names strays? → A: It
  ignores the new field and refuses the candidate as today; a plan from an older
  controller names no strays and the new runtime refuses unlabeled containers as
  today.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A finished workspace with a leftover test container is reclaimed (Priority: P1)

An operator runs the safe-tier reclaim on a remote. A workspace checkout whose jobs all
finished long ago still has one standalone container that a job started and never
removed. The container was started by hand from a public image, carries no Sandbox or
Compose labels, and its only mounts are directories inside that checkout. The plan lists
the workspace and names the container as one Sandbox will stop and remove, with the
evidence that makes it the workspace's own. The confirmed cleanup records the container
in the deletion manifest, stops it, removes it, and then removes the checkout.

**Why this priority**: This is the reported failure. Today the plan offers the candidate
and the cleanup always fails it, so the checkout's space is never reclaimed and every
run reports a partial result.

**Independent Test**: Give the cleanup a workspace candidate whose one container is
unlabeled with every mount inside the checkout; the run removes the container and the
checkout and reports the candidate removed, with manifest intent lines written before
each removal.

**Acceptance Scenarios**:

1. **Given** a reclaimable workspace whose only bound container is unlabeled, on the
   default network, with every mount source inside the checkout, **When** the operator
   plans the safe tier, **Then** the candidate lists that container under the containers
   to stop and remove, with its evidence (image, name, mount sources, networks, state and
   the time it was created and last started).
2. **Given** that plan, **When** the operator confirms the cleanup, **Then** the
   container is written to the deletion manifest before it is stopped, it is stopped and
   removed, the checkout is removed after it, and the candidate is reported removed.
3. **Given** the same workspace also has a Compose project of its own, **When** the
   cleanup runs, **Then** the project is torn down as before and the stray container is
   removed as well.

---

### User Story 2 - Containers that might belong to someone else stay protected (Priority: P1)

The same operator must never lose a container that could hold data or serve something
outside the workspace. A container with any mount outside the checkout, any named
volume, or any network other than the default bridge, or one that belongs to a Compose
project, a hosted target or a Sandbox instance, is never stopped or removed by this
path. The plan reports it, says why it is protected, and keeps the checkout.

**Why this priority**: This is a deletion path on shared hosts; a wrong ownership guess
destroys live work. Protection must hold before any reclaim is added.

**Independent Test**: Plan and run the cleanup with one such container per protection
rule; each candidate is refused before any container is touched, the checkout is kept,
and the reason names the rule.

**Acceptance Scenarios**:

1. **Given** an unlabeled container with one mount inside the checkout and one outside,
   **When** the operator plans, **Then** the container is reported as protected with the
   reason "mount outside the checkout", the workspace is not offered for removal, and a
   cleanup of an older plan that listed it refuses before touching it.
2. **Given** an unlabeled container that uses a named volume, **When** the operator
   plans, **Then** it is protected with the reason "named volume".
3. **Given** an unlabeled container attached to a user-defined network, **When** the
   operator plans, **Then** it is protected with the reason "non-default network".
4. **Given** a container carrying a Compose project label that is not the workspace's
   own project, or a hosted-target or Sandbox-instance identity, **When** the cleanup
   runs, **Then** it is never stopped or removed and the checkout is kept, as today.
5. **Given** an unlabeled container whose recorded creating job is not terminal, **When**
   the operator plans, **Then** the container is protected and the workspace is kept.

---

### User Story 3 - Cleanup removes only what was planned, and refuses what changed (Priority: P1)

Between plan and confirmation the host keeps running. A planned container may be
restarted, gain a mount or labels, or be replaced by another container with the same
name; a new unplanned container may bind the checkout. The cleanup removes only the
containers the reviewed plan named, and only while each still matches its planned
evidence. Anything else stops the candidate with an "evidence changed" reason and keeps
the checkout.

**Why this priority**: The plan is the operator's consent. Acting on a container that
differs from what the operator reviewed is a deletion without consent.

**Independent Test**: Plan a stray container, then change it (restart it, add a mount
or label, or add a second unlabeled container bound to the checkout) and confirm the
cleanup; the candidate is refused with the evidence-changed reason, nothing is removed
and the checkout is kept.

**Acceptance Scenarios**:

1. **Given** a planned stray container that was restarted after the plan, **When** the
   cleanup runs, **Then** the candidate is refused with the evidence-changed reason and
   no container or file is removed.
2. **Given** a planned stray container that gained a mount, a label or a network after
   the plan, **When** the cleanup runs, **Then** it is refused with the same reason.
3. **Given** a new unlabeled container bound to the checkout that the plan did not name,
   **When** the cleanup runs, **Then** the candidate is refused and neither container is
   touched.
4. **Given** a planned stray container that was already removed by hand, **When** the
   cleanup runs, **Then** the container is treated as already absent and the checkout
   removal proceeds.

---

### User Story 4 - A crashed cleanup can be rerun safely (Priority: P2)

A cleanup run may die after writing the manifest intent for a container and before or
after removing it. Running the cleanup again with a fresh plan of the same workspace
finishes the job: an already-removed container is accepted as absent, a still-present
one is processed again under the same ownership checks, and nothing outside the plan is
touched.

**Why this priority**: Reclaim runs on a timer and over flaky SSH; a partial run must
not leave a state that blocks or widens the next run.

**Independent Test**: Simulate a run that records intent and stops before removal, then
rerun; the second run completes the removal once, with one intent line per container per
run, and the outcome is the same as an uninterrupted run.

**Acceptance Scenarios**:

1. **Given** a manifest with a container intent and no outcome from a crashed run,
   **When** the operator plans and confirms again, **Then** the container is removed (or
   found absent), the checkout is removed, and the final outcome is removed.
2. **Given** a partial failure where the container could not be removed, **When** the
   run ends, **Then** the checkout is kept and the outcome names the container that
   failed and why.

---

### User Story 5 - The plan-id cleanup forms are documented and checked (Priority: P3)

An operator copied the documented `cleanup --plan-id <id> --confirm` example for a
plan made with `--tier` and got a plan-kind mismatch. That example is the form for
cache and stale plans; a tier plan needs the same `--tier` it was planned with. The
docs state the two forms side by side, and the cleanup refuses a tier plan whose
stored tier differs from the `--tier` given, so `--tier safe` can never run a plan
reviewed for a broader tier.

**Why this priority**: A wrong documented form wastes an operator's run but deletes
nothing; a mismatched tier could run a broader plan than the operator named.

**Independent Test**: Make a `tmp` tier plan and run cleanup with `--tier safe` and
that plan id; it refuses with the plan-kind mismatch reason before the plan is begun
or anything on the host is touched. Run it with `--tier tmp`; it proceeds.

**Acceptance Scenarios**:

1. **Given** a safe-tier plan, **When** the operator runs cleanup with the plan id and
   no tier, **Then** it refuses with the plan-kind mismatch reason, and the docs say a
   tier plan needs `--tier`.
2. **Given** a tmp-tier plan, **When** the operator runs cleanup with `--tier safe` and
   that plan id, **Then** it refuses with the plan-kind mismatch reason and the plan
   stays unused.

---

### Edge Cases

- A container with no mounts at all binds nothing in the checkout, so it is never
  attached to the workspace and this path never sees it.
- A mount source that is the checkout directory itself counts as inside the checkout; a
  sibling directory whose name starts with the checkout's name does not.
- A mount source reached through a symlink is compared after resolving it; a source
  that cannot be resolved is treated as outside the checkout.
- An anonymous volume (a volume with a generated name and no Compose label) counts as a
  named volume and protects the container, since its data might be wanted.
- `host`, `none` and container-shared network modes are not the default bridge and
  protect the container.
- Two workspaces' containers are each judged against their own checkout; a container
  mounting both checkouts is outside each and is protected.
- The engine inspection fails or returns malformed data: the candidate is refused and
  the checkout kept.
- A container that is already stopped is removed without a stop attempt failing the
  run.
- A planned container replaced by a new container with the same name has a different
  identity and is refused as changed.
- Running the cleanup on more containers than the per-candidate limit: the planner
  protects the workspace instead of offering a partial set.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The planner MUST classify each container bound to a workspace checkout as
  a stack member (the workspace's own Compose project), a reclaimable stray, or
  protected, and MUST NOT offer the workspace for removal while any bound container is
  protected.
- **FR-002**: A container MUST be a reclaimable stray only when all of these hold:
  it is bound to a workspace checkout (not a base deployment); it has no Compose, stack
  or Sandbox label and no `sandbox-` name; every mount is a bind mount whose source,
  both as written and resolved, is the checkout or inside it; it uses no named or
  anonymous volume and no tmpfs mount; its restart policy is none; its network mode and
  every attached network is the default bridge; and its creating job, when the
  container records one, is terminal.
- **FR-003**: A protected container MUST be reported in the plan with the rule that
  protects it (mount outside the checkout, named volume, non-default network, foreign
  project, hosted or instance identity, active job, or evidence unavailable).
- **FR-004**: The plan MUST list each reclaimable stray on its candidate with its
  evidence: full container identity, name, image, mount sources, networks, running
  state, creation time, last start time and restart count.
- **FR-005**: The cleanup MUST re-inspect each planned stray immediately before acting
  and MUST refuse the candidate with an evidence-changed reason when the identity,
  labels, mounts, networks, creation time, last start time or restart count differ from
  the plan, or when a container bound to the checkout is present that the plan did not
  name.
- **FR-006**: The cleanup MUST write a manifest intent for each stray container before
  stopping it, then stop it, then remove it, and only then remove the checkout.
- **FR-007**: The cleanup MUST NOT stop or remove any container the reviewed plan did
  not name, and MUST NOT use any engine-wide prune of containers or volumes.
- **FR-008**: When any planned stray cannot be stopped or removed, the cleanup MUST
  keep the checkout and report which container failed and why.
- **FR-009**: A planned stray that is already absent at cleanup time MUST be treated as
  removed, so a rerun after a crash completes without error.
- **FR-010**: Every existing protection of workspace teardown MUST remain: hosted
  `sandbox-host-*` projects, foreign Compose projects, shared volumes and networks with
  outside endpoints are never removed.
- **FR-011**: The docs MUST state that a cache or stale plan id is applied without
  `--tier` and a tier plan id with the same `--tier` it was planned with; the cleanup
  MUST refuse, before beginning the plan, a tier plan whose stored tier differs from
  the given `--tier`.
- **FR-012**: A remote runs the new behavior only after its runtime is migrated; the
  docs MUST say so, and an older runtime MUST keep refusing as today rather than act on
  fields it does not check.
  A container record without the new evidence (from an older probe) MUST be
  planned as today, and the host MUST refuse it as today.
- **FR-013**: A base deployment (a checkout without the workspace marker) MUST keep
  today's behavior; stray reclaim applies to workspace checkouts only.

### Key Entities

- **Stray container evidence**: the facts the planner recorded for one container it
  intends to remove: identity, name, image, labels, mount list (type, source,
  destination, volume name), network mode and networks, running state, created and
  started times, restart count, and the checkout it was judged against.
- **Protected container report**: a bound container the planner refused to own, with
  its identity, name and the rule that protects it.
- **Deletion manifest entry**: the existing per-run intent and outcome lines, extended
  with one intent per stray container written before its stop.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A finished workspace whose only leftover is a stray container inside its
  checkout is reclaimed in one confirmed cleanup, with zero partial outcomes.
- **SC-002**: Across the protection cases (mount outside, named volume, non-default
  network, foreign project, hosted, instance, active job), zero containers are stopped
  or removed and every checkout is kept.
- **SC-003**: A container that changed after the plan is never stopped or removed; the
  candidate reports the evidence-changed reason in 100% of the changed-since-plan cases.
- **SC-004**: Every container stop is preceded by its manifest intent line in the same
  run, and a rerun after a crash at any point reaches the same final state as an
  uninterrupted run.
- **SC-005**: Each documented plan-id cleanup example runs as written for its plan
  kind, and a tier mismatch is refused with nothing begun or touched.

## Assumptions

- Sandbox puts no job label on containers, and any container with a Sandbox label is
  protected outright, which is stricter than "its job is terminal". For an unlabeled
  stray the creating job is a job of that checkout, so the workspace's job evidence is
  the job check: any non-terminal job on the checkout protects it at plan time
  (`active_job`), and the host re-reads the job registry right before teardown and
  skips the checkout if a job became active or the registry is unreadable
  (`job_active_since_plan`).
- A Sandbox instance or hosted target is recognised by its Compose project label or
  Sandbox labels; an unlabeled container is neither.
- The per-candidate container limit is small (on the order of ten); a workspace with
  more bound strays is protected for manual review.
- Real cleanup on xspeed-dev is run by the orchestrator after merge; verification here
  uses unit tests on the remote and a dry plan.
- The local (non-remote) reclaim path uses the same workspace teardown helper and gains
  the same behavior.
