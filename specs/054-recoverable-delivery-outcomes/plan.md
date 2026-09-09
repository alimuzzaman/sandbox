# Implementation Plan: Recoverable Delivery Outcomes

**Branch**: codex/sandbox-delivery-review-20260908
**Date**: 2026-09-09
**Spec**: [spec.md](spec.md)
**Status**: Core US1–US5 implementation exists in the active dirty batch; historical acceptance remains incomplete. US6 and the remaining output corrections below are designed for independent analysis and are not implemented.

## Summary

Ordinary hosted apply must retain valid recovery authority before deployment changes. A supported delivery query then explains ordinary apply, immutable activation and exposed deploy/preview attempts without rebuilding their evidence by hand. Exposed-site success requires bounded public-route and application proof. Instance creation/reuse and URL changes remain tied to the original request and exact incarnation. Nested source keeps the original job/source commit `C` distinct from the deployed Git artifact commit `T` through one frozen typed binding.

Use existing job, hosting, activation, instance and edge owners as authorities. Add read-only owner projections and a bounded diagnostic journal, never another activation state machine. The user explicitly approved pre-effect refusal for ordinary apply without a recoverable receipt; there is no nonrecoverable path.

The accepted full-command scope now also retains one early diagnostic invocation from preflight through the existing Lenzora retained-image deployment. A fixed producer pushes validated owner projections into Sandbox; one same-controller CLI/MCP trace query joins them to existing job, activation, delivery and recovery owners. This adds visibility before an activation exists while keeping workload request identities and authority unchanged. See [Deployment trace v1](contracts/deployment-trace.md) for the normative schema, ports and limits.

## Technical Context

**Language/Version**: Python 3.9+ repository baseline; Lenzora W11 uses its existing TypeScript/Node 24.18.0 deployment entrypoint, with only a bounded launcher failure message where TypeScript cannot start.
**Primary Dependencies**: Standard-library sqlite3, dataclasses, hashlib, urllib/http, ssl, subprocess and existing Sandbox service/registry/remote/redaction helpers. No new third-party dependency.
**Storage**: Existing job SQLite and owner-managed hosting/instance/activation state; new owner-managed diagnostic SQLite under the executing controller's Sandbox home. No direct consumer of another owner's JSON.
**Testing**: Existing unittest harness, synthetic subprocess environment helpers, CLI/MCP contract checks and real supported workflow evidence after implementation.
**Target Platform**: macOS/Linux controller and existing Linux Docker/Caddy registered remotes. Optional native runtimes remain unsupported/unverified where current capability says so.
**Project Type**: CLI and MCP developer/runtime tooling.
**Performance Goals**: Recorded queries use bounded reads with a two-second database busy bound, at most 256 KiB output and no network by default. Public verification has a 120-second default aggregate deadline, configurable 10–300 seconds, including DNS/TLS and worker cleanup.
**Constraints**: At most 128 KiB per operation, 64 terminal outcomes/target, 512 globally, 30-day terminal detail retention and 128 protected open/pinned records. Permanent delivery request guards: 4,096/controller within the 96 MiB database cap. Permanent creation guards: 4,096/controller in an 8 MiB instance-owner database. Both fail closed for new keys at capacity, with bounded rollback space. Exact identities, no secrets, no effect/recovery on query.
**Scale/Scope**: W5/W7, required W1 request/incarnation relation, bounded nested-source correction, and US6's W11 deployment-trace adapter. No W10, unrelated app entrypoint work, image rebuild, historical backfill, broad restore/cleanup, dependency upgrade or provider changes.

## Constitution Check

| Gate | Design result |
|---|---|
| I: Per-project targeting | PASS. Exact project/root/remote/environment or label selection; no fallback instance. |
| II: Registry authority | PASS. Instance owner records creation relations; callers use owner services and exact incarnation. |
| III: Modular single entry | PASS. New delivery package, CommandSpec and explicit MCP/config manifests; no new legacy facade consumers. |
| IV: Real workflow evidence | PLANNED, not yet proven. Quickstart names required source, real command, remote/runtime and public route evidence separately. |
| V: Idempotency and docs | PASS by design. Stable request/intent joins, immutable terminal snapshots, no write-on-read, docs in the same implementation. |
| VI: Parity before removal | PASS with the user's explicit compatibility decision. Mandatory receipt removes the unsafe ordinary fallback only; image/recovery authority, Caddy defaults, optional runtime gates and unrelated results stay intact. |

