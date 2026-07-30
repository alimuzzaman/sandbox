# Product Requirements Draft: Agent-Aware Remote Development Sync

**Status**: Refined

**Created**: 2026-07-18

**Last Refined**: 2026-07-29

**Input**: "Resume the committed agent-aware incremental sync draft for remote
development workspaces and make it ready for formal specification."

**Drafting Model**: active root configuration (exact model and effort not exposed)

**Final Validation**: `REOPEN` — independent `gpt-5.6-sol` High

**Validated On**: 2026-07-29

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

- **Starting state**: A mode selected by the commit-trigger policy is enabled and
  the local worktree may contain both committed and supported uncommitted changes.
- **User action**: A participating developer or agent completes a local Git commit.
- **Expected outcome**: Sandbox treats the commit as a high-priority source change,
  records its identity, coalesces it with any pending edits, and synchronizes the
  resulting source generation without changing or pushing the commit. If the
  remote is unavailable, the commit still succeeds and synchronization status
  retains an actionable pending failure.

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

### Scenario 8 — Recover after interruption

- **Starting state**: Automatic change detection, the local synchronization
  process, a client, a network connection, or the remote host stops and later
  resumes.
- **User action**: The developer requests synchronization status or restarts the
  selected mode.
- **Expected outcome**: Sandbox compares local, accepted, pending, workspace, and
  runtime state; resumes only safe pending work; avoids duplicate acceptance; and
  reports any condition that needs explicit operator action.

### Scenario 9 — Protect remote divergence and excluded state

- **Starting state**: The remote source area contains an unexpected change, or a
  local change targets ignored, secret, runtime, database, upload, cache, or other
  excluded content.
- **User action**: A synchronization is requested or becomes due.
- **Expected outcome**: Sandbox does not silently overwrite unexpected remote
  source, does not transfer excluded content, and never deletes anything outside
  source entries previously managed by this synchronization relationship.

### Scenario 10 — Keep synchronization off

- **Starting state**: The project uses the current deploy-before-job workflow and
  has not opted into synchronization.
- **User action**: The developer edits or commits local source.
- **Expected outcome**: No automatic edit synchronization runs. Commit behavior
  follows the confirmed commit-trigger policy, and existing explicit deploy and
  remote-job behavior remains unchanged.

## Proposed Product Behavior

- Synchronization applies only to explicitly selected disposable remote
  development workspaces. It is off by default.
- A synchronization relationship is identified by the canonical local worktree,
  named remote, and workspace label. The worktree is the shared source owner;
  individual agent or session identities are participants used for bounded status
  and audit, not independent owners.
- The persistent modes are:
  - **off**: Existing deploy-only behavior; ordinary edits do not synchronize.
  - **live**: Supported edits synchronize automatically.
  - **checkpoint**: Ordinary edits synchronize only when explicitly requested.
- A one-time synchronization can be requested without leaving a persistent mode
  enabled.
- Commit-triggered synchronization is separately governed by the confirmed
  commit policy. It never blocks, amends, creates, or pushes a Git commit.
- Every synchronization request validates the registered remote, selected
  workspace, healthy runtime, correct source mount, and ownership relationship
  before transferring files. Repeating start or ensure is idempotent.
- Synchronization uses the existing deployment-eligible source set: all tracked
  files and supported non-ignored untracked files, subject only to established
  credential, runtime-state, ignore, and path-safety exclusions. Any additional
  synchronization-only exclusion is reported explicitly because it prevents
  exact-working-tree parity.
- Deletions affect only source entries that Sandbox can prove were previously
  managed by the same synchronization relationship. Unknown remote changes fail
  closed and require explicit resolution.
- Each accepted source state has a stable generation identity and, when relevant,
  an associated Git commit identity. A generation is accepted only as one
  complete, coherent source state; repeating the same request does not create a
  second accepted generation.
- Starting a remote job is an explicit source boundary. The job starts only after
  the latest deployment-eligible local working tree is accepted as one complete
  generation; failure to accept it prevents job launch.
- Every remote job records and remains pinned to the source generation it
  accepted. Existing parallel-safe jobs may share that generation. Later edits
  remain pending until every active job using the workspace releases it.
- Synchronization status reports mode, lifecycle, remote/workspace/runtime
  identity, redacted worktree identity, health and mount state, participating
  sessions when available, latest commit, accepted and pending generations,
  bounded change counts, active job generation, and actionable errors.
- Mode changes apply to the shared worktree/remote/workspace relationship and are
  visible to every participant. A participant disconnect does not change mode.
- Stop disables future automatic generations for the shared relationship, leaves
  pending state visible, and does not delete the remote workspace, revert accepted
  source, cancel jobs, or broaden reset/destroy authority. The treatment of a
  transfer already in progress remains an open product decision.
