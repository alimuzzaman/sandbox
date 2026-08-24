# Sol High final adjudication

Date: 2026-08-24

Model/effort: `gpt-5.6-sol` High

Reviewed tree: `b7b928c238681b1ca5739dc2627bd0cde9bb654f` plus the
uncommitted audit-document additions present at review time

Product-source basis: `f3124b09eb4ab63792886587bdfa7ed7abab7b97`

## Decision

**NO-GO for Luna execution, corpus normalization, or generated efficiency and
backlog reports in the current state.** Gate A fails because durable audit
artifacts retain unhashed local transcript/session identifiers and private
absolute paths contrary to the approved privacy contract. Gate B is not yet
demonstrated by a versioned parser, fixtures, reconciliation manifest, or
reproducibility run.

**GO for Sol Medium to perform Slice 0 only:** correct and freeze the audit
evidence contract, without reading new raw records or changing product code.
After root verifies the Gate A corrections and records Gate A `PASS`, Sol Medium
may supervise the synthetic, audit-only Slice 1 work described below. This is a
staged authorization, not approval of the full Sol Medium plan.

No Sandbox product change, shipped documentation or skill change, remote
operation, T3-store decoding, credential operation, commit, push, PR, release,
or deployment is authorized by this adjudication.

## Evidence reviewed

The adjudication used only the sanitized audit reports in this directory,
including `overall-plan.md`, `findings.md`, `evidence.md`,
`luna-xhigh-adjudication.md`, `codex-corpus-summary.md`,
`claude-corpus-summary.md`, `t3-access-report.md`, `deepswe-review.md`,
`benchmark-landscape.md`, `sol-medium-execution-plan.md`, `work-plan.md`, the
audit README, and the subagent ledger. No raw transcript body, T3 application
store, browser database, credential source, or remote host was accessed.

Repository inspection established that every product-source link in
`findings.md` resolves to an existing file and an in-range line. The branch
changes from the recorded product basis through `HEAD` are audit documents
only, so the cited product source has not drifted inside this audit branch.

## Gate verdicts

| Gate | Verdict | Reason |
| --- | --- | --- |
| A — corpus | **FAIL** | At adjudication time, the durable tree exposed exact local source IDs, source filenames, and private absolute paths. Snapshot provenance also mixed the current snapshot, a stale intermediate, and the historical pattern snapshot. Slice 0 must replace those identifiers, remove those paths, and freeze the units before Gate A can be reissued. |
| B — parser | **INCONCLUSIVE** | The bounded pilot reports plausible structural projection, zero parse failures, and deliberate redaction, but there is no versioned parser, accepted schema, synthetic fixture suite, immutable input manifest, terminal-accounting reconciler, forbidden-field test, source-to-row mapping, or two-run reproducibility proof. Three small Codex pilot files cannot establish full-corpus ordering/deduplication correctness. |
| C — cohorts | **INCONCLUSIVE** | The plan correctly separates agent tool calls from embedded `sb` commands and proposes like-for-like cohorts, but no accepted task taxonomy or cohort report exists. The current snapshot, the historical pattern snapshot, rollover files, completion events, lexical hits, and Claude records are different populations and units. Claude signatures are lexical candidates, `CommandExecution.completed` is not a verified task outcome, parent/child attribution remains partial, and missing identifiers cannot safely define an episode. |
| D — findings | **PASS** | The 27 findings are proposal-grade traceable: direct executed/failure evidence is separated from lexical evidence and later source-contract review; confidence, uncertainty, source seams, recommendations, and bounded acceptance criteria are present. Current source references exist and the product tree has not changed since the stated basis. This PASS does **not** validate prevalence, savings, historical behavior at every transcript timestamp, or implementation readiness; those claims remain blocked by Gates A-C and E. |
| E — backlog | **INCONCLUSIVE** | Priorities and safety implications are useful, but there is no reproducible score, cohort-level recurrence baseline, observed-versus-estimated call-reduction table, implementation-size estimate, dependency score, or current-source matrix for every row. Safety findings may remain high priority without a savings claim, but the package does not yet justify one final ranked implementation backlog. |
| F — benchmark | **PASS** | DeepSWE and the broader landscape are used as design references, not imported evidence or a Sandbox score. The package cites public primary/maintainer sources, distinguishes outcome metadata from trajectories and receipts, records licensing/provenance limits, rejects gated/private ingestion, and gives a careful transfer matrix. PASS is limited to benchmark-mechanism transfer; floating leaderboard values, artifact availability, image tags, prices, and public scores are not admissible Sandbox claims without pinned release/hash/retrieval evidence. |

