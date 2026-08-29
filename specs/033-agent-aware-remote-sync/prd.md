# Product Requirements Draft: Agent-Aware Remote Development Sync

**Status**: Refined

**Created**: 2026-07-18

**Last Refined**: 2026-08-26

**Input**: "Resume the committed agent-aware incremental sync draft for remote
development workspaces and make it ready for formal specification."

**Drafting Model**: active root configuration (exact model and effort not exposed)

**Final Validation**: `PASS` — independent `gpt-5.6-sol` High

**Validated On**: 2026-08-26

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Sandbox can deploy an exact local working tree before a remote job, but that
deploy-oriented flow makes rapid edit-test cycles feel stale and repetitive.
Developers and coding agents may make several small edits between remote tests,
forget whether the remote workspace has the newest source, or trigger redundant
full deployments from concurrent sessions sharing one local worktree.

Disposable remote development workspaces need an opt-in way to stay current with
their authoritative local worktree while preserving the safety properties of the
existing one-way deploy model. Synchronization must not turn a remote workspace
into a second source of truth, overwrite runtime state, expose secrets, race an
active job onto mixed source, or let two independent worktrees silently control
the same destination.

## Users and Desired Outcomes

- **Developer using a remote workspace**: See local edits reflected remotely
  quickly enough for an interactive edit-test loop and know which source
  generation the workspace currently contains.
- **Coding agents sharing one local worktree**: Contribute edits through one
  coordinated source stream without duplicate transfers or competing ownership.
- **Developer using separate worktrees**: Keep each worktree isolated and receive
  a clear conflict before either one can overwrite another's remote workspace.
- **Remote-job operator**: Run each job against one immutable accepted source
  generation while later edits remain visible as pending work.
- **Security-conscious maintainer**: Preserve current secret, path, runtime-state,
  and destructive-operation boundaries while enabling faster source updates.

## Goals

- Provide opt-in live and explicit-checkpoint synchronization for disposable
  remote development workspaces.
- Keep the canonical local worktree as the only source authority and synchronize
  source in one direction, from local to remote.
- Ensure and validate the selected remote workspace and runtime before accepting
  synchronization.
- Coordinate all participating sessions that use the same canonical worktree as
  one collaborative source.
- Reject a different canonical worktree before it can synchronize into an owned
  remote workspace.
- Preserve stable source generations so remote jobs never observe a mixture of
  pre-job and mid-job edits.
- Reconcile safely after local synchronization, client, network, or remote
  interruptions.
- Expose equivalent bounded, redacted synchronization status through CLI and MCP.
- Preserve current deploy-only behavior whenever synchronization is off.

## Non-Goals

- Bidirectional synchronization, remote-first editing, automatic merging, or
  conflict resolution for remote source changes.
- Synchronizing production, managed-production, or other permanent instances in
  the first release.
- Synchronizing files outside the existing deployment-eligible source set,
  ignored untracked files, credentials, databases, uploads, caches, logs, or
  unrelated runtime state.
- Replacing Git, pushing to Git remotes, creating or amending commits, or blocking
  a successful commit on remote availability.
- Creating remote workspaces implicitly when the caller has not selected a
  registered remote and workspace.
- Allowing different local worktrees to share ownership of one remote workspace.
- Changing the execution, retention, cancellation, reset, destroy, or cleanup
  authority of the remote-job system.
- Defining a transfer utility, watcher library, manifest format, lease mechanism,
  debounce algorithm, or storage schema at the PRD stage.
- Collecting source contents, file names, diffs, or process arguments as product
  analytics.

## Product Scenarios

### Scenario 1 — Start live synchronization

- **Starting state**: A registered remote and reusable disposable development
  workspace are selected, and synchronization is currently off.
- **User action**: The developer starts live synchronization for the canonical
  local worktree.
- **Expected outcome**: Sandbox ensures the selected workspace and runtime are
  healthy and correctly source-mounted before accepting source. Supported local
  edits then reach that workspace within a bounded freshness window, and status
  identifies the accepted and pending source generations.

### Scenario 2 — Synchronize an explicit checkpoint

