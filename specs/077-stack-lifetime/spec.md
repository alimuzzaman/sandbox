# Feature Specification: Stack Lifetime and Per-Copy Stack Identity

**Feature Branch**: `feat/stack-lifetime`

**Created**: 2026-11-10

**Status**: Draft

**Input**: User description: "Give every hosted stack and hosted target a lifetime (temporary, hosted, permanent), recorded on the controller and on the remote so the server-side reap and the daily cleanup routine can read it. Temporary is the default for a PR or feature ref, with a 14-day TTL that can be set and extended; it is torn down once the TTL passes or the ref's commit is merged into the repo's dev branch. Hosted is only the one canonical stack of a repo's dev branch and is never reaped. Permanent is for non-PR apps deployed through Sandbox (Agent Notes, mockups) and is never reaped. A missing record is never a delete signal. A stack is one copy, not one branch: many people, and one person many times, can run stacks of the same branch. The remote holds the authoritative record with an owner; reusing another owner's or copy's slug is refused, never taken over. Owner policy, verbatim: 'any instance for pr should be tmp instance. only the stack for dev branch should be hosted. also the agent note, mockup etc should be permanent if deployed using sandbox' and 'each person will create separate/duplicate version of the same branch pr. also one person may need to create multiple version from same branch etc.'"

## Clarifications

### Session 2026-11-10

Answered in this delegated thread from the owner's brief and its stated defaults; no
interactive answer was available. Each can be overridden by the owner.

- Q: When the server reaps a target whose DNS records were created per hostname, does
  it reap the server side or skip the target? → A: Reap the server side and leave the
  DNS records; the reaped record lists them and a controller-side teardown removes
  them.
- Q: How does an owner "explicitly target their own existing copy" from another
  controller? → A: By naming the copy id shown in the listing (`--copy <id>`); the
  start is accepted only when the caller's owner label matches the record's.
- Q: What happens when the owner re-runs a temporary stack that has already expired
  but is not yet reaped? → A: It is refused as expired unless a new time limit is
  given, which resets the expiry; re-running without one keeps the current expiry.
- Q: What does a generated name look like? → A: `<branch-short>-<owner-short>-<n>`,
  where n is the lowest counter free on the remote, shortened to fit the template's
  name rule; a short random suffix replaces n only if the counter cannot fit.
- Q: Who records merge evidence, given the server holds no Git history of the repo?
  → A: A controller, from its own checkout, when the canonical dev stack is updated
  and on demand (`stack sweep`); the server acts on the recorded evidence or the TTL.
- Q: Which record fields does the panel use? (agreed with the xspeed-dev orchestrator,
  final) → A: a flat record with `schema_version` (1), `created_at`/`updated_at`
  (RFC3339 UTC), `lifetime`, `lifetime_source` (`default|flag|classified`),
  `ttl_seconds`, `expires_at`, `extended_count`, `owner` `{label, controller}`,
  `copy_id`, `slug`, `ref`, `ref_branch`, `commit`, `repo`, `canonical`, `state`
  (`active|reaping|reaped`) and `reap` `{eligible, reason: expired|merged|null,
  merged_into, merged_at}`. A classified target has null `slug`, `copy_id`, `ref`,
  `ref_branch` and `ttl_seconds` (unless temporary), `commit`/`repo` when known, and
  `classified_by` plus `classified_at`.
- Q: What is the owner label? → A: `--owner` or the git `user.name`; never an email
  (the panel is shown to every tester). `owner.controller` stays an opaque id.
- Q: What is unique for hosted? → A: at most one canonical stack per (repo, dev
  branch, remote), enforced under the store lock.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A PR stack cleans itself up (Priority: P1)

A developer starts a stack for a PR branch on a shared remote and forgets about it.
Without anyone acting, the stack is torn down once its time limit passes or once the
branch is merged into the repo's dev branch, and every removal is recorded before it
happens. Nothing else on the server is touched.

**Why this priority**: Forgotten PR stacks fill the shared server (disk, ports,
hostnames). Automatic, safe cleanup is the main value of the feature.

**Independent Test**: Start a stack from a feature branch with a short time limit,
let the limit pass, run the server's cleanup, and confirm the stack's containers,
volumes, route, certificates and runtime files are gone, a deletion record exists for
each, and an unrelated hosted target and an unclassified target are untouched.

