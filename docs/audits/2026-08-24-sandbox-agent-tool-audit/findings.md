# Findings

Each finding is classified as one or more of:

- **Product gap**: a reusable Sandbox capability or contract is missing.
- **Agent misuse**: the existing capability is being called in an avoidably
  expensive or incorrect way.
- **Guidance gap**: the capability exists, but the skill/docs do not make the
  safe path obvious enough.
- **Adjacent orchestration**: the issue is in agent/thread coordination rather
  than Sandbox itself.

## ATO-001 — Add a durable job observer

Priority: P1
Classification: product gap
Confidence: high

### Evidence

The detailed storage rollout recorded 118 `job-status` calls, 43
`job-output` calls, and 77 `sleep` commands. The current detached resource helper
returns two independent polling recipes (`job-status` and `job-output`) in
[`sandbox/resources/detached.py`](../../../sandbox/resources/detached.py:113),
and the CLI manifest has no `job-observe` or `job-wait` command. MCP has a bounded
`job_follow`, but it is limited to a single request's 20-second window and returns
updates rather than a terminal result.

The independent Luna Max event pass anchored this to eight real durable job IDs;
three representative jobs received 38, 31, and 15 status calls while callers
alternated output reads and 30–55 second sleeps.

### Recommendation

Add a read-only `sb job-observe JOB_ID` / `job wait` command and matching MCP
tool. It should accept `--remote`, a finite `--max-wait`, an optional poll
interval, a cursor, and an output/result profile. It should poll internally and
return one canonical envelope containing lifecycle, health, exit code, deadline,
final result, output completeness, and bounded progress/output evidence.

Existing `job-status` and `job-output` remain compatible lower-level primitives.

### Acceptance criteria

- One request can wait for a remote job to reach terminal state or a stated bound.
- A timeout is reported as observation-incomplete, never as job failure.
- The final envelope is valid JSON exactly once; retained output remains bounded.
- The command never submits, retries, cancels, or mutates a job implicitly.
- Tests cover running, terminal success, terminal failure, partial output, remote
  transport failure, and cursor continuation.

## ATO-002 — Make CI runs replay-safe

Priority: P1
Classification: product gap plus agent misuse
Confidence: high

### Evidence

The `Check CI runner status` transcript contained two identical `ci_run` calls.
The MCP signature in [`mcp/wp-server/tools/ci.py`](../../../mcp/wp-server/tools/ci.py:38)
does not expose a durable `request_id`. Remote CI creates a random short `run_id`
in [`sandbox/commands/ci.py`](../../../sandbox/commands/ci.py:593), so an
uncertain retry is not guaranteed to resolve to the original aggregate run.
The same transcript also called `ensure_instance` before `ci_run`, although CI
provisions its own isolated matrix-cell instances.

### Recommendation

Add `request_id` to CLI and MCP CI submission. Persist it on the aggregate parent
and derive child identity from that accepted parent. Replaying the same request
ID with the same target and source should return the original parent and children;
reusing it with different inputs should fail closed with an identity conflict.

Update the CI skill to say that `ci_run` owns cell provisioning; do not require a
preparatory `ensure_instance`.

### Acceptance criteria

- CLI and MCP accept the same replay-safe request ID.
- A lost response followed by an identical retry creates no duplicate parent or
  child jobs.
- A request-ID conflict is typed and non-mutating.
- Parent and child receipts expose the request ID and replay origin.
- Tests cover local async compatibility and remote aggregate CI.

## ATO-003 — Establish one machine-readable job-output contract

Priority: P1/P2
Classification: product/contract gap
Confidence: high

### Evidence

`job-output --follow --json` prints one JSON object per update, while the
retained `data` field can itself contain JSONL. Agents in the detailed rollout
repeatedly attempted whole-output `json.loads` and received decode failures. One
agent also assumed `health` was an object although the current status contract
uses a string health enum plus separate evidence.

### Recommendation

Document and test a stable envelope for `job-status`, `job-output`, and the new
observer. Either make repeated CLI JSON explicitly JSONL (`--jsonl`) or make
`--json` return one final aggregate object. Add named profiles for structured
progress/result events so callers do not parse raw retained logs.

### Acceptance criteria

- `--json` behavior is unambiguous for one-shot and follow modes.
- The schema distinguishes lifecycle, health, health evidence, retained bytes,
  event count, result, and completeness.
- A machine caller never needs to parse human text or nested JSONL to determine
  terminal outcome.
