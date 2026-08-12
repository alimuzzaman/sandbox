# PRD 00 — WordPress Plugin Release Guardian / Operations Agent

Date: 2026-08-12 · Status: Product thesis and 12-month brief for later
`speckit-refine` conversion · Priority: highest product phase

Sources: 2026-08-12 product feedback · shipped Sandbox capabilities in specs 003
(WordPress Abilities + MCP), 013 (baseline-gated Plugin Check), 028 (PHPUnit
execution modes), 032 (durable jobs), and the remote CI/matrix surface · current
approval, capability, and trace evidence in the repository

> Build one well-operated product, not a collection of agent demos: Sandbox becomes
> the release-safety control plane for WordPress plugins. Deterministic checks decide
> whether a revision is releasable. AI may gather evidence, operate bounded tools,
> triage failures, and explain the result; it never converts a failing or missing gate
> into a pass.

---

## 1. Why this is the lead product bet

Sandbox already has much of the substrate: real WordPress instances, project-scoped
CLI/MCP routing, WordPress abilities, PHPUnit, baseline-gated Plugin Check, durable
jobs, compatibility matrices, browser/runtime diagnostics, snapshots, and explicit
capability checks. What it does not yet have is one revision-bound release product
that composes those pieces behind a coherent safety policy and produces evidence a
plugin team can trust.

That product is more valuable than adding another broad runtime or infrastructure
feature because it tests the complete proposition:

- WordPress and PHP correctness in real instances;
- Python and TypeScript orchestration through CLI and MCP;
- secure agent authority and auditable mutations;
- deterministic testing, compatibility, and security gates;
- useful AI diagnosis without allowing AI to become the verifier;
- operational quality, measured latency/cost, and real user adoption.

Outbound mail and Herd-equivalent polyglot stacks remain valid phases, but follow
this one unless they become a direct dependency of a Guardian pilot.

## 2. Product outcome

A plugin developer or release owner supplies a plugin checkout and an immutable
revision. Sandbox produces a reviewable release verdict backed by a complete evidence
bundle:

1. the exact source revision and declared release policy;
2. Plugin Check, PHPUnit, WordPress/PHP compatibility matrix, and security-scan results;
3. complete/partial/unavailable status for every required gate;
4. an AI-written triage that cites the deterministic evidence it explains;
5. an audit trail of abilities, approvals, mutations, tool calls, timings, resource
   usage, and model cost without secret values;
6. artifacts sufficient to reproduce the result and investigate failures.

The verdict is fail-closed: any required failed, incomplete, timed-out, cancelled,
unavailable, stale, or unevaluated gate prevents `ready`. AI confidence, a prior run,
or an accepted baseline cannot silently override that rule.

## 3. Users and jobs

| User | Job |
| --- | --- |
| Solo plugin maintainer | “Tell me whether this exact revision is safe to release and show me the evidence.” |
| Plugin team release owner | “Apply our repeatable policy across supported WordPress/PHP versions and give reviewers one auditable decision.” |
| Contributor | “Explain a failing gate, identify likely owning code, and suggest the smallest next check without editing or releasing.” |
| Security/release reviewer | “See what the agent could do, what it actually did, who approved mutations, and whether any evidence is missing.” |
| Sandbox maintainer | “Measure correctness, latency, reliability, and cost on real defects and real release workflows.” |

## 4. Product principles and boundaries

### 4.1 Deterministic evidence is authoritative

- Check runners, declared thresholds, and policy evaluation produce the verdict.
- AI can select permitted read operations, summarize outputs, correlate failures,
  rank hypotheses, and propose remediation.
- AI cannot mark a gate passed, edit a baseline, waive a finding, mutate the source,
  publish an artifact, or release a plugin merely because its narrative says the
  change is safe.
- Raw normalized results remain accessible beside the explanation. Every AI claim
  about a finding links to a gate, matrix cell, artifact, or bounded trace event.

### 4.2 Safe MCP/Abilities profile

The existing development MCP surface is not automatically the Guardian surface.
Guardian exposes a separately declared, capability-scoped profile:

- discovery and inspection are read-only by default;
- each ability declares input/output schema, scope, side-effect class, data-access
  class, timeout, and audit behavior;
- undeclared or unsupported abilities are absent, not merely discouraged;
- arbitrary PHP, shell, WP-CLI, SQL, file writes, plugin activation, reset, deploy,
  publication, and release operations are never implied by a Guardian request;
- a mutation requires a revision-bound plan, the exact affected resources, an
  explicit current approval, expiry/replay protection, and a postcondition record;