**Acceptance Scenarios**:

1. **Given** a stack started from a feature ref with no lifetime flag, **When** the
   stack record is shown, **Then** its lifetime is temporary, its source is default, its
   time limit is 14 days and its expiry is 14 days after the start.
2. **Given** a temporary stack whose expiry has passed, **When** the daily cleanup
   routine or a safe-tier reap runs, **Then** the stack's containers, volumes, networks,
   built images, route, certificates and runtime files are removed, each removal is
   recorded before it happens, and the stack record is kept as "reaped".
3. **Given** a temporary stack whose commit is reachable from the repo's dev branch,
   **When** the merge check has recorded that and cleanup runs, **Then** the stack is
   torn down even though its time limit has not passed.
4. **Given** a temporary stack whose branch was closed without being merged, **When**
   cleanup runs before its expiry, **Then** the stack is kept.
5. **Given** a temporary stack that has not expired and is not merged, **When** cleanup
   runs, **Then** nothing of the stack is removed.

---

### User Story 2 - Many copies of the same branch, never taken over (Priority: P1)

Several people each start their own stack of the same PR on the same remote, and one
person starts two copies of one branch with different data. Each copy gets its own
names, hostnames, ports, database and mail catcher. Nobody can silently replace
someone else's copy by picking the same name from another machine.

**Why this priority**: Today a second person who picks the same name from another
machine silently takes over the first person's stack. That is data loss for the first
person and is the second blocker for shared PR testing.

**Independent Test**: From two controllers with different owners, start a stack with
the same name on one remote; the second is refused with a clear "name taken" result
naming the holder. Start two stacks of one branch without a name from one controller;
both succeed with distinct generated names and share no hostname, port, volume or
database.

**Acceptance Scenarios**:

1. **Given** a stack named N on remote R owned by A, **When** B starts a stack named N
   on R, **Then** it is refused as "stack name taken", naming the owner, and nothing on
   R changes.
2. **Given** a stack named N on R created by one copy, **When** the same owner starts N
   again from a controller that does not hold that copy, **Then** it is refused unless
   the owner explicitly targets that existing copy.
3. **Given** a stack started without a name, **When** it is created, **Then** its name
   is generated from the branch, the owner and a counter (or a short random suffix),
   and every hostname, port, volume and project name derives from that unique name.
4. **Given** the same owner re-runs a stack they hold from the controller that created
   it, **When** they start it again at a new commit, **Then** it updates in place and
   keeps its name, ports, lifetime and expiry.
5. **Given** a project name already held on R by a different stack or by a target
   applied outside stacks, **When** a stack would render the same project name, **Then**
   it is refused.

---

### User Story 3 - See and manage everyone's stacks (Priority: P2)

A developer lists all stacks on a remote and sees every person's stacks, not only the
ones their own machine created, with owner, branch, lifetime and expiry. They extend
the time limit of their own stack, and cannot tear down or extend someone else's
stack by accident.

**Why this priority**: Shared visibility makes the lifetime policy usable; ownership
checks prevent one person's cleanup from deleting another's work.

**Independent Test**: Create stacks from two controllers; list from either and see
both with owners and expiries; extend one's own stack and see the new expiry; try to
tear down the other owner's stack and see a refusal; repeat with the explicit override
and see the override recorded.

**Acceptance Scenarios**:

1. **Given** stacks created on R from two controllers, **When** either lists stacks for
   R, **Then** both appear, each with owner, ref, lifetime, expiry and state.
2. **Given** a temporary stack A owns, **When** A extends it by 14 days, **Then** its
   expiry becomes 14 days from now and the record shows the new time limit.
3. **Given** a stack B owns, **When** A tears it down or changes its time limit without
   the explicit owner override, **Then** it is refused naming B; with the override it
   proceeds and the override (who, when) is recorded.
4. **Given** a stack the server already reaped, **When** its owner tears it down,
   **Then** the controller-side leftovers (local records, DNS records it created) are
   removed and the stack disappears from the list.

---

### User Story 4 - The dev stack and long-lived apps are never reaped (Priority: P2)

