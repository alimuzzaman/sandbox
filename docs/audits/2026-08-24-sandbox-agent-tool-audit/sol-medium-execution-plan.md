# Sol Medium execution plan

Date: 2026-08-24

Branch: `codex/sandbox-agent-tool-audit`

## Status and authority

This document plans the remaining audit work. It does not authorize a Sandbox
product-code change, a remote mutation, a transcript access expansion, a privacy
decision, or a benchmark-validity decision.

The next executable work is read-only audit tooling and report generation. Any
change under `sandbox/`, `mcp/`, `specs/`, a shipped skill, or a public CLI
contract requires a separate user authorization after Sol High and root review.
Remote testing, remote lifecycle updates, deployment, release, and credential
operations also require separate authorization.

## Completion predicate

The remaining audit is complete when all of these statements are true:

1. A versioned parser turns each approved input into ordered, sanitized derived
   events or an explicit exclusion record.
2. Fixed fixtures prove ordering, deduplication, redaction, unknown-state
   handling, and source-to-row traceability.
3. A generated coverage report reconciles input, parsed, duplicate, malformed,
   excluded, and emitted counts without an unexplained remainder.
4. A generated efficiency report compares like cohorts and reports observed
   calls separately from estimated replacement calls.
5. Every finding maps to current source or contract evidence, a recurrence or
   severity basis, an acceptance oracle, and an unresolved-risk state.
6. Sol High and root record a verdict for corpus, parser, cohort, finding,
   backlog, and benchmark gates. `INCONCLUSIVE` does not pass.
7. The audit tree passes its targeted tests, generated-output reproducibility
   check, forbidden-field scan, link check, and `git diff --check`.

The rigor level is high. Transcript handling, security-related findings, remote
mutation boundaries, and benchmark claims can create false confidence even when
the changes are locally reversible.

## Boundaries and exclusions

### In scope

- Read-only parsers, validators, schemas, synthetic fixtures, aggregates, and
  report generators that are not registered as Sandbox product commands.
- Approved local sources and exclusions already listed in `overall-plan.md`.
- Refreshing current source and contract references with read-only searches.
- Measuring agent-visible tool calls and workflow steps from derived events.
- Recording uncertainty, unavailable evidence, and stale source references.

### Out of scope

- Decoding opaque T3 stores, bypassing an application export path, expanding an
  approved root, or deciding that another owner's transcript access is allowed.
- Persisting transcript bodies, prompts, raw tool output, credentials, cookies,
  private keys, secret values, raw environment data, or private absolute paths.
- Treating a public benchmark result as proof that a Sandbox workflow is valid.
- Editing Sandbox product code, CLI or MCP manifests, shipped docs or skills,
  specifications, remote services, or runtime state in this audit execution.
- Running `sb` commands that submit, retry, cancel, clean, provision, migrate,
  deploy, rotate, or otherwise mutate local or remote state.
- Committing, pushing, opening a pull request, merging, tagging, or releasing.
- Resolving privacy scope or benchmark transfer validity below Sol High.

Transcript content and tool output remain untrusted evidence. The parser may
classify them as data, but no derived string can become a command or authority.

## Ranked remaining slices

