# Research: Remote Job Runtime

## Scope and method

This research compares durable execution, log retrieval, health, cancellation,
artifacts, and agent progress behavior from established systems, then records the
decisions appropriate for Sandbox's co-located single-host architecture. Primary
documentation was reviewed on 2026-07-18; repository behavior was verified against
the existing async-job, remote deploy, runtime adapter, E2E, CI, and MCP code.

## Decision 1: Persist output on the execution host; stream from the retained log

**Decision**: The job supervisor drains child stdout/stderr locally and persists them
before any client presentation. CLI/MCP follow operations read bounded pages from the
retained log by opaque cursor. They never attach the child pipes across SSH/MCP.

**Rationale**:

- Buildkite exposes job logs as a retained resource and supports ranged retrieval,
  allowing clients to resume without owning the running process.
- Kubernetes `logs -f` follows a runtime-owned log and supports tail/since/byte limits;
  the pod process is not coupled to the client connection.
- GitLab persists job traces independently from the runner connection and exposes
  bounded API retrieval.
- Depot exposes persisted logs with cursors and server-side streaming while retaining
  a fetch-later API.
- This model directly addresses slow/unreliable SSH. Network interruption affects only
  observation, not execution or pipe drainage.

**Alternatives rejected**:

- Holding `Popen` pipes open through an SSH/MCP request couples backpressure and
  process lifetime to the network.
- `tee` to a single combined log loses reliable stream identity and makes PID/status
  races difficult to reconcile.
- Returning output only at completion prevents useful mid-run health diagnosis.

**References**:

- Buildkite Jobs API: https://buildkite.com/docs/apis/rest-api/jobs
- Kubernetes logs: https://kubernetes.io/docs/reference/kubectl/generated/kubectl_logs/
- GitLab job logs/API: https://docs.gitlab.com/administration/cicd/job_logs/ and
  https://docs.gitlab.com/api/jobs/
- Depot CI API/CLI: https://depot.dev/docs/api/ci/reference and
  https://depot.dev/docs/cli/reference/depot-ci

## Decision 2: Use a host-local SQLite registry plus per-job append-only files

**Decision**: Store transactional metadata, idempotency keys, leases, lifecycle,
health summaries, and artifact indexes in SQLite WAL mode. Store high-volume output
and metrics in append-only per-job files. Use `synchronous=FULL` for state transitions
that acknowledge acceptance or terminal completion, bounded busy timeouts, foreign
keys, and explicit checkpoints during maintenance.

**Rationale**:

- SQLite WAL allows readers to continue while a writer appends commits and is present
  in Python's standard library.
- A relational repository gives atomic job creation + idempotency and atomic lease
  acquisition, which ad-hoc status/PID files cannot provide.
- Keeping output payloads outside SQLite avoids large write transactions and lets the
  log segmenter enforce byte limits, compression, and direct bounded reads.
- Host-local state avoids network-filesystem WAL hazards. Remote state remains on the
  remote and is accessed through the service contract.

**Alternatives rejected**:

- Extending the current `status`, `pid`, and `output.log` file convention cannot safely
  coordinate concurrent workspace leases or request idempotency.
- A required Redis/PostgreSQL service adds provisioning and recovery dependencies that
  are disproportionate for one developer host.
- Putting all output blobs in SQLite increases checkpoint/storage pressure and makes
  bounded binary reads less direct.

**References**:

- SQLite WAL: https://www.sqlite.org/wal.html
- SQLite synchronous behavior: https://www.sqlite.org/pragma.html#pragma_synchronous

## Decision 3: One detached supervisor per leaf job, no mandatory daemon

**Decision**: After durable acceptance, launch a small Python supervisor in a new
session with stdin/stdout/stderr detached from the caller. The supervisor acquires
leases, starts one child process group, drains pipes, samples metrics, enforces the
deadline, collects artifacts, and finalizes the record. Parent jobs use a coordinator
supervisor and child records.

**Rationale**:

- The existing Sandbox distribution already stages Python and can launch detached
  processes without adding a service manager requirement.
- Per-job supervisors isolate failures and allow gradual migration from existing
  `async-job` behavior.
- A durable queue plus transactional leases provides bounded concurrency without an
  always-running central dispatcher.
- Process groups provide scoped cancellation of descendants. Boot ID and process start
  identity avoid PID-reuse false positives.

**Alternatives rejected**:

- A required system daemon complicates installation, upgrade ownership, and macOS
  compatibility.
