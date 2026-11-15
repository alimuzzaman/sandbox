# Feature Specification: Stack branch follower

**Feature Branch**: `feat/stack-branch-follower`

**Created**: 2026-11-15

**Status**: Source implemented and independent review findings fixed; install and runtime acceptance pending

**Input**: The ready PRD `specs/089-stack-branch-follower/prd.md` (independent readiness
review, Haiku 5.5, 2026-11-15), which is the authoritative feature description.

## Clarifications

### Session 2026-11-15

- Q: What counts as "applied" right after `add`? → A: Nothing. The first poll that can
  fetch every repository applies the current heads once (PRD decision, accepted assumption).
- Q: Does `resume` retry failed heads? → A: No. Resume only re-enables polling; `run-now`
  is the manual retry and also forces an apply of unchanged heads.
- Q: May the follower break a lease another controller left? → A: Never. It reports busy
  with the lease age; removal stays the documented manual step.
- Q: What if the stack was taken down after the follower applied it? → A: The follow
  pauses with reason `stack_removed` (stack record gone) or `stack_reaped` (the remote's
  lifetime record says reaped); it never recreates a stack someone removed or cleanup
  reaped.
- Q: Which remotes? → A: Joined (admitted Tailnet) remotes only, through the installed
  server CLI job contract. Other remotes are refused before anything is stored.

### Session 2026-11-15 (independent review)

- Q: What if the target becomes busy while an attempt prepares or before it confirms?
  → A: The attempt ends as `busy`, not failed; its heads are not recorded as failed and
  the next poll tries them again (a `run-now` retry stays requested).
- Q: What if the stack is removed, reaped or being torn down while an attempt runs?
  → A: The attempt re-reads the target before prepare, after prepare and right before
  confirm; removed or reaped ends it as `removed` and pauses the follow; reaping is busy.
- Q: How does a follow's stack get its branch for the lifetime rules? → A: Clones are
  checked out on a local branch named after the tracked branch; with a `{repo:NAME}`
  template the lifetime follows that repository's tracked branch, and `--ref` must
  descend from its fetched head (`stack_source_unrelated` otherwise).
- Q: What is the prepare time limit's range? → A: 60–3600 s, default 1200, as the CLI
  and docs state.
- Q: How do errors surface? → A: Every refusal or error of `host follow` is one JSON (or
  `code: message`) line with a stable code, never a traceback, and a refused owner
  command is audited; an error in one follow is recorded for it and the poll continues.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Declare a follow and get deploy keys (Priority: P1)

The owner declares, from a controller, that stack `dev` on remote `xspeed-dev` follows
`dev` of three repositories, with a prepare step and the stack's template and ref. The
remote stores the declaration, enables the 5-minute poll, generates one read-only deploy
key per repository that has none, and prints only the public keys plus the GitHub step.

**Why this priority**: nothing else works without a stored declaration and fetch access.

**Independent Test**: run `add` against a fixture host home; check the stored
declaration, the rendered timer units, the key files' modes, and that output contains
public keys only. A refused `add` leaves no declaration, key or timer.

**Acceptance Scenarios**:

1. **Given** a joined remote, **When** the owner runs `sb host follow add --remote R
   --name dev --repo hub=WPDevelopers/xspeed-hub@dev ...`, **Then** the result lists
   each repository with its public key and `deploy_key: created|existing`, and an audit
   entry `add` exists on the remote.
2. **Given** a repository URL that is not GitHub over SSH or an invalid name, **When**
   the owner runs `add`, **Then** it is refused with a stable code and nothing is stored.

### User Story 2 - Re-apply when a branch moves (Priority: P1)

A commit lands on a tracked branch. Within one poll, the remote fetches, sees new heads,
checks the repositories out at exactly those commits, and starts one durable server-CLI
job that runs the prepare step and then the normal `host stack up --confirm` path. The
job waits for the stack's apply job and records the outcome.

**Why this priority**: it is the feature.

**Independent Test**: drive the poll decision with fixture heads and states; drive the
apply job body with a fake stack-up and job registry; check recorded heads and job ids.

**Acceptance Scenarios**:

1. **Given** applied heads A and fetched heads B, **When** the poll runs, **Then** one
   attempt with heads B is recorded and exactly one job is submitted with a replay-safe
   request id.
2. **Given** an attempt whose apply job succeeds, **When** it finishes, **Then** the
   applied heads become B and status reads `applied`.

### User Story 3 - Busy and failed targets are safe (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a target lease file or an active stack apply, **When** the poll runs,
   **Then** it records `busy` with the reason and age and submits nothing.
2. **Given** an attempt in flight, **When** another poll runs, **Then** it submits
   nothing.
3. **Given** a failed attempt for heads B, **When** later polls see heads B, **Then**
   they record `waiting_after_failure` and submit nothing; heads C or `run-now` start a
   new attempt.

### User Story 4 - Owner controls (Priority: P2)

`status`, `pause`, `resume`, `run-now`, `remove` and `key` act on the remote's
declaration, each audited. `status` shows tracked branches, last fetched and applied
heads, the last attempt, the busy reason and the deploy key fingerprints.

**Acceptance Scenarios**:

1. **Given** a running attempt, **When** the owner runs `remove`, **Then** it is refused
   with `follow_attempt_running`.
2. **Given** the last follow removed, **When** `remove` finishes, **Then** the timer
   units are gone and the deploy keys remain.

### Edge Cases

- Fetch fails (key not yet added in GitHub, network): recorded as `fetch_failed` with a
  redacted message; no attempt; next poll retries.
- A submitted attempt whose acceptance is unknown is replayed only with the identical
  stored argv and request id, never a new identity.
- An attempt job that ends without recording its outcome is finalized as failed from
  the job's lifecycle by the next poll.