- **Starting state**: The developer wants remote source changes only at deliberate
  boundaries.
- **User action**: The developer enables checkpoint mode and requests a checkpoint.
- **Expected outcome**: Sandbox transfers the latest supported local source once,
  reports the accepted generation, and does not synchronize later edits until
  another explicit checkpoint is requested.

### Scenario 3 — Synchronize after a commit

- **Starting state**: Live synchronization is enabled and the local worktree may
  contain both committed and supported uncommitted changes.
- **User action**: A participating developer or agent completes a local Git commit.
- **Expected outcome**: Sandbox treats the commit as a high-priority source
  change, records its identity, coalesces it with any pending edits, and
  synchronizes the resulting source generation without changing or pushing the
  commit. In checkpoint and off modes the commit does not trigger an automatic
  transfer. If the remote is unavailable, the commit still succeeds and
  synchronization status retains an actionable pending failure.

### Scenario 4 — Share one relationship across agents

- **Starting state**: Multiple sessions operate on the same canonical local
  worktree and target the same remote workspace.
- **User action**: The sessions edit files or request synchronization close
  together.
- **Expected outcome**: Sandbox treats them as participants in one source stream,
  coalesces duplicate work, and reports one ordered accepted/pending generation
  history rather than competing session ownership. Mode changes apply to the
  shared relationship and remain in effect when one participant disconnects.

### Scenario 5 — Reject a competing worktree

- **Starting state**: A remote workspace is already owned by one canonical local
  worktree.
- **User action**: A session from a different canonical worktree attempts to
  synchronize into it.
- **Expected outcome**: Sandbox fails before file transfer, identifies the
  ownership conflict without exposing sensitive paths, and directs the caller to
  use a separate workspace or an explicitly authorized lifecycle operation.

### Scenario 6 — Start a remote job with pending source

- **Starting state**: The selected workspace has an older accepted generation
  while the canonical local worktree contains a newer eligible generation.
- **User action**: The developer starts a remote job.
- **Expected outcome**: Job launch acts as an explicit source boundary. The job
  starts only after one complete generation of the latest deployment-eligible
  local working tree is accepted. If that acceptance fails, the job does not
  start, and no job starts against a source transition in progress.

### Scenario 7 — Edit while remote jobs are running

- **Starting state**: One or more remote jobs are running against the same
  accepted source generation, including jobs declared parallel-safe under the
  existing job policy.
- **User action**: A participant edits or commits local source.
- **Expected outcome**: Every running job continues against its original
  generation. The newer generation remains pending and becomes eligible for
  synchronization only after every active job using the workspace releases that
  generation.

### Scenario 7b — Queue a parallel-safe job behind a pending generation

- **Starting state**: Job A is running against generation A, local edits have
  created pending generation B, and a second job is declared parallel-safe.
- **User action**: The developer requests job B while generation B is pending.
- **Expected outcome**: The second job waits for generation B to be accepted; it
  does not silently join stale generation A. Once B is accepted, parallel-safe
  jobs may share B according to the existing job policy.

### Scenario 8 — Recover after interruption

- **Starting state**: Automatic change detection, the local synchronization
  process, a client, a network connection, or the remote host stops and later
  resumes.
- **User action**: The developer requests synchronization status or restarts the
  selected mode after an interrupted transfer or a lost acceptance response.
- **Expected outcome**: Sandbox compares local, accepted, pending, workspace, and
  runtime state; resumes only safe pending work with the same replay-safe request
  identity; avoids duplicate acceptance; and reports any condition that needs
  explicit operator action.

### Scenario 9 — Protect remote divergence and excluded state

- **Starting state**: The remote source area contains an unexpected change, or a
  local change targets ignored, secret, runtime, database, upload, cache, or other
  excluded content.
- **User action**: A synchronization is requested or becomes due.
- **Expected outcome**: Sandbox does not silently overwrite unexpected remote
  source, does not transfer excluded content, and never deletes anything outside
  source entries previously managed by this synchronization relationship.

### Scenario 10 — Handle attempted and out-of-band job source changes