Pre-research and post-design review reach the same result. The active AGENTS execution order controls: all agreed production/config/docs edits first; actual supported workflows second; focused regressions and required gates third. No generic test-first task may override it. Production/controller updates, credentials, deployment, DNS and destructive operations retain their separate existing authority. This plan itself grants none.

## Project Structure

~~~text
specs/054-recoverable-delivery-outcomes/
  prd.md, spec.md, checklists/requirements.md        existing accepted inputs
  plan.md, research.md, data-model.md, quickstart.md
  contracts/admission.md, diagnosis.md, creation.md, routes.md
  tasks.md

sandbox/delivery/
  __init__.py, models.py, repository.py
  admission.py, service.py, routes.py, route_worker.py
  context.py, manifest.py, hosting.py, exposure.py    composition, capabilities, workflow writers
sandbox/config/delivery.py                         new config provider
sandbox/config/manifest.py                         explicit registration
sandbox/commands/delivery.py                       new owned CLI
sandbox/commands/manifest.py, sandbox/cli.py        composition/legacy args
sandbox/commands/hosting.py                        ordinary admission and hooks
sandbox/commands/deploy.py, preview.py              exposure and receipt hooks
sandbox/resources/context.py                      public identity-only accessor
sandbox/jobs/registry.py                           bounded read-only evidence
sandbox/jobs/process.py                            genuine Linux/macOS boot identity
sandbox/application/job_service.py                 unknown/legacy identity cannot prove process loss
sandbox/jobs/health.py, scheduler.py                unknown status and nonterminal lease retention
sandbox/hosting/recovery/repository.py             owned read-only projection
sandbox/hosting/recovery/models.py                 closed source-artifact codec
sandbox/hosting/images/activation/status.py        pure diagnostic projection
sandbox/core/_instances.py, _remote.py              creation/URL ownership seam
sandbox/commands/instances_cmd.py                   structured ensure/lookup transport
sandbox/commands/config_setup.py                    covered apply context propagation
sandbox/application/context.py, runtime_service.py runtime context/capability propagation
sandbox/runtimes/compose.py                        generic pending incarnation owner
sandbox/server_config/models.py                    receipt validation
sandbox/server_config/creation_requests.py         durable instance-owner guards
mcp/wp-server/tools/delivery.py                    injected diagnostic group
mcp/wp-server/tools/manifest.py, remote.py
mcp/wp-server/server.py                            explicit composition

docs/delivery-outcomes.md                          new public contract guide
docs/remote-hosting.md, remote-job-runtime.md
docs/sandbox-config-reference.md, README.md
skills/sandbox-cli/SKILL.md
tests/test_delivery_*.py                           only after actual workflows
tests/test_hosting_recovery*.py
tests/test_remote_deploy*.py, test_preview*.py
tests/test_job_registry.py
~~~

**Structure decision:** A small feature-owned service consumes explicit owner projections. Each new module has one job: closed data, bounded persistence, admission validation, joined query or public route observation. Existing workflow adapters keep their runtime policy. Do not add generic plugin frameworks, schema registries or unused abstraction layers.

## Contracts and invariants

The [data model](data-model.md) defines the complete closed fields and finite bounds. [Admission](contracts/admission.md) fixes ordering and genuine durable context. [Diagnosis](contracts/diagnosis.md) fixes query/retention/CLI/MCP meaning. [Creation](contracts/creation.md) fixes request/incarnation/URL behavior. [Routes](contracts/routes.md) fixes configuration, proof and deadline. [Deployment trace](contracts/deployment-trace.md) fixes the separate early-invocation owner, producer publication, exact native joins and query union.