The one stack that runs a repo's dev branch is marked hosted, and apps that are not
tied to a PR (Agent Notes, mockups) are marked permanent. Neither is ever reaped.
Existing targets applied before this feature stay protected until someone classifies
them, and can be classified without being redeployed.

**Why this priority**: Reaping must never remove the shared dev environment or
long-lived apps; existing targets must stay safe through the rollout.

**Independent Test**: Mark a dev-branch stack as the canonical hosted stack and an
app as permanent; let cleanup run with both past any time limit; both remain. Run
cleanup on a remote with an unclassified pre-existing target; it remains. Classify the
pre-existing target as permanent without redeploying it and see the classification.

**Acceptance Scenarios**:

1. **Given** a stack whose ref is the repo's dev branch started as hosted, **When** it
   is recorded, **Then** it is the canonical hosted stack for that repo on that remote
   and cleanup never removes it.
2. **Given** a canonical hosted stack for a repo already exists on R, **When** another
   stack of the dev branch is started as hosted, **Then** it is refused; started without
   a lifetime flag it is temporary.
3. **Given** a stack or target classified permanent, **When** cleanup runs at any time,
   **Then** it is never removed.
4. **Given** a target applied outside stacks with no lifetime record, **When** cleanup
   runs, **Then** it is protected and reported as unclassified.
5. **Given** an existing target, **When** an operator classifies it as hosted, permanent
   or temporary (with a time limit), **Then** the record is written on the remote
   without redeploying the target, and its source is "classified".
6. **Given** a non-dev ref, **When** someone asks for lifetime hosted, **Then** it is
   refused; hosted is only for the repo's dev branch.

---

### User Story 5 - Copies cannot fill the server (Priority: P3)

Before a new copy is created the remote enforces limits: a per-owner cap, a
remote-wide cap and a minimum of free disk. A refused start says which limit and how
to free room.

**Why this priority**: Per-copy identity makes it easy to start many stacks; limits
keep the shared server usable.

**Independent Test**: Set a per-owner cap of 1, start one stack, try a second as the
same owner and see a refusal naming the cap; a different owner still succeeds.

**Acceptance Scenarios**:

1. **Given** an owner already at the per-owner cap of active temporary stacks on R,
   **When** they start another new copy, **Then** it is refused naming the cap and
   their stacks; re-running an existing copy is not counted as new.
2. **Given** R at its remote-wide cap, **When** anyone starts a new copy, **Then** it
   is refused naming the cap.
3. **Given** free disk on R below the minimum, **When** a new copy is started, **Then**
   it is refused naming the free space and the minimum.

---

### Edge Cases

- A record exists but cannot be parsed, or names a project that does not match its
  target: treated as unclassified (protected), reported, never reaped.
- A record exists on the remote but the target is already gone: cleanup marks it
  reaped without error.
- Cleanup is interrupted mid-teardown: the record stays "reaping"; the next run
  resumes and finishes; a second concurrent run does not start the same teardown.
- A host apply or stack start for the target is in progress when cleanup runs:
  cleanup skips the target for that run.
- The TTL is extended or the lifetime changed between the cleanup plan and its
  execution: execution re-reads the record and skips the target.
- The merge check cannot reach the dev branch (offline, missing checkout): no merge
  evidence is recorded; the stack waits for its TTL.
- A branch merged by squash or rebase: its commit is not reachable from dev, so it is
  not seen as merged and waits for its TTL.
- The controller that created a stack is gone: the stack is still listed from the
  remote, still reaped by TTL, and its owner can manage it from another controller
  with the explicit owner override.
- A record was written by a newer Sandbox with unknown fields: unknown fields are kept
  on rewrite; an unknown lifetime value is treated as protected.
- Two controllers start the same name at the same moment: exactly one claims it.
- A stack's hostnames are shared through wildcard coverage: reaping releases only
  that target's own route and certificates, never the shared wildcard record or the
  fallback route.
- A target whose DNS records were created per hostname: the server cannot remove
  those records; they stay until the owner tears the stack down from a controller,
  and the reaped record says so.
- A non-compose container left in a stack workspace (a stray test browser): reported
  in the teardown result; stack teardown removes containers by the target's project
  label only.