- The stack's planned hostnames include an apex name (a registrable domain such as
  `example.com` or `example.co.uk`): the attempt fails
  before confirm with `follow_apex_hostname_refused`.
- The applied stack was taken down: the follow pauses with `stack_removed`; reaped by
  server cleanup: it pauses with `stack_reaped`, also when this happens mid-attempt.
- The target becomes busy during an attempt: the attempt ends as `busy` and the next
  poll retries the same heads.
- The timer fires while the previous poll still runs: the second poll exits as busy.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `sb host follow add|status|pause|resume|remove|run-now|key --remote R
  --name NAME` MUST run on the remote through the installed server CLI job contract for
  joined remotes and MUST be refused for other remotes before any write.
- **FR-002**: A follow declaration MUST hold: stack name, origin remote name, stack
  template path, stack ref, owner label, optional environment and lifetime, optional
  `--generate` choice, 1–8 repositories (name, GitHub owner/repo, branch), an optional
  prepare argv with placeholders and a prepare time limit (60–3600 s, default 1200).
- **FR-003**: `add` MUST validate everything before writing; MUST generate a missing
  ed25519 key per repository with an empty passphrase in an owner-only directory; and
  MUST print only public keys. Re-running `add` for the same name MUST replace the
  declaration while keeping its state and keys.
- **FR-004**: A systemd user timer MUST run `host follow tick --here` every 5 minutes
  while at least one follow exists; it is installed by the first `add` and removed by
  the last `remove`. Installing MUST refuse a host without a user manager or lingering.
- **FR-005**: The poll MUST hold a host-wide follow lock (non-blocking; busy exits) and,
  per follow, fetch only the tracked branch over SSH with the follow's key, a pinned
  GitHub host key, `StrictHostKeyChecking=yes`, batch mode and bounded time. An
  unexpected error in one follow MUST be recorded as that follow's last check
  (`follow_poll_error`) and MUST NOT stop the poll of the others.
- **FR-006**: The poll MUST decide, in order: paused → in-flight attempt → fetch result
  → stack removed or reaped → unchanged → failed heads without retry → busy target →
  start. It MUST record the decision in the follow's last check. An in-flight attempt
  whose job the job registry cannot answer for MUST stay in flight
  (`attempt_state_unknown`) for up to 12 polls before it is settled as failed.
- **FR-007**: Starting an attempt MUST record the attempt (id, heads, request id, argv)
  before submission, check each repository out at its head on a local branch named
  after its tracked branch, and submit
  one top-level server CLI job `host follow apply --attempt ID` with the follow's origin
  and routing metadata.
- **FR-008**: The apply job MUST refuse outside its server CLI job, hold the follow's
  apply lock, verify its attempt and repository heads, run the prepare step without a
  shell under its time limit, run `host stack up` plan then confirm in its own job
  context, refuse apex hostnames (registrable domains), wait for the stack's apply job,
  and record the outcome. It MUST re-read the target before prepare, after prepare and
  right before confirm (FR-009 for busy; a removed or reaped stack ends the attempt as
  `removed` and pauses the follow), and a cancelled attempt job MUST stop the prepare
  step's process group. With a `{repo:NAME}` template it MUST pass that repository's
  tracked branch and head to `host stack up`, which MUST refuse a ref that does not
  descend from that head, MUST apply the lifetime rules to that branch, and MUST record
  it as the stack's `source_branch` and `source_commit`.
- **FR-009**: A busy target (lease file present, the stack's last apply active or of
  unknown outcome, or the stack being reaped) MUST be skipped, never waited on or
  broken. A target that becomes busy during an attempt (seen by the attempt's own
  checks, a busy refusal from `host stack up`, or a failed nested apply while the target
  is busy) MUST end the attempt as `busy`, which is not a failure.
- **FR-010**: A failed attempt MUST record its heads; those heads MUST NOT be retried
  until heads change or `run-now`. The follower MUST NOT remove or roll back anything.
- **FR-011**: `run-now` MUST set a one-shot retry and start the poll service at once,
  and MUST be refused for a paused follow;
  `pause`/`resume` MUST toggle polling without affecting a running attempt; `remove`
  MUST refuse while an attempt runs and MUST keep keys.
- **FR-012**: Every owner command (a refusal too, once its job context is known) and
  every attempt start and outcome MUST append one line to an owner-only audit log on
  the remote. Every refusal or error MUST print one line with a stable code and a short
  redacted message, never a traceback. No private key, token or secret value may appear
  in any output, log, audit line or state file.

### Key Entities

- **Follow**: the declaration plus its state (`paused`, `applied`, `failed_heads`,
  `retry`, `attempt`, `last_check`, `stack_seen`).
- **Attempt**: id, heads, request id, stored job-start argv, job id, stack apply job id,
  status (`submitting|running|succeeded|failed|busy|removed|unknown`), reason, times.
- **Deploy key**: per GitHub repository, private file owner-only on the remote and named
  after a hash of the repository URL, public file and fingerprint reported.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With fixture heads, a change produces exactly one submission; ten polls
  with unchanged or failed heads produce zero.
- **SC-002**: A lease file or active stack apply produces zero submissions.
- **SC-003**: No test output, audit line or state file contains a private key marker.
- **SC-004**: On xspeed-dev, a merge into `dev` is served by the `dev` stack within one
  poll interval plus its apply time, with no controller online (runtime acceptance,
  after install).

## Assumptions

- The remote's owner service is installed and admitted; the server CLI contract and
  nested apply contract are available (`sb remote` doctor shows them).
- The repositories are on GitHub; the owner adds each public key as a read-only deploy
  key.
- The prepare step and the stack's template live in the followed repositories.