- CLI and MCP fixtures assert equivalent field names and types.

## ATO-004 — Add a remote readiness/protocol receipt

Priority: P2
Classification: product/guidance gap
Confidence: medium-high

### Evidence

Workspace operations correctly fail closed when ownership or runtime revision is
not proven. In the sampled work, agents nevertheless repeated remote migration
planning and confirmation and retried dependent commands after revision mismatch.
An earlier detached-job attempt also hit an invalid execution-policy wire value
before the client/controller skew was repaired.

### Recommendation

Add a read-only `sb remote doctor NAME` or `remote preflight NAME --capability ...`
receipt that combines ownership, installed/local revision, protocol/schema
compatibility, listener health, and the exact migration plan/recovery command.
Dependent commands should surface that receipt or a stable plan ID instead of
forcing a caller to rediscover it. Keep migration explicit and confirmation-gated;
never auto-migrate from a preflight.

### Acceptance criteria

- One bounded read-only call explains whether `job.exec`, workspace, and resource
  capabilities are ready.
- Revision mismatch and unavailable evidence remain distinct typed states.
- The receipt contains no credentials or unsafe remote paths.
- Workspace/job commands preserve fail-closed behavior and explicit migration.

## ATO-010 — Propagate request identity through matrix jobs

Priority: P1
Classification: product gap
Confidence: high

### Evidence

The generic matrix path is separate from CI and has the same replay risk. MCP
[`job_matrix`](../../../mcp/wp-server/tools/jobs.py:148) has no
`request_id`; the CLI matrix parser exposes no request-ID option in
[`jobs_runtime.py`](../../../sandbox/commands/jobs_runtime.py:312); and
[`JobService.submit_matrix`](../../../sandbox/application/job_service.py:789)
creates the parent without one. The job registry only deduplicates submissions
when `submission.request_id` is present.
Therefore a lost matrix acceptance can create a second parent and duplicate
children even though ordinary `job-start` is replay-safe.

### Recommendation

Add a matrix-level request ID to CLI and MCP. Persist it on the aggregate parent,
derive child identity from the accepted parent, and pass the identity through
remote `submit_many`. Keep child workspace labels readable, but make the parent
the replay authority.

### Acceptance criteria

- Identical matrix replay returns the original parent and children.
- Reuse with changed command, target, workspace list, or source fails closed.
- Child submissions cannot be duplicated by an uncertain parent response.
- Local and remote matrix paths expose the same receipt fields.

## ATO-011 — Align remote resource calls with runtime/revision readiness

Priority: P1/P2
Classification: product gap plus documentation defect
Confidence: high

### Evidence

[`docs/resource-monitoring.md`](../../../docs/resource-monitoring.md:424)
currently says resource status/plan/cleanup and retention helpers ship probe
source on every call and therefore work against an older host runtime. The
implementation instead sends a typed `POST /resources` request through the
installed remote control service in
[`sandbox/resources/remote.py`](../../../sandbox/resources/remote.py:3291),
where the server runs its installed `LocalProbeAdapter`. The resource adapter
checks only that the remote is marked provisioned in
[`sandbox/resources/remote.py`](../../../sandbox/resources/remote.py:3259),
while workspace operations
already perform an ownership/revision preflight. This creates an inconsistent
boundary: workspace calls refuse stale services, but resource calls can reach one
without an equivalent readiness gate or explicit runtime revision in the result.

### Recommendation

Correct the docs and skill to state the actual control-service requirement. Add a
resource protocol/runtime handshake or reuse the shared remote readiness receipt;
return explicit stale/unavailable evidence before interpreting resource values.
Add a test for an older or semantically incompatible resource service.

### Acceptance criteria

- Resource status/plan/cleanup expose service ownership and runtime/protocol
  compatibility or fail closed before measurement.
- A stale service cannot silently produce semantically unversioned resource data.
- Docs match the actual control-plane transport and supported lifecycle update.

## ATO-012 — Prove remote provision/up readiness before claiming success

Priority: P2
Classification: product contract gap
Confidence: medium-high

### Evidence

`remote provision` and `remote up` in
[`sandbox/commands/remote.py`](../../../sandbox/commands/remote.py:546)
report a reachable/up result after staging or systemd migration, but they do not
perform the existing bounded authenticated `/mcp` readiness probe before
persisting success. The existing `remote_doctor_checks` in
[`sandbox/core/_remote.py`](../../../sandbox/core/_remote.py:610) are available
but are not part of those success paths.