1. Environment/request spelling never establishes job authority. The retained application-bound running child must match, including live process identity/group. Application source and Sandbox control revisions remain distinct.
2. Authenticated stable identity accepts only known/partial/unmanaged states; independent required resource policy remains enforced.
3. Every ordinary apply commits and reads back existing recovery admission before source/runtime/initializer/route/generation effects. A journal record cannot replace it.
4. Existing active/uncertain owners survive failed admission, response loss, diagnostics and retention. No fresh identity or competing operation bypasses a fence.
5. Existing authoritative terminal facts produce immutable diagnostic snapshots. Reads never reconcile, migrate, prune, archive, backfill or infer old success from present health.
6. Before overwriting a previous terminal authority record, command writers preserve its terminal snapshot or refuse. Post-effect storage failure remains a visible partial history gap.
7. Exposure configuration and verification are separate. Every requested host and required application/release/edge check must pass within one finite deadline.
8. Creation relations come only from the registry owner's commit. A frozen nonsecret intent digest binds every receipt/transport. A durable request guard precedes the registry commit; a crash between those stores is unknown and cannot replay. URL writes/readback revalidate exact incarnation, and no receipt grants cleanup.
9. Delivery and creation request guards survive detail/target/instance eviction, never expire or evict, and refuse new keys at their finite caps. An expired request cannot start another attempt; changed intent conflicts before effects even after detail expiry.
10. Nested source binds original source/job commit `C` to a deterministically derived deployed artifact commit `T` before admission. The frozen typed mapping is identical in recovery and delivery owners; `C` joins job/admission evidence, while runtime/edge evidence requires independently observed `T`. Legacy identity records remain byte-stable and never infer a missing subtree mapping.

## Work packages and one-writer ownership

One Astra integration owner owns final acceptance and all shared-file integration. Use Astra Low/Medium for coupled implementation and substantive review; Luna Max may implement a bounded new module from these contracts. No Sol. Parallel writers get disjoint paths; any shared-file change goes through the integration owner. All delegates return files changed, checks actually run, results and limits. During the initial coding phase their checks are read-only review only: no test or feature acceptance execution.

| Package | Outcome and owned production paths | Dependencies / model boundary |
|---|---|---|
| A: Admission and owner projections | A direct apply that previously ran without recovery now refuses before effects. Own sandbox/delivery/admission.py; resources/context.py; jobs/registry.py; jobs/process.py; hosting/recovery/repository.py; hosting/images/activation/status.py. Add read-only evidence seams, genuine Linux/macOS boot-session observation and strict context/identity validation; legacy guessed boot identities never authorize signaling or rebinding. | Astra integration owner or Astra Medium. Contracts/models from B can be agreed before parallel edits. Does not own CLI/hosting integration files in F. |
| B: Closed models and bounded journal | Retain success A after failure B, with exact loss/retention semantics. Own sandbox/delivery/__init__.py, models.py, repository.py. Implement schema/read-only readers/idempotent terminal records/pinning/capacity. | Luna Max is suitable after contract acceptance. No owner-state reads or workflow mutation. |
| C: Query service and presentation module | One query explains original ordinary/image attempts and next safe action. Own sandbox/delivery/service.py; sandbox/commands/delivery.py; mcp/wp-server/tools/delivery.py. Consume injected owner projections only. | Astra Medium or bounded Luna Max. Depends on A/B interfaces; F owns registration. |
| D: Route contract and finite observer | Exposed result verifies all aliases and declared application checks. Own sandbox/config/delivery.py; sandbox/delivery/routes.py and route_worker.py. | Astra Medium for worker/deadline semantics; Luna Max can implement normalized schema from the fixed contract. F registers provider and invokes it. |
| E: Creation and URL evidence | Created/reused and partial URL writes remain linked to frozen intent and exact incarnation. Own sandbox/core/_instances.py, sandbox/core/_remote.py, sandbox/server_config/models.py, sandbox/server_config/creation_requests.py, sandbox/commands/instances_cmd.py, sandbox/application/context.py, sandbox/application/runtime_service.py and sandbox/runtimes/compose.py. Carry optional structured context through supported ensure/apply callers; check selected-runtime capability before effects. Both WordPress and generic Compose reserve guards before an atomic pending/selected incarnation-and-receipt commit; absent/expired receipt never permits replay. | Astra integration owner or Astra Medium. One writer for these coupled files, after reading existing W1 changes. F alone owns sandbox/cli.py parsing/predispatch changes. No new deletion behavior or generic exposed-deploy downgrade. |
| F: Workflow and manifest integration | Apply/activation/deploy/preview emit the same bounded outcome; old callers receive typed capability/refusal. Own sandbox/commands/hosting.py, deploy.py, preview.py; sandbox/cli.py; sandbox/commands/manifest.py; sandbox/config/manifest.py; mcp/wp-server/tools/manifest.py, remote.py; mcp/wp-server/server.py. | Integration owner only. Depends on A–E. Preserve current dirty work; integrate hooks after contracts stabilize. |
| G: Public documentation | Users can invoke, diagnose and understand proof limits. Own README.md; docs/delivery-outcomes.md, remote-hosting.md, remote-job-runtime.md, sandbox-config-reference.md; skills/sandbox-cli/SKILL.md. | Luna XHigh mechanical docs from implemented behavior; integration owner verifies. Finish before real acceptance. Root updates managed agent context separately. |