- Operational counters may record timestamps, aggregate path counts, commit
  counts, and byte counts for reconciliation and bounded status. Source contents,
  diffs, file names, secrets, and process arguments are not analytics.

## Constraints and Dependencies

- Per-project instance identity and the registered remote/workspace model remain
  authoritative; no implicit instance or fallback workspace may be targeted.
- Existing remote deployment and durable remote-job capabilities must remain
  available and compatible.
- Synchronization cannot begin until the selected runtime is healthy and uses the
  intended workspace source mount.
- Source synchronization is local-to-remote only. A remote change is a conflict,
  not an input to be merged.
- Active jobs remain pinned to their accepted generation. Existing parallel-safe
  jobs may share it; a newer generation remains pending until every active job
  using the workspace releases it. Reset, destroy, takeover, instance replacement,
  and conflicting source mutation remain blocked while a job or unsafe
  synchronization transition is active.
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
| Commit safety | A trigger never blocks, amends, creates, or pushes a Git commit | Preserves Git authority even when remote synchronization is unavailable | Existing Git policy |
| Shared participation | Sessions in one canonical worktree share one source owner | Matches collaborative worktree behavior and avoids duplicate leases | Committed feature input |
| Shared mode | Mode changes apply to the relationship and survive participant disconnect | A shared owner cannot have conflicting per-session modes | Existing shared-ownership decision |
| Worktree isolation | Different canonical worktrees require different remote workspaces | Prevents silent cross-worktree overwrite | Committed feature input |
| Remote divergence | Fail closed; no automatic merge or overwrite | Remote source is not authoritative, but unexpected changes may be valuable | Existing safety policy |
| Source inclusion | Reuse the exact deployment-eligible source set; extra sync-only exclusions must be explicit | Preserves exact-working-tree parity without weakening established safety exclusions | Remote-job specification FR-005 |
| Job launch | Accept the latest eligible local generation before launch or do not start the job | Preserves exact-current-working-tree execution | Remote-job specification FR-005 |
| Active jobs | All jobs pin a generation; existing parallel-safe jobs may share it while newer source waits | Prevents mixed-source execution without removing current parallel-safe behavior | Existing durable-job policy |
| Deletion boundary | Only provably managed source entries can be deleted | Synchronization must not become arbitrary remote cleanup | Existing destructive-action policy |
| Status surfaces | Equivalent bounded, redacted CLI and MCP behavior | Maintains interface parity for developers and agents | Existing architecture policy |

## Open Questions

- **Commit-trigger scope**: Should a successful commit trigger synchronization in
  live mode only, in live and checkpoint modes, or in every mode including off?
  The original committed draft said every configured mode, while off and
  checkpoint were also described as deploy-only and explicit-only.
- **Stop during transfer**: When stop is requested while one generation is already
  transferring, should that complete as one coherent accepted generation, or
  should Sandbox cancel it and keep the last previously accepted generation?

## Acceptance Outcomes

- With a healthy selected disposable workspace and live mode enabled, supported
  local edits become the accepted remote source generation within 10 seconds
  under the documented healthy-connectivity acceptance profile.
- An explicit checkpoint or one-time synchronization reports its accepted
  generation before returning success; later edits remain unsynchronized in
  checkpoint mode until another explicit request.
- A successful local commit in every mode selected by the confirmed trigger policy
  is reflected in accepted or pending synchronization status within 10 seconds,
  and remote unavailability never causes the Git commit itself to fail.
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
- Restarting after a local, network, or remote interruption reconciles accepted
  and pending generations without repeating an already accepted generation or
  silently discarding supported edits.
- Exact-working-tree acceptance demonstrates that all deployment-eligible tracked
  files and supported non-ignored untracked files synchronize, while established
  credential, ignored-untracked, runtime-state, database, upload, cache, log, and
  path-safety exclusions transfer nothing and expose no protected values.
- A supported local deletion removes a remote file only when the same
  synchronization relationship previously managed it; unknown remote source and
  unrelated runtime state remain untouched.
- With synchronization off, ordinary edits cause no automatic transfer, commits
  follow the confirmed trigger policy, and all existing deploy-before-job
  acceptance scenarios continue to pass.
- CLI and MCP return equivalent target, mode, ownership, generation, lifecycle,
  partial-failure, conflict, and redaction fields for the same state.
- A disposable live acceptance run demonstrates shared-worktree edits, a commit,
  a concurrent pinned remote job, post-job synchronization, interruption
  recovery, conflict rejection, and explicit cleanup without touching a
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
- **Assumption**: Normal healthy connectivity can satisfy the 10-second live
  freshness outcome for ordinary source edits under a documented acceptance
  profile that bounds source size, changed bytes, round-trip latency, and remote
  load; larger transfers may report delayed progress without false success.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [ ] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [ ] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [ ] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: `NOT READY`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