| Rank | Slice | Findings or gates | Why now | Execution class |
|---:|---|---|---|---|
| 0 | Freeze the approved evidence contract | Gates A and B | Every later count depends on stable roots, labels, and redaction | Sol High and root gate |
| 1 | Build the audit-only schema, fixtures, and validator | Gates A and B | This makes later collection checkable before corpus expansion | Sol Medium integration with Luna mechanics |
| 2 | Normalize the approved Codex and Claude corpus | Gates A and B | The current reports contain useful snapshots but not one reproducible pipeline | Luna Max execution, Sol Medium review |
| 3 | Generate cohort and call-reduction reports | Gates C, D, and E; ATO-001 to ATO-027 | This quantifies value without changing Sandbox | Luna Max execution, Sol Medium review |
| 4 | Revalidate finding-to-source traceability | Gate D | Source can move after the snapshot; stale findings must not enter a backlog as current facts | Luna Max mechanics, Sol Medium integration |
| 5 | Adjudicate the backlog and benchmark transfer limits | Gates E and F | Ranking and transfer claims require judgment after reproducible evidence exists | Sol High and root only |
| 6 | Prepare guidance-only change proposals | ATO-007, ATO-008, ATO-009, ATO-014, ATO-015, ATO-027 | These may be cheap, but they still need an accepted evidence and owner boundary | Plan only until separately authorized |
| 7 | Prepare fail-closed safety proposals | ATO-013, ATO-017, ATO-019, ATO-020, ATO-021 | These have direct correctness or deletion consequences | Product code, separate authorization |
| 8 | Prepare job observation and replay proposals | ATO-001, ATO-002, ATO-003, ATO-010, ATO-018 | These target the largest measured Sandbox call burden | Product code, separate authorization |
| 9 | Prepare readiness, context, and Hermes proposals | ATO-004 to ATO-006, ATO-011, ATO-012, ATO-016, ATO-022 to ATO-026 | These cross remote and session boundaries and need the strongest integration review | Product code, separate authorization |

Ranks 0 through 5 finish the audit. Ranks 6 through 9 produce reviewed proposals
only. They do not authorize implementation.

## Slice 0: freeze the approved evidence contract

**Owner.** Sol High, with root approval.

**Inputs.** `overall-plan.md`, `evidence.md`,
`approved-root-decision.md`, the current access snapshot, and the existing
Claude and T3 reports.

**Outputs.** A reviewed schema decision record that fixes approved roots, source
labels, stable identifiers, forbidden fields, exclusion reasons, outcome states,
and the distinction between observed and estimated metrics. Record the verdict
in `execution-decisions.tsv` using the `show-me-your-work` format.

**Checks.** Confirm that each approved root has an authority basis and that each
excluded source has a reason. Confirm that `unknown`, `partial`, `blocked`,
`unavailable`, and `ambiguous` remain distinct.

**Acceptance oracle.** Gate A and the design portion of Gate B have explicit
`PASS`, `FAIL`, or `INCONCLUSIVE` verdicts. Only `PASS` permits Slice 1. This
plan does not decide the verdict.

## Slice 1: build the audit-only schema, fixtures, and validator

Keep the tooling outside the public Sandbox command registry. The proposed
locations are `tools/audit_agent_usage/` for code,
`tests/test_agent_tool_audit.py` for tests, and this audit directory for schemas,
manifests, and generated reports. Do not import the tooling from `sb`, command
manifests, MCP registration, or runtime services.

### Luna task L1.1: create synthetic schema fixtures

**Inputs.** The normalized evidence fields in `overall-plan.md`, known event
shapes from the approved pilot, and the frozen contract from Slice 0.

**Output.** Minimal synthetic fixtures for Codex rollover, Claude content blocks,
duplicate events, malformed records, nested sensitive-looking values, missing
timestamps, partial results, and parent-child relationships. Fixtures contain no
copied transcript prose or credentials.

**Checks.** Parse every fixture as JSONL. Scan fixture keys and values against the
forbidden-field policy. Review each fixture's expected rows by hand.

**Acceptance oracle.** Every contract branch has at least one fixture and an
expected normalized or excluded result. Sol Medium confirms that the fixture
does not encode a privacy or outcome judgment that Gate A left unresolved.

### Luna task L1.2: implement the schema and validator

**Inputs.** The accepted fixtures and schema decision record.

**Output.** A deterministic schema, parser boundary, redactor, exclusion model,
and validator. The code accepts explicit input files and an explicit output
directory. It has no network, remote, database, browser-store, or subprocess
execution path.

**Checks.** Run targeted unit tests for ordering, rollover deduplication, stable
hashing, malformed input, unknown fields, redaction, bounded strings, and formula
injection in tabular outputs.