`INCONCLUSIVE` is not a pass and grants no execution authority.

## Required corrections before Gate A can pass

1. Replace every durable raw transcript/session/thread ID and rollout filename
   with a deterministic safe source reference, for example the first 16 or more
   hex characters of `sha256("sandbox-agent-tool-audit:v1:" + raw_id)`.
   Rename the `by-transcript/` files and index accordingly. Do not persist a
   reverse map. A raw ID may be used ephemerally during an authorized join but
   must not enter generated output, logs, fixtures, commands captured in docs,
   or test failure text.
2. Remove user-home and private source-root paths from durable audit artifacts.
   Keep only approved labels such as `CODEX-LOCAL-EXACT-CWD`,
   `CLAUDE-SANDBOX`, `CLAUDE-T3-WORKTREE`, and the existing T3 safe labels.
   Repository-relative product paths and the canonical audit output path may be
   reported to the user at handoff, but generated corpus data must remain
   path-free.
3. Reconcile snapshot language everywhere. The current snapshot is 549 included
   rollout files, 75 unique session IDs, and 3,351 metadata rows at
   `2026-08-24T16:47:19Z`. Remove the undated intermediate as stale. Define the
   historical pattern population exactly once and use that unit consistently.
4. Replace the remaining strict-minimum wording with “approximate
   candidate hits; false positives and false negatives are possible.” Keep
   lexical candidates, structured tool metadata, command completions, and
   verified task outcomes in separate tables.
5. Freeze an approved-root decision record containing source label, authority
   basis, exact selection predicate, as-of time, inventory unit, schema version,
   and manifest digest. T3 remains `behavioral_coverage_unavailable`; no T3
   application/browser store may become a parser input without a supported
   owner export or share artifact and a new Sol High/root authorization.
6. Run a forbidden-field scan over the full durable audit directory after the
   corrections. At minimum it must reject raw local session/thread UUIDs,
   rollout filenames containing those UUIDs, user-home paths, transcript prose,
   command argument values, output text, tokens, cookies, credentials, private
   keys, raw environment values, and unredacted URLs from private history.

Gate A must be re-recorded as `PASS` by Sol High and accepted by root before any
Luna task starts.

## Parser and reporting constraints

After Gate A passes, Slice 1 must use these corrections to the Sol Medium plan:

- Define disjoint accounting identities rather than an ambiguous counter list:
  `input_records = malformed + duplicate + excluded + emitted`, and
  `parsed_records = duplicate + excluded + emitted`. File, metadata-row,
  session, thread, event, and command units must each have separate counters.
- Separate `transport_status`, `tool_call_status`, `command_exit_status`, and
  `task_outcome`. A completed runtime event is not a completed user task.
- Preserve file order and explicit event indices. Do not manufacture a global
  chronology from Claude timestamps; retain timestamp inversions and missing
  timestamps as uncertainty.
- Treat missing request/job IDs as non-joinable. Two missing IDs never define
  one episode. Parent/child/subagent relations remain `unknown` unless an
  approved metadata join proves them.
- Keep raw values in memory only for the minimum projection step. The parser
  must accept explicit files and an explicit temporary output directory and
  have no network, browser-store, database, subprocess, remote, or product
  registry path.