- destructive, external, production, publish, deploy, merge, tag, or release actions
  remain outside the initial Guardian run even when another Sandbox profile supports
  them.

The initial product may invoke existing broad developer tools internally only through
an adapter that enforces this policy and records the bounded operation. It must not
advertise broad development authority as a safe release ability.

### 4.3 One evidence envelope

Every run has a stable ID and binds together revision, policy version, environment,
gate versions, normalized outcomes, artifacts, tool events, approvals, timestamps,
latencies, retries, terminal lifecycle, resource use, and AI usage/cost. Secrets,
credentials, full environment values, sensitive command lines, and unnecessary source
content are excluded or redacted before persistence.

## 5. Prioritized 12-month scope

### P0 — Trustworthy release decision (months 1–4)

1. **Guardian policy and authority boundary.** Define the safe ability manifest,
   read-only default, mutation classes, approval protocol, revision binding, retention,
   redaction, and fail-closed verdict states. Prove that every rejected/expired action
   stops before side effects.
2. **One release-run contract.** Given a clean immutable revision and project policy,
   orchestrate the existing Plugin Check and PHPUnit capabilities plus a declared
   WordPress/PHP compatibility matrix and a security scanner. Use isolated cells and
   bounded durable jobs; never infer success from submission alone.
3. **Deterministic verdict engine.** Normalize `pass`, `fail`, `partial`, `unavailable`,
   `timed_out`, `cancelled`, and infrastructure-error outcomes. Only all-required-pass
   yields `ready`; policy and baselines are explicit, versioned inputs.
4. **Minimum audit trail from the first runnable slice.** Record the exact revision,
   policy, tool/version, start/finish, latency, terminal result, retry lineage,
   artifact digest, and approval/mutation facts. Observability is not postponed until
   after automation because untraced early runs cannot become evaluation evidence.
5. **Pilot discovery in parallel.** Identify at least five plausible outside users and
   one candidate plugin team, interview enough of them to choose the first supported
   release policy, and secure one design partner before broadening the gate catalog.

P0 is independently valuable when one real plugin revision can be evaluated end to
end, all missing evidence fails closed, and a reviewer can reproduce why it passed or
failed without trusting the AI narrative.

### P1 — Explainable operations and measured quality (months 4–8)

1. **Evidence-grounded AI triage.** Explain failures, group duplicates, identify likely
   owning files/components, distinguish product failures from infrastructure failures,
   and recommend the next read-only diagnostic. Cite evidence IDs and state uncertainty.
2. **Human-reviewed mutation workflow.** Where pilots need a bounded mutation (for
   example, creating a candidate patch or updating an explicitly selected baseline),
   produce a plan first and require fresh approval. Verification runs afterward; the
   release verdict remains deterministic. Publishing/releasing is still out of scope.
3. **Real-defect evaluation set.** Curate versioned, licensed/reproducible cases from
   actual plugin defects: compatibility regressions, Plugin Check violations, PHPUnit
   failures, security findings, flaky/infrastructure failures, and at least one case
   where the correct response is “insufficient evidence.” Keep hidden expected outcomes
   separate from prompts and prevent evaluation cases from contaminating production
   release data.
4. **Quality and operations scorecard.** Measure gate accuracy, false-ready count,
   triage correctness/usefulness, citation support, mutation-policy violations,
   run completion rate, per-gate and end-to-end latency, retries, compute/tool usage,
   AI tokens/cost, and human review time. Report distributions and failures, not only
   averages.

### P2 — Adoption and public evidence (months 8–12)

1. **Pilot-ready packaging and onboarding.** A maintainer outside the Sandbox repo can
   configure a plugin, understand permissions, run Guardian, inspect evidence, and
   report a problem without maintainer intervention for the happy path.
2. **Adoption outcome.** Achieve either five outside users completing a real release
   evaluation or one real plugin team adopting Guardian in its release workflow. A demo,
   interview, cloned repo, or maintainer-operated run does not count as adoption.
3. **Measured report.** Publish the evaluation design, supported scope, sample size,
   gate and triage results, latency/cost distributions, limitations, and enough
   reproducible methodology to challenge the claims.
4. **Failure/postmortem case.** Publish at least one real failure, near miss, unsafe AI
   suggestion, missed defect, flaky gate, or operational incident with impact, timeline,
   detection, contributing conditions, corrective actions, and which claimed guarantee
   changed. Do not curate away the product’s weakest evidence.

## 6. Initial gate contract

