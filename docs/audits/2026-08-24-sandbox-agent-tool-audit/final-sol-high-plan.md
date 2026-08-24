# Final Sol High plan

Date: 2026-08-25

Model/effort: `gpt-5.6-sol` High

Audit branch: `codex/sandbox-agent-tool-audit`

Product-source basis: `f3124b09eb4ab63792886587bdfa7ed7abab7b97`

## Final decision

The audit supports two actions now:

1. Prepare separate, reviewable proposals for the high-consequence fail-closed
   findings. Safety does not need a call-savings estimate to matter.
2. Design the durable job observer and its machine-readable output contract.
   The repeated observation loops are the clearest measured source of avoidable
   agent calls.

The audit does not support a corpus-wide savings percentage, a model comparison,
or one final scored product backlog. L1.1 through L1.3 prove the synthetic schema,
redaction, terminal accounting, and deterministic output. They do not parse the
actual Codex or Claude schemas. More audit work is justified only if root wants
Gates B, C, and E to pass and wants quantified call-reduction claims.

No product code, shipped documentation or skill, remote host, credential,
runtime, commit, push, PR, release, or deployment is authorized by this plan.

## Final evidence inventory

### Collected and retained

| Evidence | Retained result | What it proves |
| --- | --- | --- |
| Approved roots | Five source classes in `approved-root-decision.md`, schema `audit-root-v1`, and manifest digest `sha256:2eb606a67f63d6b6ac613c1c6f9189d42bbf470e8ca0cbfe1efe1c1e479e565b` | Authority, selection predicates, snapshot units, safe references, and T3 exclusions are explicit. |
| Current Codex inventory | 549 included rollout files, 75 unique session IDs, and 3,351 metadata rows at `2026-08-24T16:47:19Z` | The current local exact-CWD population is frozen. It is not the historical pattern population. |
| Current Codex structural projection | 3,092 `CommandExecution` completions across five sessions, including 2,925 completed and 167 failed statuses. The projection includes 1,375 normalized Sandbox signatures. | Executed-event and status fields exist in a bounded part of the corpus. A completed event is not a verified user-task outcome. |
| Independent Codex normalization | 582 rollover files, 107 thread IDs, 44 command-bearing threads, and 2,476 deduplicated completion events | Rollover duplication and command-bearing populations are material. These counts use a different snapshot and rule set from the current 549-file inventory. |
| Detailed Codex source | 1,041 command executions, including 118 `job-status`, 43 `job-output`, 77 sleeps, and 182 collaboration waits | One long storage workflow provides exact repeated-call evidence and failure examples. |
| Safe-source findings | Seven `CODEX-SRC-*` reports that cover ATO-001 through ATO-027 | Finding provenance is reviewable without raw transcript IDs or a reverse map. |
| Claude local inventory | 21 approved roots, 64 JSONL files, 37,027 records, 86 top-level schema variants, 946 timestamp inversions, and 1,888 repeated-line occurrences | The local Claude files are readable and structurally varied. Their Sandbox command counts remain lexical candidates. |
| T3 metadata | Safe file and application metadata only | T3 is installed, but behavioral transcript access is unavailable under the approved boundary. |
| Source and contract review | Current source seams and acceptance criteria for 27 ATO findings | The package supports proposal-grade findings against the recorded product basis. |
| Public benchmark review | DeepSWE plus SWE-bench, Terminal-Bench and Harbor, BrowserGym and WebArena, OSWorld, tau-bench, and ToolSandbox | The package supports benchmark-mechanism transfer, not a Sandbox benchmark score. |
| L1.1 synthetic fixtures | 19 input lines, 15 emitted rows, two duplicates, one unsupported exclusion, and one malformed row | The frozen synthetic contract covers the selected ordering, redaction, status, relation, formula, and terminal-accounting cases. |
| L1.2 parser and validator | `tools/audit_agent_usage/` and 13 parser tests | The synthetic `audit-fixture-v1` lane is deterministic, bounded, redacted, and unregistered. |
| L1.3 coverage reconciler | `audit-coverage-v1` and six coverage tests | File, record, session, event, and command units remain separate on the synthetic fixture. |

The final review reran all 19 audit-tool tests twice. Both runs passed. Two clean
coverage generations were byte-identical and produced SHA-256
`b3f912ba4d0e50ea175a5682a4b955b8230a1f1012077bb4cd932815b7806d48`.
The durable-field scanner and `git diff --check` also passed.

### Not collected or not established

- No transcript body, prompt, reasoning text, raw tool input, raw tool output,
  credential, cookie, private key, environment dump, private path, or private
  browser URL is retained.