- Use only synthetic prose in fixtures. Include nested sensitive-looking values,
  formula-injection strings, malformed JSONL, rollover duplicates, missing
  timestamps, partial/unknown states, and parent-child variants.
- Require byte-identical output from two clean runs, manifest/count
  reconciliation, a deterministic sample seed, source-to-row/exclusion
  traceability through safe references, and a zero-match forbidden-field scan.
- Generate into a temporary audit-only directory. Promotion of derived reports
  requires Sol Medium inspection and root rerun of the checks. A leakage,
  unexplained remainder, unstable order, or collapsed unknown state is a hard
  stop; it does not authorize a broader raw-data inspection.

## Luna slice decision

### Currently approved

None. Gate A is `FAIL`, and the plan itself requires Gate A plus the design
portion of Gate B to pass before Slice 1.

### Conditionally approved after Gate A/root acceptance

- **L1.1 — synthetic schema fixtures:** approved first, with synthetic data only
  and no corpus reads.
- **L1.2 — schema/parser/redactor/validator:** approved only after Sol Medium
  reviews the fixture expectations. The implementation remains audit-only and
  unregistered.
- **L1.3 — coverage reconciler:** approved only with the disjoint arithmetic
  identities above.

These are provisional Luna mechanics. Sol Medium must inspect every diff and
root must rerun the targeted checks before the result can support Gate B.

### Blocked until later gates

- **L2.1-L2.3:** blocked until Gate A passes and Slice 1 proves Gate B on fixed
  fixtures. T3 behavioral collection remains excluded.
- **L3.1-L3.3:** blocked until Gate B passes and Sol High accepts the task,
  episode, outcome, and comparability rules for Gate C. Every replacement count
  remains `ESTIMATED` until a separately authorized controlled replay observes
  it.
- **L4.1:** blocked until normalized evidence exists and the source revision is
  pinned in the generated matrix. It is read-only source lookup only.
- **L4.2:** blocked until Gates C and D pass for the generated corpus. Luna may
  mechanically apply accepted weights but may not choose weights, severity,
  product ownership, or final priority.
- **Slice 5:** Sol High/root work only; never a Luna acceptance task.
- **Slices 6-9:** proposal text only under this audit. Product code, public
  command/MCP contracts, shipped docs/skills, specifications, and tests remain
  outside the current authorization.

## Acceptance conditions for remaining gates

### Gate B

- Versioned schema, parser, redactor, fixtures, and manifest are present.
- Every input reaches exactly one terminal accounting state.
- Ordering, duplicate handling, bounded strings, formula injection, unknown
  fields, partial/unknown outcomes, and stable safe identifiers have fixed tests.
- Two independent runs are byte-identical and the forbidden-field scan is clean.
- A recorded deterministic spot check traces safe source references to derived
  or exclusion rows without persisting raw content.

### Gate C

- Task and outcome taxonomies are explicit and versioned.
- Cohorts match task class, target class, outcome class, and evidence-completeness
  class.
- `agent_tool_call` and embedded `sandbox_command` counts remain separate.
- Lexical candidates never enter executed-call or success denominators.
- Missing identifiers, timestamps, tokens, duration, and task outcome remain
  unavailable rather than zero or inferred.

### Gate D

The current proposal-grade PASS remains valid only while every ATO row retains a
current source or contract reference, evidence class, confidence, uncertainty,
safe source reference, owner boundary, test seam, and recorded source SHA.
Historical transcripts cannot prove current source behavior. Any source change
marks the corresponding row stale until revalidated.

### Gate E

- Every ranked row has repeated observed evidence or a documented severe safety
  event, current source evidence, owner boundary, dependency and effort class,
  compatibility limits, and an executable acceptance oracle.
- Observed calls and estimated replacement calls are separate; estimates name
  assumptions and cannot be negative or exceed observed calls.
- Safety findings such as fail-open exit status, destructive retention, and
  remote preflight are ranked on consequence independently of call savings.
