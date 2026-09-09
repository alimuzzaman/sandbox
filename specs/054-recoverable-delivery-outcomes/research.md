# Research: Recoverable Delivery Outcomes

The remaining US6 decisions below extend the original core research; they do not reset prior implementation or acceptance evidence.

Date: 2026-09-09. Decisions are based on the accepted spec, current source, the saved W5/W7 evidence, and the required W1 ownership seam. This is design evidence, not a claim that runtime acceptance has run.

## 1. Admission must use a genuine durable child

**Decision:** Ordinary apply requires a retained, running job on the controller executing the command. Environment fields identify a candidate record; they do not prove admission. Add a read-only job-evidence accessor in sandbox/jobs/registry.py, opening the existing SQLite database with mode=ro without constructing JobRepository or JobService. Compare the fixed durable context fields with the retained record, the resolved application root/source, and the running child's process group/start/boot identity. A maximum five-second read-only wait handles the supervisor's child-identity publication race. Missing or conflicting proof refuses before protected effects.

**Why:** _durable_host_context currently accepts nonempty environment strings. JobService.get(reconcile=False) can still schedule queued work, while durable_job_services construction can migrate and reconcile. Neither is a safe diagnostic/admission reader. The supervisor starts the child in a new session, then persists its child PID/group/start identity; the caller can prove membership in that retained live group without introducing another credential.

**Owner correction:** sandbox/jobs/process.py currently substitutes platform.node() when the Linux boot-ID file is absent. A hostname is not a boot-session identity on supported macOS controllers. Package A also owns a bounded read-only native macOS boot-session observation (for example, the native kern.bootsessionuuid query with a finite subprocess timeout and strict result validation), retaining the Linux boot-ID path. Unavailable or legacy hostname-derived evidence remains unsupported; it never authorizes admission, killing an old process group, or rebinding an old durable record to newly guessed identity. This supplies the specified macOS proof rather than excluding macOS admission.

**Alternatives rejected:** A request-ID flag or copied environment is not authority. Running all jobs from the Sandbox control checkout conflates application and tooling revisions. A new job subsystem would duplicate existing ownership.

## 2. Stable target identity is separate from telemetry policy

**Decision:** Add a public identity-only accessor in sandbox/resources/context.py. Reuse authenticated transport and validated HostMemoryRemote response decoding, but do not instantiate a telemetry repository or apply the memory-policy success projection. Explicitly accept only known, partial, or unmanaged evidence states with a valid authenticated stable identity; reject unknown, malformed, unsupported, absent, and unrecognized states. Preserve every independent required memory/capacity policy.

**Why:** The current ordinary path demands evidence_state=known, and _build_host_memory_service creates local repository directories. Optional telemetry may be incomplete while machine identity is valid. A denylist would accidentally admit future/unrecognized states.

**Alternatives rejected:** Globally weakening resource checks; inventing identity from a remote name or SSH hostname; using a private resource helper from hosting.

## 3. Keep existing authority; retain diagnostic outcomes separately

**Decision:** Add sandbox/delivery as a bounded diagnostic service and SQLite journal. Existing hosted recovery, immutable activation, job, instance, runtime, and edge owners remain authoritative. New writer hooks capture allowlisted snapshots at already-authorized lifecycle boundaries. Queries join read-only owner projections and never record new terminal outcomes, reconcile jobs, or run recovery.

Admission for ordinary apply is: validate all available inputs; retain existing recovery authority; retain the initial diagnostic record; then perform protected effects. Other covered commands retain their diagnostic record after capability/contract validation and before their first effects. A journal write cannot authorize an effect. Failure after authoritative effects is reported as delivery_record_incomplete with known effects. Before a later operation may overwrite a prior terminal authority record, the command writer must preserve that terminal snapshot or refuse the new mutation.

**Why:** hosts.json currently holds one current ordinary operation, so replacement loses useful terminal evidence. Reading legacy image authority can explain it without changing its meaning. A separate store provides history without enlarging every activation state object.

**Alternatives rejected:** A second activation state machine; deriving old success from current health; write-on-read history backfills; silent loss of a prior terminal result; cross-database transaction claims.

## 4. One controller owns the journal and its request scope

**Decision:** The journal lives under that controller's Sandbox home, alongside the existing local hosting authority. In delivery inspect, --remote selects the deployed target. It does not move the journal or select a different job controller. Operations executed on a remote controller are inspected there through the existing supported execution transport.

