# Product Requirements Draft: Stack branch follower

**Status**: Ready

**Created**: 2026-11-15

**Last Refined**: 2026-11-15

**Input**: "Server-side follower that re-applies a hosted stack when a tracked Git branch moves"

**Drafting Configuration**: Claude Opus 5.5 root session (implementation sub-agent); owner brief as the decision record

**Final Validation**: `PASS` — independent readiness review (Haiku 5.5, read-only); three earlier REOPEN rounds applied

**Validated On**: 2026-11-15

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

A hosted test stack on a Sandbox remote shows only the commits it was last
applied from. Today someone has to notice that a branch moved, rebuild the
stack's inputs and run `sb host stack up` again from a controller that is
online. The xSpeed team wants one stack on `xspeed-dev` (Hub, WordPress with
xSpeed Free and Pro, mail) that always runs the `dev` branches of three
repositories, so QA and PR sites attach to current code without anyone acting.
The owner approved building this ("ask 2" of the xspeed-dev test-stacks plan).

## Users and Desired Outcomes

- **Remote owner**: declares once which stack follows which branches, and can
  see, pause, resume, re-run (`run-now`) or remove that follow later, every
  change recorded.
- **QA and PR authors**: the shared stack serves the latest commits of the
  tracked branches a few minutes after each merge, with no laptop online.
- **Other operators of the same remote**: an apply they start is never
  interleaved with a follower apply, and the follower never touches any target
  other than the declared stack.

## Goals

- The remote itself notices that a tracked branch moved and re-applies the
  declared stack, with no controller online.
- Each re-apply runs the declared prepare step and then the same stack apply a
  person would run, as a durable job with its normal evidence.
- At most one apply runs on a target at a time; a busy target is skipped and
  checked again on the next poll.
- A failed re-apply leaves the stack's previous deployment as the failure
  handling of a normal apply leaves it, is recorded, and is not retried until a
  new commit arrives or the owner asks for a retry.
- Private repositories are read with one read-only deploy key per repository
  that the remote generates; only its public half is ever shown.

## Non-Goals

- Webhooks or any inbound connection from GitHub.
- Following branches for targets that are not Sandbox stacks, or for remotes
  that are not joined (admitted Tailnet) registrations.
- Post-apply steps such as a mail name or a WordPress attach. The follower
  runs only the prepare step and the stack apply; it may create a stack that
  does not exist yet, but its first-time steps stay with the owner.
- Building plugin archives or any repository-specific preparation inside
  Sandbox; that belongs to the declared prepare step.
- Writable deploy keys, key rotation policy, or adding keys to GitHub.

## Product Scenarios

### Scenario 1 — Declare a follow

- **Starting state**: a joined remote; the owner knows the stack name,
  template, repositories, branches and prepare step.
- **User action**: `sb host follow add --remote R --name NAME ...`.
- **Expected outcome**: the declaration is stored on the remote, the background
  poll is enabled, each repository without a key gets one, and only the public
  keys are printed with the GitHub step the owner must take. Nothing counts as
  applied yet, so the first successful poll applies the current heads once.
  A refused declaration stores nothing and creates no key.

### Scenario 2 — A tracked branch moves

- **Starting state**: an active follow whose last apply succeeded.
- **User action**: none; a commit lands on a tracked branch.
- **Expected outcome**: within one poll interval the remote fetches, sees the
  new head, runs the prepare step and the stack apply in a durable job, and
  records the applied heads and job ids.

### Scenario 3 — Target busy

- **Starting state**: a person's apply (or an earlier follower apply) is running
  on the same target.
- **Expected outcome**: the poll records "busy" and starts nothing; the next
  poll tries again.

### Scenario 4 — Apply fails

- **Starting state**: the prepare step or the apply fails.
- **Expected outcome**: the failure, the heads it tried and the job id are
  recorded; later polls do not retry those heads; only a new commit or
  `run-now` starts a new attempt. The stack keeps its previous deployment
  (containers and route) exactly as a failed person-run apply leaves it; the
  follower removes nothing.

### Scenario 5 — Owner controls

- **User action**: `status`, `pause`, `resume`, `run-now`, `remove`.
- **Expected outcome**: status shows tracked and applied heads, the last
  attempt and its outcome, and any busy reason with its age; pause stops new
  attempts without changing the stack (an attempt already running finishes and
  is recorded); resume re-enables polling but keeps failed heads blocked;
  run-now starts an attempt at once even for unchanged or failed heads; remove
  deletes the declaration (and the poll when it was the last one), keeps the
  stack running and keeps deploy keys, which other follows may share, and is
  refused while an attempt runs. Every one of these is recorded in an audit log
  on the remote.

### Negative scenarios

- A non-joined remote, an invalid name, an unknown repository host, or a
  prepare step that names no program is refused before anything is stored.
- A fetch that fails (key not yet added, network) is recorded and retried on the
  next poll; it never starts an apply.
- A writer lease left by another controller keeps the target busy; status shows
  it with its age and the follower never breaks it (existing lease policy).