No source test file belongs to A–G. Test ownership is assigned only after all packages and actual command exercises are complete. If source requires another production path or contradicts a fixed contract, report it and revise this plan before assigning another writer; do not weaken a guarantee to stay inside a file list.

**Bounded caller correction:** Remote ensure invokes `sb ensure --local`, whose supported path is instances_cmd.py → RuntimeService → the selected adapter. OperationRequest.arguments carries optional creation_context and expected_incarnation unchanged; the WordPress composition closures forward them to the owner, while ComposeAdapter consumes the same contract directly. Existing WordPressAdapter forwarding needs no new wrapper or facade consumer. E owns narrow read-only capability/receipt access through the application boundary; F adds the bounded structured CLI input and pure lookup/capability modes before compatibility writers. Lookup must not invoke ensure or construct mutating runtime state. Generic Compose gains a project-locked, allocation-serialized pending incarnation/receipt commit before overlay/start effects, retaining that identity on success and failure. Existing generic records without an incarnation receive no invented historical creation claim. Unrelated ensure remains additive-compatible; current generic exposed success stays supported once required proof passes. The existing install snapshot implementation remains in sandbox/commands/data.py and is outside E ownership.

These are observed caller/owner omissions within FR-022–024 and the existing admission requirement, not new feature scope. Prior U1/I1 closure remains intact: both permanent 4,096-entry deny-only guards, their finite storage caps, no expiry/reset/replay and the ordered two-store crash refusal are unchanged.

**Nested source binding correction:** The accepted bounded correction is recorded in the [nested-source binding plan](../../tmp/054-delivery-acceptance/nested-source-binding-plan.md). It adds the closed `source.artifact` / `application.source_artifact` mapping and `source_schema=2` while preserving legacy `source_schema=1`. The source owner derives `T` with the existing `_source_tree_commit`, compares its tree to `C` before effects, freezes the T-derived configuration, publishes literal `T`, and requires the returned SHA to match before reset, runtime, initializer or edge work. Recovery reads actual remote source independently; the requested `T` is never substituted as observed evidence.

Ownership is bounded: `sandbox/hosting/recovery/models.py` owns the pure codec; Package A owns recovery projections/policy; Package C owns delivery model/service joins; Package F owns the shared hosting writer and capability registration. The integration owner controls the coupled `hosting.py` changes. This correction does not add a job schema, cleanup authority, source-root migration or transport rewrite.

## Implementation sequence

1. Root runs managed agent-context update and independent speckit-analyze on this complete design/tasks bundle. Resolve actual blocking contradictions in owned artifacts before implementation.
2. Define B models/interfaces, then implement A/B/D/E in disjoint files where useful. C consumes the finished projection contracts. F wires the complete flows and manifests; G updates matching public docs.
3. For the nested-source correction, prepare and validate `C`/`T`, selected prefix/tree equality, and the T-derived final configuration before recovery admission. Commit/read back `C` plus the artifact mapping, then publish literal `T` and assert the returned SHA before any reset, runtime, initializer, route or edge effect. Preserve null source evidence until an independent observation proves `T`.
4. Finish all agreed production/config/docs edits. Review the stable diff for missing call sites, unsafe serialization, callback failure paths, capability skew and protected-effect ordering. This is inspection, not test execution.
5. Exercise supported commands as in quickstart. Read-only/default refusal/fixture diagnosis can run immediately after coding. Protected remote/runtime cases run only with the existing concrete authorization and capability/revision proof. Observe defects, correct the complete affected production set, then repeat the affected actual command.
6. Only after those command exercises, author focused regression tests from the observed boundaries and run focused plus repository-wide required gates with finite durable jobs.
7. Independent correctness and product-output review consume the stable diff and observed evidence. Integration owner fixes supported findings and reruns only affected commands/gates, then records exact proof/limits and performs the repository-required commit/push of the completed scope.