Ordinary apply derives its delivery operation key from the authoritative target/job/request tuple. Immutable activation binds the existing activation operation identifier. Synchronous deploy/preview may allocate one UUID operation ID, persisted before effects; an explicit --request-id or eligible inherited durable request binds retries. A duplicate request with identical intent returns the retained operation or typed expired evidence without re-execution; conflicting intent refuses even after detail eviction. Permanent compact request guards preserve that distinction. Without a retained request ID, reconnect by exact target/operation and do not manufacture a fresh identity to retry uncertain work.

**Why:** Existing source transfer, local controller, and deployed remote are different objects. A new global remote ledger would require another authority and transport model.

## 5. Bounded history, explicit loss

**Decision:** Schema v1 retains at most 64 terminal outcomes per exact target, 512 terminal outcomes per installation, and terminal data for 30 days. It holds at most 128 open/uncertain diagnostic records; no retention rule evicts those. Each operation document is at most 128 KiB including a bounded 64-event ring. SQLite is capped at 96 MiB, with at most another 96 MiB of transactional rollback space. Before admitting new work, prune eligible terminal records or refuse delivery_history_capacity. Summary metadata covers at most 1,024 targets; evicted metadata means unknown history, never complete history.

**Why:** A finite disk claim needs global as well as per-target limits. Active ownership remains in its original repository and is never pruned by this feature; the diagnostic cap refuses new covered work instead of erasing unresolved operations.

**Alternatives rejected:** Unlimited open history; TTL deletion of recovery fences; presenting a retained success as the last success across expired history.

**Readiness correction:** Delivery request_bindings are permanent deny-only guards, at most 4,096/controller and 1 KiB each within the journal cap. Creation has its own instance-owner guard repository, at most 4,096/controller and 8 MiB plus bounded rollback space, surviving per-instance receipt eviction/deletion. Neither store expires or evicts guards. New keys at capacity refuse before effects; existing lookups remain usable. Detail loss returns expired/unknown, never a fresh request. This finite lifetime admission budget is preferable to silently reopening used identities; increasing/resetting it is outside this feature.

## 6. Exposed-site proof needs a declared application contract

**Decision:** Register project configuration delivery through sandbox/config/manifest.py. delivery.routes defines a finite verification deadline, application response checks, optional required release identity, and applicable edge evidence. Route hostnames come from the requested primary/aliases. WordPress receives a documented runtime-provided REST metadata check; custom applications must declare a non-generic response marker before requested exposure begins. Missing public release identity is explicit and cannot satisfy a required exact-release claim.

Use a new public TLS observer in sandbox/delivery/routes.py, with a bounded child worker in route_worker.py so DNS resolution, TLS, body reads, and redirects cannot outlive the aggregate deadline. Reuse standard-library HTTP/TLS and existing redaction. Do not repurpose the local HTTP-only diagnostic helpers or globally change immutable activation's stronger _verify_edge authority.

**Why:** Existing deploy/preview considers route configuration enough. The current hosted edge loop accepts an arbitrary 3xx and has a per-host unbounded aggregate. The new proof must validate the redirect destination, path and query, TLS certificate, every alias, and the application response.

**Alternatives rejected:** Accept any 200/3xx; disable certificate verification; infer application identity from route configuration; add a new synthetic identity endpoint to every application; retry beyond a finite overall budget.

## 7. Persist creation relations where instance ownership is committed

**Decision:** Extend the instance owner's ensure result with a versioned request-bound creation receipt. The authoritative registry transaction that commits the pending incarnation also records created/reused relation. Completion of the existing ensure operation updates the receipt with its actual result. A read-only owner accessor returns an exact receipt by project/root/label/request. Remote ensure/reconcile/URL helpers transport and validate it; they do not compare inventory before and after.

The receipt binds the delivery operation and original request, plus job when applicable, and a frozen nonsecret intent_digest. Its closed canonical fields bind the delivery intent, target scope, project/root/label, resolved instance configuration including runtime overrides, and create policy. The owner recomputes the applicable fields before effects. Replaying the same ensure request returns its retained relation only after exact intent/incarnation validation, or expired/unknown with no replay when detail is gone. Another new request that selects an existing instance gets reused. URL mutation rechecks the expected incarnation before each required write and readback. No receipt grants cleanup authority.

Reserve the compact creation guard before the registry's pending/selected incarnation commit. Those are two ordered stores, not an atomic cross-store transaction: a reservation without receipt after a crash stays unknown and cannot replay. The instance owner exposes a read-only guard lookup independent of the current instance row.

**Why:** WordPress ensure in sandbox/core/_instances.py serializes ownership, mints an incarnation and persists pending registration before boot. Generic Compose ensure in sandbox/runtimes/compose.py currently writes its overlay and starts the service before its first registry write, which occurs only after health succeeds; it has no incarnation. Both are reachable through supported remote ensure, and deploy exposure already supports generic Compose. The latter therefore needs the same guarded pending ownership boundary, not an unsupported-runtime exemption.

