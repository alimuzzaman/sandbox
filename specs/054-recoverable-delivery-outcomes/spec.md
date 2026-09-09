# Feature Specification: Recoverable Delivery Outcomes

**Feature Branch**: codex/sandbox-delivery-review-20260908

**Created**: 2026-09-09

**Status**: Deployment-trace scope clarified for plan refresh, 2026-09-09

**Input**: The complete approved [PRD](prd.md), covering saved delivery repair packages W5/W7 and the W1 ownership links they require. The user confirmed on 2026-09-09: ordinary hosted apply must stop before changes whenever it cannot create the required recovery receipt. No nonrecoverable mode is included.

## Clarifications

### Session 2026-09-09

- Q: Does the requested traceability mean issues linked to changes and tests? → A: No. The user explicitly requested traceability for deployments.
- Scope assumption stated to the user: cover the full top-level deployment command, including early preflight and release/artifact selection, rather than only its admitted apply or activation. The optional scope question has not received an answer; this is the stated working assumption, not a claimed user confirmation.
- Research and planning configuration: Sol XHigh researches the existing deployment owners; Astra XHigh creates and revises the plan from that research. Existing authority and release restrictions remain unchanged.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Know recovery is available before deployment changes (Priority: P1)

An operator reviews a hosted deployment plan and starts an authorized apply. Sandbox either retains the exact authority needed for supported recovery before deployment changes, or explains what is missing and stops.

**Why this priority**: Once changes begin without recovery authority, a later failure can leave the operator unable to safely reconcile the requested deployment.

**Independent Test**: Use an owned target with eligible and deliberately ineligible inputs. Observe the plan, admission result, and target effects; this story provides value without the new history query or public exposure workflow.

**Acceptance Scenarios**:

1. **Given** a valid authenticated stable target identity and eligible durable source/request evidence, but incomplete optional memory or swap telemetry, **When** the operator plans and applies, **Then** identity eligibility remains valid, any separate required resource policy remains visible, and successful recovery admission precedes every protected effect.
2. **Given** a direct invocation with only a request-ID flag and no required durable context, **When** the operator applies, **Then** Sandbox refuses before protected effects and gives a concrete supported durable invocation; the flag does not count as a receipt.
3. **Given** missing or mismatched durable evidence, dirty source, unknown/malformed/unsupported identity, or an invalid or unretainable admission receipt, **When** apply is attempted, **Then** Sandbox gives a typed refusal, creates no source/runtime/route effects, and does not advance generation.
4. **Given** a plan whose source or target authority changes before effects, **When** apply revalidates the operation, **Then** it refuses using the current evidence instead of relying on the earlier plan.
5. **Given** another active or uncertain operation owns the target, **When** a conflicting apply is attempted, **Then** the existing owner remains intact, no competing effects begin, and diagnosis points to the retained operation.
6. **Given** admission persistence is interrupted immediately before or after commitment, **When** the caller reconnects, **Then** no uncommitted admission authorizes effects, and any committed admission remains discoverable using its original identity.

---

### User Story 2 - Explain and safely continue an interrupted delivery (Priority: P1)

An operator or deployment frontend reconnects after a lost response or failed operation. One supported diagnosis query explains what was requested, what is known to have happened, what is uncertain, and the next permitted action.

**Why this priority**: Clear retained evidence prevents blind replay and repeated reconstruction of private state during the most consequential failure paths.

**Independent Test**: Inspect retained eligible ordinary-apply and immutable-activation success/failure fixtures. The diagnosis must explain each fixture without new workload effects, a fresh deployment, or manual joining of logs and state files.

**Acceptance Scenarios**:

