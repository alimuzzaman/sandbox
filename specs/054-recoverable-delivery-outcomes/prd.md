# Product Requirements Draft: Recoverable Delivery Outcomes

**Status**: Ready for specification

**Created**: 2026-09-09

**Last Refined**: 2026-09-09

**Input**: Finish the saved Sandbox delivery repair packages. This feature covers W5 recovery eligibility before hosted effects, W7 complete queryable delivery outcomes, and the W1 creation/ownership links needed by those outcomes.

**Drafting Configuration**: `gpt-6-astra`, `xhigh`; bounded delegated drafting with one root integration owner, under the active repository model policy

**Final Validation**: `PASS` — independent `gpt-6-astra`, `medium` readiness review; no blockers

**Validated On**: 2026-09-09

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`, after the readiness gate passes

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

A developer or agent can begin a hosted deployment and only discover after an interruption that Sandbox retained no authority to recover it. Even when individual records exist, supported queries do not provide one complete answer to what was requested, what ran, which application revision is serving, and what remains safe to do. Deploy and preview can also report success after configuring a route without observing its public response.

This feature makes eligibility visible before protected deployment effects and makes each covered delivery outcome explainable after reconnect. It joins existing authority and observations; it does not create new permission to deploy, recover, or clean up resources.

The [saved repair plan](../../docs/audits/2026-09-08-sandbox-delivery-review/plan.md) and findings F05/F11/F12 in the [delivery review](../../docs/audits/2026-09-08-sandbox-delivery-review/report.md) establish the product gaps. Source was rechecked at `eec81ed`:

- Ordinary hosting's `_authenticated_machine_identity` requires complete resource evidence by default. `_accept_hosting_operation` returns no operation when durable context, clean matching source, identity, or bounded receipt requirements are missing. `_apply_host` then continues and can advance generation without that receipt. See [hosting command](../../sandbox/commands/hosting.py), those named functions.
- [Deploy exposure](../../sandbox/commands/deploy.py) and [preview creation](../../sandbox/commands/preview.py) configure routes and URLs without an independent public-route acceptance observation.
- Ordinary host status reports current records and the latest recovery summary; [image status](../../sandbox/hosting/images/activation/status.py) reports retained activation state without observing the runtime. Neither is a complete delivery history.
- The first [delivery repair candidate](../../docs/delivery-repair-implementation.md) already retains uncertain/reused instance data and binds URL updates to the selected instance. This feature adds the missing durable links without undoing that correction.

The separate [Amar Sonar deployment evidence](/Users/alim/Sites/git/amarsonar-bangla/.ai/implementation/2026-09-09-editor-modes-evidence.md) records a 2026-09-09 authorized apply that exited 1 after an initializer refusal. Five services were healthy and the requested asset was served, while recorded/deployed revision remained older and edge proof remained pending. The direct invocation supplied a request ID but had no durable job receipt. This is retained incident evidence, not an additional production observation by this feature; it demonstrates why a request flag, healthy services, and an exact asset cannot substitute for completed delivery or recovery authority.

The user confirmed on 2026-09-09 that deployments unable to create a recovery receipt must stop before changes. This feature applies that policy to all ordinary hosted apply calls and introduces no nonrecoverable mode.

## Users and Desired Outcomes

- **Developer or deployment operator**: Know before effects whether the intended deployment can be recovered, then obtain exact outcome evidence through supported commands.
- **Agent or application deployment frontend**: Reconnect using retained operation identities, distinguish acceptance from completion, and present a safe next action without reconstructing private state.
- **Reviewer or incident responder**: Find the latest recorded complete success and the latest attempt separately, with exact source, target, runtime, route, and evidence limits.

## Goals

- Make every ordinary hosted apply acquire durable recovery authority before protected deployment effects.
- Separate authenticated target identity from optional resource telemetry, while keeping unknown or unverifiable identity ineligible.
- Retain bounded, queryable outcomes that join request/job, application source, Sandbox revision, artifact, target, runtime, and requested exposure evidence.
- Report partial, failed, uncertain, and complete outcomes truthfully; make safe diagnosis possible after response loss.
- Preserve creation/reuse ownership through deploy and preview so failed work cannot claim another operation's resources.

## Non-Goals

- A replacement activation/recovery state machine, broader recovery authority, automatic effect replay, or relaxed identity, initializer, generation, source, and trust checks.
- A new build, image rebuild, deployment, controller update, production repair, restore, credential access, or destructive cleanup as part of PRD work.
- Broader backup/restore proof coverage (W6), the adjacent Lenzora shell entrypoint (W11), feedback closure (W9), or competing PostgreSQL verifier changes.
- Retrofitting every Sandbox command with delivery receipts, an unlimited audit archive, or certifying historical deployments whose evidence is missing.
- A new default URL provider, bypassing Docker/Caddy, changing optional runtime support, or changing existing exposure/pruning authority.

## Product Scenarios

### Scenario 1 — Recoverable apply with partial resource telemetry

- **Starting state**: A registered target has a valid authenticated stable identity; memory or swap observation is incomplete. Durable request/job and eligible exact source/config evidence are available.
- **User action**: Review a plan and submit the authorized recoverable apply.
- **Expected outcome**: The plan separates identity eligibility from resource/capacity checks. Missing optional telemetry alone does not invalidate identity. The operation either retains recovery authority before deployment effects or stops with a typed reason; separate required capacity policy still applies.

### Scenario 2 — Ineligible or changed authority

- **Starting state**: Identity is unknown, malformed, unsupported, or changed; durable/source evidence is missing; source is dirty or mismatched; or another operation owns the target.
- **User action**: Plan or attempt an ordinary hosted apply, including a direct invocation that supplies only a request-ID flag.
- **Expected outcome**: Plan identifies each known eligibility gap. Apply revalidates current authority and refuses before source transfer, runtime or route changes, or generation advancement. It gives a supported durable invocation and preparation steps tied to the original request when one exists. A request ID alone is not a durable receipt; direct and dirty-source callers have no silent nonrecoverable fallback.

### Scenario 3 — Receipt persistence or acceptance response is interrupted

- **Starting state**: A recoverable apply is being admitted, or its accepted response is lost near the first effect boundary.
- **User action**: Reconnect and inspect the original submission.
- **Expected outcome**: Uncommitted admission cannot permit protected effects. A committed admission remains discoverable using its retained identity. Missing or contradictory observations remain unknown; a new submission identity cannot conceal or replay the uncertain operation.

### Scenario 4 — Deployment stops after effects begin

- **Starting state**: An admitted operation has staged source or entered initializer, runtime, or edge work, then fails or disconnects. Some services or requested assets may already be healthy or visible.
- **User action**: Query its outcome and use the existing authorized recovery path if eligible.
- **Expected outcome**: The query retains the original request, known effects, failing phase, and recovery relation. Healthy services or a matching asset do not override an initializer refusal, unresolved runtime revision, or pending required edge proof. Read-only diagnosis has no workload effects. Recovery keeps its existing distinct recovery request bound to the original operation; observation cannot repeat an initializer, advance generation without proof, or supply missing edge authority.

### Scenario 5 — Route configured, public result incomplete

- **Starting state**: Deploy exposure or preview has written a hostname route and any authorized DNS/WordPress option changes.
- **User action**: Wait for the requested exposed-site outcome.
- **Expected outcome**: A finite verification deadline bounds DNS, TLS, redirect/query-string, and declared application-response checks for requested hostnames. A wrong backend, invalid certificate, unavailable route, or required stale edge proof produces an explicit partial or failed outcome with known effects and the same target identity. A configuration reload or generic HTTP 200 alone does not prove the promised release.

### Scenario 6 — Reconnect after success and after a later failed attempt

- **Starting state**: One covered delivery completed with required proof; a later attempt failed. Its job may run from a different Sandbox control revision than the application revision.
- **User action**: Query the exact operation or the target's latest outcomes through CLI or MCP.
- **Expected outcome**: One supported diagnosis query distinguishes the latest attempt, latest recorded complete success, and current observation. It joins application and control identities without substituting one for another. Any disagreement or stale observation remains visible.

### Scenario 7 — Lost creation response or reused instance

- **Starting state**: Two callers operate on labelled instances of one project; an ensure/create result is lost or one caller reuses an instance.
- **User action**: Inspect the affected deploy/preview outcome and retry the supported observation or continuation.
- **Expected outcome**: The creation link identifies request, exact project/label/incarnation, and created versus reused status when proved. Unproved ownership remains unknown. No inventory difference or receipt from a reused instance authorizes deletion; a still-running child is not treated as terminal. URL results refer only to the selected instance, including partial option writes.

### Scenario 8 — History absent, bounded, or from an older controller

- **Starting state**: A target has no complete history, retained detail has expired, a receipt is partial, or the controller lacks the new outcome capability.
- **User action**: Ask for its last successful delivery.
- **Expected outcome**: The query distinguishes absent, incomplete, expired, and unsupported evidence with bounds and a safe next action. It never manufactures success from a healthy current container, a local release file, an accepted job, or an older record with narrower claims.

## Proposed Product Behavior

- **Covered operations**: All ordinary hosted apply calls and their existing recovery outcomes; immutable hosted activation outcomes through their existing authority; deploy with requested exposure; preview creation; and the creation/reuse links those deploy/preview operations need. Pure source transfer and unrelated jobs retain their existing outcome scope.
- **Plan and admission**: Show recoverability, required evidence, known missing evidence, and prerequisites that can only be checked at submission. A plan is advisory evidence, not a durable admission or effect authority. Revalidate at the effect boundary. Admission-only persistence may establish authority; source staging/transfer, runtime changes, initializer work, route changes, and generation advancement cannot precede successful recovery admission for ordinary hosted apply. Non-durable, dirty, mismatched, or otherwise ineligible input refuses with concrete supported preparation/invocation guidance. The command never silently downgrades guarantees or creates a new request identity to bypass the refusal.
- **Truthful layers**: Acceptance, observation success, command execution, workload completion, runtime health, and exposure verification remain distinct. Preserve existing meanings of unrelated `ok` fields. A covered requested outcome is complete only when every applicable requirement is proven; non-applicable evidence must be distinguished from missing evidence.
- **Outcome evidence**: Retain exact operation/request/job and target/project/environment identities; relevant workspace or instance incarnation and ownership; application source and dirty-source policy; Sandbox source and installed runtime revisions separately; available immutable artifact/config/plan/proof identities; lifecycle and observation times; terminal state and known effects; recovery/retry relations; required runtime and route/edge evidence; completeness, boundedness, reason, and supported next action. Only evidence needed for the declared outcome is required; absent release proof cannot be replaced with a control checkout SHA.
- **Durability**: Retain completed and unsuccessful outcomes without silently replacing earlier terminal evidence when a later attempt starts. In-flight and uncertain admissions remain findable. Receipt persistence failure cannot claim completed durable evidence or trigger a replay. Repeated queries do not create competing terminal histories.
- **Public exposure**: Distinguish configuration from observation for the requested primary hostname and requested aliases. Honor the declared health/redirect/query/identity contract and verification deadline. If the project cannot prove a release identity at the route, report that limit; do not invent one. Apply required edge proof only where the requested outcome calls for it. Keep existing stronger immutable activation proof authoritative.
- **Supported diagnosis**: One read-only CLI/MCP query, selectable by exact operation or target, explains the latest attempt and latest recorded complete success, failing stage, known effects, evidence times, and safe next command. It supplies bounded continuation or detail references where needed. It must state whether it is returning recorded proof or making a current observation, and expose history completeness.
- **Ownership and disclosure**: Creation receipts and joined outcomes describe existing authority; they do not grant new cleanup rights. Preserve uncertain resources and reused data. Retain only bounded, secret-safe projections and opaque proof links; credentials, login tokens, raw environments, and private command payloads are excluded.

## Constraints and Dependencies

- The [constitution](../../.specify/memory/constitution.md) and [agent guide](../../AGENTS.md) require per-project ownership, the supported registry/services, idempotency, docs with code, and actual supported-workflow evidence. Public options and result/wire changes need declared compatibility, docs/tests, and revision/capability evidence before use.
- Existing [activation recovery](../051-immutable-activation-recovery/contracts/recovery-integration.md) and ordinary recovery have distinct authorities. A unified query cannot reinterpret legacy receipts, remove fences, widen an effect scope, or bypass registration, source, generation, secret-binding, trust, or no-replay checks.
- W1–W4 provide ownership retention, initializer fidelity, truthful readiness, and bounded job detail/history. This feature consumes those results and must expose their remaining acceptance limits. Local checks do not stand in for remote or public-route proof.
- Use registered capability and repository/service boundaries. No raw state-file reconstruction, facade consumers, raw SSH/Docker recovery workaround, or controller downgrade is an acceptable user workflow.
- Adjacent application frontends may attach exact release evidence through a supported contract. Missing frontend evidence is a visible gap; changes in another repository remain separately owned.
- Implementation must finish across its agreed files before exercising real commands; focused regression tests follow those runs. Production, deployment, controller updates, credential access, release, and cleanup retain their explicit authority and human review gates. This PRD authorizes none of them.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Ordinary apply admission | Require durable eligible authority before protected effects for every ordinary hosted apply; refuse when it cannot be retained | Prevent the demonstrated post-effect recovery dead end | Direct user confirmation, 2026-09-09: "Stop before changes (recommended)" |
| Partial resource telemetry | Accept only authenticated stable identity; optional telemetry does not define identity; independent resource policy remains | Separate identity from availability without weakening authority | W5; existing identity-only image path |
| Legacy caller compatibility | No nonrecoverable mode; refuse direct/non-durable or dirty-source apply when it cannot meet existing recovery admission requirements, with supported migration guidance | Make the requested receipt guarantee dependable for all callers | Direct user confirmation, 2026-09-09: "Stop before changes (recommended)" |
| Recovery and ownership | Keep existing refusals, distinct recovery identities, no-replay, and exact-incarnation requirements | Evidence joins must not add mutation or deletion authority | Existing AGENTS and recovery contracts; W1/W5 |
| Completion and history | Separate outcome layers; preserve historical unknowns and bounded history limits | Prevent accepted/configured/current-health results from certifying a full delivery | W7 and findings F11/F12 |
| Existing protocols | Extend supported projections; retain stronger activation proof and legacy meanings | Avoid a competing state machine or silent reinterpretation | W7; existing recovery contracts |

## Open Questions

- No blocking product decision remains. The user confirmed mandatory pre-effect refusal when a recovery receipt cannot be created.
- Independent readiness validation passed. Exact command naming, versioned wire shape, retention bounds, and verification deadlines belong to downstream specification/design; they must satisfy the observable outcomes here.

## Acceptance Outcomes

- **AO1 — Eligibility before effects:** For eligible input with partial optional telemetry, admission is retained before the first protected effect. Missing/changed identity, missing durable inputs (including direct invocation with only a request-ID flag), dirty/mismatched source, invalid config/receipt, ownership conflict, and persistence failure each produce a typed pre-effect result. Refused cases cause zero source transfers, initializer/runtime/route effects, and generation advances. Apply revalidation catches a target/source change after planning.
- **AO2 — Interrupted admission:** Interrupt immediately before and after admission persistence and lose the accepted response. Supported original-identity lookup distinguishes no admitted effects from retained or uncertain work without a second delivery identity or duplicated effect.
- **AO3 — Recovery remains bounded:** An interrupted eligible hosted operation can be diagnosed through its original identities and, where existing recovery permits, reconciled with the linked recovery request. A sentinel initializer executes at most once, generation advances only with existing authoritative proof, and foreign/stale/contradictory evidence remains fenced. Healthy services and an exact served asset do not turn an initializer failure or pending required edge proof into completed delivery.
- **AO4 — Complete exposure:** A successful exposed deploy/preview proves every requested hostname against its declared DNS/TLS/route/application requirements within a finite documented deadline. Negative cases include TLS failure, unavailable DNS, wrong backend, redirect/query errors, unhealthy application response, and stale required edge evidence. Each returns a non-complete result with known effects and retained target identity.
- **AO5 — Joined diagnosis:** After client reconnect, one supported query for a covered successful or failed delivery returns the exact identities and evidence needed to explain its requested outcome, failing stage, effects, times, and safe next action. Distinct application/control revisions remain distinct. CLI and MCP agree on their meaning without raw-state or log reconstruction.
- **AO6 — Historical truth:** After success A and failed attempt B, target diagnosis names B as latest attempt and A as latest recorded complete success, without presenting A as proof of current runtime state. Missing, truncated, expired, unsupported, and contradictory records never become complete success; all bounded pages/details declare completeness or its absence.
- **AO7 — Ownership preserved:** Two concurrent labelled-instance operations, creation response loss, a nonterminal child, reused data, and changed incarnation preserve exact request/target relations or an explicit unknown. No unrelated instance is mutated, and a reused instance never becomes cleanup-owned. Authorized URL changes and partial writes remain bound to the exact selected target.
- **AO8 — Safe compatibility and output:** Every ordinary apply caller either obtains eligible durable admission or refuses before protected effects with a supported next invocation; no nonrecoverable fallback is available. Older controller or receipt capability is reported before dependent effects; existing authority is not broadened. Full default text/JSON/MCP output, retained receipts, and failure diagnostics expose no synthetic credential or login-token values.
- **AO9 — Evidence required for release:** Record observed outcomes on authorized disposable hosted and deploy/preview fixtures with exact source/runtime versions, target identities, finite deadlines, terminal jobs, and public-route proof. Exercise the joined diagnosis for immutable activation outcomes as well as ordinary hosted applies. Source regressions cover the corresponding negative/interruption cases after real command runs. Unavailable protected or optional-runtime acceptance stays explicitly unverified.

## Risks and Assumptions

- **Compatibility risk**: The confirmed mandatory-recoverability policy changes behavior for direct callers and dirty development applies. Versioned behavior and clear preparation/durable-invocation guidance must accompany the refusal; unsupported older callers cannot silently proceed.
- **Evidence risk**: Runtime, edge, job, and frontend observations can be from different times or identities. A join must show gaps and contradictions rather than combine them into stronger proof.
- **Persistence risk**: Effects and local receipts can be interrupted independently. Retained uncertainty must fence replay even when a terminal receipt cannot be written.
- **Retention risk**: Bounded history can outlive or lose referenced detail. Missing proof cannot be recreated from current health, and expired history must not erase active-operation fences or ownership guards.
- **Assumption**: Existing registered services can expose bounded safe projections and exact identity links. An unsupported capability produces an explicit limit; this draft does not authorize private-state access as a fallback.
- **Assumption**: The user's repair goal authorizes preparing these product changes; actual protected acceptance and release require their separate explicit approval against a concrete candidate.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent readiness review verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