- A followed stack that was applied before and has since been taken down or
  reaped is not brought back: the follow pauses and records why.
- `run-now` on a paused follow is refused with a reason; resume first.
- A tracked branch that was deleted fails the fetch (recorded, no apply); a
  force-pushed branch is simply a new head and is applied.
- A prepare step that exceeds its time limit fails the attempt with a timeout
  reason; nothing is applied.
- The follower never runs an apply for a stack other than its declared one and
  refuses declarations that would target the apex domains the hosting rules
  protect.

## Proposed Product Behavior

- The poll runs on the remote every 5 minutes.
- A follow declares: stack name, stack template, stack ref, owner label,
  optional environment and lifetime, repositories with one tracked branch each,
  and an optional prepare step with an owner-set time limit (default 20
  minutes).
- Owner commands, attempt starts and attempt outcomes are audited; a poll that
  skips only updates the follow's last-check record.
- A change means any tracked head differs from the last applied heads.
- Outcome states: up to date, busy (skipped), running, applied, failed (waiting
  for a new commit or a retry), paused, fetch failed.
- Every owner action and every attempt is appended to an audit log on the
  remote; attempts are durable jobs with their usual logs.

## Constraints and Dependencies

- Runs on the remote, as the remote's Sandbox owner, through the existing
  server-side job contract and the existing stack apply path.
- Respects the existing per-target writer lease and the stack's own "apply in
  progress" refusal.
- Private keys never leave the remote and never appear in output, logs or
  state; GitHub host identity is pinned, never trusted on first use.
- Hosting rules: never touch the apex `xspeedcdn.com` / `xspeedcache.com`.
- The xspeed-dev rollout needs a server-side prepare script in
  xspeed-dev-setup (overlay plus plugin archives), outside this repository.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Trigger | Background poll on the remote every 5 minutes; no webhooks | No inbound exposure; works with no controller | Owner brief |
| GitHub host identity | Pinned to GitHub's published host key, never trusted on first use | A deploy key must not be offered to an unverified host | Assumption (security default) |
| First apply | No heads count as applied after `add`; the first poll applies once | A re-apply of the running commits is harmless; no extra flag | Assumption |
| Removed stack | A previously applied stack that is gone pauses the follow | Never resurrect a stack someone took down | Assumption |
| Credentials | Read-only deploy key per repository, generated on the remote; only the public key printed | Owner approved generating it | Owner brief, test-stacks plan |
| Failure policy | Record, no tight retry; retry on new commit or manual retry | Safe failure | Owner brief |
| Concurrency | Lease-respecting; busy means skip | Never two applies on one target | Owner brief |
| Commands | `sb host follow add\|status\|pause\|resume\|remove\|run-now`, all audited | Owner brief | Owner brief |
| Prepare step and keys | The prepare step runs as the user that owns the deploy keys and could read them; accepted residual risk because the keys are read-only and the step is owner-declared | Separate Linux users are out of scope | Assumption; flag for human security review |
| Deploy keys after remove | Kept on the remote and shared by follows of the same repository; deleted by hand | Avoids breaking another follow; GitHub side is the owner's | Assumption; flag for human security review |
| run-now while paused | Refused; resume first | Pause must hold | Assumption |
| One follow per stack | A declaration is keyed by stack name on the remote; `add` with the same name replaces it | No two followers can race on one stack | Assumption |
| Scope | Joined remotes and Sandbox stacks only | The server-side job contract exists only there | Assumption (existing server CLI policy) |

## Open Questions

- None blocking. Rollout questions (where the prepare step builds plugin
  archives on the remote) belong to xspeed-dev-setup.

## Acceptance Outcomes

- After a merge into a tracked branch, the stack serves the new commit within
  one poll interval plus the apply time, with no controller online.
- An apply already running on the target causes zero follower applies to start.
- A failing commit produces exactly one recorded attempt until a new commit or
  a manual retry.
- No private key material appears in any output, log, audit entry or state file
  that Sandbox itself writes; `add` prints only public keys. What the owner's
  prepare step prints is the owner's responsibility.
- A refused declaration leaves no declaration, key or poll behind.
- Every owner command, attempt start and attempt outcome leaves exactly one
  audit entry; a poll that skips (busy, fetch failed, up to date, paused) only
  updates the follow's last-check record.
- After a failed attempt, the stack's previous containers and route are what a
  failed person-run apply would leave.

## Risks and Assumptions

- **Risk**: a canonical stack apply also runs the existing merge sweep of
  temporary copies merged into the tracked dev branch (spec 077); following
  `dev` makes that sweep run after each merge.
- **Risk (security, human review)**: the prepare step is owner-supplied code run
  on the remote as the same Linux user that owns the deploy keys, so it could
  read them. It is declared only by the owner, runs with a time limit, gets no
  Git credential settings, and its output goes to the job log.
- **Assumption**: the remote's Sandbox owner service is installed and admitted
  (joined), so server-side jobs can be started locally.

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
