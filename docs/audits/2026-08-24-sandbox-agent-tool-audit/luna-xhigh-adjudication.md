# Luna XHigh cross-corpus adjudication

Date: 2026-08-24
Scope: read-only review of the sanitized audit reports in this directory.
Access boundary: no raw transcript bodies, prompts, outputs, secrets, tokens,
cookies, private keys, opaque T3 stores, or owner-private T3 data were read.

This pass validates the evidence register and recommendations for Sol High. It
does not authorize product changes, remote operations, credential work, cleanup,
deployment, or release decisions.

## Recommendation

**Conditional GO for Sol High adjudication and a narrowly scoped Slice 1 design;
NO-GO for broad prevalence claims, release-gating metrics, or implementation of
the full backlog from these reports without the corrections below.**

The durable-job observer and its machine-readable output contract have direct,
executed-call support and are suitable for the next planning gate. CI/matrix
request identity and fail-closed command contracts are also strong safety cases,
but should be treated as contract work rather than measured call-savings claims.
Remote readiness/resource preflight is supported by source-contract evidence and
must remain fail-closed. Claude lexical counts are useful for discovery only; T3
behavior is unavailable. Sol High should require a snapshot manifest and explicit
unit labels before using any corpus count as a baseline.

## Count consistency

The arithmetic inside each report is consistent, but several values describe
different units or snapshots and must not be added or compared as if they were
one corpus.

| Source/measure | Reported value | Adjudication |
|---|---:|---|
| Historical Codex exact-CWD pattern scan | See `HISTORICAL-CODEX-PATTERN` in the approved-root decision | Historical approximate candidate hits. Do not substitute them for current or executed counts. |
| Current Codex exact-CWD inventory | 549 included rollout files, 75 unique session IDs, and 3,351 metadata rows at `2026-08-24T16:47:19Z` | Current point-in-time inventory frozen as `CODEX-LOCAL-EXACT-CWD`. It remains separate from the historical pattern snapshot. |
| Luna Max normalized rollover pass | 582 rollout files including rollover duplicates; 107 unique thread IDs; 44 threads with concrete `CommandExecution` completions; 2,476 deduplicated completion events | Internally consistent: 2,333 completed + 143 failed = 2,476. This is a normalized event corpus with rollover duplication, not the historical pattern scan. |
| Independent SQLite history index | 2,554 exact-CWD command-execution rows across 49 threads | Not contradictory to 2,476: the index and event pass have different sources, deduplication and thread-eligibility rules. Treat the difference (78 rows and five threads) as an unresolved crosswalk, not as a defect or combined total. |
| Detailed storage rollout | 1,041 `CommandExecution` events; 996 completed + 45 non-zero/failed | Internally consistent. The 45 failures are explicitly mixed (interrupts, typos, broad-test failures, transport friction, and revision guards); they are not one Sandbox failure rate. |
| Claude selected corpus | 21 roots (1 Sandbox, 20 T3), 64 JSONL files, 37,027 records | Internally consistent. The source-root rows sum to 64 files, 37,027 records, 1,888 duplicate-line occurrences and six duplicate UUID occurrences. The type rows also sum to 37,027. |
| Claude timestamp/lexical indicators | 32,666 timestamped records; 946 adjacent inversions; 4,361 without parseable timestamps; 929 lexical `sb` hits; 193 tokenization errors | These are diagnostic/lexical measures, not execution telemetry. The 929 hits cannot establish that a command ran, succeeded, targeted a host, or was not an example. |
| T3 local stores | Metadata-only inventory; no verified export/API or owner-shared transcript | No behavioral count is available. Store sizes and app/bundle presence must not enter usage denominators. |

The Claude type arithmetic is a useful integrity check, not a claim that each
record is an independent conversation event. Marker rows and lifecycle records
are included by design.

## Confirmed findings and evidence strength

### Direct executed-call or direct-failure evidence

- **ATO-001 (P1, high):** one detailed rollout has 118 `job-status`, 43
  `job-output`, and 77 `sleep` calls. The independent pass anchors the pattern
  to eight durable job IDs; representative jobs received 38, 31, and 15 status
  calls. This supports a bounded observer and a shared output contract, subject
  to finite timeout, cursor, completeness, and no-implicit-mutation semantics.
- **ATO-002 (P1, high):** the CI status transcript visibly made two identical
  `ci_run` calls. The current signature lacks a durable request ID and an
  unnecessary `ensure_instance` preceded CI's own isolated matrix provisioning.
  This is direct duplicate-call evidence plus a current contract gap, though one
  transcript is not a CI retry-rate estimate.