- **Starting state**: A synchronized job has a read-only projection of its pinned
  managed source generation.
- **User action**: The job attempts to write managed source, or an explicitly
  isolated job copy writes source-like output, or an out-of-band remote actor
  changes the managed source area.
- **Expected outcome**: A shared-job write is rejected with no managed-source
  mutation. An isolated-copy write remains only in the existing job-artifact or
  output boundary and is never adopted locally. An out-of-band change is
  reported as remote divergence and requires explicit resolution before a later
  synchronization can mutate that area.

### Scenario 11 — Keep synchronization off

- **Starting state**: The project uses the current deploy-before-job workflow and
  has not opted into synchronization.
- **User action**: The developer edits or commits local source.
- **Expected outcome**: No automatic edit synchronization runs. Commit behavior
  does not trigger synchronization, and existing explicit deploy and remote-job
  behavior remains unchanged.

## Proposed Product Behavior

- Synchronization applies only to explicitly selected disposable remote
  development workspaces. It is off by default.
- A synchronization relationship is identified by the resolved project identity,
  named remote, and durable workspace ID. A workspace label is only a human-facing
  locator and may change without transferring ownership. The canonical worktree
  is the shared source owner; individual agent or session identities are
  participants used for bounded status and audit, not independent owners.
- Canonical identity follows the repository/worktree resolution rules: symlinked
  paths resolving to the same project identity share ownership, a relocated
  worktree retains ownership only when its durable project identity is preserved,
  and a fresh clone is a different owner until an explicit lifecycle operation
  authorizes adoption.
- The persistent modes are:
  - **off**: Existing deploy-only behavior; ordinary edits do not synchronize.
  - **live**: Supported edits synchronize automatically.
  - **checkpoint**: Ordinary edits synchronize only when explicitly requested.
- A one-time synchronization can be requested without leaving a persistent mode
  enabled.
- In live mode, a successful local commit is a high-priority synchronization
  signal; checkpoint and off modes remain explicit-only. The signal never blocks,
  amends, creates, or pushes a Git commit.
- Every synchronization request validates the registered remote, selected
  workspace, healthy runtime, correct source mount, and ownership relationship
  before transferring files. Repeating start or ensure is idempotent.
- Synchronization starts from the deployment-eligible source set and applies
  ordinary exclusions to runtime state, databases, uploads, caches, logs, and
  unsafe paths before generation capture. A separate fail-closed credential
  screen examines every tracked, modified, untracked, and explicitly included
  file. A credential-like name, value, key material, or local environment file
  is a generation-fatal finding: the entire generation is rejected before any
  remote mutation, and the result reports refusal rather than silently
  narrowing the source. Ordinary non-credential exclusions may be omitted from
  the captured generation.
- Deletions affect only source entries that Sandbox can prove were previously
  managed by the same synchronization relationship. Unknown remote changes fail
  closed and require explicit resolution.
- Each accepted source state has a stable generation identity and, when relevant,
  an associated Git commit identity. A generation is accepted only as one
  complete, coherent source state; repeating the same request does not create a
  second accepted generation.
- Starting a remote job is an explicit source boundary. The job starts only after
  the latest eligible local working tree is accepted as one complete generation;
  if generation A is active and generation B is pending, a new job waits for B
  rather than joining stale A. Failure to accept B prevents job launch.
- Every remote job records and remains pinned to the source generation it
  accepted. Existing parallel-safe jobs may share the same accepted generation.
  Later edits remain pending until every active job using the workspace releases
  its generation.
- During a synchronized job, the managed source projection is read-only and
  source-mutating job requests are rejected unless they explicitly request an
  isolated job copy. The existing job-artifact channel remains the supported
  writable output path; isolated or rejected source changes are never adopted
  automatically, and a detected pre-existing managed-source change remains
  remote divergence requiring explicit resolution.
- Synchronization status reports mode, lifecycle, remote/workspace/runtime
  identity, redacted worktree identity, health and mount state, participating
  sessions when available, latest commit, accepted and pending generations,
  bounded change counts, active job generation, and actionable errors.
