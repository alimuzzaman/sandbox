# Durable remote job operations

Remote test and CI jobs are detached before the control connection returns. The
supervisor is the only process that reads the child stdout/stderr pipes; it writes
stdout, stderr, combined event order, metrics, and terminal integrity metadata to
the job store. SSH and MCP retrieve bounded retained pages and never own the
child pipes.

## Normal operation

The examples use the repository launcher (`./sb`). From another checkout, use
the installed `sb` command; the command surface and selectors are the same.

Use a finite timeout for every long-running command, a reusable workspace for
development, and isolated labels for matrix cells:

```sh
./sb exec --remote NAME --workspace node-unit --timeout 3600 --detach \
  --request-id node-unit-tests-1 -- npm test
./sb e2e --remote NAME --workspace e2e-dev --timeout 7200 --workers 4 --json
./sb job-status JOB --json
./sb job-output JOB --stream combined --cursor CURSOR --max-bytes 65536
./sb job-output JOB --stream stderr --tail-bytes 8192 --wait-seconds 2
./sb job-metrics JOB --remote NAME --limit 500 --json
```

`job-output --stream` is for retained `combined`, `stdout`, and `stderr` logs.
Resource samples are a separate bounded control-plane document retrieved with
`job-metrics`; use `job-status` for the live health summary.

If local `job-status` returns `job_not_found`, it has not inferred a remote.
Run `./sb remote list`, then repeat the same observation with the explicit
`--remote NAME` selector. This returns a structured error rather than a Python
traceback, and never resubmits the job.

`job-output` accepts exactly one retained-output position selector: an opaque
cursor, a byte offset across the rendered stream, a trailing byte count, a line
count, or an RFC 3339/Unix-seconds `since` timestamp. Its bounded long-poll
interval is 0-20 whole seconds; zero disables a one-shot wait. Output page
sizes are bounded to 1-262144 bytes. The CLI and remote transport reject
values outside either bound before target lookup, so an invalid remote query
returns a stable `invalid_output_query` envelope instead of a traceback.
`job-output
--follow` is a client polling loop over retained files and converts a validated
zero into its one-second polling wait. `full`,
`smart`, `errors`, `sampled`, `quiet`, and declarative custom profiles affect
presentation only; complete output remains retained until cleanup/retention
policy removes it. Output pages preserve the existing `bytes_read` page-accounting
semantics and add `rendered_bytes` as the exact UTF-8 size of the final returned
`data` string. This includes replacement characters and profile filtering; for
base64 pages it counts the returned base64 text bytes.

Remote follow bounds the health/status check after every output page. If remote
process-identity inspection does not answer within five seconds, follow stops
with a redacted status-unknown recovery envelope. Resume with `job-status` or a
non-follow `job-output` read.

Remote E2E submits a durable matrix parent with one isolated workspace leaf per
Playwright shard. Each leaf runs exactly one `--shard=i/N` coordinator, so its
status, output, retry, and failure retention remain independently observable.
Use `--local` only when deliberately keeping the coordinator local.

If a remote control call cannot return a valid acceptance or retained-output
receipt, the CLI emits one redacted `remote_job_transport_error` envelope (or a
bounded human error) and exits non-zero. The envelope marks acceptance as
`unknown` for submission operations; it never invents a job ID or implies that a
retry is safe. A malformed or truncated control response is never echoed from
stdout as an error detail, so retained job pages cannot become traceback text.
Inspect remote job state before replaying the request.

Queued jobs expose advisory scheduler evidence in `job-status --json` and the
acceptance envelope. `queue.position` follows durable acceptance order, while
`queue.blocking_jobs` contains bounded opaque job IDs and workspace labels.
Terminal or expired leases are reaped on the next admission.

Synchronized-generation job execution is not yet a supported runtime path.
Submissions carrying synchronization metadata fail closed unless an authoritative
sync gateway is composed; the CLI does not accept hidden relationship or source
policy claims from callers. Jobs without sync fields keep the existing
deploy-before-job path unchanged.

Likewise, a remote WordPress unit or integration run with two or more repeated
`--workspace` labels becomes one durable matrix parent with an isolated test
leaf for each named workspace.

Remote CI safe mode neutralizes known deployment/release/publish operations and
blocks unknown external mutation actions before deployment. CI secrets must be
explicitly named in `ci_secrets`; environment-backed `SANDBOX_CI_SECRET_*`
values additionally require `ci_secret_allowlist`.

## Declared test plans

Projects may declare `runtime.testPlans` with explicit argv, stable step IDs,
dependencies, optional artifact paths, and `parallelSafe` steps. Submit one as
a durable parent with independently inspectable isolated children:

```sh
./sb test matrix --remote NAME --plan verify --timeout 1800 --json
```

Steps serialize unless `parallelSafe: true`; declared `needs` and `maxParallel`
become durable child dependencies. Sandbox never discovers package scripts or
test commands automatically.

## Health and recovery

Status separates lifecycle from health. A quiet process is not failed; suspected
stall, supervisor-unresponsive, process-missing, orphaned, and unreachable states
include the evidence used for classification. Stall warnings do not cancel a job
unless the submission explicitly opts into cancellation-on-stall.

When a child exits with POSIX `SIGKILL` (`-9` or shell status `137`), the durable
record uses `termination_reason=process_killed` instead of the generic
`exit_nonzero`. This is intentionally not labelled OOM: distinguishing a kernel/
container OOM from an operator or runtime kill still requires independent host or
cgroup evidence.

