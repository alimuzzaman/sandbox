# Implementation Plan: Remote Job Runtime

**Branch**: `main` | **Date**: 2026-07-18 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/032-remote-job-runtime/spec.md`

## Summary

Add a remote-first execution layer built around a host-local durable job service. A
small detached supervisor process owns each accepted job, drains the child process's
stdout and stderr locally, redacts and persists output before exposing it, records
heartbeats and resource evidence, enforces a finite deadline, and owns cancellation
of the job's process group. CLI and MCP callers submit work and poll retained state;
they never hold the executed process's pipes open over SSH or MCP.

The job service is runtime-neutral. Local and remote transports, reusable workspace
leases, exact-working-tree deployment, generic commands, WordPress tests, E2E shards,
and compatible Linux GitHub Actions workflows all adapt to the same contracts. The
existing async-job, E2E, CI, and MCP result shapes remain available while their
mechanisms migrate behind the service and parity tests are retained.

## Technical Context

**Language/Version**: Python 3.10+ for Sandbox runtime and supervisor; shell only for
the existing `sb` bootstrap; project test commands may be Node, PHP, WordPress, or any
explicit Linux argv supported by the selected runtime.

**Primary Dependencies**: Python standard library (`sqlite3`, `subprocess`,
`selectors`, `signal`, `gzip`, `hashlib`, `json`, `tarfile`, `resource` where
available); existing SSH/SCP transport, runtime adapters, FastMCP server, Docker
Compose/WordPress adapters, and `act` for the bounded remote-CI compatibility layer.
No new Python package is required for the core job runtime.

**Storage**: Host-local SQLite registry in WAL mode at
`$SANDBOX_HOME/runtime/jobs/registry.sqlite3`; immutable job specifications and
segmented output/metric/artifact files under `$SANDBOX_HOME/runtime/jobs/<job-id>/`.
The local host stores local jobs; each remote host stores its own remote jobs. Local
callers do not mount or directly read remote state.

**Testing**: Stdlib `unittest` for models, repositories, services, CLI and MCP
contracts; mocked remote transport and process tests; existing whole-suite tests;
disposable remote acceptance fixtures for disconnect, matrix, Node, PHP, WordPress,
E2E, CI, artifacts, reset, and cleanup behavior.

**Target Platform**: Linux remote hosts with a provisioned co-located `sb` runtime;
local compatibility on macOS and Linux. Remote CI v1 is Linux-only and single-host.

**Project Type**: Modular Python CLI/application service with MCP adapters and
co-located remote execution.

**Performance Goals**:

- Return an accepted durable job ID within 10 seconds after validation/deploy intake.
- Return reachable-host status in at most 5 seconds.
- Persist output continuously with no SSH round trip per output chunk.
- Serve bounded output pages of at most 64 KiB or 500 events by default.
- Support four active jobs and five simultaneous followers per host by default,
  with configuration bounds and queued excess work.
- Sample health every 5 seconds and resource metrics every 10 seconds by default.

**Constraints**:

- Every execution has a finite effective deadline; maximum accepted deadline is seven
  days.
- Executed process lifetime and pipes are independent of CLI, MCP, SSH, and network
  sessions.
- Secrets are redacted before bytes reach retained output or presentation filters.
- Output retention is complete unless an explicit storage/output failure is recorded;
  success must not mask truncation.
- Default retained-output cap is 1 GiB per job, minimum free-disk reserve is 2 GiB,
  segment size is 8 MiB, and default terminal-job retention is seven days.
- Same-workspace commands are exclusive by default. Shared leases require an explicit
  parallel-safe declaration; matrix cells use isolated labels and runtime state.
- Existing per-project registry ownership remains authoritative. Job/workspace state
  is accessed through repositories/services, never by new direct JSON consumers.
- `runtime/wp/` and `vendor/` remain untouched.

**Scale/Scope**: One developer-controlled remote host per submission, tens of reusable
workspaces per project, hundreds of retained jobs per host, parent jobs with tens of
children, and bounded CI matrices. Autoscaling, multi-host scheduling, production
deployment, and non-Linux CI runners are out of scope.

## Constitution Check

### Pre-design gate

| Principle | Result | Design evidence |
|---|---|---|
| I. Per-project instance model | PASS | Every target request contains a canonical project root and workspace label; no implicit global instance is introduced. |
| II. Registry source of truth | PASS | Existing project/instance registry resolves local runtime ownership. The new job registry owns only jobs, leases, output, and artifacts through a repository API. |
| III. Single entry, modular package | PASS | `sb` remains unchanged as the entry file. New logic lives in `sandbox/jobs/`, feature-owned commands, services, transports, and explicit manifests. |
| IV. Live-stack verification | PASS | The task plan includes local live smoke tests and disposable remote acceptance runs with captured job IDs/results. |
| V. Idempotency and docs-with-code | PASS | Submission supports request idempotency; lifecycle operations are scoped/re-runnable; README, CLI guide, skill, MCP guidance, config docs, and AGENTS guidance are in the same implementation. |
| VI. Parity before removal | PASS | Existing async/E2E/CI/MCP interfaces remain compatibility adapters until contract and live parity tests pass. No facade removal is planned. |

Additional gates pass: capability checks occur before deployment/execution, remote
Docker is not exposed locally, destructive workspace actions are explicit, secret
values never enter retained output, and Spec Kit artifacts are excluded from releases.

### Post-design gate

PASS. The data model gives each workspace and job a project owner; the contracts expose
service operations rather than storage files; process, repository, transport, and
runtime policy boundaries are explicit; and compatibility keys are documented. No
constitutional exception or complexity waiver is required.

## Architecture

### Execution path

1. `TargetResolver` merges explicit CLI/MCP options, project runtime configuration,
   and operation profiles. It validates remote registration, project ownership,
   workspace label, output profile, cleanup policy, and finite deadline without side
   effects.
2. The transport performs an exact-working-tree deploy for remote submissions using
   the existing commit push plus uncommitted/untracked overlay mechanism. Deployment
   returns a source identity and target path.
3. The host-local `JobService.submit()` creates the job specification and repository
   row in one transaction. A unique request key makes lost-response retries return the
   original job.
4. A detached supervisor is started with every standard descriptor redirected away
   from the caller. It transitions the durable job from `accepted` to `queued` or
   `running`, acquires host/workspace leases, then launches the child in a new process
   session.
5. The supervisor uses non-blocking local pipes and a selector to drain stdout/stderr.
   It applies streaming secret redaction, writes bytes to separate segmented streams,
   and appends a combined-order event index. Presentation filters read only retained
   events and cannot affect the child.
6. The supervisor records heartbeat, process identity, resource metrics, deadline,
   cancellation state, output completeness, artifacts, cleanup, and final integrity
   identity. It releases leases in a finalization transaction.
7. CLI and MCP status/output/follow calls execute bounded host-local reads. Remote
   callers invoke the co-located remote `sb`/MCP and receive small JSON pages, not a
   long-lived test pipe.

### Process supervision

- One supervisor per leaf job avoids a required always-running scheduler daemon.
- Queueing is represented durably. A queued supervisor waits with bounded backoff and
  heartbeat while trying to acquire host capacity and the requested workspace lease.
- Parent jobs have no executable PID. A coordinator supervisor creates declared child
  jobs, applies dependency/fail-fast policy, and aggregates terminal outcomes.
- Child execution uses a new session/process group. Graceful cancel sends `SIGTERM` to
  the owned group, waits the configured grace period, then optional force cancel sends
  `SIGKILL`. PID, PGID, host boot ID, `/proc/<pid>/stat` start ticks on Linux, and a
  random launch nonce prevent PID-reuse confusion.
- Reconciliation runs during submit/status/list/maintenance. A changed boot ID,
  missing/mismatched process identity, stale supervisor heartbeat, or terminal child
  without a final row produces an explicit interrupted/orphaned/process-missing state;
  it never infers success.

### Output and streaming

- `stdout` and `stderr` are persisted as bytes in numbered 8 MiB segments.
- `events.ndjson` records monotonically increasing sequence, timestamp, stream,
  segment, offset, byte length, and event kind. This reconstructs observed combined
  order without duplicating payload bytes.
- A cursor is an opaque, versioned encoding of job ID, stream selection, and the
  next sequence/offset. New cursors use the strict v2 `{v:2,j,s,q,o}` envelope;
  v1 sequence-only cursors remain readable for compatibility. Callers must not
  parse it. Capped pages resume at the same event plus byte offset, report
  `has_more` from unread bytes, and do not repeat metadata for a resumed suffix.
- Output retrieval supports stream, cursor, byte offset, tail bytes, line count, time
  boundary, UTF-8 replacement text, and base64. Each response includes the next cursor,
  bounded/truncated flags, retained range, and output-completeness state.
- `full`, `smart`, `errors`, `sampled`, `quiet`, and named custom profiles are
  presentation policies. `smart` keeps state transitions, failures, section markers,
  first/last context, periodic heartbeat, and deduplicated repeated lines within a
  byte/event budget. `sampled` supports every-N-lines/events and time-based sampling.
- Follow is repeated bounded retrieval with optional host-local long-poll (maximum 20
  seconds). MCP progress is optional, monotonic, rate-limited, and never the source of
  truth.

### Health model

Lifecycle and health are separate dimensions. Lifecycle is `accepted`, `queued`,
`running`, `cancelling`, or a terminal outcome. Health is derived from durable evidence:

- `active`: recent output, metric movement, explicit progress, or child activity.
- `quiet`: child identity is valid and heartbeat/resource observations continue, but
  output is absent within the quiet window.
- `suspected_stalled`: no output, explicit progress, or meaningful metric movement for
  the configured stall period.
- `stuck`: stalled evidence persists for a second threshold or the process is in an
  uninterruptible/waiting state with no progress.
- `supervisor_unresponsive`: child may exist but supervisor heartbeat is stale.
- `orphaned`: owned process survives without a valid supervising job relationship.
- `process_missing`: expected process identity is absent or mismatched.
- `unreachable`: selected remote could not be inspected within the transport timeout.
- `unknown`: evidence is insufficient and no stronger classification is safe.

Stall detection warns by default. It cancels only when `cancel_on_stall` was explicitly
set. The status response always includes the evidence and thresholds used.

### Workspace and matrix scheduling

- Named workspaces persist by default and map to existing project labels/instances on
  the selected host. Local and remote labels occupy separate target namespaces.
- Lease modes are `exclusive` and `shared_parallel_safe`. Deploy, ensure, reset, and
  destroy require an exclusive lifecycle lease. Ordinary commands/tests require an
  exclusive execution lease. Shared execution is accepted only when both caller and
  workspace policy permit it.
- Host capacity is a lease counter. Excess jobs remain queued with position/reason.
- An immediate request against a busy exclusive workspace fails with a suggestion to
  create an isolated workspace; Sandbox does not create one without caller intent.
- Generated matrix labels use a normalized project/job/cell prefix plus a SHA-256
  digest of parent identity and canonical matrix values. Labels are deterministic for
  retry, unique across concurrent parents, valid for runtime naming, and at most 21
  characters.
- Parent jobs own children and aggregate results. Each matrix child owns a distinct
  isolated workspace, deadline, output, artifacts, and cleanup result. Reusable
  workspaces remain after failure; ephemeral cells follow explicit cleanup policy.

### Remote CI boundary

- CI v1 accepts a declared workflow file, event, selected jobs, inputs, secrets
  references, deadline, safe-mode policy, and named divergence acceptances.
- Preflight parses the workflow, validates Linux runners, expands dependencies/matrix,
  and detects known `act` differences before deployment or execution. The compatibility
  catalog is versioned and testable.
- The first engine uses the existing `act` integration to execute the complete selected
  graph on one remote host. Sandbox owns the outer parent/child job records, deadline,
  output, artifact validation, and cleanup even when `act` owns inner workflow steps.
- Safe mode blocks or neutralizes deployment/release/publishing activity and records the
  semantic difference. No production credential is forwarded by default.
- Unsupported runner environments and unaccepted known divergences fail preflight with
  exact workflow locations. A green `act` exit cannot override an incomplete-output,
  deadline, artifact, or cleanup failure.

### Migration and compatibility

1. Land contracts, models, repositories, supervisor, and local generic jobs.
2. Add target/config resolution and remote job transport over co-located `sb`.
3. Add reusable workspace lifecycle and exact-tree deploy-before-submit.
4. Route generic exec/test and WordPress tests through jobs while preserving legacy
   result keys (`ok`, `passed`, `summary`, `stdout`, `bytes_read`, `truncated`, etc.).
5. Route E2E fan-out and CI through parent/child jobs; add strict CI preflight.
6. Add MCP job group and optional progress presentation.
7. Adapt legacy `async-job` and Hermes job views only after parity tests; keep their
   command/tool contracts as rollback controls.

## Project Structure

### Documentation (this feature)

```text
specs/032-remote-job-runtime/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli.md
│   ├── config.schema.json
│   ├── job-service.md
│   ├── mcp.md
│   └── remote-ci.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/
├── application/
│   ├── job_service.py              # use cases and policy-independent orchestration
│   ├── target_service.py           # shared local/remote/workspace resolution
│   └── workspace_service.py        # lifecycle and lease orchestration
├── jobs/
│   ├── models.py                   # immutable requests/results/enums
│   ├── registry.py                 # SQLite repository contract/implementation
│   ├── supervisor.py               # detached process owner and pipe drainer
│   ├── output.py                   # segments, cursors, redaction, presentation
│   ├── health.py                   # evidence collection/classification
│   ├── artifacts.py                # constrained collection/retrieval
│   ├── scheduler.py                # host/workspace leases and parent coordination
│   ├── retention.py                # reconciliation and garbage collection
│   └── manifest.py                 # explicit job engine/profile registrations
├── config/
│   ├── manifest.py                 # explicit common config-provider registrations
│   └── runtime.py                  # runtime/output/execution profile schema
├── transports/
│   ├── jobs.py                     # local host transport contract
│   └── remote_jobs.py              # bounded co-located remote sb transport
├── commands/
│   ├── jobs_runtime.py             # job status/output/follow/cancel/retry commands
│   ├── runtime.py                  # remote-aware exec/test adapters
│   ├── workspaces.py               # create/list/reset/destroy
│   ├── e2e.py                      # parent/child job adapter
│   └── ci.py                       # CI preflight/parent adapter
└── ci/
    ├── compatibility.py            # versioned act divergence catalog
    └── workflow.py                 # workflow preflight and normalized graph