- No opaque T3 IndexedDB, LevelDB, blob, session, browser-history, or application
  record was opened or decoded.
- No T3 owner-shared transcript, supported export, or authenticated transcript
  API was verified.
- No cloud or team history, other-owner history, deleted history, or project root
  outside the approved classes was collected.
- No versioned adapter has normalized the actual Codex or Claude source schemas.
  The existing corpus summaries came from bounded one-off structural passes.
- No task-level ground truth joins the recorded tool events to verified user-task
  completion. Duration, token, cost, and outcome fields remain incomplete.
- No public benchmark corpus, gated task set, hidden test, or third-party run
  archive was downloaded or redistributed. Sandbox did not run any reviewed
  benchmark.
- No remote or product test ran during this final review.

## Patterns that can reduce agent calls

### Confirmed repeated Codex patterns

| Rank | Pattern | Evidence | Planning decision |
| ---: | --- | --- | --- |
| 1 | Durable job status, output, sleep, and manual-parse loops | The detailed source has 118 status reads, 43 output reads, and 77 sleeps. Eight durable jobs were anchored. Three received 38, 31, and 15 status calls. The broader normalized evidence has 130 anchored status events across five sessions and 73 output events across three. | Proceed with a bounded observer and one output contract. Preserve status/output primitives. An observer timeout means incomplete observation, not job failure. |
| 2 | Repeated context discovery | One workflow repeated `guide` four times and the selected skill seven times. The independent pass found 82 guide/skill events across 37 sessions. | Design a compact, cacheable context receipt. Count savings only inside a defined cold-start window. |
| 3 | Remote readiness and state interpretation | Remote-target options occurred 408 times across 17 sessions. The storage and Hermes sources repeated revision, service, health, dashboard, and recovery checks. | Define one read-only readiness receipt before adding more commands. The receipt cannot migrate, provision, repair, rotate, clean, or deploy. |
| 4 | Feedback listing and manual pagination | The independent pass found 37 `feedback list` events across six sessions. The detailed workflows also reconstructed cursor and JSON handling even though bounded export exists. | Change guidance first to use `feedback export --format jsonl`. Measure again before adding another command. |

The package also has direct, narrow evidence for two duplicate `ci_run` calls and
an unnecessary `ensure_instance` before CI. That episode justifies replay-safe CI
design, but one episode is not a recurrence rate. Generic matrix replay risk is a
current-source contract finding, not an observed duplicate-submission count.

Repeated test probing, manual narrowing, and Hermes setup are plausible savings
families. Their current evidence comes from one detailed workflow, one setup
family, or broad pattern extraction. Keep them below the four confirmed patterns
until the normalized corpus produces comparable episodes.

### Lexical and mechanical candidates

These counts locate samples. They do not prove execution, success, intent, or a
safe replacement count.

- The historical Codex pattern population contains 408 `guide`, 318 `skill
  show`, 255 `job-status`, 190 `job-output`, and 426 feedback candidate hits.
  Shell loops, examples, nested commands, and tokenization errors affect these
  counts.
- The current Codex projection has 321 adjacent repeated Sandbox signatures.
  Adjacency is a repeat signal, not proof of a retry.
- Codex text fields produced 19,542 failure-like, 11,036 timeout-like, and 8,290
  retry-like hints. The enclosing operation may have discussed a failure rather
  than experienced one.
- Claude has 929 lexical `sb` or `./sb` hits, including 266 `sb wp`, 236
  `sb test`, and 101 `sb selftest` candidates. The pass had 193 shell
  tokenization errors. No Claude Sandbox execution, host, permission, or outcome
  claim follows from these values.

### Safety-only and correctness findings

These findings do not need a step-reduction claim. Handle each in a separate
authorized change with a failing contract test first.

- ATO-013: a non-zero remote transport can look like a positive status receipt.
- ATO-017: `secrets run` can present a failing trusted child as shell success.
- ATO-019: malformed job IDs and limits can escape as raw errors.
- ATO-020: the default retention command can delete historical job data without
  a preview or confirmation.
- ATO-021: remote resource operations do not share the workspace-grade
  ownership and runtime-revision preflight.
- ATO-016: repeated confirmed provisioning can rotate a healthy credential
  instead of converging.
- ATO-027: a delegated validation pass is not acceptance evidence unless it is
  bound to the exact tree and rerun after composition.

ATO-006, the remote-only target guard, has both safety and efficiency value. The
package has one direct mis-targeting episode, so it supports a fail-closed guard
but not a prevalence claim.

