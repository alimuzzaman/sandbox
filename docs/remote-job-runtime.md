# Durable remote job operations

Remote test and CI jobs are detached before the control connection returns. The
supervisor is the only process that reads the child stdout/stderr pipes; it writes
stdout, stderr, combined event order, metrics, and terminal integrity metadata to
the job store. SSH and MCP retrieve bounded retained pages and never own the
child pipes.

## Normal operation

Use a finite timeout for every long-running command, a reusable workspace for
development, and isolated labels for matrix cells:

```sh
./sb exec --remote NAME --workspace node-unit --timeout 3600 --detach -- npm test
./sb e2e --remote NAME --workspace e2e-dev --timeout 7200 --workers 4 --json
./sb job-status JOB --json
./sb job-output JOB --stream combined --cursor CURSOR --max-bytes 65536
./sb job-output JOB --stream stderr --tail-bytes 8192 --wait-seconds 2
```

`job-output --follow` is a client polling loop over retained files. `full`,
`smart`, `errors`, `sampled`, `quiet`, and declarative custom profiles affect
presentation only; complete output remains retained until cleanup/retention
policy removes it.

Remote E2E uses the same detached outer job and then runs the co-located E2E
coordinator, which gives its Playwright workers their existing isolated runtime
instances. Use `--local` only when deliberately keeping the coordinator local.

## Health and recovery

Status separates lifecycle from health. A quiet process is not failed; suspected
stall, supervisor-unresponsive, process-missing, orphaned, and unreachable states
include the evidence used for classification. Stall warnings do not cancel a job
unless the submission explicitly opts into cancellation-on-stall.

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
original result. Failed reusable workspaces are retained by default. Reset and
destroy are explicit and refuse active workspace leases.

`job-cleanup` is terminal-only and reports which logs/artifacts were removed.
For scheduled/maintenance cleanup, apply the configured age explicitly with
`./sb job-retention --retention-days 7 --json`; it removes terminal output,
metrics, and artifacts and records `cleanup_state` in the registry.
If the host is below its configured free-disk reserve, use
`./sb job-retention --storage-pressure --json`; only the oldest terminal jobs
are reclaimed until pressure clears. Active jobs and retained failed workspaces
remain protected. Output writes fail explicitly as `storage_pressure` if the
reserve is crossed, never as a false successful test.
Artifact collection rejects symlinks, non-regular objects, path escapes, and
per-job count/size limits. Retrieve artifacts by immutable ID with
`./sb job-artifacts JOB --json` and `./sb job-artifact-get JOB ARTIFACT --output-file PATH`.
Never delete a workspace or job directory by hand: the registry and lease store
must remain authoritative.

## Remote CI

`ci run --remote NAME` preflights the workflow, deploys the exact working tree
once, creates deterministic per-label workspace copies from that deployed tree,
and returns a durable parent ID with isolated child jobs for selected matrix
cells. Inspect the parent for aggregate counts and each child for output,
deadline, artifacts, and cleanup. Known compatibility differences must be
accepted by exact ID; safe mode neutralizes deployment/release/publish side effects
by default and records each semantic difference in the child result. Use `--local`
when deliberately choosing the local `act` path.