- Mode changes apply to the shared worktree/remote/workspace relationship and are
  visible to every participant. A participant disconnect does not change mode.
- Stop disables future automatic generations for the shared relationship, leaves
  pending state visible, and does not delete the remote workspace, revert accepted
  source, cancel jobs, or broaden reset/destroy authority. The treatment of a
  transfer already in progress is deterministic: it completes as one coherent
  generation if its capture and validation succeed; stop prevents new transfers
  and leaves a failed/incomplete generation unaccepted.
- Operational counters may record timestamps, aggregate path counts, commit
  counts, and byte counts for reconciliation and bounded status. Source contents,
  diffs, file names, secrets, and process arguments are not analytics.

## Constraints and Dependencies

- Per-project instance identity and the registered remote/workspace model remain
  authoritative; no implicit instance or fallback workspace may be targeted.
- Durable workspace ID and resolved project identity are authoritative for
  ownership; labels and local paths are relocatable locators only. Symlinked paths
  that resolve to the same identity may participate, while fresh clones and
  unresolved relocations require explicit lifecycle adoption.
- Existing remote deployment and durable remote-job capabilities must remain
  available and compatible.
- Synchronization cannot begin until the selected runtime is healthy and uses the
  intended workspace source mount.
- Source synchronization is local-to-remote only. A remote change is a conflict,
  not an input to be merged.
- A synchronized job sees its pinned managed generation through a read-only
  projection. A source-mutating job cannot share the synchronized workspace;
  it must either use an isolated copy whose writes stay in the artifact/output
  boundary or be rejected before launch. Existing deploy-only behavior is
  unchanged while synchronization is off.
- A generation is captured from a stable local view. If files change during
  capture, the transfer is retried within a bounded limit and then fails without
  accepting mixed content. Concurrent synchronization and job-launch requests
  serialize at the relationship boundary; a lost acknowledgment is replayed with
  one request identity rather than creating a second generation.
- Active jobs remain pinned to their accepted generation. Existing parallel-safe
  jobs may share it; a newer generation remains pending until every active job
  using the workspace releases it, and a new job waits for the newest pending
  generation rather than joining an older one. Reset, destroy, takeover, instance
  replacement, and conflicting source mutation remain blocked while a job or
  unsafe synchronization transition is active.
- Job-produced source changes are never silently adopted. Shared synchronized
  jobs cannot write managed source; isolated-copy writes stay in the job-artifact
  or output boundary. Managed-source divergence requires explicit resolution
  before another synchronization can mutate that area.
- CLI and MCP must resolve the same target, ownership, generation, lifecycle, and
  error semantics.
- Public output and persisted non-secret metadata must not contain credentials,
  source contents, raw sensitive paths, environment values, or process arguments.
- Lifecycle and cleanup commands retain their existing confirmation and safety
  gates; this feature grants no new destructive authority.
- Live proof must use a disposable remote development workspace and explicit
  cleanup. Production or permanent instance state is outside acceptance scope.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Default state | Off; current deploy-only workflow remains unchanged | Makes the faster loop opt-in and preserves compatibility | Committed feature input and existing deploy policy |