## Requirements *(mandatory)*

### Functional Requirements

**Lifetime**

- **FR-001**: Every stack and every classified hosted target MUST carry exactly one
  lifetime: temporary, hosted or permanent, plus the lifetime's source (default, flag
  or classified).
- **FR-002**: A stack started from any ref other than the repo's dev branch MUST
  default to temporary. A stack of the dev branch without a lifetime flag MUST also be
  temporary; hosted requires the explicit flag.
- **FR-003**: A temporary lifetime MUST carry a time limit (default 14 days) and an
  expiry instant. The time limit MUST be settable when the stack starts and changeable
  later; changing it sets the expiry to now plus the new limit.
- **FR-004**: Hosted MUST be allowed only for a stack whose ref is the repo's dev
  branch, and at most one hosted stack per (repo, dev branch) per remote MUST exist;
  it is the canonical dev stack.
- **FR-005**: Permanent MUST be allowed for any stack or target and MUST never expire.
- **FR-006**: The repo's dev branch MUST default to `dev` and MAY be set per template.
- **FR-007**: The lifetime record MUST be stored on the remote, where server-side
  cleanup reads it, and mirrored on the controller.
- **FR-008**: A target with no lifetime record, or with an unreadable or invalid one,
  MUST be treated as protected. A missing record MUST never cause a deletion.
- **FR-009**: Operators MUST be able to classify an existing target (one applied
  outside stacks) as temporary, hosted or permanent without redeploying it, and to
  remove a classification (which makes it protected again).

**Reaping**

- **FR-010**: Server-side cleanup (the daily routine and the safe-tier reap) MUST tear
  down a temporary target when, at execution time, its expiry has passed or merge
  evidence is recorded for it. It MUST never tear down a hosted, permanent,
  unclassified or not-yet-eligible target.
- **FR-011**: Teardown MUST remove the target's containers, volumes, networks, own
  built images, route, certificates and runtime files, and MUST record each removal
  in the deletion manifest before it happens.
- **FR-012**: Teardown MUST be resumable: an interrupted teardown leaves the record in
  a "reaping" state and the next run finishes it; a completed teardown leaves a
  "reaped" record.
- **FR-013**: Cleanup MUST skip a target while a host apply, recovery or destroy for it
  is in progress, or while another cleanup holds the host.
- **FR-014**: Merge evidence MUST come from a reachability check of the stack's commit
  against the repo's dev branch, using only the access the controller already has.
  Closed-but-unmerged branches MUST be reaped by TTL only.
- **FR-015**: The merge check MUST run on a controller, from its own checkout, when
  the canonical hosted dev stack is updated and on demand, and MUST record its evidence (dev branch, dev commit, time) on each
  affected record.
- **FR-016**: Server-side cleanup MUST never remove a shared wildcard DNS record, a
  shared fallback route, or a DNS record created per hostname; the last remains until
  the stack is torn down from a controller, and the reaped record MUST say so.
- **FR-017**: After a server-side reap, tearing the stack down from a controller MUST
  remove the remaining controller-side state and DNS records and drop the stack from
  the list.

**Identity and ownership**

- **FR-018**: A stack MUST be identified on the remote by its name, held by exactly one
  copy. The ref MUST be an attribute, never the identity.
- **FR-019**: Each stack record MUST name its owner: the controller identity (opaque)
  and a human label (the git user name by default, or an explicit owner label). An
  email address MUST be refused as a label.
- **FR-020**: Claiming a name or project on a remote MUST be atomic across controllers.
  A start whose name or project is held on that remote by another owner or another
  copy MUST be refused as "stack name taken" and MUST change nothing.
- **FR-021**: Re-running a stack MUST be allowed from the copy that holds it. The
  owner MAY explicitly target their own existing copy from another controller by
  naming its copy id; this is accepted only when the owner label matches.
- **FR-021a**: Re-running an expired, not-yet-reaped temporary stack MUST be refused
  as expired unless a new time limit is given; re-running without one keeps the
  current expiry.
- **FR-022**: A stack started without a name MUST get a generated unique name of the
  form `<branch-short>-<owner-short>-<n>` (n the lowest counter free on the remote; a
  short random suffix only when the counter cannot fit), valid for the template's name
  rules. All hostnames, ports, volumes and the project name MUST
  derive from that name, so two copies never share any of them.