### Recommendation

After the candidate service metadata exists, run a bounded authenticated control
probe and return structured readiness states such as `ready`, `listener_only`,
`auth_failed`, or `unavailable`. Persist `provisioned=true` only according to the
existing authority policy; never claim “reachable” from systemd evidence alone.

### Acceptance criteria

- Provision/up success proves the advertised control endpoint and authentication,
  not only service-file installation.
- Proxy, listener, auth, and revision failures are distinct and redacted.
- A failed readiness probe leaves a recoverable, non-success receipt and does not
  claim the remote is ready.

## ATO-013 — Normalize remote service status transport failures

Priority: P2
Classification: product contract bug
Confidence: high

### Evidence

[`remote_mcp_service_status`](../../../sandbox/core/_remote.py:3000) parses
stdout without checking the SSH result code.
An SSH failure with empty output can therefore become an ordinary `{ok: true,
status: observed}` response containing mostly `unknown` values. The service
command wrapper then presents that as a positive-looking status envelope, and
mutation paths may continue after unverified status.

### Recommendation

Check the transport return code and return an explicit unavailable/error state.
Make migration and other mutating service operations refuse transport failure,
not merely unknown parsed fields. Add a non-zero-SSH contract test and preserve
credential/path redaction.

### Acceptance criteria

- Non-zero SSH/control transport produces stable `ok:false` or a typed
  `status=unavailable` envelope.
- No service mutation proceeds on missing ownership/revision evidence.
- Human and JSON modes communicate the same underlying state.

## ATO-014 — Remove resource documentation/spec drift

Priority: P3
Classification: documentation/spec defect
Confidence: medium

### Evidence

The resource documentation describes a directory-walk depth that differs from
the current remote adapter default and Spec 036. It also describes unused managed
networks as possible cache candidates, while the adapters classify those networks
as non-eligible and the planner accepts only `disposable_cache`.

### Recommendation

Choose the intended contract, align docs, skill, specs, and tests, and state when
a category is diagnostic-only rather than cleanup-eligible. Do not broaden cleanup
authority merely to make the prose match an old example.

### Acceptance criteria

- Depth/default examples match implementation and Spec 036.
- Network classification and cleanup eligibility are described consistently.
- A contract test fails if docs-facing values drift again.

## ATO-015 — Clarify Spec 036 cache-state wording

Priority: P3
Classification: specification/documentation defect
Confidence: medium

### Evidence

Spec 036's plan language describes a one-in-memory scan with no persistent
feature state, while the implementation and resource docs intentionally persist
the host directory index and expose `--refresh`. The likely intended distinction
is “no new cleanup plan records” rather than “no persistent scan cache,” but the
current wording can make an agent treat the implemented cache as out of contract.

### Recommendation

Clarify the plan and contract wording: distinguish the bounded persisted directory
index/cache from durable cleanup plans, leases, or mutation state. Keep the
existing cache safety and refresh semantics explicit.

### Acceptance criteria

- Spec, docs, skill, and implementation agree on what state is persisted.
- Cache persistence is described as diagnostic/index state, not cleanup authority.
- A contract test or checklist prevents this wording from drifting again.

## ATO-016 — Make confirmed remote provisioning convergent

Priority: P2
Classification: product/idempotency gap
Confidence: high

### Evidence

The confirmed [`remote provision`](../../../sandbox/commands/remote.py:546)
path stages/bootstrap-installs again, mints a fresh bearer token, restarts the
service, and overwrites the local remote record.
The result intentionally does not return the credential. A second provisioning
run can therefore invalidate an existing external MCP client even when the
remote was already healthy. The remote-hosting specification requires safe
second provisioning, but the current path behaves like implicit credential
rotation.

### Recommendation

Make a healthy repeated provision a no-op/convergence path that preserves the
existing token and reports `already_ready`, or require an explicit `--rotate`
operation with a separate confirmation and structured credential-change receipt.
Do not rotate credentials as a side effect of retrying an uncertain provisioning
response.

### Acceptance criteria

- Repeating confirmed provision on a healthy remote preserves the token and
  endpoint identity.
- Explicit rotation is separate, clearly named, and confirmation-gated.
- Lost output followed by an identical retry is safe and inspectable.
- Tests cover healthy, partially provisioned, stale, and failed remotes.

## ATO-005 — Provide a compact agent bootstrap context