| Eligible targets | Disposable remote development workspaces only | Prevents an edit-oriented feature from mutating permanent instances | Committed feature input |
| Source authority | Canonical local worktree; one-way local-to-remote | Preserves the established source-of-truth boundary | Existing remote deployment policy |
| Modes | Off, live, checkpoint, plus one-time sync | Separates continuous, deliberate, and ad-hoc workflows without overloading off | Refined committed feature input |
| Commit trigger | Successful commits trigger synchronization only in live mode; checkpoint and off remain explicit-only | Keeps live edit-test loops current without contradicting checkpoint/off semantics | Safe default adopted during refinement; commit safety remains existing Git policy |
| Commit safety | A trigger never blocks, amends, creates, or pushes a Git commit | Preserves Git authority even when remote synchronization is unavailable | Existing Git policy |
| Shared participation | Sessions in one canonical worktree share one source owner | Matches collaborative worktree behavior and avoids duplicate leases | Committed feature input |
| Shared mode | Mode changes apply to the relationship and survive participant disconnect | A shared owner cannot have conflicting per-session modes | Existing shared-ownership decision |
| Worktree identity | Ownership uses resolved project identity plus durable workspace ID; labels and symlinked paths are locators | Prevents false conflicts after relocation while rejecting fresh-clone takeover | Existing workspace identity model and safe default adopted during refinement |
| Worktree isolation | Different canonical worktrees require different remote workspaces | Prevents silent cross-worktree overwrite | Committed feature input |
| Remote divergence | Fail closed; no automatic merge or overwrite | Remote source is not authoritative, but unexpected changes may be valuable | Existing safety policy |
| Source inclusion | Start from deployment eligibility but fail closed on credential-like content across tracked and untracked files | Prevents a tracked secret from bypassing the untracked-file boundary | Existing secret-inspection policy and safe default adopted during refinement |
| Job launch | Accept the newest pending eligible generation before launch; queue a new job behind it rather than joining stale source | Preserves exact-current-working-tree execution and makes parallel sharing generation-consistent | Remote-job specification FR-005 and existing durable-job policy |
| Active jobs | All jobs pin a generation; parallel-safe jobs may share the same generation after it is accepted | Prevents mixed-source execution without removing current parallel-safe behavior | Existing durable-job policy |
| Stop during transfer | Complete an in-flight transfer only if it validates as one coherent generation; otherwise leave it unaccepted | Avoids partial source while making stop deterministic and non-destructive | Safe default adopted during refinement |
| Job-produced changes | Reject shared managed-source writes, keep isolated-copy writes in the artifact/output boundary, and treat out-of-band edits as divergence | Preserves immutable generations without adopting remote-produced state | Existing remote-job artifact policy and safe default adopted during refinement |
| Credential finding | Reject the entire generation before any remote mutation; ordinary non-credential exclusions may be omitted | Prevents a secret from being hidden by source-set narrowing | Existing secret-inspection policy and safe default adopted during refinement |
| Synchronized job writes | Managed source is read-only for shared synchronized jobs; source-mutating requests require an isolated copy or are rejected | Preserves immutable pinned generations and safe parallel sharing | Safe default adopted during refinement |
| Deletion boundary | Only provably managed source entries can be deleted | Synchronization must not become arbitrary remote cleanup | Existing destructive-action policy |
| Status surfaces | Equivalent bounded, redacted CLI and MCP behavior | Maintains interface parity for developers and agents | Existing architecture policy |

## Open Questions

None. The refinement adopts the safe defaults recorded in the Decisions table:
live-only commit triggers, deterministic completion-or-rejection for an in-flight
transfer, newest-generation queueing for new jobs, durable workspace identity,
fail-closed credential screening, and explicit handling of job-produced divergence.

## Acceptance Outcomes

- With a healthy selected disposable workspace and live mode enabled, supported
  local edits become the accepted remote source generation within 10 seconds
  under the documented healthy-connectivity acceptance profile.
- An explicit checkpoint or one-time synchronization reports its accepted
  generation before returning success; later edits remain unsynchronized in
  checkpoint mode until another explicit request.
- A successful local commit in live mode is reflected in accepted or pending
  synchronization status within 10 seconds; checkpoint and off mode perform no
  automatic transfer, and remote unavailability never causes the Git commit
  itself to fail.
- Two sessions using the same canonical worktree and workspace produce one
  ordered generation stream with no duplicate acceptance for identical source.
- A different canonical worktree is rejected before remote file mutation and
  receives an actionable ownership-conflict result.
- A job requested while accepted generation A is older than eligible local
  generation B does not start until complete generation B is accepted; acceptance
  failure prevents job launch.
- Two parallel-safe jobs sharing generation A continue to report A after local
  generation B is created; B remains pending and is accepted only after both jobs
  release A.
- If job A runs on generation A while generation B is pending, a new job request
  waits for B and never reports A as its accepted generation; once B is accepted,
  parallel-safe jobs may share B.
- Restarting after a local, network, or remote interruption reconciles accepted
  and pending generations without repeating an already accepted generation or
  silently discarding supported edits; a lost response is safely replayable with
  the original request identity.