**Acceptance oracle.** The same fixtures produce byte-identical derived data on
two clean runs. A single source record maps to its derived row or exclusion row
through a non-sensitive source key and event index. No forbidden value appears
in the output.

### Luna task L1.3: add a coverage reconciler

**Inputs.** Parser counters and exclusion rows.

**Output.** A machine-readable manifest with `input`, `parsed`, `duplicate`,
`malformed`, `excluded`, and `emitted` counts per source and schema version.

**Checks.** Assert an arithmetic identity for every input category. Assert that
an exclusion reason is present when a record is not emitted.

**Acceptance oracle.** Every input record has exactly one terminal accounting
state. An unexplained remainder or double count fails Gate B.

## Slice 2: normalize the approved corpus

### Luna task L2.1: inventory approved files

**Inputs.** Only the exact roots and source filters approved at Gate A.

**Output.** A sanitized source manifest with source label, safe file identifier,
schema family, byte count, readable state, and exclusion reason. Do not persist
raw absolute paths in generated reports.

**Checks.** Compare manifest totals with a fresh read-only file inventory. Keep
the current 549-file snapshot and the separately defined historical pattern
snapshot as distinct cohorts.

**Acceptance oracle.** Counts reconcile to the current inventory, and the report
does not merge rollover files into unique-session counts without showing both.

### Luna task L2.2: run the Codex and Claude lanes

**Inputs.** The manifest and Slice 1 parser. T3 remains metadata-only unless Gate
A approves a supported export path.

**Output.** Versioned normalized events, exclusions, and source summaries. Use a
temporary output directory until validation passes. Promote only derived files.

**Checks.** Run the validator, the forbidden-field scanner, count reconciliation,
and a bounded random spot check. The spot-check seed and sampled safe identifiers
must be recorded so root can repeat it.

**Acceptance oracle.** Gate B passes. Any leakage, unstable ordering, unexplained
count, or collapsed unknown state stops expansion and deletes the temporary
derived output. The raw inputs remain untouched.

### Luna task L2.3: produce the uncertainty ledger

**Inputs.** All exclusion and parser-warning rows.

**Output.** A bounded report grouped by reason, source, and schema family. It may
link safe identifiers, but it must not copy the rejected content.

**Checks.** Assert that each warning maps back to one manifest entry. Compare the
warning total with the coverage reconciler.

**Acceptance oracle.** Root can explain every missing record through the ledger.
`Unreadable` and `authorization_uncertain` remain evidence gaps, not zero counts.

## Slice 3: generate cohort and call-reduction reports

### Counting unit

Report two count layers and never combine them:

- `agent_tool_call` counts one tool invocation recorded by the agent runtime.
- `sandbox_command` counts one parsed `sb` invocation inside an approved command
  execution event. A shell line with two `sb` invocations counts as two Sandbox
  commands and one agent tool call.

Also report `manual_wait`, `manual_parse`, `retry`, `duplicate_submission`,
`context_discovery`, and `productive_action_index`. The productive action is the
first call that attempts the task outcome rather than discovering context.

### Episode and cohort rules

An episode groups events by safe thread identifier, target, capability, and a
hashed request or job identifier when one exists. Do not infer that two missing
identifiers are the same job. Compare only episodes with the same task class,
local or remote target class, outcome class, and evidence completeness class.

### Luna task L3.1: generate the observed baseline

**Inputs.** Validated normalized events and the frozen cohort rules.

**Output.** Per-episode and cohort summaries for total calls, Sandbox commands,
status reads, output reads, sleeps or waits, manual parsing, retries, failures,
turns, elapsed time, and tokens when recorded.

**Checks.** Reproduce the anchored durable-job counts from `evidence.md`, including
the representative 38, 31, and 15 status-call episodes, within the exact source
scope. Differences require a traceable explanation, not a tolerance band.

**Acceptance oracle.** The generated report links each aggregate to its episode
rows and reports missing duration, token, or outcome fields as unavailable.

