# Research: Agent-Aware Remote Development Sync

## Decision 1: Add a relationship-owned synchronization service instead of extending deploy

**Decision**: Keep `./sb deploy` and `host apply` as explicit, one-way
operations. Add a feature-owned synchronization service and command surface
that calls the existing remote adapter and durable workspace/job services.

**Rationale**: `sandbox/core/_remote.py::deploy_exact_working_tree` resets a
remote Git checkout and applies the current dirty overlay. `docs/remote-hosting.md`
and `specs/014-remote-vps-hosting/` intentionally define that operation as
on-demand and never continuous. Reusing its validation and transport seams is
useful, but making deploy itself watch files would change an established
compatibility contract and could make `host apply` semantics ambiguous.

**Alternatives considered**:

- A process-local rsync daemon was rejected because it cannot coordinate agents,
  durable job leases, replay, or credential refusal.
- A second Git branch per session was rejected because it would make each agent
  an independent source owner and would not carry uncommitted edits without
  another overlay protocol.
- Replacing `deploy_exact_working_tree` was rejected because old deploy and job
  callers already depend on its reset-before-overlay behavior.

## Decision 2: Use a relationship journal plus existing remote workspace identity

**Decision**: Persist synchronization relationship metadata under the existing
`SANDBOX_HOME/runtime` state boundary. Key a relationship by resolved project
identity, named remote, and durable workspace ID. Store accepted/pending
generation metadata, request identity, mode, participant heartbeat, and bounded
divergence state; do not store source contents or secret values.

**Rationale**: The remote workspace controller already accepts path-free project
identity and workspace IDs through `sandbox/transports/remote_workspaces.py`,
while `sandbox/jobs/registry.py` demonstrates durable request IDs, lifecycle
transitions, and transactional state. A relationship journal can reuse those
boundaries without making a local path or human label authoritative.

**Alternatives considered**:

- The remote workspace index alone was rejected because local trigger state and
  replay identity must survive a disconnected client.
- A database migration in the job repository was rejected for v1 because sync
  state has a different lifecycle and must not make job storage mandatory for
  an off-mode project.
- A path-keyed JSON file was rejected because symlinks and relocated worktrees
  would create false ownership; the path is retained only as a redacted locator.

## Decision 3: Capture a stable manifest before mutation

**Decision**: Build one bounded source manifest from a stable local view. Apply
ordinary exclusions, then run the credential screen across tracked, modified,
untracked, and explicit inputs. A credential finding rejects the complete
generation before remote mutation. Transfer a staged generation to a temporary
remote location, validate its manifest/digest, and publish it atomically before
refreshing the workspace source mount.

**Rationale**: Existing `capture_uncommitted` and
`validate_deploy_include_paths` provide useful Git-relative and path-safety
rules, but the former treats Git ignore as the main boundary and therefore does
not prove that tracked secrets are absent. The new source manifest must make
eligibility and refusal explicit and must never expose a partial generation as
current.

**Alternatives considered**:

- Blind `rsync --delete` was rejected because it has no durable generation
  acknowledgment, can mix files during a write burst, and makes credential
  refusal difficult to prove before mutation.
- Direct writes into the active workspace were rejected because a failed
  transfer could leave a mixed source tree visible to a running job.
- Transferring full Git history for every edit was rejected because the feature
  targets small dirty generations and must not block the healthy-profile target.

## Decision 4: Reuse the durable job scheduler as the source-generation gate

**Decision**: A synchronized job pins one accepted generation before launch.
Relationship-level serialization prevents two launches or a sync and launch
from accepting conflicting generations. A new job waits for the newest pending
generation; parallel-safe jobs may share only an already accepted generation.

**Rationale**: `sandbox/application/job_service.py`,
`sandbox/jobs/registry.py`, and `sandbox/jobs/scheduler.py` already provide
durable request IDs, lifecycle states, dependency handling, and workspace
leases. The sync feature should add a generation boundary to those seams rather
than inventing an independent job lifecycle.

**Alternatives considered**:

- Joining the currently active generation was rejected because it runs a new
  job against stale source when a newer generation is pending.
- Cancelling all active jobs for every edit was rejected because it breaks
  existing parallel-safe behavior and violates the non-goal of changing job
  cancellation authority.

## Decision 5: Make synchronized job source read-only

**Decision**: A job using a synchronized workspace receives a read-only managed
source projection. A write attempt fails without changing the managed source.
A source-mutating job must explicitly request an isolated copy; its writes are
retained only through the existing job-artifact/output boundary. Out-of-band
edits are reported as divergence and require explicit resolution.

**Rationale**: This preserves an immutable pinned generation while allowing
parallel-safe jobs to share it. It also avoids a post-completion divergence
check that would be too late to protect another peer using the same source.

## Decision 6: Keep watch behavior opt-in and bounded

**Decision**: Live mode uses a relationship-owned watcher/trigger loop with a
debounce window and one in-flight generation per relationship. Checkpoint and
off modes never start that loop. The implementation reports delayed progress
when healthy-profile bounds are exceeded instead of claiming the 10-second
result.

**Rationale**: The feedback requests watch-and-push behavior, but a permanent
daemon, implicit remote selection, or unbounded transfer is outside the existing
safety model. Bounded ownership and explicit stop preserve control.

## Evidence and constraints carried into design

- CLI and MCP must resolve the same target and redacted fields; new command and
  MCP registration must use explicit manifests rather than registry/state JSON
  consumers.
- Remote source paths and credentials never appear in public envelopes or the
  relationship journal.
- The timed acceptance window starts when a supported trigger is accepted and
  ends at durable remote generation acknowledgment; preflight, credential
  screening, capture, transfer, and validation are included.
- The first live acceptance uses only a disposable remote workspace and leaves
  permanent instances and production hosting out of scope.

## Repository evidence that changes implementation scope

- `sandbox/application/workspace_service.py` and
  `sandbox/workspaces/repository.py` already provide durable workspace IDs,
  ownership validation, revision/health preflight, and a cross-process
  workspace operation lock. The sync service must call those seams instead of
  resolving registry JSON or inventing a second workspace owner.
- The current project identity resolver hashes the canonical absolute root and
  label. Symlinks resolve consistently, but a relocation currently changes the
  identity. The implementation therefore needs an explicit registry-owned
  durable identity/adoption operation for a relocated worktree, or it must fail
  closed and require lifecycle adoption; it must not silently claim relocation
  support from a path-derived hash.
- Existing remote jobs persist replay-safe request IDs and source identity, but
  do not yet persist a synchronized workspace/generation pin or enforce a
  read-only managed-source projection. Those are additive job fields and a
  capability-gated projection boundary, not a replacement of deploy-only jobs.
- Public CLI and MCP commands are manifest-registered. New sync commands/tools
  must register through those manifests and must not consume `sandbox_core.py`,
  raw registry JSON, or MCP helper namespaces directly.
- A synchronized source generation needs an owner-only local spool/manifest and
  a remote staged generation. The active remote workspace is not mutated until
  the staged content and manifest digest validate; a crash leaves an explicit
  indeterminate state for reconciliation.