| Gate | Initial authority | Required P0 behavior |
| --- | --- | --- |
| Plugin Check | WordPress.org Plugin Check through Sandbox's baseline-gated runner | Tool/version and baseline digest recorded; new gating findings fail |
| PHPUnit | Project-declared unit/integration suites | Exact suite/config and terminal status recorded; no tests or missing harness is not a pass |
| Compatibility | Isolated declared WordPress/PHP matrix | Every required cell terminal and independently observable; unsupported cells are explicit |
| Security | A pinned, selected deterministic scanner plus policy | Scanner identity/version/ruleset recorded; unavailable or incomplete scan fails closed |

The security scanner, supported matrix defaults, baseline-waiver governance, artifact
retention, and acceptable pilot release policies are product decisions for
`speckit-refine`; this brief does not silently select them.

## 7. Negative scenarios that must be first-class

- The agent says a failure is harmless, but the required gate failed: verdict remains
  blocked.
- One matrix cell never starts, times out, or loses logs: the run is incomplete, not
  ready.
- A baseline changes between runs or is proposed by the same run: the digest mismatch is
  visible and approval is required before a later run may use it.
- The checkout is dirty, the revision cannot be resolved, or deployed bytes do not match
  the recorded digest: no release verdict is issued.
- A tool emits malformed output, a scanner is missing, or the agent cannot parse a
  result: infrastructure/coverage failure, not pass.
- An approval refers to a different revision, resource, action, or expired plan: reject
  before mutation.
- A trace contains a possible secret or source excerpt beyond policy: redact/quarantine
  it and mark trace coverage partial; do not publish it.
- AI triage is persuasive but unsupported by evidence: label it unsupported and score it
  as an evaluation failure.
- A test is flaky: preserve all attempts and retry policy; do not retain only the green
  attempt.

## 8. Success criteria

### Safety and correctness

- Across the evaluation set and pilots, zero known required-gate failures are reported
  `ready`; missing or non-terminal required evidence is blocked 100% of the time.
- All attempted unauthorized, stale, replayed, over-scoped, or wrong-revision mutations
  are rejected before side effects in deterministic tests and adversarial acceptance.
- A reviewer can map every verdict component and every material AI claim to immutable
  evidence and reproduce the policy decision from the normalized bundle.

### Product and operations

- A supported plugin can run the full required gate set from one declared policy without
  custom orchestration code.
- The product reports end-to-end and per-gate p50/p95 latency, completion/retry rates,
  artifact retention, and AI/tool cost for evaluation and pilot runs.
- The evaluation set contains representative real defects across all four gate classes
  plus infrastructure/insufficient-evidence controls, with provenance and expected
  outcomes reviewed by a human.
- By month 12, five outside users have each completed a real release evaluation or one
  plugin team has integrated Guardian into its real release workflow.
- A measured results report and one failure/postmortem case are public and accurately
  state unsupported scopes and negative results.

## 9. Non-goals for this phase

- Replacing Plugin Check, PHPUnit, compatibility runners, or security scanners with an
  LLM judgment.
- Autonomous release, WordPress.org publication, Git tag/push, deployment, merge, or
  production mutation.
- Generic “run any command” agent access marketed as a safe operations surface.
- Supporting every CI provider, WordPress host, plugin architecture, or scanner in the
  first release.
- Treating Sandbox's own test suite or maintainer-operated demos as outside adoption.
- Hiding failures, excluding expensive runs, or publishing only successful case studies.

## 10. Dependencies and sequencing rules

- Reuse specs 003, 013, 028, 032 and the current CI/matrix service as inputs; do not
  duplicate their mechanisms. Guardian owns composition, policy, evidence, and verdicts.
- Close dangerous authority gaps before adding new mutation abilities.
- Add trace fields with each gate rather than retrofitting provenance after pilot data
  exists.
- Start design-partner recruitment during P0, but do not count adoption until an outside
  user operates a real plugin release evaluation.
- Gate broadening follows observed pilot gaps and evaluation evidence, not a catalog of
  every available Sandbox tool.

## 11. Decisions required before formal specification

1. Which deterministic security scanner and initial ruleset are supported and pinned?
2. What WordPress/PHP matrix is the default, and what makes a cell required versus
   optional?
3. Can Guardian ever update a Plugin Check/security baseline, or only produce a
   separately approved proposal?
4. What trace/evidence retention, privacy, local-only versus hosted storage, and cost
   budget apply to pilots?
5. Which plugin/team is the first design partner, and what real release policy defines
   the P0 vertical slice?

Until these are resolved through `speckit-refine` and an independent Sol High review,
this PRD remains a roadmap brief and is **not ready for `speckit-specify`**.