**Caller correction:** ensure_remote_instance runs `sb ensure --local`; sandbox/commands/instances_cmd.py constructs OperationRequest, sandbox/application/runtime_service.py selects the adapter, and sandbox/application/context.py forwards WordPress ensure arguments. Those three files and sandbox/runtimes/compose.py join package E. Optional creation_context and expected_incarnation use OperationRequest.arguments; the composition closures forward them without dropping fields. WordPressAdapter already forwards the request, so no additional adapter/facade layer is needed. F retains parser/predispatch ownership in sandbox/cli.py. Pure capability/receipt queries use injected owner reads before compatibility writers and never call ensure, render an overlay, initialize runtime repositories or infer ownership from inventory.

For generic Compose, validate resolved nonsecret intent and selected-runtime capability before effects; under the project and allocation serialization, reserve the existing permanent guard, then commit the selected/new incarnation and receipt in the same registry write before overlay/start. A new row gets an opaque incarnation and created relation; an existing incarnation stays reused. An existing generic row without incarnation may receive a present-tense opaque incarnation in that authorized guarded selection commit, always with relation=reused and no claim about historical creation. This narrow generic identity initialization does not change WordPress W1 legacy handling or grant runtime/cleanup authority. Health completion updates that same receipt/row and failures retain pending/unknown ownership. Never prune guards or grant cleanup when a row disappears. Existing unrelated ensure behavior and supported generic exposed success remain additive-compatible. Existing install snapshots stay in sandbox/commands/data.py; this correction does not relocate or reimplement them.

**Alternatives rejected:** Inventory-difference ownership; labelling all returned instances created; broad rollback after a partial URL update; deleting a reused instance.

The caller correction preserves prior U1/I1 closure: delivery and creation guards remain permanent, limited to 4,096 each with their existing storage caps, and a reservation without receipt remains unknown with no replay. Present-tense generic identity initialization requires a newly reserved request; a retained request/receipt with missing or mismatched current incarnation refuses, never remints identity. WordPress legacy refusal stays unchanged. No new reset, expiry, cleanup authority or cross-store atomicity claim is introduced.

## 7a. Bind original source commit to the deployed nested artifact

**Decision:** Preserve the original durable job/source commit `C` and add one closed, optional source-artifact mapping to the existing recovery and delivery contracts. Resolve the actual selected checkout and prefix before ordinary admission with `_source_tree_commit`; use `kind=git_commit`, `root_relative="."`, `revision=C` for the identity case, and `kind=git_subtree`, a normalized proper prefix and the derived commit `T` for a nested source. Compare the selected tree under `C` with `T^{tree}` using bounded read-only Git probes. Freeze the mapping and the T-derived configuration, store the same object in `hosting_operation.source.artifact` and `requested_outcome.application.source_artifact`, and publish literal `T` after admission. `application.commit`, the retained job and admission remain bound to `C`.

**Why:** The current hosting writer freezes configuration from `C` before preparation, then discovers a different nested subtree commit `T` during transfer. Recovery and delivery projections also require remote `source_revision == source.commit` and discard source fields beyond identity/commit, so a valid nested deployment cannot be joined without conflating source authority and deployed artifact. The mapping must be prepared once and passed to both owner records; the diagnostic service cannot invent it after admission.

**Exact limits:** `SourceArtifact` rejects unknown/extra fields, booleans-as-integers, noncanonical or unsafe POSIX paths, and unsupported commit spelling. `source_schema=1` identity records remain readable and byte-stable; absent optional artifacts never imply a historical subtree. Extended admission starts with null source evidence and accepts only an independent remote observation of `T`; the requested artifact is not proof of observation. A pushed SHA that differs from `T` is a post-dispatch publication anomaly, so reset/runtime/initializer/route/edge must stop while possible source effects remain visible.

The implementation correction is bounded to `sandbox/hosting/recovery/models.py`, the hosting admission/recovery writer and observer, recovery repository/policy projections, delivery model/service joins and the additive `ordinary_source_artifact_v1` capability. It does not change job schema or lifecycle, OCI `artifact_digest`, source-root migration, W10 isolation, cleanup authority or transport design. The accepted details are in the [nested-source binding plan](../../tmp/054-delivery-acceptance/nested-source-binding-plan.md).

## 8. Public capability and modular boundaries

