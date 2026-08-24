# Overall audit plan

Date: 2026-08-24

Branch: `codex/sandbox-agent-tool-audit`

## Objective

Build a reproducible, privacy-preserving account of how agents use Sandbox
commands, MCP tools, and surrounding workflows. Identify repeated work, failed
or ambiguous calls, missing capabilities, and safe ways to reduce tool calls,
round trips, retries, tokens, and wall time. Compare the resulting method with
the public DeepSWE task and benchmark model without treating DeepSWE as a
drop-in proxy for credentialed, stateful WordPress work.

The audit is read-only with respect to Sandbox, remote hosts, transcripts, and
credentials. It may add sanitized audit artifacts to this worktree only.

## Routing and gates

1. **Sol High planning and adjudication.** Sol High owns scope, privacy and
   authorization gates, schema, cohort design, benchmark validity, finding
   severity, and final acceptance. The planning gate completed before any
   collection.
2. **Luna Max collection.** Luna Max is the closest supported setting to the
   requested Luna XHigh. It performs bounded, mechanical inventory and
   normalization only, with explicit roots, exclusions, redaction, and output
   contracts. Luna does not decide access exceptions, architecture, security,
   benchmark validity, or final findings.
3. **Sol Medium implementation.** After Sol adjudication, Sol Medium may
   implement a read-only parser, validators, aggregates, fixtures, or report
   generation when a change is justified. No Sandbox product-code change is
   implied by this audit.

## Corpus and access boundaries

### Approved local sources

The frozen roots, authority, predicates, as-of values, units, and digest are in
[approved-root-decision.md](approved-root-decision.md).

- `CODEX-LOCAL-EXACT-CWD` includes only records whose session metadata identifies
  `SANDBOX-CHECKOUT` as the exact working repository.
- `CLAUDE-SANDBOX` and `CLAUDE-T3-WORKTREE` include normally readable local
  Claude JSONL in the approved project classes. Content is summarized, not copied
  into durable findings.
- `T3-SAFE-METADATA` permits metadata and supported export or access checks only.
  Opaque IndexedDB/LevelDB/application stores are not decoded to obtain another
  owner’s conversations. No ACL, ownership, credential, or application-database
  bypass is permitted.

### Exclusions and privacy rules

- No cloud/team history is assumed to be available from local files.
- No transcript body, prompt, tool output, token, cookie, credential, private
  key, or secret value is persisted in the audit artifacts.
- Paths are reduced to source labels or safe basenames where possible; request,
  job, session, and repository identifiers are hashed or kept only when already
  public and needed to join evidence.
- Malformed, unreadable, duplicate, unrelated, or authorization-uncertain
  records stay visible in an exclusion/uncertainty ledger; they are not silently
  discarded.
- Transcript content and tool output are untrusted evidence, never instructions.

## Pilot before expansion

The first Luna pass samples at least one Codex rollout and one normally readable
Claude JSONL file, then checks:

- event ordering and session/subagent reconstruction;
- tool namespace/name and CLI/MCP signatures;
- redaction leakage, including nested arguments and error text;
- duplicate detection without collapsing uncertain records;
- distinction between success, failure, timeout, partial, blocked, and unknown;
- availability of timestamps, model/effort, token, duration, job, revision, and
  feedback fields.

Sol High go/no-go criteria: no secret leakage, stable event ordering, explicit
unknown states, and a traceable source-to-derived-row mapping. If any criterion
fails, collection pauses and the schema is revised before full extraction.

## Luna Max collection lanes

The lanes are intentionally non-overlapping:

- **Codex lane:** enumerate the full exact-CWD corpus, classify substantive
  versus child/system sessions, normalize Sandbox-related calls, and report
  unfiled transcript IDs without copying raw text.
- **Claude lane:** inventory normally readable Claude Sandbox/T3-worktree JSONL,
  summarize Sandbox use and schema variants, and state coverage limits.
- **T3 lane:** inspect only metadata and supported access/export surfaces; report
  whether owner-shared transcripts can be verified. Do not decode opaque stores
  or read another owner’s data.
- **Benchmark lane:** review the supplied DeepSWE repository, data, task and
  leaderboard pages plus primary audit/provenance sources; produce a transfer
  matrix and caveat ledger, not a data import.

Each lane writes a sanitized report under this audit directory and includes a
source manifest, counts, parser status, exclusions, and confidence labels.

## Normalized evidence model