- A shell wrapper cannot reliably multiplex separate streams, collect metrics, redact
  across chunk boundaries, or reconcile process identity.
- Killing by PID alone can target a reused PID or leave descendants running.

## Decision 4: Separate lifecycle from evidence-based health

**Decision**: Keep lifecycle outcomes and health classifications as separate fields.
Sample supervisor heartbeat every 5 seconds and process/resource evidence every 10
seconds by default. `suspected_stalled` is a warning based on lack of output, explicit
progress, and meaningful resource movement; it does not imply failure. Automatic stall
cancellation is opt-in.

**Rationale**:

- Nomad exposes allocation status and logs separately and provides workload inspection
  evidence rather than equating silence with failure.
- Long test setup, browser waits, database migrations, and external service waits may
  legitimately be quiet.
- Agents need the evidence and threshold to decide whether to wait, inspect full logs,
  cancel, or retry.

**Alternatives rejected**:

- “No output for N seconds means stuck” produces false positives.
- Automatic default cancellation can destroy useful failure evidence and reusable state.
- A single `running` boolean cannot represent unreachable, orphaned, or
  supervisor-unresponsive conditions.

**References**:

- Nomad allocation logs: https://developer.hashicorp.com/nomad/commands/alloc/logs
- Nomad workload inspection: https://developer.hashicorp.com/nomad/docs/monitor/inspect-workloads

## Decision 5: Keep complete retained output; reduce only presentation

**Decision**: Provide `full`, `smart`, `errors`, `sampled`, `quiet`, and declarative
custom profiles. All operate on retained redacted events. Smart mode keeps failures,
state changes, section boundaries, initial/final context, and heartbeat summaries under
a byte/event budget. Sampled mode supports every-N events/lines and time intervals.

**Rationale**:

- Dagger offers selectable progress renderers for interactive and machine-oriented
  consumers without changing the work being run.
- Buildkite provides agent/LLM-oriented log formatting and retained full logs.
- Agents benefit from a compact default but must be able to request complete evidence
  later.

**Alternatives rejected**:

- Piping execution through `grep`, `tail`, or arbitrary filters changes buffering,
  failure propagation, and potentially command semantics.
- Deleting skipped lines makes later diagnosis impossible.
- One universal every-10-lines rule misses rare failures and over-emits repetitive
  progress bars.

**References**:

- Dagger CLI progress: https://docs.dagger.io/reference/cli/
- Buildkite agent/job CLI: https://buildkite.com/docs/platform/cli/reference/job and
  https://buildkite.com/docs/agent/cli/reference/start

## Decision 6: Opaque cursors and bounded long-poll, not permanent streams

**Decision**: Output pages return an opaque cursor and bounded payload. Follow repeats
pages and may ask the host to wait up to 20 seconds for new events. Defaults are 64 KiB
or 500 events per response. Duplicate-free resume is defined by the next event sequence,
not a client-calculated byte offset.

**Rationale**:

- Opaque cursors permit log segmentation/compression changes without breaking clients.
- Bounded long-poll tolerates reconnects and proxies better than a single hours-long
  SSH response.
- MCP Tasks defines fetch-later task state and opaque paginated cursors; requestors must
  not rely on optional notifications.

**Alternatives rejected**:

- An endless SSH stream is fragile on poor networks and consumes one transport slot.
- Raw byte offsets alone cannot safely span multiple streams, redaction, and event
  ordering.
- Client-side line counting mishandles partial lines and binary output.

**References**:

- MCP Tasks (2025-11-25):
  https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks

## Decision 7: MCP progress is optional presentation, never durable state

**Decision**: MCP start calls return a durable job result. If a client supplies a
progress token and asks to wait/follow, Sandbox may emit monotonic rate-limited progress
notifications. Notifications stop at tool completion and can be omitted without losing
status or output.

**Rationale**:

- MCP progress requires a unique active token, monotonically increasing progress, and
  recommends rate limiting.
- MCP task notifications are optional and requestors must not depend on receiving them.
- Durable job APIs remain usable by MCP clients that do not implement progress.

**Alternatives rejected**:

- Treating notifications as the log source loses data when the client disconnects.
- Emitting each output line can flood the MCP channel and agent context.

**References**:

- MCP Progress: https://modelcontextprotocol.io/specification/2025-03-26/basic/utilities/progress
- MCP Tasks: https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks

## Decision 8: Exact source deployment precedes remote submission