**Decision:** New delivery CLI uses CommandSpec and the explicit command manifest. New MCP delivery group receives an injected delivery_service_factory through the tool manifest/server composition; it never consumes the MCP app helper namespace. Existing deploy/preview arguments gain optional request IDs and a verification deadline override. Instance receipt schema and delivery output schema each declare v1 capability. The nested source mapping declares additive `ordinary_source_artifact_v1`; extended ordinary writes require it before effects, while older capabilities remain intact. Missing controller capability refuses dependent creation/URL/exposure effects.

Runtime revision remains the existing content-derived revision, with source and installed-controller evidence recorded separately. No release/tag or VERSION bump is implied by this source feature; release notes document the mandatory ordinary-apply compatibility break and exact capability checks.

**Alternatives rejected:** New registry.COMMANDS, sandbox_core, hermes facade, or app helper consumers; silently using an older weaker controller; conflating public schema version with a requested release.

## 9. Verification order and unresolved limits

**Decision:** Finish all production/config/docs edits before running supported workflows. Then exercise real safe/authorized commands, record outcomes, author focused regressions from those observations, and run required gates. Independent correctness and product reviews follow a stable implementation. This follows the active AGENTS execution order over generic test-first templates.

Protected remote deployment, controller update, credentials, DNS mutation and cleanup require their existing explicit authority against the concrete candidate. Their absence does not stop useful source work, but the corresponding acceptance remains unverified. Nested-source acceptance additionally needs a real clean outer/nested fixture proving `C != T`, literal-T publication and C-bound job/admission against independently observed T runtime/edge evidence; a same-SHA fixture is insufficient. W10 follow-up, unrelated feedback/entrypoint work, broader restore proof, and optional runtime adoption remain outside Feature 054.

**Research status:** All design choices needed for tasks are resolved. Actual candidate revision, owned target names, application checks, remote capability and authorization are execution inputs to be observed at acceptance time, not facts inferred here.

## 10. Full-command deployment trace and the fixed producer boundary

**Decision:** Add an early diagnostic trace owner in Sandbox plus a push-only fixed Lenzora producer. The first trace begins before preflight results, when safe executable/bootstrap resolution is possible; unresolved source/remote context stays partial. The existing activation journal starts too late to explain preflight, release/artifact selection or checkout failures. The normative fields, owner APIs, limits and lifecycle are in [Deployment trace v1](contracts/deployment-trace.md).

The current W11 source at 63a74f95c129cac249a2d143fa886a31bb2e5f6b already validates private deployment runs and derives parent/stage/phase requests in scripts/hosted-deployment/state.ts. Reuse those owners in a TypeScript projection adapter. A compiled Python descriptor decodes pushed projections independently. Query uses retained projections and native read-only owner ports; it never imports project code or reconstructs private application JSON. Producer claims are producer_recorded until an exact native join proves them.

**Alternatives rejected:** Extending one activation operation into a whole-command state machine; embedding Lenzora's private file reader inside Sandbox; query-time executable plugins; trace IDs in immutable worker argv; one mutable active-invocation pointer. Each either loses early failures, creates a second owner, broadens query effects, or changes request/submission identity.

## 11. Stable parent publications, exact proof and honest bounded reads

**Decision:** Keep invocation trace UUIDs separate from the existing deterministic deployment parent. Register a stable ProducerRunRecord before original jobs; workers derive that same parent and publish role-specific stages/projections without changed argv. Main alone freezes command result. Lost acknowledgments use original trace/mutation/publication IDs for readback, then identical replay only after conclusive absence. CAS plus monotonic role/identity merge prevents stale snapshots erasing terminal children. A resumed invocation may link the same run; earlier unrecorded history stays unavailable.

The existing activation status projection contains result/generation digests but not every source/config/plan/proof/runtime/time needed by US6. Add a bounded owner-native trace read with those closed dimensions; missing legacy proof remains partial. Generic RecoveryRepository.load uses an uncapped JSON read, so it cannot satisfy this port. Job proof independently verifies control-source submission and keeps wrapper jobs distinct from application-bound admission. Valid schema-v2 image sets above 32 remain partial with a proof reference. Reverse recovery lookup is scoped, capped, and never changes the original failed snapshot.

One cooperative five-second budget flows through read-only SQLite, owner file reads, bounded decodes and related-record loops; byte caps bound one in-flight decode and final serialization overrun, which acceptance measures. Query uses a separate elidable detail codec so optional omission remains schema-valid. Mutation receipts always identify the original accepted snapshot, unaffected by later query enrichment. Full limits are defined once in the trace contract.

**Alternatives rejected:** Treating producer success as owner proof, making unbounded readers appear bounded through a detached thread, nulling frozen role links during reconnect, replaying under new request identities, or rewriting legacy terminal bytes to improve wording. The remaining implementation instead corrects new writers and query-only guidance, populates observed event hooks, and leaves unavailable historical evidence explicit.