## Claude and T3 access conclusion

The Claude and T3 evidence describes two different access classes.

Local Claude JSONL is available under `CLAUDE-SANDBOX` and
`CLAUDE-T3-WORKTREE`. The audit read approved local files and retained only
aggregated structure and lexical command signatures. This access does not cover
Claude cloud history, team history, other owners, or unavailable project roots.
The 929 Sandbox signatures are not execution telemetry.

T3 behavioral coverage is unavailable. T3 Code and its browser-facing stores
exist locally, but the audit found no supported transcript export, documented
transcript API, or owner-shared artifact. Installed application state, bundle
strings, file sizes, and a loopback listener do not grant transcript authority.
Do not decode opaque stores. The only admissible next input is an owner-provided
export or share URL, or a documented vendor API with explicit authorization.

## Benchmark transfer decision

Gate F applies to measurement design only.

### Transfer

- Use a versioned task manifest with the repository SHA, Sandbox revision,
  runtime or image digest, target, seed or snapshot, allowed capabilities, and
  finite budgets.
- Run a behavior-focused verifier in a separate disposable environment. Keep
  the user's dirty worktree and production state outside the verifier.
- Retain a structured run receipt with the patch or state delta, test or browser
  evidence, terminal status, tool events, model and effort, provider route,
  tokens when available, duration, cost as-of time, and exclusion reason.
- Keep pass-to-pass and fail-to-pass checks, partial results, repeated verifier
  runs, and an error ledger. Missing evidence remains unknown or unverified.
- Use Harbor and Terminal-Bench as the closest durable-job model. Use
  WebArena-Verified for offline browser evidence, tau-bench for state replay,
  ToolSandbox for state and milestone receipts, SWE-bench for patch-plus-test
  outcomes, and OSWorld for long-horizon checkpoints and partial completion.

### Do not transfer

- Do not ingest DeepSWE or another benchmark corpus as local telemetry.
- Do not assume that a `has_*` flag means that every trajectory, patch, log, or
  verifier receipt is available.
- Do not copy a binary pass-only outcome model into stateful WordPress, browser,
  remote-host, or credentialed work. Keep blocked, unverified, infrastructure
  error, timeout, provider error, and unknown states.
- Do not treat public reference solutions, hidden tests, image tags, live
  leaderboards, gated data, or license-unclear trajectories as immutable or
  redistributable evidence.
- Do not compare model rows without the harness, effort, provider route, model
  snapshot, price schedule, and exact benchmark release.

## Final gate verdicts

| Gate | Verdict | Final reason |
| --- | --- | --- |
| A: corpus | **PASS** | The approved roots, safe identifiers, current and historical snapshot units, T3 boundary, and durable-field scan are frozen and verified. |
| B: parser | **INCONCLUSIVE** | The L1 synthetic lane passes its contract, but it supports only `audit-fixture-v1`. It does not parse actual Codex or Claude records. A Claude line can contain more than one tool block, while the current synthetic parser emits at most one event per input line. The parser also tracks one previous timestamp across the whole input file, so a mixed-source file could receive a cross-source inversion label. Actual source adapters, multi-event record accounting, a bounded pilot, and a reproducible full manifest are still missing. |
| C: cohorts | **INCONCLUSIVE** | The counting rules are written, but no versioned task taxonomy, episode table, verified outcome mapping, or comparable cohort report exists. Missing job IDs, timestamps, tokens, duration, and task outcomes remain unresolved. |
| D: findings | **PASS** | All 27 findings have safe provenance, confidence, current source seams or an explicit source-review basis, recommendations, and acceptance criteria. The PASS is proposal-grade and bound to product source `f3124b09eb4ab63792886587bdfa7ed7abab7b97`. Revalidate every row before product work. |
| E: backlog | **INCONCLUSIVE** | The package has useful priorities, but no reproducible score combines recurrence, observed cost, estimated savings, safety consequence, compatibility, dependency, and effort. There is no controlled replay that observes a reduction. |
| F: benchmark | **PASS** | Public provenance, licensing limits, receipt design, and transfer limits are explicit. The PASS authorizes design comparison only, not a benchmark-derived product, model, cost, or marketing claim. |

Gate B's synthetic design portion is `PASS`. The overall Gate B remains
`INCONCLUSIVE` until the source adapters and corpus pilot pass. `INCONCLUSIVE`
does not authorize the next dependent slice.

## Ranked next plan

### 1. Complete the real-source parser gate

Owner: Sol Medium for adapter design and integration. Luna Max may do only the
bounded mechanical tasks listed below. Root verifies the outputs. Sol High
reissues Gate B.