## Acceptance matrix

| Area | Fixtures / observed action | Required assertions and disproof case |
|---|---|---|
| Admission | Valid clean durable app child on Linux/macOS; genuine boot-session identity, unavailable/legacy guessed identity; partial optional telemetry; missing/spoofed context; dirty/mismatched source; target drift; owner conflict; oversized/unavailable persistence; interruption around commitment | All ineligible cases have zero protected effects/generation movement. A copied environment from a real unrelated running job must fail. Admitted case retains exact authority before effects; full-telemetry policy can still fail independently. Genuine macOS boot identity is supported; legacy or unavailable boot proof cannot authorize signaling, rebinding or admission. |
| Nested source binding | Clean outer checkout with nested site; actual `_source_tree_commit` yielding `C != T`; pre-effect source/prefix/tree/config conflicts; literal-T publication and unexpected post-dispatch SHA; legacy identity record | Freeze identical typed artifact in recovery and delivery owners. `C` remains job/admission source; runtime/edge requires independently observed `T`. Pre-effect conflicts make zero protected calls; post-dispatch mismatch records possible publication and forbids reset/runtime/initializer/edge. Missing artifact never infers a subtree or joins `C` to `T`. |
| Diagnosis | Ordinary and immutable success/failure, lost response, active job, initializer refusal, pending edge, contradictory generations; same CLI/MCP query | Query makes zero state changes and exposes exact identities, failure stage, effects and next action. A healthy runtime plus initializer refusal must remain unsuccessful. |
| History | Success A then failed B; different app/control revisions; missing/expired/unsupported detail; retention/guard saturation and cursor invalidation; replay after detail/target-metadata eviction | B latest, A latest retained success; current observation separate. Permanent guards prevent expired-ID replay and changed-intent reuse; full capacity refuses new keys. No backfill, duplicate terminal row or eviction of pinned authority. A journal fault before replacing A prevents the replacement effect. |
| Exposure | Primary and alias; DNS/TLS; wrong origin/backend; generic 200; HTTP upgrade; encoded/repeated query; release identity absent/required; stale edge; worker deadline | Every required hostname/check passes or operation remains non-success within deadline. One alias failing disproves an overall success. Worker is reaped; no hidden background retry. |
| Creation/URL | Supported CLI/runtime chain for WordPress and generic Compose; two labelled instances with distinct sentinels; created/reused, lost ensure response after pending commit, failed generic startup, nonterminal original job, incarnation drift, partial WordPress home/siteurl, changed intent, receipt eviction, guard capacity and crash between guard/registry commits | Exact relation/request/intent/incarnation; context is not dropped, capability refusal precedes effects, generic ownership survives failed startup, and positive generic exposed delivery remains supported. Expired/unknown requests never replay, guard survives instance deletion, capacity refuses. No unrelated data/URL changes or new cleanup rights. A reused instance returned by ensure must never become created/deletable. |
| Compatibility/redaction | Older controller, unknown schema, direct/legacy apply, default/error/detail/cursor output containing synthetic sensitive values | Refuse before dependent effects; identical CLI/MCP meaning; output stays valid/bounded and excludes sentinel secrets/login tokens/private payloads. |

Each area must retain actual command, candidate/control/application/installed revisions, target identity, request/job/operation IDs, timestamps, terminal result, assertions and unavailable proof. Use safe evidence references, not raw secrets/log dumps. SC-001–SC-009 map to these rows and tasks.

## Release and limits