Priority: P2
Classification: product/guidance gap
Confidence: medium-high

### Evidence

Across the detailed rollout, exact duplicates included `./sb guide
--project-dir .` four times and `./sb skill show sandbox-cli` seven times. In the
broader 473-rollout corpus, 303 sessions contained a guide pattern and 223
contained a skill-show pattern.

### Recommendation

Add `sb context --project-dir . --skill sandbox-cli --json` (or an equivalent
agent bootstrap API) that returns the relevant guide, selected skill revision,
project identity, runtime target, local/remote revision, and safe next commands
in one bounded receipt. Make it cacheable and invalidate it on config, branch, or
Sandbox revision changes.

### Acceptance criteria

- A fresh agent can establish target and operating rules with one call.
- The receipt does not include credentials or unbounded instruction text.
- It identifies whether the next operation is local, remote, or ambiguous.
- Existing `guide` and `skill show` remain available for targeted refreshes.

## ATO-006 — Make remote-only intent explicit and fail closed

Priority: P2
Classification: agent misuse plus product guard
Confidence: medium

### Evidence

In a remote-storage transcript, the agent initially inspected local state even
though the user had said to use only the Sandbox remote. The current `resources`
command supports explicit `--remote`, but the caller has no durable context flag
that says local probing is forbidden for the current operation.

### Recommendation

Add an explicit `--remote-only`/target policy for multi-step diagnostic workflows,
or provide a `sb remote resources ...` alias that cannot silently resolve local.
Every receipt should show the resolved target. Update the skill to require target
declaration before any capacity, workspace, or job observation call.

### Acceptance criteria

- A remote-only workflow refuses an implicit local target before side effects.
- The refusal names the missing remote without probing another host.
- Explicit `--local` remains available when the user intentionally overrides.

## ATO-007 — Make bulk feedback export the default agent path

Priority: P3
Classification: guidance gap
Confidence: medium

### Evidence

`feedback export --format jsonl` already exists and is bounded/path-free, but
agents still used `feedback list` plus shell cursor loops. Prior runs recorded
control-character and pagination parsing failures.

### Recommendation

Update the skill and guide examples to prefer export for audits. If bulk review is
common, add an explicit bounded `--all` cursor-drain mode rather than requiring
each agent to reimplement pagination. Do not add a second feedback storage path.

### Acceptance criteria

- The documented audit path produces stable bounded JSONL without shell parsing.
- Cursor, invalid-record, and max-byte states remain visible.
- Existing redaction and untrusted-data behavior is preserved.

## ATO-008 — Improve test selection and failure summaries

Priority: P3
Classification: guidance/product ergonomics
Confidence: medium

### Evidence

The detailed rollout contained hundreds of raw Python/unittest and broad-suite
commands, repeated interruptions, two `./ .cli-venv/bin/python` typos, and a zsh
`echo ====` typo. Agents often needed manual narrowing after an expensive broad
run.

### Recommendation

Expose a clear `sb test plan`/`sb test --select` path for named modules or test
groups, with a bounded summary that distinguishes expected skips, failures,
interrupts, and incomplete execution. Improve the guide examples before adding
new test-runner machinery.

### Acceptance criteria

- A caller can select a named test group without reconstructing a Python command.
- Interrupted or timed-out suites are not reported as passing.
- The summary includes the exact selection and target.

## ATO-009 — Batch agent waits outside Sandbox

Priority: P3
Classification: adjacent orchestration
Confidence: medium-high

### Evidence

The detailed rollout recorded 182 collaboration wait calls, 32 unique agent
paths, and 9 interrupted subagents.

### Recommendation

Use aggregate thread waits, explicit lane stop criteria, and bounded status
snapshots. This is not a Sandbox command change, but it is the largest observed
non-Sandbox source of coordination overhead.

## ATO-017 — Make `secrets run` fail closed on child failure

Priority: P1/P2
Classification: product/contract bug plus security-workflow safety
Confidence: high

### Evidence

The Hermes setup transcript `01a027e9-9d13-7b20-aa49-cef6c5e847b0` recorded a
trusted child exiting with code 11 while `sb secrets run` reported success and
returned a zero shell status. The current command path always emits the broker
result with `as_json=False` and does not translate the child result into the
process exit status in [`sandbox/commands/secrets.py`](../../../sandbox/commands/secrets.py:202).
The runner does retain `exit_code` and `termination` in
[`sandbox/secrets/runner.py`](../../../sandbox/secrets/runner.py:135), and the
model exposes those fields in [`sandbox/secrets/models.py`](../../../sandbox/secrets/models.py:70).