After a host restart or supervisor failure, run:

```sh
./sb job-reconcile --json
```

Reconciliation verifies the recorded boot/PID/start identity. A missing or
mismatched supervisor becomes `interrupted`, never `succeeded`, and its partial
output remains available. Expired workspace and capacity leases are released;
active jobs with a matching supervisor are left untouched.

## Cancellation, retry, and cleanup

`job-cancel` sends a verified graceful signal first; `--force` uses the verified
owned process group. `job-retry` creates a linked attempt and does not mutate the
original result. Retry reads a bounded canonical submission snapshot, preserving
artifact declarations, compatibility/dependency/failure policy, cleanup policy,
environment key names, source/workspace/deadline/output settings, and parent context.
Legacy rows fall back only to safely persisted fields. Standalone retries remain
parentless; CI children retain their actual parent and appear under parent
`retry_attempts`, outside frozen original aggregate membership. Aggregate-parent retry
fails `aggregate_retry_unsupported` until scoped graph retry exists. Failed reusable
workspaces are retained by default. Reset and destroy are explicit and refuse active
workspace leases.

Remote CI uses the built-in `ci` execution profile when no project/workspace profile
is declared; that profile requests `ephemeral` cleanup for its isolated cells. After
the job has a durable terminal row and complete retained output/artifact evidence, the
supervisor may release only an exact indexed `workspace_id` backed by a controller-run
CI materialization receipt whose immutable digest was stored with acceptance. A fresh
check must prove no live recorded child/supervisor, residual child process group,
owned child cgroup, container mount, host mountpoint or bind source, resource binding,
lease, or other active job. The exact filesystem identity is moved into a private
owner-only cleanup root and deleted through its continuously open directory descriptor;
the final directory entry is revalidated against that descriptor, so pathname
replacements are never deletion targets. Workspace validation/materialization and
durable job acceptance hold the same controller lock as terminal deletion, so a new
accept cannot commit after the final active-job check. The same seam covers
`supervisor_launch_failed`. Retry restores from one retained archive capped at 512 MiB
for both apparent input and compressed output, 100,000 entries, and a 1 GiB post-write
free-space reserve; it does not accumulate a new archive per
attempt. The ownership projection reports its retained byte count, and explicit/age or
pressure cleanup retires the exact digest-verified archive. Restore hashes, sizes, and
extracts one open archive identity; retirement hashes and
moves that same open identity before unlinking it. A path replacement fails closed and
a failed restore removes staging without publishing a checkout.
Generic jobs, reusable/index/legacy
workspaces, `retain`, failed `on-success`, missing authority, unknown observations,
ownership drift, and unsafe paths all prevent deletion. Cleanup failure is separate
from lifecycle, exit code, result, logs, metrics, and artifacts. This is local candidate
behavior until the open remote and measured-reclamation gates in Spec 032 pass.

`job-cleanup` is terminal-only and reports which logs/artifacts were removed.
For scheduled/maintenance cleanup, apply the configured age explicitly with
`./sb job-retention --retention-days 7 --json`; it removes terminal output,
metrics, declared artifacts, and retained CI rematerialization archives, then records
`cleanup_state` in the registry.
If the host is below its configured free-disk reserve, use
`./sb job-retention --storage-pressure --json`; only the oldest terminal jobs
are reclaimed until pressure clears. Active jobs and retained failed workspaces
remain protected. Output writes fail explicitly as `storage_pressure` if the
reserve is crossed, never as a false successful test.
Artifact collection rejects symlinks, devices, sockets, FIFOs, path escapes, and
per-job count/size limits. Files that grow, shrink, change identity, or cross a live byte
limit during collection fail rather than producing a misleading hash. Literal directories
become sorted deterministic tar archives with normalized metadata. Retrieve artifacts by immutable ID with
`./sb job-artifacts JOB --json` and `./sb job-artifact-get JOB ARTIFACT --output-file PATH`.
CLI file retrieval downloads every bounded chunk to a same-directory temporary file,
validates total size and SHA-256, then atomically publishes it; MCP returns one bounded
chunk with next-offset metadata. Offset must be non-negative and page size 1..1 MiB.
Cleanup removes the authoritative `metrics.jsonl`, retains audit rows, marks removed output
and metrics unavailable, and marks artifacts expired; post-cleanup metric reads fail
`metrics_unavailable`. Never delete a workspace or job directory by
hand: the registry and lease store must remain authoritative.

## Remote CI

`ci run --remote NAME` preflights the workflow, deploys the exact working tree
once, creates deterministic per-label workspace copies from that deployed tree,
and returns a durable parent ID with isolated child jobs for selected matrix
cells. Inspect the parent for aggregate counts and each child for output,
deadline, artifacts, and cleanup. Known compatibility differences must be
accepted by exact ID; safe mode neutralizes deployment/release/publish side effects
by default and records each semantic difference in the child result. Upload-artifact
preflight requires literal relative paths and `if-no-files-found: error`; patterns,
expressions, and unsupported options block before execution. Terminal parent
status persists a normalized result capped at 256 KiB containing aggregate counts and
bounded original-child references. Full current artifact/difference/output/cleanup detail
remains in frozen `children`; retries are separate in `retry_attempts`. Existing
`aggregate`, `children`, and `result_json` keys remain. MCP `ci_run` accepts both local `cells`
reports and remote durable `parent_job_id`/`children` reports. Use `--local` when
deliberately choosing the local `act` path.
