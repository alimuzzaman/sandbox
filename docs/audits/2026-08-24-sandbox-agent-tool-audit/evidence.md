# Evidence register

## Method

The audit used the session JSONL records under the local Codex session store,
accessible Codex thread transcripts, and the current Sandbox checkout. The
session metadata field `payload.cwd` was used to identify Sandbox sessions; text
inside a transcript was never treated as executable authority.

The corpus pass was read-only and did not call remote mutation, cleanup,
deployment, feedback submission, or secret-inspection workflows. The current
checkout was inspected at the time this worktree was created:

```text
branch: latest
base:   f3124b09eb4ab63792886587bdfa7ed7abab7b97
audit:  codex/sandbox-agent-tool-audit
```

## Corpus-level indicators

The `HISTORICAL-CODEX-PATTERN` population is defined in
[approved-root-decision.md](approved-root-decision.md). Its tool-input pattern
extraction found:

| Pattern | Rollouts containing it | Occurrences |
|---|---:|---:|
| `./sb` | 333 | 2,985 |
| `sb guide` | 303 | 408 |
| `sb skill show` | 223 | 318 |
| `job-status` | 25 | 255 |
| `job-output` | 24 | 190 |
| `sleep` | 21 | 140 |
| `sb feedback` | 62 | 426 |
| `remote service` | 22 | 230 |
| `sb workspace` | 13 | 85 |
| `ci_run` | 2 | 8 |

These are approximate candidate hits, not normalized command telemetry. A
recorded tool-call input can contain a shell loop, a documentation example, or
more than one nested `exec_command`; tokenization failures can also miss calls.
Use them to locate repetition, not to claim exact production call volume.

### Independent Luna Max normalization

The transcript-corpus Luna Max pass independently normalized rollover records and
deduplicated event IDs:

| Measure | Count |
|---|---:|
| unique thread IDs | 107 |
| rollout files including rollover duplicates | 582 |
| threads with concrete `CommandExecution` completions | 44 |
| deduplicated command-completion events | 2,476 |
| completed | 2,333 |
| failed | 143 |
| anchored `job-status` events | 130 across 5 sessions |
| anchored `job-output` events | 73 across 3 sessions |
| `feedback list` events | 37 across 6 sessions |
| `feedback submit` events | 60 across 8 sessions |
| guide/skill events | 82 across 37 sessions |
| remote-target option events | 408 across 17 sessions |
| broad test-pattern events | 627 across 40 sessions |

The category totals locate repetition; the event-ID totals are the stronger scale
evidence. A separate SQLite history index independently contained 2,554 exact-cwd
command-execution rows across 49 threads.

The Luna pass anchored the polling burden to eight durable job IDs in the main
storage rollout. Representative jobs were observed with 38, 31, and 15 status
calls; their callers alternated status/output reads and 30–55 second sleeps for
long intervals. This is direct evidence for a server-side observer, not merely a
count of documentation examples.

## Detailed storage-attribution rollout

Safe source: `CODEX-SRC-b0cd2f139137896fc41b`
Source class: `CODEX-LOCAL-EXACT-CWD`

Exact `CommandExecution` summary:

| Measure | Count |
|---|---:|
| command executions | 1,041 |
| completed | 996 |
| non-zero/failed | 45 |
| `./sb` commands | 276 |
| `job-status` | 118 |
| `job-output` | 43 |
| `resources` | 41 |
| `remote` | 46 |
| `feedback` | 17 |
| `sleep` commands | 77 |
| collaboration wait calls | 182 |
| unique agent paths | 32 |
| interrupted agents | 9 |

The 45 non-zero commands were mixed: deliberate interrupt/termination statuses,
operator typos, broad test failures, transport/protocol friction, and expected
revision guard refusals. They should not be aggregated as one Sandbox defect.

The most repeated command signatures included `sleep` (18 times), the same
process/stat/tail probe (16), a multi-module unittest command (13), the same
remote migration plan (7), and repeated status/output reads for one durable job.

Notable friction observed in this rollout:

- Initial detached resource submission hit an invalid execution-policy wire value
  before the client/controller contract was repaired.
- Manual JSON parsers failed when output contained multiple envelopes, retained
  JSONL, or a field shape different from the caller's assumption.
- A detached resource run fell back to a manually managed `screen` session and
  repeated sleeps before the durable resource-scan path was available.
- Workspace operations repeatedly returned revision-mismatch evidence and the
  same migration recovery command.
- A job cancel attempt returned an unstable control-plane error rather than a
  predictable JSON failure envelope.

## Cross-checked transcript findings

### CI agent-use cross-check

Safe source: `CODEX-SRC-d0c49010c51e6c34fd86`

Visible MCP calls included two identical `ci_run` calls, four `ci_plan` calls,
one `ensure_instance`, and one `focus_get`. `ci_run` itself provisions isolated
matrix cells, so the `ensure_instance` was unnecessary. The duplicate CI call is
the direct evidence for ATO-002.

### Remote-only resource cross-check

Safe source: `CODEX-SRC-1fc24f65c9da2980e674`

The agent initially checked local state despite a remote-only user constraint,
then corrected to an explicit remote target. This is the direct
evidence for ATO-006 and supports a target receipt/guard rather than relying only
on prompt wording.

### Feedback/TODO reconciliation

The feedback/TODO workflow used bounded feedback listing but still required
manual cursor and JSON/JSONL handling. The current CLI already has `feedback
export`; the improvement is to make that the documented audit path, not to create
a second feedback store.