1. **Given** the accepted response was lost while the original job remains active, **When** the caller queries the retained request or operation, **Then** it sees the original job, target, requested outcome, known progress, and uncertainty rather than a fabricated terminal success or a new submission identity.
2. **Given** a deployment failed after some effects, **When** the operator queries it, **Then** the result retains exact source/artifact/target links, the failing stage, known effect scope, evidence times and limits, and a supported next action.
3. **Given** healthy services or an exact served asset but an initializer refusal, unresolved required runtime revision, or pending required edge proof, **When** the outcome is queried, **Then** the requested delivery remains unsuccessful or incomplete despite those positive observations.
4. **Given** existing recovery permits reconciliation, **When** the operator uses that separately authorized recovery path, **Then** its distinct recovery request remains linked to the original operation, initializer execution is not duplicated, and generation changes require the existing authoritative proof.
5. **Given** foreign, stale, contradictory, or unverified observations, **When** diagnosis or recovery runs, **Then** the evidence gap stays visible and existing recovery fences remain; useful read-only diagnosis continues without inventing missing authority.

---

### User Story 3 - Receive a verified exposed-site result (Priority: P1)

An operator requests deploy exposure or preview creation and receives proof that the requested public route meets the declared application requirements, or a bounded result showing which configured changes remain unverified.

**Why this priority**: A route file, successful reload, or responding server does not establish that the requested site is usable or serving the intended application.

**Independent Test**: Use an authorized disposable deploy/preview target with a primary hostname, requested alias, and declared application checks. Observe successful exposure and controlled DNS, TLS, redirect, query-string, backend, and required-edge failures.

**Acceptance Scenarios**:

1. **Given** all requested hostnames resolve and satisfy their declared TLS, route and application checks, **When** verification completes within the declared deadline, **Then** exposure is verified and the outcome retains the target, evidence and observation times.
2. **Given** route configuration succeeded but DNS, TLS, redirects, query strings, or an alias check fails, **When** verification fails or reaches its deadline, **Then** exposure is non-complete and the result preserves each known effect and failed/unverified check.
3. **Given** a wrong backend, an unhealthy application response, or a healthy runtime with stale required edge proof, **When** exposure is evaluated, **Then** a successful reload or unqualified HTTP response cannot produce a verified exposed-site result.
4. **Given** the application does not offer public release-identity proof, **When** route verification succeeds, **Then** the result states the scope it proved and the missing identity evidence; it does not invent exact-release proof or satisfy an exact-release requirement with generic route health.

---

### User Story 4 - Find the latest attempt and retained success (Priority: P2)

A reviewer asks what last completed successfully for an exact target while also seeing newer attempts and any current observation. The answer makes historical limits explicit.

**Why this priority**: Retained history lets operators distinguish the last proven result from later failures and present health, without certifying unknown historical deployments.

**Independent Test**: Query a target containing success A followed by failed attempt B, differing application/control revisions, and bounded or expired evidence. This story can be checked against retained outcomes without running another deployment.

**Acceptance Scenarios**:

1. **Given** successful delivery A followed by failed attempt B, **When** target diagnosis is requested, **Then** B is the latest attempt and A is the latest retained complete success; neither substitutes for a current runtime observation.
2. **Given** a job used a Sandbox control checkout different from the application source, **When** the outcome is shown, **Then** the two revisions remain distinct and the application artifact/runtime join uses application evidence.
3. **Given** missing, expired, bounded, unsupported or contradictory history, **When** the last successful delivery is requested, **Then** the result states that limitation and never derives historical success from a healthy container, accepted job, or unattached release file.
4. **Given** retained ordinary apply and immutable activation results, **When** the same outcome is queried repeatedly through CLI and MCP, **Then** both interfaces agree on meaning, queries create no duplicate terminal results, and existing activation proof remains authoritative.

---

### User Story 5 - Keep creation and URL changes tied to the right instance (Priority: P2)

An operator inspects a deploy or preview that created or reused a labelled instance. Its outcome identifies the exact owned incarnation or explicitly reports that ownership is unknown.

**Why this priority**: Interrupted creation and concurrent callers must not turn inventory guesses into permission to change another instance or delete retained data.

**Independent Test**: Use two labelled instances with distinct data and URLs. Exercise created/reused cases, lost creation responses, a nonterminal child, incarnation drift, and a partial URL update through supported operations.

**Acceptance Scenarios**:

1. **Given** creation or reuse returns authoritative ownership evidence, **When** its deploy/preview outcome is queried, **Then** the request, project, label, exact incarnation, and created-versus-reused relation remain joined.
2. **Given** a response is lost, inventory is ambiguous, or the original child is not terminal, **When** another caller inspects or continues its own operation, **Then** ownership remains unknown where unproved and no unrelated or reused instance becomes cleanup-owned.
3. **Given** a reused selected instance and an unrelated instance, **When** an authorized URL update occurs, **Then** only the selected instance is changed and its values are read back; reuse never grants deletion authority.
4. **Given** only one required URL write completes or the selected incarnation changes, **When** the operation reports its outcome, **Then** the partial result or identity refusal remains tied to that request and no broad rollback mutates another instance.

---

### User Story 6 - Follow one deployment from invocation to its outcome (Priority: P1)

An operator runs the supported deployment command and receives a trace identity. From that identity, one supported read explains the requested release, preflight, artifact reuse or preparation, staging, activation, verification, and any separately authorized recovery. The operator does not need to reconstruct private files or confuse a successful inner step with the whole deployment.

**Why this priority**: Early failures and separate parent, preparation, activation, and recovery records currently leave gaps between the command the operator ran and the delivery outcome.

**Independent Test**: Start the literal supported deployment command on an owned fixture. Query its returned trace after early refusal, retained-image preparation, child acceptance loss, activation, and recovery. Confirm each link against its existing owner and distinguish requested from observed facts.

**Acceptance Scenarios**:

1. **Given** a valid project-scoped invocation and an available trace owner, **When** preflight succeeds or refuses before a release-bound run exists, **Then** its trace remains discoverable, records the preflight result, and shows later stages as not started; a successful preflight-only command is not a successful deployment.
2. **Given** the deployment owner establishes its existing parent, staging, activation, and job identities, **When** the trace is read, **Then** it follows those exact validated links without creating new workload identities, treating an environment variable as proof, or replacing the application revision with a control-checkout revision.
3. **Given** retained signed images must be reused, **When** the command progresses, **Then** the trace distinguishes reuse from a build, retains the exact selected artifact and observed deployment evidence, and creates no image build merely to obtain traceability.
4. **Given** failure or response loss before run creation or after child acceptance or effects, **When** the operator inspects or reconnects, **Then** the trace identifies the furthest proved stage, original known IDs and effects, and unavailable evidence; reconnect follows the existing owner rather than launching a replacement child.
5. **Given** a separate recovery result exists, **When** the original deployment or its trace is inspected, **Then** the retained recovery can be found within explicit history bounds, while the original failed result remains immutable and recovery metadata is not mistaken for evidence of its executor revision.
6. **Given** legacy history, expired detail, missing capabilities, mismatched child links, or bounded image/event detail, **When** the trace is read through CLI or MCP, **Then** both interfaces show the same exact facts and limitations with no fabricated early events, truncated-success claim, workload effect, or authority change.
7. **Given** bootstrap cannot reach the trace owner, **When** the command refuses, **Then** it explicitly reports trace unavailable rather than claiming a retained identity. If trace persistence becomes unavailable before protected effects, those effects do not begin; post-effect storage loss preserves the original owner and reports the trace gap.

### Edge Cases

- Admission or terminal-result storage is unavailable, oversized, or interrupted; known effects and uncertain persistence must remain distinct.
- A valid-looking request flag lacks durable admission; a malformed or reused identity cannot gain authority from its spelling alone.
- A terminal job transition races a status read; observation must not create a second operation or imply that acceptance was completion.
- Original and recovery requests differ by design but must remain linked; the same delivery identity with conflicting inputs must not silently represent another operation.
- Two observations disagree in revision, generation, target incarnation, or observation time; stronger evidence must not be assembled by ignoring the conflict.
- The public route succeeds for the primary hostname but fails for an explicitly requested alias, redirect destination, or query-string path.
- A generic HTTP 200 or authentication rejection proves a server answered but fails the declared application-health or exact-release requirement.
- Receipt history remains while referenced detail expires, or an older controller has no outcome capability; history completeness must be explicit.
- A failed preview retains an instance but may have some separately authorized route cleanup; the outcome must distinguish retained, removed, failed-cleanup and unknown effects without expanding cleanup authority.
- Default output, error output, continuation pages, and opaque evidence references must remain bounded and secret-safe even when underlying detail is large or malformed.
- An invocation trace exists before a release-bound deployment request; a later invocation may refer to the same existing run without becoming a new workload attempt.
- A wrapper job uses a different control checkout from its application; role-specific job links must not satisfy application admission or source proof.
- An invocation is abandoned while an authoritative child remains active or uncertain; trace retention must not erase the child owner or imply cancellation.