- ATO-009 remains adjacent orchestration unless an enforceable Sandbox-owned
  seam is separately proven.

### Gate F

The current PASS remains valid for design comparison only. Any numeric,
marketing, or cross-model claim must additionally pin the benchmark release,
source/harness commit, image and artifact digest or ETag, provider route, model
snapshot, effort, pricing as-of time, retrieval time, verifier receipt, and
exclusion denominator. Gated or license-unclear task/trajectory data may not be
copied, trained on, or redistributed without separate review and authority.

## Final authorized next step

Sol Medium may now prepare the bounded Slice 0 correction patch and a Gate A
re-review packet. It must not start Luna, read additional raw records, generate
corpus outputs, or change Sandbox product code. Root should inspect the corrected
tree, the forbidden-field scan, exact snapshot labels, and the approved-root
decision record before asking Sol High to reissue Gate A.

## Gate A re-adjudication after Slice 0

Reissue date: 2026-08-24

### Verdict

**Gate A — corpus: PASS.** This reissued verdict supersedes the original Gate A
`FAIL` and the original “Currently approved: None” Luna status above. The
historical text remains in place as the audit trail for why Slice 0 was required.

The corrected sanitized tree satisfies the Gate A privacy, authorization,
source-root, identifier, snapshot, and exclusion conditions:

- `approved-root-decision.md` freezes five source classes under
  `audit-root-v1`/`historical-v1`, records the authority and selection predicate
  for each, and gives the current Codex snapshot as 549 rollout files, 75 unique
  sessions, and 3,351 metadata rows at `2026-08-24T16:47:19Z`.
- The historical 473-file pattern population appears only in the approved-root
  decision and is explicitly non-additive and non-comparable without a validated
  crosswalk.
- The stale intermediate count is absent. The apparent `19,542` text-hint value
  in `codex-corpus-summary.md` is an unrelated five-digit metric, not the stale
  snapshot.
- Seven `CODEX-SRC-*` files preserve the finding evidence by deterministic safe
  source reference. Together they cover all 27 unique ATO finding IDs. No reverse
  map is retained in the audit tree.
- `T3-SAFE-METADATA` remains metadata-only with
  `behavioral_coverage_unavailable`; application/browser stores remain excluded
  without a supported owner export or share artifact and a new Sol High/root
  authorization.
- `check-durable-artifacts.sh` passed over durable content and filenames. An
  independent bounded search also found no raw UUID, private home path, raw
  source filename, or exact stale snapshot value in the scanned durable formats.
- `git diff --check` passed. Content-aware no-index whitespace checks passed for
  every untracked audit artifact.

### Luna authorization

**L1.1 may start now.** L1.1 is limited to synthetic schema fixtures and expected
normalized/exclusion rows. It may not read a corpus file, transcript, T3 store,
browser store, credential source, remote host, or product runtime.

**L1.2 and L1.3 are approved sequentially, not concurrently:**

1. Sol Medium must first inspect and accept L1.1's synthetic fixtures and expected
   rows against the frozen contract.
2. L1.2 may then implement the audit-only schema, parser boundary, redactor, and
   validator with no network, browser-store, database, subprocess, remote, or
   Sandbox command/MCP registration path.
3. After Sol Medium reviews L1.2 and its targeted tests, L1.3 may add the coverage
   reconciler using the disjoint accounting identities already specified in this
   adjudication.

Each Luna result remains provisional. Sol Medium reviews every changed artifact,
and root reruns the targeted fixture, redaction, reproducibility, forbidden-field,
and whitespace checks before any result supports Gate B.

### Still blocked

Gate B remains `INCONCLUSIVE`. L2 corpus normalization, L3 cohort/report work,
L4 backlog mechanics, all product/guidance implementation slices, T3 behavioral
collection, remote activity, and commit/push remain unauthorized. A Gate B PASS
requires the accepted L1.1-L1.3 artifacts and the evidence listed under the Gate
B acceptance conditions above.