### Recommendation

Return nonzero for a nonzero child exit, timeout, or failed start, while keeping
redacted output and a stable `--json` result available to automation. Distinguish
child failure from broker failure and timeout; never print secret values or raw
child environment data.

### Acceptance criteria

- Exit 0 is possible only when the child exits 0 and the broker result is valid.
- Child exit, timeout, startup failure, and malformed result have distinct codes.
- JSON mode is one bounded envelope with `exit_code`, `termination`, and
  redaction metadata; human mode communicates the same state.
- Tests prove a failing trusted child cannot be mistaken for a successful secret
  operation.

## ATO-018 — Restore CLI/MCP job-follow parity

Priority: P2
Classification: product/contract drift
Confidence: high

### Evidence

The Spec 032 CLI contract declares `sb job follow JOB_ID` with cursor, polling,
and output-profile options in [`specs/032-remote-job-runtime/contracts/cli.md`](../../../specs/032-remote-job-runtime/contracts/cli.md:60).
The current CLI only adds `--follow` to `job-output` in
[`sandbox/commands/jobs_runtime.py`](../../../sandbox/commands/jobs_runtime.py:201)
and registers no `job-follow` command in its manifest at line 928. MCP does
expose a bounded `job_follow` in [`mcp/wp-server/tools/jobs.py`](../../../mcp/wp-server/tools/jobs.py:291).
The CLI/MCP surface-sweep transcript `019fc242-d2cd-7500-b4b9-84f184f1324e`
recorded the mismatch while checking the documented interface.

### Recommendation

Either implement the declared CLI command using the same bounded follow service
as MCP, or revise the contract and add an explicit parity test. Prefer one
shared implementation so cursor, reconnect, terminal-state, and output-profile
semantics do not diverge.

### Acceptance criteria

- The documented CLI command exists with the documented options, or the contract
  is intentionally revised in the same change.
- CLI and MCP return equivalent event, cursor, bound, and terminal semantics.
- Follow is finite by default and never silently submits, retries, or cancels.
- Help, specs, skill examples, and parity fixtures agree.

## ATO-019 — Normalize malformed job-ID errors

Priority: P2
Classification: product/contract ergonomics
Confidence: high

### Evidence

The same surface-sweep transcript recorded Python `ValueError: job id is
invalid` tracebacks for `job-status`, `job-cancel`, `job-retry`, and
`job-output`, while sibling entry points rejected malformed IDs with structured
errors. The current command handlers call transports/services directly in
[`sandbox/commands/jobs_runtime.py`](../../../sandbox/commands/jobs_runtime.py:398)
and the shared validator already exists in [`sandbox/jobs/models.py`](../../../sandbox/jobs/models.py:118).
MCP wrappers catch validation inconsistently; the transcript also recorded a
malformed `job-list` limit escaping as a raw error.

### Recommendation

Validate job IDs and numeric bounds at every CLI/MCP boundary, then emit the same
`ok:false`, stable code, safe message, and nonzero status in human and JSON
modes. Keep internal `ValueError` details out of caller-facing output.

### Acceptance criteria

- Every job verb handles malformed IDs and bounds without a traceback.
- Local and remote CLI paths and every MCP job tool use one error schema.
- Human output and JSON output preserve the same error code and retryability.
- Tests cover malformed IDs, missing jobs, invalid limits, and valid IDs for all
  status/output/cancel/retry/list verbs.

## ATO-020 — Put a confirmation gate on job-retention deletion

Priority: P1/P2
Classification: safety/product contract gap
Confidence: high

### Evidence

The retention transcript `019fc1f7-ec2d-7d81-95dc-195376f44fe7` recorded
`./sb job-retention --json` removing logs and metrics for many historical jobs;
the command had no `--dry-run` or `--confirm`. Current
[`configure_retention_parser`](../../../sandbox/commands/jobs_runtime.py:304)
and [`cmd_job_retention`](../../../sandbox/commands/jobs_runtime.py:790)
directly invoke the cleanup sweep. A later bounded call in the same transcript
returned no candidates, confirming the side effect was persistent.

### Recommendation

Make the default command a read-only retention plan/preview. Require an explicit
`--confirm` for deletion and return candidate IDs, age, target, and estimated
bytes in dry-run output. Keep any scheduled internal sweeper as a separate,
reviewed service path with its own policy and receipt; MCP must use the same gate.