Before any corpus run, revise the contract to support zero or more event rows per
physical input record. Record accounting and event accounting must remain
separate. Scope timestamp inversion to the proven source stream or require one
source stream per input file. Add synthetic cases for multiple Claude tool
blocks, Codex call/result joins, missing event keys, schema variants, and mixed
source labels.

Acceptance oracle:

- Every physical record has one record terminal state.
- Every projected event has a stable event index and a safe source reference.
- A record with multiple tool blocks emits every supported event without
  multiplying the record count.
- Missing event or join IDs never merge two events.
- File order is preserved. Timestamp inversion never compares unrelated source
  streams.
- Two clean runs are byte-identical and pass the forbidden-field scanner.

### 2. Normalize the approved Codex and Claude corpus

Owner: Luna Max for bounded execution, Sol Medium for review, and root for the
integration checks. T3 remains metadata-only.

The result must include a sanitized file manifest, normalized events, exclusion
rows, one uncertainty ledger, and per-unit coverage. Run a bounded pilot first.
Do not expand after any leakage, unexplained remainder, unstable order, or
collapsed unknown state.

Acceptance oracle:

- The manifest reconciles the 549-file Codex snapshot and the 64-file Claude
  snapshot without combining files, sessions, records, threads, events, or
  commands.
- A recorded sample seed lets root repeat the safe-source spot check.
- The Codex adapters explain their relationship to the 3,092 current completions
  and the separate 2,476-event rollover pass. Differences have source-level
  reasons.
- Claude duplicate markers, six duplicate UUID occurrences, 946 timestamp
  inversions, 4,361 missing timestamps, and 193 tokenization errors remain
  visible uncertainty.
- No Claude lexical candidate becomes an executed event without structural
  call and result evidence.

### 3. Define cohorts and generate the observed baseline

Owner: Sol High defines taxonomy and outcome semantics. Sol Medium implements
the report contract. Luna Max generates rows after the contract is frozen.

Use task classes for durable job observation, CI or matrix submission, remote
readiness, context discovery, feedback audit, test execution, Hermes operations,
and fail-closed contract checks. Group an episode only when thread, target,
capability, and request or job evidence support the join. Missing IDs remain
non-joinable.

Acceptance oracle:

- Each aggregate links to safe event rows.
- Cohorts match task class, target class, outcome class, and evidence
  completeness.
- `agent_tool_call` and embedded `sandbox_command` counts remain separate.
- The report reproduces the anchored 38, 31, and 15 status-call episodes or gives
  a source-level explanation.
- Duration, token, cost, and task outcome gaps remain unavailable, not zero.

### 4. Produce estimates and a replay plan

Owner: Sol Medium. Luna Max may apply frozen formulas. Sol High accepts the
assumptions and Gate E. Root verifies the generated report.

Report observed calls separately from estimated replacement calls. Do not call
an estimate an observed saving. Build a paired replay specification for the
observer, request identity, readiness receipt, context receipt, feedback export,
and test-selection proposals.

Acceptance oracle:

- Each estimate names the source episode, candidate recipe, assumptions,
  confidence, and unresolved states.
- Estimated replacement calls are neither negative nor greater than observed
  calls.
- The legacy and candidate recipes use the same initial state, target, deadline,
  verifier, and evidence requirements.
- A candidate passes only with fewer agent-visible calls and the same terminal
  outcome, completeness, mutation count, and permission boundary.

### 5. Revalidate and score the backlog

Owner: Luna Max may build the mechanical contract matrix. Sol Medium reviews
source and test seams. Sol High and root own weights, severity, product
boundaries, and the final order.

Acceptance oracle:

- Every ATO row records the current source SHA, symbol or contract, test seam,
  evidence class, drift state, owner, authorization class, compatibility limit,
  and acceptance test.
- A historical source alone cannot prove current behavior.
- Safety consequence and observed call cost use separate score fields.
- ATO-009 remains adjacent orchestration unless a Sandbox-owned mechanism is
  proven.

### 6. Prepare separately authorized product slices

Use this provisional order after current-source review:

1. Fail-closed fixes: ATO-013, ATO-017, ATO-019, ATO-020, and ATO-021.
2. Durable observation and output: ATO-001, ATO-003, and ATO-018.
3. Replay-safe CI and matrix identity: ATO-002 and ATO-010.
4. Shared readiness and convergence: ATO-004, ATO-011, ATO-012, ATO-016, and
   ATO-023.