- A capture that observes a file change, a simultaneous sync/job launch, or two
  identical launch requests either retries and accepts one coherent generation or
  fails without accepting mixed content or starting a job against it.
- Exact-working-tree acceptance demonstrates that eligible files synchronize only
  after the credential screen passes for tracked, modified, untracked, and
  explicitly included content; credential-like values, ignored-untracked files,
  runtime-state, databases, uploads, caches, logs, and unsafe paths transfer
  nothing and expose no protected values. A credential finding rejects the
  whole generation before remote mutation; it is never silently narrowed.
- A supported local deletion removes a remote file only when the same
  synchronization relationship previously managed it; unknown remote source and
  unrelated runtime state remain untouched.
- A synchronized shared job cannot write its managed source projection: the
  attempted write is rejected with no managed-source mutation. A source-mutating
  request is rejected before launch unless it explicitly uses an isolated job
  copy; isolated writes remain retrievable output and cannot alter the accepted
  generation or a parallel-safe peer's view. An out-of-band managed-source edit
  is surfaced as divergence and requires explicit resolution.
- With synchronization off, ordinary edits cause no automatic transfer, commits
  cause no synchronization, and all existing deploy-before-job acceptance
  scenarios continue to pass.
- CLI and MCP return equivalent target, mode, ownership, generation, lifecycle,
  partial-failure, conflict, and redaction fields for the same state.
- A disposable live acceptance run demonstrates shared-worktree edits, a commit,
  a concurrent pinned remote job, post-job synchronization, interruption
  recovery, queued-new-job behavior behind a pending generation, conflict
  rejection, credential refusal, shared-write rejection, isolated-output
  handling, out-of-band divergence, and explicit cleanup without touching a
  permanent instance.

## Risks and Assumptions

- **Risk**: Automatic change detection, the local synchronization process, or
  commit integration can stop silently and leave users believing the remote is
  current; status and restart reconciliation must make freshness observable.
- **Risk**: Large edit bursts or slow links can exceed the freshness target;
  partial and delayed states must remain visible rather than appearing current.
- **Risk**: Remote tools or users may change synchronized source directly;
  fail-closed conflict handling can interrupt work but prevents silent loss.
- **Risk**: Incorrect exclusions could leak secrets or overwrite runtime state;
  safe defaults and negative acceptance coverage are required.
- **Risk**: A stale ownership record could block reuse after a crash; recovery
  must distinguish safe reconciliation from an ownership takeover that needs
  explicit lifecycle authority.
- **Risk**: Holding pending source behind a long-running job can make later tests
  appear stale; job generation and pending generation must remain prominent.
- **Assumption**: Canonical worktree identity can be resolved consistently across
  CLI and MCP without exposing its raw path publicly.
- **Assumption**: Existing registered remotes and reusable workspace lifecycle
  provide enough stable identity to own one synchronization relationship.
- **Assumption**: Existing exact-working-tree deployment defines the authoritative
  inclusion baseline; synchronization reuses it rather than silently narrowing it.
- **Assumption**: The 10-second live freshness outcome is measured end to end
  from the client monotonic timestamp when a supported edit/commit trigger is
  accepted until the remote durable generation-accepted acknowledgment. The
  timed path includes preflight, credential screening, stable capture, transfer,
  remote validation, and acceptance acknowledgment. It applies only under the
  documented healthy profile: eligible checkout at most 512 MiB, one generation
  changing at most 10 MiB or 100 paths, round-trip latency at most 100 ms,
  sustained measured transfer throughput at least 5 MiB/s, packet loss at most
  1%, remote CPU below 70%, and at least 20% free workspace storage. Larger or
  busier transfers report delayed progress rather than false success.
- **Assumption**: A successful commit is a synchronization signal only in live
  mode; checkpoint and off deliberately require an explicit request.
- **Assumption**: An in-flight transfer either validates as one generation or is
  left unaccepted when stopped; no partial generation is ever visible as current.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed or explicitly recorded as safe defaults.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