## Requirements *(mandatory)*

### Scope and Boundaries

This feature covers every ordinary hosted apply, its existing recovery outcomes, immutable hosted activation outcome diagnosis, deploy with requested exposure, preview creation, and the instance creation/reuse links needed by those deploy/preview operations. It also covers full-command diagnostic tracing through registered deployment producers, with the existing Lenzora deployment entrypoint as the first integration. Pure source transfer and unrelated jobs retain their current outcome scope.

Protected deployment effects mean source staging/transfer, runtime or initializer changes, route changes, and deployment generation advancement. Admission-only persistence may establish recovery authority; it does not count as a completed deployment.

This feature excludes a new activation/recovery state machine, new effect-replay or cleanup rights, broader restore proof, image rebuilding, historical success backfills, unlimited archival history, and unrelated application entrypoint or feedback work. Existing per-project targeting, URL-provider defaults, optional-runtime boundaries, and explicit release/deployment/credential/destructive-action authority remain.

### Functional Requirements

- **FR-001**: Every ordinary hosted apply MUST retain eligible durable recovery admission before its first protected deployment effect. No nonrecoverable mode or silent fallback is permitted.
- **FR-002**: Planning MUST state recovery eligibility, known missing evidence, independent prerequisite failures, and evidence that can only be established at submission. A plan MUST NOT be presented as admission or effect authority.
- **FR-003**: Recovery identity eligibility MUST rely on authenticated stable target identity independently of optional memory/swap telemetry. Unknown, malformed, unsupported, or changed identity MUST remain ineligible; separate required resource/capacity policy MUST still apply.
- **FR-004**: Apply MUST revalidate exact target, source, configuration, required durable request/job context, and current ownership before protected effects. A request-ID argument alone MUST NOT establish admission.
- **FR-005**: Missing, mismatched, dirty, conflicting, or unretainable recovery inputs MUST cause a typed pre-effect refusal with concrete supported preparation/invocation guidance. Refusal MUST NOT advance deployment generation, silently change the source policy, or create a new identity to bypass the problem.
- **FR-006**: Admission persistence failure MUST prevent protected effects. If a response or persistence acknowledgment is lost, Sandbox MUST preserve the original identity and report what is known, unknown, or retained; it MUST NOT infer success or absence merely from missing output.
- **FR-007**: An accepted, running, interrupted, or uncertain operation MUST remain discoverable through its retained identity. A competing or conflicting operation MUST NOT overwrite the existing owner or acquire its authority.
- **FR-008**: Outcome diagnosis MUST preserve existing ordinary and immutable recovery authority, their distinct linked recovery requests, and their identity/generation/initializer/edge fences. It MUST NOT repeat workload effects or fill gaps in authority through observation.
- **FR-009**: Public outcomes MUST distinguish request acceptance, successful observation, command execution, workload completion, runtime health, exposure verification, and evidence completeness. A fully explained terminal failure MUST NOT be represented as a successful delivery.
- **FR-010**: Each covered outcome MUST retain exact applicable operation/request/job and target/project/environment identities, relevant workspace or instance incarnation, creation/reuse relation, application source and dirty-source policy, Sandbox source and installed-runtime revisions, immutable artifact/configuration/plan/proof links, lifecycle and observation times, phase, terminal result, known effects, and recovery/retry relation. Applicable runtime evidence MUST identify observed generation/services/images, installed state and declared health; exposure evidence MUST identify requested hostnames, route/redirect responses, TLS result and required edge proof.
- **FR-011**: Evidence MUST state its source, observation time, applicability, completeness, and safe reason for missing or unknown values. A non-applicable evidence dimension MUST be distinguishable from a missing required dimension. Application and Sandbox control revisions MUST never substitute for each other.
- **FR-012**: A covered delivery MUST be reported as successful only when every requirement of its declared requested outcome has authoritative proof. Existing stronger immutable activation evidence MUST remain authoritative; healthy services, an exact asset, a local release record, or accepted work MUST NOT replace missing initializer, runtime, target, or required edge proof.
- **FR-013**: Users MUST have one supported read-only diagnosis query, selectable by exact operation or exact target, that explains the latest attempt, latest retained complete success, applicable current observation, failing stage, known effects, evidence times/limits, and the next safe command or explicit absence of an authorized continuation.
- **FR-014**: The diagnosis query MUST identify whether it returns recorded proof or obtains a current observation. It MUST cause no workload effects, authority changes, generation changes, duplicate outcomes, or automatic recovery. Missing proof MUST NOT prevent useful bounded diagnosis of known state.
- **FR-015**: Later attempts MUST NOT silently replace earlier retained terminal outcomes. The latest attempt and latest retained complete success MUST remain separately identifiable within the declared retention window, even when the latest attempt failed.
- **FR-016**: History, detail and query output MUST have documented finite bounds and explicit completeness/continuation semantics. Missing, partial, truncated, expired, unsupported or conflicting history MUST not be presented as complete. Retention MUST NOT remove active-operation ownership, uncertainty fences, or replay/cleanup guards.
- **FR-017**: Evidence joins MUST require exact matching identities and report mismatches or unavailable links. They MUST NOT certify old success from present health or combine incompatible observations into a stronger proof.
- **FR-018**: Requested deploy/preview exposure MUST distinguish route configuration from verification. Every requested primary hostname and alias MUST satisfy the declared DNS, TLS, redirect, query-string and application-response obligations before the exposed-site outcome is successful. A redirect status alone MUST NOT pass verification; its destination and query handling MUST satisfy the declared route policy.
- **FR-019**: Exposure verification MUST terminate within a finite declared deadline with a verified, failed, or explicitly incomplete result. It MUST retain the exact target and known configured, observed, cleaned-up and uncertain effects when verification or cleanup is partial.
- **FR-020**: Public-route proof MUST check intended backend/release identity where the requested application contract provides and requires it. Generic availability or an unqualified response MUST NOT satisfy application health or exact-release requirements. Unsupported identity proof MUST be visible and MUST NOT be fabricated.
- **FR-021**: Required edge proof MUST remain separate from runtime health and origin-route proof. It MUST only be required when applicable to the requested outcome, and pending/stale required edge evidence MUST prevent full delivery success.
- **FR-022**: Creation/reuse evidence used by deploy/preview MUST bind the original request to the exact project, label, instance incarnation and created-versus-reused state when proved. A lost response or inventory difference MUST leave unproved ownership unknown.
- **FR-023**: Joined creation/outcome evidence MUST NOT grant new cleanup authority. Reused instances MUST never become cleanup-owned; uncertain or nonterminal creation MUST not authorize deletion. Existing cleanup requirements for a terminal original operation and independently verified exact ownership MUST remain.
- **FR-024**: Authorized URL changes and readback MUST target only the selected exact instance. Partial required writes or incarnation drift MUST yield a typed result tied to that request; unrelated instances and retained data MUST remain unchanged.
- **FR-025**: CLI and MCP MUST agree on outcome meaning, identifiers and evidence limits. New public behavior MUST declare compatibility and required capability before dependent effects; unsupported older controllers/callers MUST produce a typed limitation instead of silently weaker guarantees. Unrelated existing result meanings MUST remain unchanged.
- **FR-026**: Default, failure, retained, paginated and detail output MUST use bounded secret-safe evidence. Credentials, login tokens, raw secret environments and private command payloads MUST NOT be exposed or retained in delivery outcomes; safe opaque references MAY link to separately authorized evidence.
- **FR-027**: Users MUST obtain diagnosis and permitted continuation through supported commands without raw private-state reconstruction or a controller downgrade. Outcome evidence MUST NOT authorize deployment, image rebuilding, credential access, production change, release, or destructive cleanup beyond existing explicit authority.
- **FR-028**: Release evidence MUST distinguish source checks, recorded authority, actual ordinary-host and immutable-activation diagnosis, deploy/preview public-route acceptance, and unavailable protected or optional-runtime checks. A configured lane or accepted job MUST NOT count as a completed acceptance result.
- **FR-029**: A supported deployment producer MUST retain a project-scoped diagnostic invocation identity before its first preflight result when the trace owner is reachable. It MUST distinguish that identity from the later release-bound parent, stage, activation, job and operation IDs, and report trace unavailable when bootstrap prevents retention.
- **FR-030**: Full-command trace MUST retain observed stage transitions and their times, requested source and artifact policy, producer/control identity, exact owner-validated parent and child links, command result and deployment result separately. Not-started, active, failed, unknown and non-applicable stages MUST remain distinguishable. Application-bound and control-wrapper jobs MUST retain their separate roles.
- **FR-031**: One supported project-scoped, same-controller query MUST expose the joined deployment trace through CLI and MCP without private-file reconstruction. Registered producers and existing owner services MUST supply bounded validated projections; arbitrary query-supplied code, unverified IDs and current health MUST NOT supply historical proof.
- **FR-032**: Trace persistence, terminal records, stage history, related recoveries and query output MUST have finite declared limits. Reads MUST NOT create, migrate, prune or rewrite trace or workload records. Missing or omitted detail MUST be explicit; a failed original outcome MUST remain unchanged after recovery, and absence of retained recovery detail MUST NOT prove no recovery occurred.
- **FR-033**: Trace creation or required pre-effect recording failure MUST prevent dependent protected effects. An uncertain acknowledgement MUST retain the original trace/request identity for lookup before any replay. Post-effect recording failure MUST preserve existing workload authority and report known effects and the trace gap; diagnostic records MUST never grant retry, recovery, cleanup, image-build or deployment authority.
- **FR-034**: Legacy deployment records MAY be projected from existing validated owner links, but early history that was never recorded MUST remain unavailable. A new invocation may refer to an existing deployment run without changing its immutable requests, source policy, recovery fences or terminal child evidence. Inherited original-deployment metadata MUST not be represented as fresh recovery-executor evidence.