mcp/wp-server/tools/
├── jobs.py                         # runtime-neutral job tools
├── runtime.py                      # remote-aware exec/tests
├── instances.py                    # workspace lifecycle adapters
├── manifest.py                     # explicit jobs group and dependencies
└── instructions.py/app guidance    # remote-first operational text

tests/
├── test_job_models.py
├── test_job_registry.py
├── test_job_output.py
├── test_job_supervisor.py
├── test_job_health.py
├── test_job_artifacts.py
├── test_job_scheduler.py
├── test_target_resolution.py
├── test_runtime_config.py
├── test_remote_job_transport.py
├── test_job_cli.py
├── test_job_mcp.py
├── test_workspace_runtime.py
├── test_ci_compatibility.py
└── acceptance/test_remote_job_runtime.py
```

**Structure Decision**: Use a runtime-neutral `sandbox/jobs/` domain and application
services, registered through explicit manifests. Command and MCP modules only validate
transport input and translate service results. Runtime adapters own Node/PHP/WordPress/
Compose policy; transports own SSH/local mechanics; repositories own SQLite and log
layout. This preserves the module-boundary rule and avoids new consumers of legacy
facades or registry JSON.

## Complexity Tracking

No constitution violations require justification.

## Convergence amendment — 2026-08-13 (durable workspace metadata/index)

The existing remote job runtime remains the execution owner. A workspace repository is
added as a durable identity/index seam so jobs, remote controls, and resource monitoring
share one workspace owner without using checkout paths as identity.

### Implementation sequence

1. Add the owner-only SQLite index/repository under
   `$SANDBOX_HOME/runtime/workspaces/index.sqlite3` with versioning, WAL, foreign keys,
   bounded busy handling, opaque IDs, alias collision records, and unique
   `(project_identity, workspace_label)`.
2. Implement exact legacy discovery at
   `runtime/jobs/workspaces/<legacy-namespace>/<label>/workspace.json`; correlate only
   exact job project-root/namespace and one project identity, preserve source bytes, and
   persist adopted/unresolved/conflict/invalid decisions.
3. Add immutable migration plan/apply orchestration with full inventory digest, index
   generation, expiry, global/per-workspace locks, pre-apply rescan, one transaction, and
   stable drift/incomplete errors. No migration path performs cleanup or resource release.
4. Route workspace lifecycle through the service/repository. Remote list/status/migrate
   accept project identity/workspace ID without project-dir; create registers the exact
   deployed tree, reset/destroy require confirmation and a busy lock, and startup marks
   unfinished destructive actions indeterminate.
5. Publish typed workspace resource bindings to Spec 035. The resource service consumes
   the projection and never opens the index/legacy JSON; duplicate or stale bindings are
   unknown/indeterminate and cannot become cleanup candidates.

### Acceptance and release gates

- Focused tests cover empty/indexed legacy fixtures, idempotency, exact adoption,
  unresolved/conflict/invalid/symlink/oversize inputs, alias collision, missing checkout,
  plan expiry/digest/generation drift, lock contention, relocation, and remote strict
  response parsing.
- CLI/MCP tests prove workspace controls do not require a checkout and destructive
  controls use opaque IDs plus confirmation; job acceptance remains durable before reply.
- Read-only live evidence records workspace/job/resource/network/container counts before
  and after metadata migration. Apply is allowed only for a zero-collision, unchanged
  digest plan; cleanup, reset, destroy, deploy, and network release are separate actions.