- **ATO-003 (P1/P2, high):** the detailed rollout encountered repeated-envelope,
  nested-JSONL and field-shape parsing failures. This justifies one explicit
  machine-readable contract; it does not justify treating every parse error as a
  product defect without separating human-mode, follow-mode and retained-output
  contracts.
- **ATO-006 (P2, medium):** one remote-only request initially inspected local
  state. The explicit target guard is supported as a safety control, not as a
  prevalence measurement.
- **ATO-019 (P2, high):** malformed job IDs/limits escaped as raw `ValueError`
  in a surface sweep. This is a contract ergonomics finding, not a usage-count
  finding.
- **ATO-020 (P1/P2, high):** a retention invocation removed historical logs and
  metrics without confirmation; a later bounded call showed the side effect was
  persistent. This is a high-safety correction even though it does not reduce
  steps.
- **ATO-027 (P1/P2, medium-high):** a delegated compile-pass report conflicted
  with the root integration finding a `SyntaxError`. The exact tree, files,
  command and receipt binding are missing, so the failure is strong evidence for
  root-verifiable validation but not a rate of delegated false passes.

### Current source-contract evidence (not original-agent observation)

The report correctly labels ATO-011--ATO-016 as Luna source-contract expansion
attached to the storage rollout, and ATO-017--ATO-026 as follow-up source and
workflow expansion. They should not be summarized as facts the original agent
observed in that transcript. Subject to current-branch verification, the
source-backed findings are credible:

- **ATO-010, ATO-011--ATO-013, ATO-016, ATO-017, ATO-018--ATO-021:** request
  identity, resource/runtime readiness, transport-failure handling, convergent
  provisioning, secret-child exit propagation, CLI/MCP parity, malformed-input
  normalization, retention confirmation, and resource reclamation preflight.
- **ATO-022--ATO-026:** Hermes absence/readiness states, host/repository
  boundary, resumable cloning, and dashboard/session attachment. These are
  reliability and safety proposals supported by one setup family plus source
  inspection; they are not cross-corpus recurrence estimates.
- **ATO-014--ATO-015:** documentation/spec drift and cache-state wording are
  low-priority contract repairs. They do not support a new runtime capability
  or fewer-step claim by themselves.

### Guidance and orchestration evidence

ATO-004 (readiness receipt), ATO-005 (bootstrap context), ATO-007 (feedback
export), ATO-008 (test selection), and ATO-009 (aggregate agent waits) are
reasonable workflow recommendations. ATO-005 has exact duplicate guide/skill
calls in the detailed rollout (four and seven respectively), while the 303/223
broader figures are pattern hits. ATO-009 is outside Sandbox's product boundary
and is based on one rollout's 182 collaboration waits, 32 agent paths and nine
interruptions. Keep these classifications and do not turn them into product
call-volume claims.

## Corrections required before final adjudication

1. **Snapshot labels and units.** Use the approved-root decision for the current
   and historical populations. Keep file, unique-session, metadata-row, thread,
   event, and command units explicit.
2. **Pattern-count wording.** The reports described broad counts as strict
   minima while also warning that shell loops, documentation examples, and
   nested commands can over-count. That claim is invalid when both missed tokens
   and false positives exist. Use `approximate candidate hits; both false
   positives and false negatives are possible`. Reserve volume claims for
   deduplicated executed events.
3. **Executed versus lexical.** Keep Codex `CommandExecution` completions,
   visible MCP calls, and exact detailed-rollout counts in a separate table from
   Codex tool-input pattern extraction and Claude lexical signatures. Do not use
   Claude `sb` hits to prove success, host, remote target, or permission mode.
4. **Duplicate and subagent units.** Keep 582 rollover files, 107 unique IDs,
   44 command-bearing threads, 49 SQLite-index threads and the detailed rollout's
   32 agent paths as separate populations. No report currently supplies a
   validated root/child task mapping or proves that an agent path equals a task.
   Mark parent/child attribution and subagent duplication `unknown` unless a
   metadata-only join key is added.
5. **Source-contract attachment.** Preserve the explicit note that source
   findings ATO-011--ATO-016 and ATO-017--ATO-026 were discovered in follow-up
   reviews. Their transcript IDs are provenance anchors, not proof of original
   tool behavior.
6. **Claude chronology and duplicates.** Retain file-order semantics, 946
   timestamp inversions, marker duplicate lines and six duplicate UUIDs as
   uncertainty. Do not sort into a false global chronology or deduplicate
   conversations from marker repeats.