### Acceptance criteria

- A bare or `--json` retention command cannot delete data.
- Deletion requires explicit confirmation and identifies the target and policy.
- Preview and apply return stable, bounded receipts and are replay-safe.
- Tests prove malformed, remote, repeated, and interrupted retention requests do
  not broaden the deletion set.

## ATO-021 — Apply remote-service preflight to resource reclamation

Priority: P1/P2
Classification: product/safety boundary gap
Confidence: high

### Evidence

Workspace mutations call `_assert_remote_service_ready` before dispatch in
[`sandbox/application/workspace_service.py`](../../../sandbox/application/workspace_service.py:404),
but [`reclaim_service`](../../../sandbox/resources/context.py:53) constructs a
`RemoteResourceAdapter` directly. Its `_entry` checks only configured and
`provisioned` state in [`sandbox/resources/remote.py`](../../../sandbox/resources/remote.py:3259),
then sends `/resources` requests without an ownership or runtime-revision
probe. Spec 035 requires equivalent remote safety semantics. The remote storage
transcript `01a0068a-ee37-7260-9aff-888a5fd36c89` supplied the partial-inventory
and unavailable-measurement scenario where cleanup authority must remain
fail-closed.

### Recommendation

Centralize the remote-service readiness receipt in the resource adapter or make
the `/resources` endpoint self-verifying. `workspace release`, TTL, and reap
must refuse stale, ambiguous, or mismatched services before any remote mutation,
and the result must retain target, ownership, revision, and completeness state.

### Acceptance criteria

- Resource observe, lease, release, TTL, reap, and revalidation share the
  workspace-grade ownership/revision preflight.
- A stale or misattributed service cannot receive a resource mutation request.
- Unavailable, partial, mismatched, and complete evidence are distinct states.
- CLI/MCP tests cover local, healthy remote, stale remote, and transport failure.

## ATO-022 — Distinguish expected Hermes absence from doctor failure

Priority: P2
Classification: product/status contract gap
Confidence: medium-high

### Evidence

The Hermes transcript found a reachable, provisioned remote whose Hermes binary
and MCP catalog were not installed. `hermes doctor` returned a degraded
`doctor_failed` result rather than a typed install-needed state, interrupting a
normal setup decision. The current doctor implementation collapses all missing
prerequisites into one result in [`sandbox/core/_hermes.py`](../../../sandbox/core/_hermes.py:2521).

### Recommendation

Return explicit states such as `not_installed` or `install_needed` for an
expected pre-install probe, with exit 0 only for an observational probe that
completed successfully. Keep `--require-ready` or the setup command strict and
nonzero when the caller needs a usable Hermes installation.

### Acceptance criteria

- Missing Hermes, missing MCP registration, broken prerequisites, and transport
  failure are distinct machine-readable states.
- Setup can branch from `install_needed` without parsing a generic failure.
- A ready check remains strict and cannot be satisfied by an absent installation.

## ATO-023 — Add component-scoped Hermes readiness

Priority: P2
Classification: product/status ergonomics
Confidence: medium-high

### Evidence

`hermes health` computes separate component objects but extends one global
`degraded_reasons` list for remote MCP, gateway, scheduler, cron, sessions, and
worktrees in [`sandbox/core/_hermes.py`](../../../sandbox/core/_hermes.py:2761).
The Hermes transcript observed remote MCP as healthy/revision-matched while an
unrelated legacy gateway or cron drift made the overall command nonzero, which
blocked a capability-specific host decision. The docs explicitly describe this
aggregate behavior in [`docs/hermes-agent.md`](../../../docs/hermes-agent.md:289).

### Recommendation

Add a capability-scoped readiness query (for example
`hermes health --component remote_mcp` or `hermes remote readiness`) with its
own status and exit policy. Preserve aggregate health for operations that truly
need the whole installation, but do not force callers to treat unrelated drift
as a blocker for a proven capability.

### Acceptance criteria

- Component and aggregate results expose the same bounded evidence and reason
  codes.
- A caller can decide whether remote MCP, gateway, scheduler, or dashboard is
  ready without scraping the aggregate text.
- Overall health remains conservative and no component-scoped command performs
  repair implicitly.

## ATO-024 — Separate Hermes host operations from repository runs

Priority: P2
Classification: product boundary/guidance gap
Confidence: medium-high