- **FR-023**: Tearing down a stack or changing its time limit or lifetime MUST be
  refused for a stack the caller does not own unless an explicit owner override is
  given; an override MUST be recorded (who and when) on the record.
- **FR-024**: Listing stacks for a remote MUST show every stack recorded on that
  remote, from any controller, each with owner, ref, lifetime, expiry and state.

**Limits**

- **FR-025**: Before claiming a new copy the remote MUST enforce a per-owner cap on
  active temporary stacks (default 5), a remote-wide cap on stack records (default
  40) and a minimum free disk (default 20 GiB, or the remote's storage floor when it is
  higher). Each limit MUST be configurable per remote. Re-running an existing copy
  MUST not count as a new copy.

**Panel**

- **FR-026**: The record MUST expose the fields agreed with the xspeed-dev
  orchestrator (see Clarifications): `schema_version`, `created_at`, `updated_at`,
  `lifetime`, `lifetime_source`, `ttl_seconds`, `expires_at`, `extended_count`,
  `owner`, `copy_id`, `slug`, `ref`, `ref_branch`, `commit`, `repo`, `canonical`,
  `state`, `reap` and, for classified targets, `classified_by` and `classified_at`.
  `stack list --json` MUST return the same fields per row. Records MUST be written to
  a temporary file in the same directory, flushed to disk, then renamed; a reader
  that meets a malformed record MUST treat it as unclassified and protected.

**Compatibility**

- **FR-027**: Stacks created before this feature MUST keep working: on their next start
  they are claimed on the remote by their existing controller and get the default
  lifetime; until then they are unclassified and protected.
- **FR-028**: Every refusal MUST use a stable code and a message naming the holder,
  limit or reason, and MUST be returned before any remote change.

### Key Entities

- **Lifetime**: one of temporary, hosted, permanent; with its source, time limit and
  expiry.
- **Target record (remote)**: the authoritative record for one hosted target on a
  remote: target identity (project, environment, project name on the server), lifetime,
  state (active, reaping, reaped), reap evidence, and for stacks the stack identity.
- **Stack identity**: name, copy id, owner (controller id and label), repo, ref, branch,
  commit, template, whether it is the canonical dev stack.
- **Merge evidence**: dev branch name, dev commit, time of the check, which controller
  checked.
- **Remote limits**: per-owner cap, remote-wide cap, minimum free disk.
- **Reap record**: the deletion manifest lines and the record's reaped state with the
  run that reaped it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of temporary stacks past expiry or with merge evidence are removed
  by the first cleanup run after they become eligible, with a deletion record for each
  removed resource.
- **SC-002**: 0 hosted, permanent or unclassified targets are removed by cleanup in
  the test suite and in the live check.
- **SC-003**: Two controllers starting the same name on one remote never both succeed
  (0 takeovers across repeated concurrent attempts in the test suite).
- **SC-004**: Ten copies of one branch can run on one remote with no shared hostname,
  port, volume, database or mail catcher.
- **SC-005**: A listing from any controller shows 100% of the stacks recorded on the
  remote, with owner and expiry.
- **SC-006**: An interrupted teardown is completed by the next cleanup run with no
  manual step.

## Assumptions

- Trust model: everyone with access to a remote can already run commands there.
  Ownership checks prevent accidents and silent takeovers; they are not a security
  boundary against a hostile operator.
- The merge check uses `git fetch` with the controller's existing access; squash- and
  rebase-merged branches are not detected and fall back to the TTL.
- The server cannot remove DNS records created per hostname (it holds no DNS
  credentials); with wildcard coverage there are none.
- Classifying the xspeed-dev targets is done by the orchestrator after this feature is
  merged and migrated, not by this work. The guided targets are never touched here.
- A remote must run a Sandbox runtime with this feature (after `sb remote service
  migrate`) for server-side cleanup to reap; until then records are written but
  nothing is reaped.
- The existing controller identity (a random id per controller) is reused as the
  owner's controller id.
- A stray non-compose container in a CI or development workspace is outside stack
  teardown; the workspace reap reports it and keeps the checkout, as it does today.