### Luna task L3.2: calculate replacement-call estimates

**Inputs.** The observed episodes and each finding's proposed caller recipe.

**Output.** A table with `observed_calls`, `estimated_replacement_calls`,
`absolute_reduction`, `reduction_percent`, assumptions, and confidence. Use:

```text
absolute_reduction = observed_calls - estimated_replacement_calls
reduction_percent = absolute_reduction / observed_calls * 100
```

For ATO-001, the estimate replaces status, output, sleep, and manual parse calls
only when one bounded observer request would cover the same observation window.
For ATO-005, count guide and skill reads only inside a cold-start discovery
window. For replay findings, report avoided duplicate submissions separately
from ordinary call reduction.

**Checks.** Reject a negative or greater-than-observed reduction. Keep partial
windows and timeout-bound observer continuations visible. Label all unimplemented
replacement counts `ESTIMATED`.

**Acceptance oracle.** Each estimate has a source episode, an explicit proposed
recipe, and a preserved outcome and safety state. The audit makes no claim of an
observed reduction before an authorized implementation and controlled replay.

### Luna task L3.3: define the post-implementation measurement

**Inputs.** Accepted proposal acceptance criteria. This task writes a test plan
only and does not run product changes.

**Output.** A paired replay specification that executes the legacy and candidate
recipes against the same fixture or fake adapter, records actual agent-visible
calls, and compares terminal outcome, target, timeout, completeness, and mutation
count.

**Checks.** The two recipes use the same initial state and bounded deadline. A
candidate cannot pass by hiding `unknown`, broadening a mutation, or omitting an
evidence read.

**Acceptance oracle.** An authorized product change earns a call-reduction claim
only when the candidate uses strictly fewer agent-visible calls, reports the
absolute and percentage reduction, preserves the outcome, and does not increase
implicit mutations, retries, permission prompts, or unresolved states.

## Slice 4: revalidate finding-to-source traceability

### Luna task L4.1: build the contract matrix

**Inputs.** `findings.md`, `work-plan.md`, current source, current contracts, and
targeted tests.

**Output.** One row per ATO finding with current symbol or contract, test seam,
evidence episode, confidence, drift state, proposed owner, and authorization
class.

**Checks.** Use targeted `rg` and bounded file reads. Do not run product tests or
commands in this mechanical pass. Mark a moved or fixed contract as `STALE` or
`RESOLVED_IN_SOURCE`; do not rewrite history.

**Acceptance oracle.** Every finding has a current source basis or an explicit
stale/unverified state. A historical transcript alone cannot prove current
behavior.

### Source seams for the matrix

- Job observation and input contracts use
  `sandbox/commands/jobs_runtime.py`, `sandbox/application/job_service.py`,
  `sandbox/jobs/output.py`, `mcp/wp-server/tools/jobs.py`, and Spec 032.
- CI and matrix identity use `sandbox/commands/ci.py`,
  `sandbox/application/job_service.py`, `mcp/wp-server/tools/ci.py`, and
  `mcp/wp-server/tools/jobs.py`.
- Remote and resource readiness use `sandbox/core/_remote.py`,
  `sandbox/commands/remote.py`, `sandbox/resources/context.py`,
  `sandbox/resources/remote.py`, Specs 035 and 036, and their current tests.
- Secret exit behavior uses `sandbox/commands/secrets.py`,
  `sandbox/secrets/runner.py`, and `tests/test_secret_commands.py`.
- Hermes findings use `sandbox/core/_hermes.py`,
  `sandbox/commands/hermes.py`, `mcp/wp-server/tools/hermes.py`,
  `docs/hermes-agent.md`, and the current Hermes test modules.

### Luna task L4.2: regenerate the ranked evidence backlog

**Inputs.** The contract matrix and generated efficiency report.

**Output.** A candidate backlog ordered by safety consequence, repeated observed
cost, expected reduction, dependency, and implementation size. Luna copies the
approved scoring rules and does not choose weights or final priority.