### Key Entities *(include if feature involves data)*

- **Requested delivery outcome**: The exact target and scope the operator authorized, including the selected application source/artifact and applicable runtime, route and edge requirements.
- **Recovery admission**: Durable pre-effect evidence linking an ordinary apply to eligible request/job, target, source, configuration and existing ownership authority. Its existence is distinct from job acceptance and delivery success.
- **Delivery outcome**: Retained evidence of an attempt, including its admission links, progress/terminal state, known effects, required proofs and evidence limits. It may describe a successful, failed, cancelled, active or uncertain attempt without changing existing execution states.
- **Creation/reuse receipt**: The request-bound relationship between a deploy/preview and an exact project/label/incarnation, including whether it was created, reused or remains unknown. It records existing ownership and does not grant new cleanup rights.
- **Observation**: A time-bound statement about a specific runtime, route, edge or record with exact identity, result, applicability and evidence quality; it is not independent mutation authority.
- **Recovery relation**: The link from the original delivery to an existing recovery attempt and its distinct request/result, preserving the original authority and effect scope.
- **Outcome history**: Bounded retained attempts and their detail/completeness boundaries, supporting separate latest-attempt and latest-retained-success answers.
- **Deployment trace**: One diagnostic command invocation and its bounded stage observations, linked to an existing release-bound deployment run when available. It is distinct from workload authority and can end at preflight without a deployment.
- **Role-specific owner link**: A validated relation to an existing parent, stage, prepare job, activation job, delivery operation or recovery, preserving its project, request and source role and stating unavailable or mismatched binding.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every defined ineligible ordinary-apply case stops before protected effects: zero source transfers, initializer/runtime/route changes, or generation advances. Every admitted positive case retains recovery authority before its first such effect.
- **SC-002**: For every defined interruption immediately before/after admission and after effects begin, the operator can locate the retained or explicitly uncertain original operation without creating a replacement delivery identity; permitted reconciliation executes a sentinel initializer at most once.
- **SC-003**: After reconnect, one supported diagnosis query explains each ordinary-apply and immutable-activation success/failure fixture with its exact applicable identities, stage, effects, times, limits and next safe action, requiring zero manual joins of private files or raw logs.
- **SC-004**: Every requested primary hostname and alias either satisfies its declared public-route/application obligations within the declared deadline or returns a non-complete result. All listed negative controls remain non-success even when configuration or another hostname succeeds.
- **SC-005**: For success A followed by failed attempt B, every target query identifies B as latest attempt and A as latest retained complete success while both remain in retention, and reports current observations separately. All missing/expired/unsupported/conflicting-history cases produce zero invented historical successes.
- **SC-006**: Across two concurrent labelled-instance operations, response loss, reuse, nonterminal creation and incarnation drift, zero unrelated instances or data are changed, and zero reused or unproved instances become cleanup-owned. All partial URL results retain the correct request/target relation.
- **SC-007**: Repeated CLI and MCP diagnosis of the same retained outcome agrees on identity and meaning, creates zero new workload effects or duplicate terminal outcomes, and states the completeness of every bounded page/detail response.
- **SC-008**: Every affected direct/legacy caller either meets mandatory durable admission or receives pre-effect refusal with a supported next invocation. All default/failure/retained outputs in the synthetic sensitive-value cases contain zero credential or login-token values.
- **SC-009**: Each release claim has observed exact-candidate evidence for its affected authorized ordinary-host, immutable-diagnosis and deploy/preview route scenarios; every unavailable protected or optional-runtime acceptance remains explicitly unverified.
- **SC-010**: Every defined full-command fixture returns a retained trace or explicit trace-unavailable refusal. From the returned trace, one supported query identifies its preflight/command result, furthest proved stage, exact applicable parent/child/source/artifact identities and required verification gaps, with zero manual private-file joins.
- **SC-011**: The retained-image, response-loss, reconnect, wrong-link and recovery fixtures create zero unauthorized builds, duplicate workload requests or initializer executions, application/control substitutions, or historical terminal rewrites. CLI/MCP trace output agrees on role, identity, outcome and history limits in every fixture.