**Decision**: Reuse the existing one-way deploy path: push the selected commit into a
Sandbox-owned remote repository, reset the target, then stream supported dirty and
untracked files in one compressed transfer. Compute and return a deployment identity
covering commit, dirty manifest, remote target, and workspace. Only after deployment
and runtime capability checks succeed is the executable job accepted.

**Rationale**:

- Tests must reflect the developer's actual working tree, not merely `HEAD`.
- The current deploy implementation already avoids dependence on the project's Git
  remote and streams dirty files without echoing their contents.
- A deployment identity makes retry and result provenance inspectable.

**Alternatives rejected**:

- `git push HEAD` omits uncommitted/untracked changes.
- A shared network mount exposes remote Docker and creates local/remote path coupling.
- Deploying after the job starts allows races between source identity and execution.

## Decision 9: Serialize mutable workspaces; isolate matrices

**Decision**: Ordinary jobs and lifecycle mutations use exclusive leases. Shared
leases require explicit `parallel_safe` intent and workspace policy. Matrix cells get
deterministic isolated labels and separate runtime instances. Host concurrency is
bounded; excess cells queue.

**Rationale**:

- Multiple tests in one mutable instance may race databases, ports, caches, fixtures,
  and generated files.
- CI systems model matrix cells as independent jobs with separate logs/results.
- Persistent development workspaces need predictable state and explicit cleanup.

**Alternatives rejected**:

- Allowing concurrent tests by default is unsafe for WordPress and most integration
  environments.
- Automatically creating a workspace when busy hides resource use and cleanup policy.
- Running a matrix in one instance defeats isolation and makes retries ambiguous.

## Decision 10: Remote CI v1 uses `act` behind a strict compatibility gate

**Decision**: Support complete compatible Linux workflow graphs on one remote host.
Preflight blocks known unsupported/different behavior unless each named divergence is
accepted. Safe mode blocks or neutralizes deployment/release/publishing activity and
records the difference. Sandbox owns outer job records, limits, logs, artifacts, and
cleanup.

**Rationale**:

- Reusing workflows avoids a second CI definition.
- `act` explicitly states it cannot be completely compatible. Its documented gaps
  include ignored concurrency, job timeouts, permissions, environments, annotations,
  summaries, and incomplete cancellation/context behavior.
- A versioned preflight catalog prevents a green local-emulator result from being
  silently represented as hosted GitHub Actions parity.

**Alternatives rejected**:

- Claiming arbitrary GitHub Actions fidelity is not supportable on a generic VPS.
- Re-implementing the full Actions runner protocol is much larger and introduces GitHub
  registration/security concerns.
- Allowing deployment steps by default exceeds development-test authority.

**References**:

- `act` unsupported functionality: https://nektosact.com/not_supported.html
- GitHub workflow logs:
  https://docs.github.com/en/actions/how-tos/monitor-workflows/use-workflow-run-logs
- GitHub workflow artifacts:
  https://docs.github.com/en/actions/concepts/workflows-and-actions/workflow-artifacts

## Chosen defaults

| Policy | Default | Bound/rationale |
|---|---:|---|
| Remote target | Project `runtime.default`; `remote` when configured | No implicit remote without project configuration |
| Workspace | `default` | Persistent reusable development environment |
| Unit deadline | 1,800 s | Reminder when profile fallback is used |
| Integration deadline | 3,600 s | Finite, overridable |
| E2E/CI deadline | 14,400 s | Supports long suites |
| Overall plan deadline | 21,600 s | Bounds parent execution |
| Overnight deadline | 86,400 s | Explicit long-run profile |
| Maximum deadline | 604,800 s | Seven-day hard bound |
| Heartbeat | 5 s | Fast liveness evidence |
| Metrics sample | 10 s | Low overhead |
| Stall warning | Profile-defined; 300 s base | Warning only unless opted in |
| Log page | 64 KiB / 500 events | Agent and transport bounded |
| Long poll | 20 s max | Reconnect-friendly |
| Log segment | 8 MiB | Bounded file operations |
| Per-job output cap | 1 GiB | Explicit failure on exhaustion |
| Free disk reserve | 2 GiB | Prevent host destabilization |
| Job retention | 7 days | Configurable; active jobs protected |
| Segment compression | after 24 h | Gzip terminal inactive segments |
| Active jobs | 4/host | Configurable capacity lease |
| Followers | 5/host | Prevent observer overload |
| Matrix label | <=21 chars | Deterministic runtime-safe labels |