5. Guidance before new commands: ATO-005 through ATO-008, ATO-014, and ATO-015.
6. Hermes reliability proposals: ATO-022 and ATO-024 through ATO-026.
7. Tree-bound validation receipts: ATO-027.

This order is provisional because Gate E is `INCONCLUSIVE`. It separates
high-consequence correctness work from measured call-reduction work.

## Exact Luna Max tasks

Run these tasks only after the named owner freezes their inputs. Each task uses
an isolated output directory and reports the model, effort, input digest, output
paths, commands, exit statuses, bound tree, and unresolved states.

### LM2.1: build the sanitized file manifest

Inputs: the Gate A root decision, exact source filters, and a root-recorded
snapshot time.

Outputs: source label, safe file reference, schema family, byte count, readable
state, and exclusion reason. Do not emit a raw path.

Oracle: file and source totals match the approved inventory. Every candidate has
one manifest state. Codex rollover-file and unique-session counts stay separate.

### LM2.2: run the bounded source-adapter pilot

Inputs: Sol Medium's accepted adapters, fixed synthetic source-schema fixtures,
and an approved sample manifest with a recorded seed.

Outputs: temporary normalized rows, exclusions, accounting, and uncertainty.

Oracle: root reproduces the sample, every record reconciles, multiple event
blocks are preserved, outputs are byte-identical, and the forbidden-field scan
has zero matches. Stop on any failure.

### LM2.3: normalize the full approved corpus

Inputs: the exact LM2.1 manifest and the adapter tree that passed LM2.2.

Outputs: temporary derived events, exclusions, coverage, and source summaries.

Oracle: the full counts reconcile without an unexplained remainder. T3 is not an
input. Sol Medium inspects the derived artifacts before root promotes them.

### LM2.4: generate the uncertainty ledger

Inputs: LM2.3 exclusions and parser warnings only.

Outputs: bounded counts by source, schema family, and reason, with safe
references only.

Oracle: every warning maps to one manifest entry. Unreadable,
authorization-uncertain, malformed, partial, and unknown remain distinct.

### LM3.1: generate the observed cohort baseline

Inputs: validated normalized events and a Sol High-approved taxonomy.

Outputs: per-episode and per-cohort call, wait, parse, retry, failure, turn,
duration, and token fields when available.

Oracle: aggregates link to event rows, anchored episodes reproduce, and missing
metrics remain unavailable.

### LM4.1: build the current-source contract matrix

Inputs: the 27 ATO rows, current source SHA, approved source seams, and test
paths.

Outputs: one mechanical row per finding with drift state and authorization class.

Oracle: every row has current source evidence or `STALE`, `RESOLVED_IN_SOURCE`,
or `UNVERIFIED`. Luna does not choose severity, score weights, or final priority.

No other Luna task is justified before these outputs pass. In particular, Luna
must not decide access, privacy, task outcomes, architecture, security severity,
benchmark validity, product ownership, or final acceptance.

## Blocks and non-goals

- Do not decode or inspect an opaque T3 or browser store.
- Do not treat Claude lexical hits as executions.
- Do not combine the current Codex snapshot, the historical pattern population,
  the rollover pass, the SQLite index, Claude records, or lexical counts.
- Do not infer a task outcome from `CommandExecution.completed`, an exit code of
  zero, a tool-call completion, or an assistant claim.
- Do not infer parent, child, retry, replay, or episode identity from missing
  identifiers.
- Do not add an arbitrary-shell host operation to solve a Hermes boundary.
- Do not let a readiness observation perform migration, provisioning, repair,
  token rotation, cleanup, deployment, or another mutation.
- Do not broaden deletion, credential, remote, transcript, or production
  authority through a report or test plan.
- Do not publish a benchmark, cost, model, or call-reduction claim from this
  package without the required pinned receipts and a controlled replay.
- Do not implement product code, update shipped guidance, run remote tests,
  commit, push, open a PR, merge, tag, release, or deploy under this plan.

## Final acceptance oracles

The audit is complete only when all of these checks pass:

1. Gate B passes on the actual approved Codex and Claude source adapters, not
   only on synthetic fixtures.
2. Gate C passes with a versioned taxonomy, episode rules, task-outcome
   semantics, and comparable cohort report.
3. Gate D is revalidated against the current product SHA.
4. Gate E passes with a reproducible score and observed-versus-estimated report.
5. Gate F remains limited to pinned, licensed, receipt-backed design claims.
6. Every generated artifact passes the terminal accounting, deterministic
   two-run comparison, forbidden-field scanner, local-link check, and
   `git diff --check`.
7. Root inspects the actual artifacts and reruns the checks. A delegate receipt
   remains provisional until that review.
