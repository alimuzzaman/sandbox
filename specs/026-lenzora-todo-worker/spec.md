# Feature Specification: Lenzora TODO Worker

**Feature Branch**: `026-lenzora-todo-worker`

**Created**: 2026-07-13

**Status**: Approved

**Input**: User description: "For Lenzora, make Hermes do all `TODO.md` tasks."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Progress TODO Work Safely (Priority: P1)

As the Lenzora owner, I want Hermes to work through the unchecked tasks in the repository-root `TODO.md` so the documented backlog advances without manual dispatch.

**Why this priority**: The requested source of truth is `TODO.md`; without a worker tied to that file, the backlog cannot advance automatically.

**Independent Test**: With a clean dedicated Lenzora worktree and one approved unchecked Markdown task, trigger the worker and verify it selects only that first task, runs its required checks, and records a terminal result without committing or pushing.

**Acceptance Scenarios**:

1. **Given** a root `TODO.md` with unchecked Markdown tasks, **When** the scheduled worker runs, **Then** it works on at most the first actionable task and leaves all other tasks for later runs.
2. **Given** an earlier unchecked task explicitly blocked by a prerequisite, **When** a later unchecked task is actionable, **Then** the worker skips only the blocked item and works on the later actionable task.
3. **Given** the selected task's required checks pass, **When** the worker completes it, **Then** it marks only that task complete in its isolated worktree and leaves the change uncommitted for review.
4. **Given** the worktree is dirty, **When** the worker runs, **Then** it reports `REVIEW_REQUIRED` and changes nothing.

---

### User Story 2 - Avoid Idle or Unsafe Work (Priority: P2)

As the Lenzora owner, I want absent or empty TODO files to be a safe no-work outcome so scheduling does not create errors or invent tasks.

**Independent Test**: Trigger the worker when root `TODO.md` is missing or has no unchecked Markdown task and verify a bounded `NO_TODO_WORK` result with no file mutation.

**Acceptance Scenarios**:

1. **Given** root `TODO.md` is missing, **When** the worker runs, **Then** it reports `NO_TODO_WORK` and makes no changes.
2. **Given** root `TODO.md` has no unchecked Markdown task, **When** the worker runs, **Then** it reports `NO_TODO_WORK` and makes no changes.

## Edge Cases

- A malformed checkbox, unavailable dependency, or failed validation leaves the task unchecked and reports an actionable failure.
- If every unchecked item is explicitly blocked, the worker reports the first blocking prerequisite and changes nothing.
- A task that requires credentials, production deployment, deletion, external communication, payments, or an architectural decision is not executed autonomously and is reported for review.
- Concurrent runs do not share a worktree or process more than one task per execution.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Lenzora scheduled worker MUST treat repository-root `TODO.md` as its sole automatic work source.
- **FR-002**: The worker MUST recognize unchecked Markdown checklist items and select at most the first actionable item per run, skipping only items that explicitly state an unmet prerequisite.
- **FR-003**: The worker MUST use a dedicated isolated Lenzora worktree and require that worktree to be clean before changing files.
- **FR-004**: The worker MUST run task-relevant checks before marking its selected checklist item complete.
- **FR-005**: The worker MUST not commit, push, deploy, remove worktrees, edit credentials, or perform external mutations.
- **FR-006**: Missing or fully completed `TODO.md` MUST return `NO_TODO_WORK` without a file mutation or error status.
- **FR-007**: The desired catalog MUST keep the Kanban dispatcher, quota requeue, and Sandbox task worker disabled while their respective prerequisites are absent.
- **FR-008**: The worker MUST use the Terra profile at medium effort; Spark remains orchestration-only and Luna remains read-only.
- **FR-009**: The worker MUST return a non-empty terminal result identifying completion, no-work, or the concrete review blocker.

### Key Entities

- **TODO task**: One unchecked Markdown checklist item in Lenzora's root `TODO.md`, classified as actionable or explicitly prerequisite-blocked.
- **Lenzora TODO worker**: A scheduled, bounded Terra run that can advance one TODO task in an isolated worktree.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Each worker execution selects no more than one unchecked TODO task.
- **SC-002**: Missing or empty TODO state produces a successful no-work result in 100% of verified runs.
- **SC-003**: A verified task run leaves no commit, push, deployment, or shared-primary-worktree change.
- **SC-004**: The live catalog contains exactly one enabled job after reconciliation: the Lenzora TODO worker.
- **SC-005**: Every verified run produces an inspectable non-empty terminal result.

## Assumptions

- Lenzora's authoritative TODO file will be `TODO.md` at repository root and use standard `- [ ]` / `- [x]` Markdown checkboxes.
- One bounded task per run is safer and more reviewable than attempting the full backlog in a single run.
- The worker may safely report no work until the user adds eligible TODO tasks.