**Checks.** Each row contains repeated traces or a documented severe event,
current source evidence, an oracle, expected call savings, compatibility limits,
and an owner boundary.

**Acceptance oracle.** Sol Medium can regenerate the ordering from the recorded
scores. Sol High and root, not Luna, decide whether the scoring model and final
order are accepted.

## Slice 5: Sol High and root adjudication

Review the six gates from `overall-plan.md` in order:

1. Gate A reviews corpus roots, authority, privacy, and exclusions.
2. Gate B reviews parser accuracy, ordering, deduplication, redaction, and count
   reconciliation.
3. Gate C reviews task taxonomy, cohort comparability, and outcome semantics.
4. Gate D reviews evidence traceability, current-source drift, confidence, and
   uncertainty bounds.
5. Gate E reviews recurrence, expected savings, safety, compatibility, effort,
   and owner boundaries.
6. Gate F reviews benchmark provenance, licensing, receipt design, and transfer
   limits.

Sol High records `PASS`, `FAIL`, or `INCONCLUSIVE` for each gate. Root checks the
artifacts and either accepts the verdict or returns a bounded correction. Gate F
may remain inconclusive without blocking the local efficiency report, but no
benchmark-derived product or marketing claim may proceed.

## Slices 6 through 9: proposals that need separate authorization

These slices are planning outputs only. Each authorized product slice must start
from a fresh current-source review and its own acceptance test. Do not combine
all findings into one product change.

### Slice 6: guidance-only proposals

Prepare small, independent proposals for feedback export guidance, test selection,
agent wait batching, resource documentation drift, Spec 036 cache wording, and
tree-bound delegated receipts. Keep ATO-009 outside Sandbox product ownership
unless root identifies an enforceable Sandbox seam.

**Tests and oracle.** Run docs-link and docs-contract checks. Run command examples
only when they are read-only. A docs change passes only when it matches current
behavior and does not broaden cleanup, remote, credential, or transcript authority.

### Slice 7: fail-closed safety proposals

Sequence the work as separate changes: remote transport status, secret-child
exit propagation, malformed job-input errors, retention preview and confirmation,
then resource preflight. Put failing contract tests before each fix.

**Tests and oracle.** Target `tests/test_remote.py`,
`tests/test_secret_commands.py`, job CLI and MCP contract tests, retention tests,
and resource remote or reclaim tests. The oracle is that failure, timeout,
unknown ownership, revision mismatch, malformed input, and unconfirmed deletion
cannot produce a success receipt or dispatch a mutation.

### Slice 8: job observation and replay proposals

Define one observation envelope before adding a command. Then address CLI and MCP
follow parity, CI request identity, and generic matrix request identity through
the existing job service and registry. Preserve lower-level status and output
commands.

**Tests and oracle.** Target `tests/test_job_observation_contracts.py`,
`tests/test_job_output.py`, `tests/test_job_output_cursor.py`,
`tests/test_job_cli.py`, `tests/test_job_mcp.py`, `tests/test_job_matrix.py`,
`tests/test_ci.py`, and `tests/test_remote_ci_jobs.py`. An identical replay must
return the accepted parent and children. A conflicting replay must fail before
child dispatch. Observation timeout must remain incomplete evidence, not failure.

### Slice 9: readiness, context, and Hermes proposals

Start with a shared read-only readiness receipt. Keep target selection explicit.
Treat context bootstrap, convergent provisioning, Hermes absence and component
health, host operations, durable clone, and session attachment as separate
changes. A host-operation proposal cannot add arbitrary shell execution.

**Tests and oracle.** Target current remote, resource, Hermes, and remote-first
guidance tests. Every receipt must state target, ownership, local and installed
revision, capability, completeness, and recovery state without credentials or
unsafe paths. No readiness observation may migrate, provision, repair, rotate,
clean, or deploy implicitly.

## Integration and rollback

Integrate audit work in this order:

1. Schema and synthetic fixtures.
2. Parser and validator.
3. Coverage reconciler and uncertainty ledger.
4. Corpus-derived reports.
5. Contract matrix and backlog generator.
6. Sol High and root verdicts.

Each step must pass before the next starts. Keep generated data separate from
the parser and fixtures so a report can be deleted and regenerated without
changing the method. If a privacy or traceability check fails, discard only the
temporary derived output, fix the schema or parser in its own change, and rerun
from the approved inputs. Never edit raw transcript sources.

If an accepted audit-tool change must be undone, revert its isolated commit or
remove its generated output. Do not use a destructive reset. Product changes,
if later authorized, use separate branches and commits. Preserve existing
lower-level commands as rollback controls until parity and compatibility pass.
A remote lifecycle update or deployment is not part of rollback for this audit.

## Model routing

- **Sol High.** Owns Gates A through F, privacy and authorization boundaries,
  schema semantics, cohort validity, security decisions, benchmark transfer,
  final priority, and any product architecture decision.
- **Sol Medium.** Owns this execution plan, integrates accepted audit tooling,
  reviews every Luna diff and derived artifact, runs targeted local checks, and
  prepares root-ready gate packets. Sol Medium does not overrule Sol High on a
  reserved decision.
- **Luna Max.** Executes only the bounded L1 through L4 tasks with exact inputs,
  exclusions, output paths, and deterministic checks. Luna does not decide
  privacy, access, security, architecture, benchmark validity, or acceptance.
- **Root.** Verifies the integrated tree and source evidence, resolves overlap
  with concurrent work, approves or rejects each Sol High gate, and requests any
  separate authorization from the user.

Luna work must run in isolated worktrees or non-overlapping output directories.
Each Luna handoff names the model and effort, input manifest digest, output paths,
commands run, exit statuses, bound tree or SHA, and unresolved states. A Luna
pass is provisional until Sol Medium inspects the artifacts and root reruns the
integration checks.

## Required checks for the audit implementation

Run the narrowest checks that match the implemented audit files. The expected
set is:

```text
python3 -m unittest discover -s tests/audit_agent_usage -p 'test_*.py' -v
python3 -m tools.audit_agent_usage --input docs/audits/2026-08-24-sandbox-agent-tool-audit/fixtures/synthetic-events.jsonl --output-dir <temporary-parser-output>
python3 -m tools.audit_agent_usage --input docs/audits/2026-08-24-sandbox-agent-tool-audit/fixtures/synthetic-events.jsonl --output-dir <second-temporary-parser-output>
diff -ru <temporary-parser-output> <second-temporary-parser-output>
python3 -m tools.audit_agent_usage.coverage --input docs/audits/2026-08-24-sandbox-agent-tool-audit/fixtures/synthetic-events.jsonl --output-dir <temporary-coverage-output>
python3 -m tools.audit_agent_usage.coverage --input docs/audits/2026-08-24-sandbox-agent-tool-audit/fixtures/synthetic-events.jsonl --output-dir <second-temporary-coverage-output>
diff -ru <temporary-coverage-output> <second-temporary-coverage-output>
bash docs/audits/2026-08-24-sandbox-agent-tool-audit/check-durable-artifacts.sh
git diff --check
```

The exact commands may change during Slice 1 if repository conventions require a
different module layout. Sol High must approve any change that alters the schema
or privacy gate. Do not run the corpus generator until fixture validation passes.

## Root review packet

Root receives these artifacts for each gate:

- the current diff and worktree status;
- the input manifest digest and generated coverage summary;
- the test commands and exact exit statuses;
- the reproducibility diff result;
- the forbidden-field scan result;
- the uncertainty ledger and stale-source rows;
- the call-reduction report with observed and estimated values separated;
- the Luna model, effort, task boundary, and provisional receipt;
- the Sol High gate verdict and reasons.

Root acceptance requires direct inspection of the generated artifacts and a
rerun of the integration checks. A delegate summary, historical green test, or
matching filename is not acceptance evidence.