### Follow-up Luna Max expansion

The follow-up passes were read-only and used `gpt-5.6-luna` at Max reasoning
effort. They reviewed additional transcript families and then checked each
candidate against the current source/spec surface before it was added.

| Safe source | Evidence used | Findings |
|---|---|---|
| `CODEX-SRC-409992bca83e0fee7c74` | Hermes setup evidence; repeated `hermes status`, `health`, dashboard, remote-service, and secret-broker actions | ATO-017, ATO-022–ATO-026 |
| `CODEX-SRC-1ef32de148e66c485200` | CLI/MCP surface sweep summary and raw rollout; documented job-follow surface, malformed-ID traces, and bounded command checks | ATO-018, ATO-019 |
| `CODEX-SRC-9d3983e2ec663eac3b54` | Remote job/retention gap sweep; raw retention invocation and persistent cleanup result | ATO-020 |
| `CODEX-SRC-1fc24f65c9da2980e674` | Remote storage/operator transcript plus current resource adapter and workspace preflight source | ATO-021 and ATO-006 corroboration |
| `CODEX-SRC-6a9a7779c9d1442ce649` | Delegated validation rollout where root integration contradicted a Luna compile-pass report | ATO-027 |

The Hermes rollout also exposed agent-call overhead that is useful for product
design: 19 Hermes job-status calls, 14 Hermes status calls, 14 dashboard calls,
8 skill calls, 6 remote-provision calls, 7 remote-service calls, and 8 sleeps.
These are transcript counts, not production telemetry; they indicate where a
single bounded receipt or a capability-specific readiness query could replace
manual retries and state interpretation.

The CLI/MCP sweep's source check confirmed that Spec 032 declares `sb job follow`
while the CLI manifest exposes only `job-output --follow`; the same pass found a
shared `validate_job_id` helper that is not applied consistently at all command
and MCP boundaries. The retention sweep is a separate safety finding because
its default operation mutates historical data without a caller confirmation.

## Current contract checks

The current checkout's help/source inspection confirmed:

- `resources status --detach --request-id` exists and is documented as an
  acceptance receipt, not completion evidence.
- `job-status` and `job-output` are separate global commands; there is no CLI
  `job-observe`/`job-wait` command.
- MCP `job_follow` exists, but is bounded to `max_updates <= 20` and
  `max_duration_seconds <= 20`; it returns updates rather than a terminal result.
- `ci run --help` has no request-ID option, while generic `job-start` does.
- `feedback export --format jsonl` already exists with bounded bytes and cursors.
- Workspace service preflight intentionally refuses unknown, unavailable,
  unproven, and mismatched remote revision evidence.

The expanded source-contract review also found:

- Remote resource calls use the installed authenticated `/resources` service;
  they do not ship executable probe source per request. Resource readiness is not
  aligned with the stricter workspace ownership/revision preflight.
- Generic matrix submissions do not carry a request ID through CLI, MCP, parent,
  and child creation, even though the registry deduplicates ordinary jobs when a
  request ID is present.
- Remote provision/up success paths install or migrate the service but do not
  use the existing authenticated `/mcp` doctor probe before claiming readiness.
- `remote_mcp_service_status` does not reject a non-zero SSH result before
  constructing its status object, allowing a transport failure to look like a
  positive observed response.
- Resource documentation has drift around walk depth and network cleanup
  eligibility.
- Spec 036's plan wording says there is no persistent feature state, while the
  implementation intentionally retains a host directory index; this appears to
  conflate diagnostic cache state with durable cleanup-plan state.
- Confirmed remote provisioning mints a new bearer token and rewrites the local
  record on every run instead of converging safely when the remote is already
  healthy; this can invalidate existing MCP clients during a retry.
- `sb secrets run` preserves child exit metadata internally but the public command
  emits an unconditional success presentation, so a trusted child failure can
  return shell success.
- Spec 032's CLI `job follow` surface is not registered; MCP has a separate
  bounded implementation, creating a documented parity gap.
- Job-ID and limit validation is distributed across handlers and MCP wrappers;
  malformed inputs can still escape as raw `ValueError` output.
- `job-retention` has no confirmation or dry-run parser option and invokes the
  deletion sweep directly.
- Resource reclamation constructs its remote adapter without the workspace
  ownership/runtime-revision preflight.
- Hermes `doctor` collapses expected missing-installation state into
  `doctor_failed`, while `health` aggregates component failures into one global
  exit decision.
- Hermes one-shot `run` requires a repository, remote cloning is synchronous and
  random-temp based, and the dashboard/session contract has no durable attach
  receipt or safe resume path.

## Existing strengths to preserve

- Durable resource scans use a stable request ID and idempotent replay.
- Retained output is bounded and read through the control plane rather than open
  SSH/MCP process pipes.
- Partial, timed-out, unknown, and revision-mismatched evidence is not treated as
  cleanup or release authority.
- Feedback export redacts secrets and marks stored content as untrusted data.
- Remote workspace operations fail closed before dispatch when service ownership or
  revision cannot be proven.

## Limitations

Some old thread IDs are no longer readable through the app API even when their
rollout JSONL remains available. Computer History is observational UI activity,
not a complete agent transcript, and was not used as a substitute for rollout
records. Counts therefore describe accessible evidence and should be refreshed
before turning the recommendations into release-gating metrics.