## Assumptions

- Existing registered project/target, source, job, runtime, route, staging and recovery services remain the authorities for their evidence. This specification adds user-visible admission and joined outcomes without assigning authority to diagnostic copies.
- The full-command extension reuses existing validated deployment-run and phase-job associations. It adds early diagnostic retention and a supported joined view, not a replacement deployment orchestrator or cross-controller search service.
- Mandatory receipt admission is the confirmed compatibility decision. Direct and dirty-source callers may need different preparation or a supported durable invocation; keeping an unsafe legacy path is not a compatibility requirement.
- Existing W1–W4 ownership retention, initializer checks, readiness and bounded job history are dependencies. Their unperformed remote/platform acceptance remains unverified until observed; this feature does not inherit a broader proof claim.
- Exact command names, wire versions, output/retention bounds and verification-deadline values are downstream design choices. They must remain finite, documented, observable, and sufficient for the acceptance scenarios; no unlimited retention, unbounded wait or undocumented truncation is allowed.
- Application-specific route identity and edge obligations come from the requested application's declared contract. A contract that lacks identity proof can establish only its supported scope; it cannot satisfy a separately requested exact-release claim.
- No new regulatory, localization, graphical-interface, or optional-runtime commitment is introduced. Existing supported CLI/MCP presentation and authorization conventions apply.
- The [constitution](../../.specify/memory/constitution.md), [agent guide](../../AGENTS.md), and [existing activation recovery contract](../051-immutable-activation-recovery/contracts/recovery-integration.md) remain constraints. Required proof uses supported commands, with implementation completed before actual command exercises and focused regressions afterward.
- This specification does not authorize any production operation, deployment, controller update, credential access, cleanup, or release. Such acceptance needs its existing separate authority against the concrete candidate.