7. **T3 status.** Keep T3 as `behavioral coverage unavailable`. Installed/running
   app metadata and opaque store sizes do not establish transcript access or
   agent-tool usage.

## Unknowns and limits

- The current and historical Codex populations have no validated source-level
  crosswalk. Their relationship remains unknown.
- The 78-event/5-thread difference between the SQLite index and normalized event
  pass has no source-level reconciliation in the reports.
- Old Codex thread IDs may be unreadable through the app API; inaccessible,
  deleted, moved, cloud/team, and other-owner records are not represented.
- Claude coverage excludes cloud/team history, unavailable/deleted/moved files,
  and project roots outside the encoded Sandbox/T3 selection. No MCP Sandbox
  attribution was observed, but that absence is not proof of no MCP usage.
- T3 has no verified supported export/API or owner-shared transcript in scope.
- There is no cohort-level baseline of wall time, turns, tokens, retries, or
  verified outcomes against which to claim a percentage reduction. The reports
  contain no experiment showing that a proposed observer actually removes calls.
- Current source checks are point-in-time checks of the audit checkout. They do
  not prove that the same behavior existed at every historical transcript event.
- No report proves that a “completed” command event means successful Sandbox
  business outcome; use the recorded completed/failed labels only.

## Ranked common patterns and capability support

| Rank | Pattern/capability | Support in the sanitized corpus | Decision |
|---:|---|---|---|
| 1 | Durable job observer plus one output envelope (ATO-001, ATO-003) | Direct counts and anchored IDs in one detailed rollout; repeated parsing failures; current CLI/MCP split | **Proceed to bounded design.** Preserve lower-level primitives and require terminal/incomplete/partial/transport states. |
| 2 | Replay-safe CI and matrix identity (ATO-002, ATO-010) | Direct duplicate CI call plus current request-ID gaps; generic matrix is source-backed rather than a measured duplicate | **Proceed as safety contract work.** Do not claim measured step savings until replay fixtures exist. |
| 3 | Remote readiness, revision and resource preflight (ATO-004, ATO-011--ATO-013, ATO-021) | Repeated migration/status interpretation in a detailed workflow plus current source boundary inconsistency | **Proceed with fail-closed receipt design.** No auto-migration or cleanup authority from a receipt. |
| 4 | Compact bootstrap context (ATO-005) | Four duplicate guides and seven duplicate skill loads in one rollout; 303/223 broad pattern hits | **Design/document first.** Treat broad counts as lexical candidates and measure after adoption. |
| 5 | Explicit remote-only target policy (ATO-006) | One direct remote-only mis-targeting episode | **Add guard in workflow.** Do not infer prevalence. |
| 6 | Hermes capability readiness and durable session operations (ATO-022--ATO-026) | One setup family plus current source review; no second corpus or execution baseline | **Keep as conditional reliability backlog.** Require capability-specific receipts before any implementation. |
| 7 | Feedback export and named test selection (ATO-007, ATO-008) | Existing bounded export and repeated manual parsing/broad-suite narrowing | **Guidance-only first.** Re-measure before adding new command surfaces. |
| 8 | Fail-closed secrets, retention, malformed IDs (ATO-017, ATO-019, ATO-020) | Direct failure/side-effect traces and source confirmation | **Prioritize safety fixes independently of savings.** These are not fewer-step experiments. |
| 9 | Delegated validation receipts (ATO-027) | One contradictory child/root validation result | **Require tree-bound provisional receipts.** Root rerun remains acceptance authority. |
| 10 | Aggregate agent waits (ATO-009) | 182 waits, 32 paths, nine interruptions in one rollout | **Handle in orchestration, not Sandbox product scope.** No cross-corpus prevalence claim. |

## Sol High gate

Sol High can accept this evidence for adjudication if the artifact set is
corrected as above and the next slice carries a small, immutable evidence
manifest with:

- snapshot/as-of time, source root class, exact-CWD predicate and unit;
- file/session/thread/event counts before and after deduplication;
- explicit root/child/subagent attribution or `unknown`;
- lexical-hit versus executed-completion status;
- source checkout revision for every current contract check; and
- a deterministic observer/replay test oracle with timeout, partial, unknown,
  transport and confirmation states.

Without that manifest and relabeling, **NO-GO** for treating the historical
pattern population, current snapshot, rollover pass, history index, Claude
lexical hits, or Claude records as one comparable usage denominator. With it,
**GO** for Sol High to adjudicate Slice 1 and safety-contract slices; T3 remains
explicitly unverified until an owner supplies a supported export or share URL.