New public capabilities: delivery_outcomes_v1, ordinary_recovery_admission_v1, ordinary_source_artifact_v1, instance_creation_receipt_v1 and delivery_route_verification_v1. Register and expose them through the current supported capability/instance transport contract before dependent effects. `ordinary_source_artifact_v1` is required for extended nested ordinary writes; a controller without it must refuse before effects and must not reinterpret two revisions as one. Existing runtime revision hashing automatically covers new sandbox/MCP Python; record the actual source and installed revision independently. No VERSION bump, tag, release or controller update is authorized by writing this plan.

The source artifact is additive: legacy `source_schema=1` identity records and absent optional fields remain readable without digest rewrites. This plan does not authorize remote runtime migration, deployment or release; source-local checks and a same-SHA fixture do not prove nested hosted recovery.

Source-local checks do not prove installed remote protocol or public deployment. A controller update uses only the supported lifecycle path after the concrete candidate passes required gates and receives any needed existing authority. Preserve unverified protected and optional-runtime checks in the final report.

## Complexity Tracking

No unapproved constitutional exception. The diagnostic journal and bounded route worker are justified by observed history loss and potentially blocking public DNS/TLS; they do not own execution authority. Mandatory ordinary-apply refusal is the user's explicit compatibility decision, not a general removal of hosted/immutable functionality.

## Remaining full-command implementation: US6

This section extends the completed core coding sequence; it does not reset historical checked tasks or claim their remaining acceptance has run. Its production tasks finish before any further feature acceptance or tests. The integration owner refreshes the independent Spec Kit analysis and owns shared Sandbox registration, existing delivery presentation/writers and cross-repository acceptance. Planning/revision uses Astra XHigh; coupled implementation and substantive review use Astra Low/Medium, simple bounded implementation may use Luna Max, and evidence collection may use Luna XHigh/High. No Sol.

The exact initial W11 input is /Users/alim/Sites/git/lenzora/.codex/worktrees/delivery-entrypoint-20260909 at 63a74f95c129cac249a2d143fa886a31bb2e5f6b. Refresh its clean status/SHA before assignment and preserve concurrent changes. All W11 paths below are relative to that checkout. Its current saved deployment run uses deterministic parent IDs; prepare job is parent + -prepare, stage request is parent + -stage, and activation job/request share parent + -activate in distinct roles. Existing worker arguments remain exactly phase and directory. Do not put a trace ID into those arguments or alter the frozen five-element Node/tsx/worker/phase/directory submission.

| Package / owner | Exact production ownership | Required interface and completion evidence |
|---|---|---|
| H: Trace data/store; bounded implementation | sandbox/delivery/trace_models.py, trace_repository.py, producers/__init__.py, producers/manifest.py, producers/lenzora.py | Closed storage/query/input codecs; fixed decoder; atomic trace guard/document and parent registration; CAS mutation/publication receipts; immutable terminal bytes; finite capacities. Pure Python decoder reconstructs existing W11 request bytes without executing TypeScript. |
| I: Native read ports; Astra integrator or Medium | sandbox/jobs/registry.py; sandbox/hosting/images/activation/status.py; sandbox/hosting/recovery/repository.py; sandbox/delivery/repository.py | read_trace_job_evidence(job_id, deadline), read_trace_activation_evidence(request_id, scope, deadline), bounded owner-native activation state reader, and read_related_recoveries(original, scope, deadline). Exact control/application/source/target/proof roles and capped reads; no reconciliation, generic unbounded JSON load or direct foreign state consumer. |
| J: Query/composition/output; integration owner | sandbox/delivery/trace_service.py, trace_context.py, manifest.py, service.py, hosting.py; sandbox/commands/delivery.py, hosting.py; sandbox/commands/manifest.py; sandbox/cli.py; mcp/wp-server/tools/delivery.py, tools/manifest.py, server.py | Registered capabilities and six trace commands/MCP equivalents; separate query grammar/codec before compatibility writers; injected ports; shared deadline; bounded JSON/human render; terminal reason correction for future writes, populated event hooks and related recovery display. trace_context.py is the dedicated composition entry; extend context.py only if its existing factory owns shared dependency wiring. |
| K: W11 adapter; one Astra Medium owner | scripts/hosted-deployment/{trace.ts,trace-projection.ts,cli.ts,preflight.ts,defaults.ts,files.ts,state.ts,job.ts,native.ts,worker.ts,command.ts,release.ts}; src/types/hosted-deployment.ts; deploy | Early ticket/bootstrap, one JSON stdout, exact owner projection, original-ID readback/replay, stage-enter before effects, stable parent publications and explicit post-effect gaps. New trace modules own the integration; existing owners keep all private-state reads. No source policy, worker argv, release selection or initializer changes. |
| L: Public guidance; bounded docs writer | Sandbox README.md, docs/delivery-outcomes.md, docs/remote-hosting.md, docs/remote-job-runtime.md, skills/sandbox-cli/SKILL.md; W11 README.md and docs/deployment.md | Document implemented selectors, same executable/controller/home/cwd, separate command/deployment results, failure/no-action meaning, missing early history and proof limits. Verify W11's existing documentation destination before writing; if docs/deployment.md does not exist, use README.md rather than creating duplicate guidance. |