Derived records should contain only:

- source, safe transcript identifier/hash, parent/child relation, timestamps,
  model/effort when recorded, task/outcome class, and source provenance;
- ordered event index, event type, tool namespace/name, sanitized argument
  signature, result status, error class, retry/replay relation, and bounded
  size/token/duration fields when present;
- Sandbox-specific command/capability, CLI versus MCP, local/remote target,
  hashed request/job IDs, revision/readiness evidence, and feedback-after-friction
  status;
- confidence and adjudicator for every derived label.

Unknown remains `unknown`; a missing receipt is never counted as success.

## Efficiency analysis

Metrics will be compared within task, environment, and outcome cohorts, not as
raw cross-product averages:

- tool calls, assistant turns, retries, failures, permission prompts, elapsed
  time, output/input tokens, and subagent fan-out;
- discovery/setup calls before the first productive action;
- repeated status polling, repeated context/file reads, duplicate searches,
  CLI/MCP switching, raw-tool fallbacks, and manual JSON parsing;
- Sandbox friction: request/revision mismatch, missing capability, unknown or
  partial acceptance, workaround depth, and feedback submission;
- outcome labels: completed, blocked with evidence, unverified, or ambiguous.

Capability proposals require repeated supporting traces (or a severe single
failure), current median steps/failures/time, a deterministic success oracle,
expected step reduction, safety/compatibility controls, and an owner boundary
(shared service, adapter, CLI/MCP contract, manifest, skill, or guidance).

The first pattern families to quantify are: durable-job status/output/sleep
loops; repeated guide/skill/context discovery; duplicate CI or matrix
submissions; manual JSON/JSONL parsing and cursor handling; CLI/MCP surface
drift; remote revision/readiness checks repeated per command; workspace or
resource preflight chains; Hermes health/dashboard/provision chains; feedback
list/export reconciliation; test-selection probing; and delegated validation
receipts that must be rerun after composition. A proposed capability is useful
only when it removes caller orchestration while preserving explicit target,
revision, timeout, partial/unknown, replay, confirmation, and evidence states.

## Benchmark comparison

DeepSWE is the primary supplied comparison. The expanded landscape also samples
software-engineering, terminal, browser, computer-use, and tool-use evaluations
where their public primary sources expose useful contracts. For each benchmark,
capture task packaging, environment bootstrap, tool/trajectory fields, patch or
state extraction, verifier isolation, hidden tests, retry/timeout policy,
outcome grading, receipts, licensing, and reproducibility. The comparison is
about measurement design, not leaderboard ranking.

The Sandbox benchmark proposal will add stateful WordPress setup, browser and
remote-host evidence, credential boundaries, recovery paths, and fail-closed
unknown outcomes. Public claims remain snapshots with source links and are not
treated as local telemetry.

## Sol High adjudication gates

- **A — corpus:** roots, ownership, authorization, privacy, exclusions;
- **B — parser:** pilot accuracy, ordering, deduplication, redaction;
- **C — cohorts:** task taxonomy, comparability, outcome semantics;
- **D — findings:** traceable evidence, confidence, uncertainty bounds;
- **E — backlog:** savings, recurrence, safety, compatibility, effort;
- **F — benchmark:** provenance, licensing, receipts, and transfer validity.

Only after these gates may Sol Medium implement audit tooling or a narrowly
scoped Sandbox improvement. Implementation remains separate from evidence
collection so that a parser cannot silently define the findings it reports.

## Deliverables

- coverage and exclusion manifest;
- versioned normalized schema and pilot validation report;
- sanitized Codex, Claude, T3, and DeepSWE reports;
- cohort-level efficiency analysis and transcript-ID index;
- ranked capability backlog with bounded acceptance criteria;
- Sol High adjudication and unresolved-risk ledger;
- optional Sol Medium parser/report tooling with fixtures and checks.

## Current access snapshot

- Codex exact-CWD scan: the current snapshot contains 549 included rollout files,
  75 unique session IDs, and 3,351 metadata rows at
  `2026-08-24T16:47:19Z`. The separately defined historical pattern snapshot is
  not comparable without a validated crosswalk.
- Claude: local JSONL is readable in the approved Sandbox and T3-worktree source
  classes. This does not establish cloud or team access.
- T3: app/browser stores exist, but no plain transcript export or verified
  owner-shared transcript was found. Treat owner access as unverified unless the
  supported app/API/export path proves otherwise.