### Evidence

The Hermes CLI rejects `run` unless both `--repo` and `--prompt` are present in
[`sandbox/commands/hermes.py`](../../../sandbox/commands/hermes.py:160). In the
Hermes setup transcript, a host-only service-maintenance attempt therefore
failed with `missing_run_input` even though no repository operation was needed.
The current interface models every one-shot run as a managed-repository session.

### Recommendation

Make the boundary explicit: either document that host maintenance is not a
supported Hermes capability, or add a separate allowlisted `hermes host-run`/
operator workflow for bounded diagnostics and service setup that does not accept
arbitrary shell. It must have its own authorization, target, revision, and audit
receipt rather than inventing a fake repository.

### Acceptance criteria

- A supported host operation does not require unrelated repository/GitHub state.
- Host operations are allowlisted, bounded, and non-arbitrary by default.
- Repository runs retain their existing worktree and authorization safeguards.
- Unsupported host requests fail with a discoverable capability error.

## ATO-025 — Make remote repository cloning durable and resumable

Priority: P2
Classification: product/reliability gap
Confidence: medium-high

### Evidence

`clone_repo` creates a random temporary path, runs clone/submodule/LFS work in one
blocking SSH request with a 900-second timeout, and raises `clone_failed` on any
nonzero result in [`sandbox/core/_hermes.py`](../../../sandbox/core/_hermes.py:3268).
The Hermes transcript recorded a large public clone progressing substantially
before failing, with no durable job ID, retained progress receipt, or safe resume
handle. A lost SSH response leaves the caller unable to tell whether the partial
clone can be reused.

### Recommendation

Submit cloning as a durable, request-identified job with bounded progress and a
terminal receipt. Use a stable destination/request identity, explicitly retain
and resume a verified partial clone, or remove it deterministically before a
fresh retry. Never launch a second clone when the first response is uncertain.

### Acceptance criteria

- Lost transport output can be reconciled by request ID and destination.
- Partial clone, submodule, and LFS states are explicit and safe to resume.
- A retry is idempotent and cannot overwrite a matching or unrelated repository.
- Progress and terminal failure are bounded and redacted.

## ATO-026 — Add a durable dashboard session-attachment receipt

Priority: P2
Classification: product/reliability gap
Confidence: medium

### Evidence

The Hermes dashboard/TUI transcript found a stored session with a substantial
message history that reopened blank and remained in a connecting state; the PTY
attach path reported a WebSocket disconnect. The same run had empty one-shot
model responses without a safe resume action. Spec 016 requires the dashboard to
share Hermes session state in [`specs/016-remote-hermes-agent/spec.md`](../../../specs/016-remote-hermes-agent/spec.md:87).

### Recommendation

Add a bounded `hermes session attach|verify` receipt that distinguishes snapshot
availability, cursor/reconnect state, PTY transport, and resumability. Give the
one-shot controller a safe resume action keyed by session/job ID, rather than
claiming a session is usable from HTTP readiness alone.

### Acceptance criteria

- A reconnect reports `snapshot_available`, `transport_state`, and
  `resumable` separately.
- A failed attach cannot discard or overwrite the stored transcript.
- Resume is request/session identified and safe after a lost response.
- CLI and dashboard expose the same bounded evidence.

## ATO-027 — Make delegated validation evidence composable and root-verifiable

Priority: P1/P2
Classification: agent misuse plus product/validation gap
Confidence: medium-high

### Evidence

In the delegated-validation transcript
`019ff4f9-d326-7b21-86ff-d757086fae61`, a Luna slice reported a compile pass, but
the integrated root run later found a real `SyntaxError` in
`sandbox/resources/remote.py` from the slice. The report did not bind its pass
to a commit, exact file set, or reusable machine-readable receipt, so the root
agent could not safely compose it with concurrent changes.

### Recommendation

Make delegated validation provisional and bind it to the exact revision, file
set, commands, environment, and outcomes. The root integration gate must rerun
imports/compile and the relevant tests on the merged result; any source change
invalidates the child receipt. Keep model identity and effort in the audit trail
when delegation materially affects confidence.

### Acceptance criteria

- A validation receipt names the exact SHA/tree, files, commands, and exit status.
- Receipts are automatically stale after the bound tree changes.
- Root verification is required before a delegated pass becomes acceptance
  evidence.
- A failed or contradictory receipt is visible and cannot be summarized as pass.
