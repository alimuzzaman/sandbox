# Product Requirements Draft: Agent-aware incremental sync for remote `dev/tmp` instances

**Status**: Discovery

**Created**: 2026-07-18

**Last Refined**: 2026-07-19

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

## Summary

Add opt-in remote development sync for disposable `dev/tmp` workspaces.

Sync modes:

- `live`: adaptive incremental synchronization while agents edit.
- `checkpoint`: synchronization only when explicitly requested.
- `once`: one immediate synchronization.
- `off`: existing deploy-only behavior.

All modes also synchronize after a successful local Git commit when the worktree is configured for remote sync.

The canonical local worktree is the source owner. Multiple agents operating in the same worktree are treated as one collaborative source. Agents using different worktrees require separate remote workspaces.

## Remote-instance prerequisite

Sync cannot start against only a source directory.

Before any sync session begins, Sandbox must:

1. Resolve and validate the canonical project configuration.
2. Validate the registered and provisioned remote.
3. Validate remote-deploy capability.
4. Create or ensure the selected remote workspace/instance.
5. Confirm the instance is healthy and its source mount points to the selected workspace.
6. Record the resolved remote instance, workspace label, target path, and source generation.
7. Only then start the watcher or accept sync requests.

If instance creation, workspace creation, health probing, or source mounting fails, sync remains stopped and reports the failure before any file transfer begins.

`sync start` may perform this ensure operation idempotently. `sync once`, `sync checkpoint`, and commit-triggered sync must use the same prerequisite validation.

## Sync behavior

- Detect ordinary file edits with a local filesystem watcher.
- Detect successful Git commits using a repository-local post-commit integration owned by Sandbox.
- A commit-triggered sync must:
  - capture the new commit identity;
  - include committed changes plus any remaining supported uncommitted/untracked files;
  - coalesce with an already-pending sync;
  - cause a sync no later than the ten-second maximum debounce;
  - never create a duplicate sync if the same generation is already accepted remotely.
- The commit trigger must not alter commit contents, amend commits, push to Git remotes, or block Git from completing if the remote is unavailable. It records a pending sync and reports the error through sync status.
- Agent-requested immediate sync remains available.

## Incremental transfer

Use incremental rsync over the existing SSH transport:

- local → remote only;
- preserve symlinks and modes;
- include tracked and non-ignored untracked files;
- exclude dependencies, build trees, secrets, uploads, databases, and runtime state;
- maintain a managed-file manifest so deletions affect only Sandbox-managed source files;
- use delayed/atomic updates where supported;
- keep remote database, uploads, caches, and instance runtime state outside the source-sync path.

Adaptive batching defaults:

- two-second quiet debounce;
- extend during active edit bursts;
- ten-second hard maximum;
- flush immediately at 100 changed paths or 10 MiB;
- flush immediately for an explicit sync request;
- flush immediately or within the ten-second bound after a successful commit event.

Record aggregate edit telemetry—timestamps, path counts, commit frequency, and byte counts only—to tune defaults without storing source contents.

## Agent and workspace coordination

- Acquire one shared source/workspace lease after the remote instance has been ensured.
- Key synchronization state by:

  `canonical local worktree + remote + workspace label`

- Any agent using the same canonical worktree may contribute edits and request sync/status.
- Agent/session IDs are used only for audit and telemetry; they do not create separate ownership leases.
- All edits and commits are coalesced into a pending source generation.
- A different canonical worktree cannot sync into the same workspace and receives a conflict response.
- Remote changes outside the managed source manifest fail closed; Sandbox never silently overwrites or merges them.
- Watcher, Git-hook, or SSH interruption is recoverable through `sync status`, which reconciles:
  - local source generation;
  - latest commit identity;
  - remote source generation;
  - managed-file manifest;
  - instance health;
  - workspace lease;
  - pending changes.

## Remote jobs

Every remote job records the source generation accepted at submission.

If any agent edits or commits while a remote job runs:

- the job continues against its accepted generation;
- later edits and commits remain pending;
- the pending generation syncs after the job releases its execution lease.

Reset, destroy, takeover, and instance replacement are blocked while sync or jobs are active.

## Public interface

Add CLI and MCP operations:

- `sync start --remote NAME --workspace LABEL --mode live|checkpoint`
- `sync stop --remote NAME --workspace LABEL`
- `sync once --remote NAME --workspace LABEL`
- `sync checkpoint --remote NAME --workspace LABEL`
- `sync status --remote NAME --workspace LABEL`

Status must report:

- canonical worktree identity;
- remote, workspace, and ensured instance identity;
- instance health and source-mount status;
- shared source owner;
- participating agent/session IDs, when available;
- sync mode and lifecycle;
- latest commit identity;
- last accepted and pending source generations;
- debounce or threshold reason;
- active remote job generation;
- changed-file and commit counts;
- conflict, instance, Git-hook, or connection errors.

## Testing and acceptance

- Refuse sync when the remote is unregistered, unprovisioned, instance creation fails, instance health fails, or the source mount is incorrect.
- Verify repeated ensure/start operations reuse the same `dev/tmp` instance and workspace.
- Verify multiple agents using the same canonical worktree share one lease and their edits are coalesced.
- Verify agents using different worktrees cannot sync into the same workspace.
- Test tracked/untracked files, ignored files, deletions, symlinks, path traversal, partial transfers, retries, and managed-file conflict detection.
- Test quiet debounce, burst extension, ten-second maximum, path-count threshold, byte threshold, explicit checkpoint, and immediate sync.
- Test successful commits trigger sync, including commits followed by uncommitted edits.
- Test commit-trigger installation, duplicate hooks, hook failure, repository without a writable hook path, and remote-unavailable commit behavior.
- Run a remote command, edit and commit from another agent sharing the same worktree, and verify:
  - the command retains its original source generation;
  - edits and commits become pending;
  - the latest generation syncs after command completion.
- Test watcher restart, Git-hook restart, SSH interruption, remote restart, stale leases, duplicate sync requests, instance reset, and explicit workspace destruction.
- Add CLI/MCP contract tests for instance prerequisite validation, shared-worktree ownership, commit generation reporting, bounded output, and secret redaction.
- Perform a disposable live acceptance run with a remote `dev/tmp` instance, multiple agents sharing one worktree, repeated edits, commits, a running remote job, post-job synchronization, and explicit cleanup.

## Assumptions

- “Same worktree” means the same canonical filesystem path after Sandbox root resolution.
- Agents sharing that worktree are one collaborative source regardless of agent identity.
- Different worktrees remain isolated even if they belong to the same repository.
- A healthy, source-mounted remote instance is mandatory before every sync session.
- Sync is strictly local → remote.
- A successful commit triggers synchronization but does not block or fail the commit if remote sync is unavailable.
- The maximum sync debounce is ten seconds.
- Live sync is opt-in; current deploy and remote-job contracts remain compatible.
- This PRD remains the single pre-spec artifact. A formal `spec.md`, plan, tasks, contracts, and quickstart may be generated later by their owning Spec Kit phases.

## Readiness for Specification

- [ ] Normalize this initial technical draft into the PRD template's problem, user, goal, non-goal, and scenario sections.
- [ ] Confirm consequential product choices and record remaining open questions.
- [ ] Separate product outcomes from implementation proposals.
- [ ] Validate measurable acceptance outcomes without implementation details.
- [x] Keep downstream specification, planning, task, and implementation artifacts out of this PRD.

**Readiness**: `NOT READY`