H and I may run in parallel after interfaces are fixed. J waits for H/I's ports; K can implement its push client against the closed contract while H/I run, with J as sole cross-repository integration owner. L follows the agreed public shapes and finishes with all production changes. Shared files previously owned by A–G transfer to J/I only after the current writer releases them. No writer runs tests or acceptance during this coding phase.

The trace contract defines trace_capabilities/start/record/owner_status/owner_record and inspect services. Trace start precedes preflight results and allows unresolved/dirty producer context as partial. Required recording must commit/read back before each dependent protected phase. Main attaches the stable ProducerRunRecord before submitting its original worker. Workers derive that same parent through readDeploymentRun and publish monotonic closed observations without selecting an arbitrary invocation. Main alone freezes its command result; child outcomes remain separate when main disappears. A diagnostic legacy export is a supported producer read with mode=legacy_projection; query never runs project code.

Original operation output corrections are part of J: map future failed terminal reasons correctly, preserve old terminal bytes/digests, populate the existing 64-event writer ring only from observed transitions, expose bounded reverse recovery relations, and decode schema-v2 manifest digests without raising the public 32-image cap. A complete failed operation has no missing-proof loop; a complete nonterminal owner without a usable child read is authority_pending. An offered safe read keeps the original failure stage. Render command argv with shlex.join and explicit same-executable/cwd guidance. Inherited recovery source/control remains original deployment metadata.

### US6 acceptance and release boundary

The normative matrix is in quickstart.md sections 7–9 and deployment-trace.md. It covers literal W11 preflight success/failure/unavailable, retained-artifact selection through existing owner workers, interrupted main with committed child, same parent across resumed invocations, unknown start/record/publication acknowledgment, wrong roles/source/runtime/target/proof, old preflight schemas, legacy exports, immutable history, recovery, retention/guard/output/deadline saturation and CLI/MCP parity. Every case records exact command, trace/parent/child IDs and revisions, effect counters, retained snapshots and unavailable proof. Synthetic external ports are labelled synthetic and prove no real hosting result.

After H–L complete, run actual supported local CLI/MCP/W11 workflows on owned fixtures, then write focused regressions and run the combined outstanding core/trace gates. W11 uses Node 24.18.0 and its existing package scripts: typecheck, lint, Prisma runtime-manifest, feature-registry, architecture, API contract checks and grouped targeted deployment tests. The earlier 4 GiB typecheck OOM and 8 GiB pass are only saved baseline evidence; record the actual candidate result and explicit memory limit. No dependency updates or new W11 Spec Kit chain are needed. Sandbox full unittest uses its documented PYTHONPATH and finite durable jobs with synthetic captured subprocess environments.

Independent correctness and product reviewers assess the completed shared diff and observed evidence before final commit/push. Expose deployment_trace_v1 and lenzora_hosted_trace_v1 with producer version 1 and executing runtime revision; independently report installed target runtime. Capability/source drift refuses dependent trace writes/effects. Neither this plan nor synthetic acceptance authorizes image build, runtime migration, deployment, credentials, release or merge. The trace's separate 128 MiB store/rollback cap and 4,096 permanent shared trace/parent guards are diagnostic capacity, never retry authority; its cooperative five-second local read budget and bounded decode overrun are reported honestly.
