# Sandbox delivery repair plan

Prepared 8 September 2026 from the [delivery review](report.md), pinned to `fd7d650f8bfbb1760d931e2484cc01fa0badf546`.

Status: proposed implementation plan. This research task has not authorized or performed product implementation, deployment, production repair, image rebuilding, data changes, or cleanup. Existing task owners retain their work and runtime authority.

## Required outcome

A user should be able to create a usable local instance, deploy the selected application revision, and inspect or recover an interrupted operation through Sandbox's supported commands. Sandbox should carry the target identity, progress, evidence, and next safe action through that whole operation. A green source test or an accepted asynchronous request must not be mistaken for the requested outcome.

The work should start with narrow corrections to the demonstrated defects. Broader changes to public result contracts, receipts, or recovery coverage need a scoped PRD and the existing Spec Kit process. This plan is not a replacement specification and does not create a new Spec Kit feature number.

## What counts as complete

| User outcome | Required evidence | Failure behavior |
|---|---|---|
| Create local WordPress | Exact root/label/incarnation; expected source mount; installed WP; backend health; advertised route; usable REST/admin entrypoint | Typed failing stage; owned resumable state; no ready result without evidence |
| Reuse or restart | Same retained instance/data; healthy backend and clean URL after restart/wake; no duplicate stack | Retain original ownership; bounded diagnosis; no silent new instance |
| Create generic Compose instance | Exact service/container; declared health response; correct status for running, absent, stopped, unhealthy, and unknown | Unknown stays unknown; empty output never proves ready |
| Expose or deploy | Exact target and application source/artifact; one accepted request; terminal job; intended runtime; requested public route and edge checks | Report known effects and the original request; no fabricated success or blind replay |
| Recover interrupted work | Original operation/creation receipt; exact owned resources; independently observed terminal state; authorized bounded continuation | Unknown/foreign evidence blocks effects but still permits useful diagnosis |
| Verify backup/restore | Fresh capture bound to this backup operation; exact immutable archive and declared data/schema checks; independent restore/reference proof when required | Same-operation replay stays idempotent; no verified receipt while a required comparison is unresolved |

The working backend and public route checks are distinct. A 401/403 can prove a web server answered but cannot by itself prove a healthy application. Health evidence must match the project's declared contract. Inspect commands may successfully report unknown or failed state; their observation success must remain distinct from the workload result.

## Order and ownership

| Wave | Work package | Findings | Dependency / acceptance gate |
|---|---|---|---|
| 0 | W0: bind the baseline and preserve concurrent work | All | Required before implementation |
| 1 | W11: make the literal deployment entrypoint reachable | F14 | Exact shell invocation and outer Node bootstrap; prerequisite gate |
| 1 | W1: preserve instance ownership through cleanup and URL updates | F01, F02 | Source regressions first; two-caller/two-instance runtime fixture before release |
| 1 | W2: correct initializer identity parsing | F03 | Real Compose contract fixture and exactly-once initializer proof |
| 1 | W3: make readiness, status and default output truthful | F04, F08, F16 | W8 local lifecycle and full-output checks before release |
| 1 | W4: make job control usable under failure | F09, F10 | State-race tests and bounded real controller pagination |
| 1 | W12: bind capture identity to a distinct backup operation | F15 | Distinct-set content update and same-set retry acceptance |
| 2 | W5: require recovery eligibility before hosted effects | F05 | Scoped contract decision; partial-telemetry and interruption acceptance |
| 2 | W6: complete the existing restore correction and define proof coverage | F07 | Existing recovery owner; separate verification acceptance |
| 2 | W7: unify completion evidence and public-route checks | F11, F12 | W1–W5; scoped receipt/result design |
| Cross-cutting | W8: isolate source tests and add product acceptance lanes | F06, F13 | Required for the affected Wave 1/2 releases |
| 3 | W9: close the feedback loop and simplify operator work | Repeated operational friction | Verified fixes and W7's supported evidence query |
| Bounded follow-up | W10: resolve the remaining isolation/coverage questions | Unconfirmed candidates | Investigation before any new source-isolation implementation |

One Astra owner should make integration and acceptance decisions. Astra should handle ownership, lifecycle, recovery, and protocol decisions directly. Luna can make fixture, metadata, documentation, or repetitive CI edits from an accepted detailed plan with disjoint files. No Sol. Do not split simultaneous edits to `hosting.py`, `_remote.py`, or the job envelope contract among independent implementers. Give those files one owner or integrate the packages sequentially.

The PostgreSQL helper already has an active owner. Coordinate using its committed plan and final evidence; do not overwrite its dirty work or start a competing implementation.

## W0 — Freeze the implementation inputs

1. Read the current applicable guidance and run the CLI guide. Inspect all relevant worktrees, branches, dirty files, and current source revision. Rebase the findings mentally against newer code; a pinned report is not authority to overwrite a later fix.
2. Record the registered target identity, local runtime revision, installed runtime revision, protocol/capability availability, and selected application revision separately. Revision equality is an input check, not runtime/deployment proof. The shared controller currently carries another task's initializer fixes. Resolve branch ownership and required fixes before migration; do not obtain equality by downgrading that controller or overwriting an active target owner's runtime.
3. Preserve the immutable source, signed bundles, recovery archives, request identities, and owner records used by existing tasks. Reusing an image must not trigger a new build.
4. Record which findings still reproduce. Run the saved synthetic probes against their pinned baseline as evidence, then turn the relevant cases into proper regressions for the changed code. The saved probes intentionally assert broken behavior and should not become the repaired suite unchanged.

Deliverable: a small work package per change containing exact input revision, owned paths, preserved paths/state, expected behavior, and acceptance commands. No feature work should start from an unspecified “latest state.”

## W11 — Repair the public deployment entrypoint before deeper deployment work

Late evidence added this package after W0–W10 were numbered. It belongs at the start of Wave 1. It is owned in the adjacent Lenzora repository: `package.json`, the repository Node bootstrap, `scripts/hosted-deployment/defaults.ts`, deployment CLI entrypoints, runbooks, and shell-entry acceptance tests.

1. Resolve the literal command contract first. Native pnpm reserves `deploy`, so adding a package script cannot implement the promised `pnpm deploy dev/prod`. The supported native form is `pnpm run deploy`. If the exact original form remains mandatory, a separately approved, explicitly installed project-scoped command wrapper would be needed; prove its dispatch and ordinary pnpm passthrough, and do not silently replace the user's global pnpm. If the wrapper is not acceptable, request a concrete command-contract change. Until then, mark the literal requirement unmet.
2. Put Node selection before pnpm's engine admission. The existing wrapper works when invoked outside pnpm; a wrapper inside the package script is too late. Select the existing pinned Node runtime without weakening engine checks or changing the pin to match an arbitrary host version.
3. Add one read-only preflight for command dispatch, actual Node/pnpm versions, clean control checkout, local/installed Sandbox revision, and supported capabilities. Return all relevant missing prerequisites and the next supported action before image selection or durable deployment submission.
4. Preserve the Sandbox revision guard. After the reviewed candidate passes and the operator authorizes service migration, use the supported migration command and independently verify the installed runtime. No automatic rebuild, moving-image fallback, or fresh request identity should compensate for a prerequisite failure.
5. Test from the real shell/package-manager boundary, not only by importing the TypeScript deployment function. Cover the literal promised command, the native explicit run form, wrong host Node, missing pinned Node, revision mismatch, and a matching preflight. Assert that failures create no image build, deployment job, or activation request.

Acceptance: on the exact supported shell/bootstrap environment, dev and prod each reach the intended CLI through the approved public entrypoint and pass preflight with clean recorded control/runtime identities. Then the separately authorized deployment must still satisfy W2, W5, W7, and W8 using the retained exact signed bundle. Reaching the CLI is only entrypoint acceptance, not deployment completion.

The [six-attempt evidence](research/deployment-attempts.md) is the baseline. All six were terminal failures before image selection. The source cleanliness and no-mutation assertions come from the parent task's summary; raw logs independently prove the three stopping gates. Do not attribute these attempts to the unfinished PostgreSQL edits, which the parent preserved separately during the attempts.

## W1 — Require exact ownership for destructive cleanup and URL changes

Owned paths: `sandbox/commands/deploy.py`, `sandbox/commands/preview.py`, the relevant helpers in `sandbox/core/_remote.py`, and focused remote/deploy/selector tests. Use one owner for these shared paths.

Implementation:

1. Remove the authority assumption that a uniquely new inventory name belongs to the failed caller. Retain such records when no creation receipt exists.
2. Carry a creation/ensure receipt that binds request, project, label, instance incarnation, and created-versus-reused state. Reuse an existing receipt mechanism where possible; any new public wire shape must be versioned and documented.
3. Permit automatic cleanup only after the original operation is terminal and the exact owned incarnation is independently observed. A timeout or lost response remains completion-unknown and must reconcile the same request. A reused instance never becomes cleanup-owned.
4. Change URL updating to require the exact instance returned by ensure/apply. Use explicit local/project/instance selection in the nested CLI and validate consistency before either option mutation. Ensure ambient `SANDBOX_INSTANCE`/label values cannot redirect the request.
5. Read back both options for the exact target. If only one update completes, retain a typed partial result tied to the same identity; avoid broad rollback against another instance.

Required regressions: caller A/caller B interleaving; ensure timeout with a still-running child; created versus reused record; mismatched incarnation; empty/ambiguous inventory; two labels with a default; ambient selector conflict; failure between the two URL writes.

Runtime acceptance: an authorized disposable two-instance project, with distinct pre-existing data and URLs. Create/expose one labelled preview, verify only its options changed, and prove the other instance and its sentinel data are unchanged. Inject transport loss and verify no unrelated instance is deleted. Human review is required before releasing these data-deletion and routing changes.

## W2 — Match real Compose initializer evidence

Owned paths: initializer proof generation/classification in `sandbox/commands/hosting.py`, its focused tests, and the corresponding hosting docs.

An existing separately owned correction is now available at `c7ed709ba35ef2485c4e3d71ccb80ad09207be2c`, on top of format correction `0942d14`. Saved evidence shows all six resolved initializer hashes matching and a successful generation-10 reconciliation of existing Amar Sonar runtime. This is useful acceptance evidence, with a fresh exactly-once initializer lane still separate. Review the exact source and evidence first. Reuse verified work through authorized integration; do not edit the Amar Sonar deployment checkout or migrate its shared controller concurrently.

Implementation:

1. Define supported Compose output formats and parse exactly one record for the requested service. Reject extra/missing services, duplicate records, malformed hashes, and truncated output.
2. Canonicalize the full image identity at a single boundary. Do not use short IDs, tags, substring matching, or a broad “strip anything before a colon” rule as proof. Hash the same resolved environment/model used for creation on the supported Compose versions. Keep resolved configuration private in a bounded process pipe. Include env-file and escaped-dollar round trips, and give the resolved model an explicit justified byte bound rather than inheriting a tiny identity-output limit.
3. Keep the project/service labels, creation-time bound, current config, full image, single-container requirement, successful terminal exit, and no-replay rules.
4. Make the refusal identify the failed evidence dimension using safe closed labels. No raw Compose environment, secrets, or command payloads belong in the error.
5. Check parser capability before runtime effects when the needed format can be established in preflight.

Required regressions: real service/hash wire format; prefixed/bare full digest; foreign project/service; wrong config/image; multiple containers; old creation epoch; malformed marker; running timeout; failed initializer; missing initializer. Include a captured safe fixture from an actually supported Compose version.

Runtime acceptance: on an authorized disposable host, execute an initializer that increments a sentinel exactly once. Exercise fresh convergence, successful dependency execution, reconnect, and refused foreign evidence. Verify that the dependent application starts and that no reconnect runs the initializer twice. Reusing a retained release must keep build/pull disabled according to its existing contract.

## W3 — Make ready mean the requested service is usable

Owned paths: fresh/multisite completion in `sandbox/core/_instances.py`, generic status in `sandbox/runtimes/compose.py`, ensure/install output in `sandbox/commands/instances_cmd.py` and `sandbox/commands/lifecycle.py`, relevant runtime/CLI tests, and local lifecycle docs. Preserve clean-URL and activation ownership rules. Use one owner for the shared lifecycle files.

Implementation:

1. Treat false `_wait_http` and `_wait_reachable` results as failed readiness. Do not write a ready registry entry, return a usable login result, or take a successful-ready receipt from that path.
2. Keep the pending/error state resumable with the same name, ports, incarnation, and data. Include the failing stage, bounded safe diagnostics, deadline, and supported retry.
3. Validate the final advertised URL after WP install, multisite recreation, and URL repair. Use a backend-only probe during wake to avoid the previously fixed recursive activation path.
4. Parse supported generic Compose row/array output. No matching container means absent/stopped according to the public contract; malformed/truncated output means unavailable. A running container and a healthy application should remain distinct observations where the command promises both.
5. Preserve the real source-mount and installed-state guards on reuse. Do not “fix” readiness by bypassing Caddy/DNS or accepting the setup screen.
6. Make installation progress secret-free at its producer. Carry explicit reveal authority to the final intended interactive result only; ordinary ensure, JSON mode, nested install calls, failure output, and durable logs must not inherit it implicitly. Redacting the final JSON cannot repair earlier stdout/stderr. Preserve useful safe progress and typed failures; avoid unbounded stream capture as a substitute for correct output production.

Required regressions: failed backend wait; failed multisite wait; installed CLI with dead web tier; clean route failure; ready reuse with drift; absent/malformed/array Compose output; wrong service; exited/unhealthy container; output truncation; healthy control case. With synthetic login markers, inspect the full stdout/stderr stream for successful install and plugin/theme/snapshot failure, both JSON and text modes. Explicit reveal must remain scoped and never reach retained job output. Human review is required before releasing the authentication-output change.

Runtime acceptance: default Docker/Caddy on macOS and Linux; fresh WordPress, reuse, down/ensure, restart, and request wake with retained data; single-site and multisite; declared plugin activation; generic Compose start/status/stop/resume. Query-string and canonical REST/admin routes must work. Optional Herd parity is a separate declared gate and must not be inferred from Docker results.

## W4 — Repair job escalation and bounded history

Owned paths: `sandbox/application/job_service.py`, `sandbox/jobs/models.py`, `sandbox/transports/remote_jobs.py`, `sandbox/commands/jobs_runtime.py`, existing job projections, and focused cancellation/transport tests.

Implementation:

1. Make cancellation of a cancelling job state-aware. Ordinary repeats must be safe; force escalation must reach the owned process group after identity revalidation. Handle a concurrent terminal transition without signaling a stale/reused process identity.
2. Define a compact list-row projection and byte-bounded page. Keep full command/result/output data in explicit detail operations. Check both the page and a single oversized record.
3. Expose/document the continuation cursor already supported internally. The controller should return a deterministic next cursor and completeness metadata; the client must distinguish a complete page, a partial/truncated envelope, and malformed output.
4. Preserve the current transport output limits. Return typed safe errors such as response-too-large with the limit/observed size; do not expose raw stdout tails as diagnostics.
5. Keep asynchronous acceptance wording explicit: accepted/queued is not a terminal pass. Provide the exact job-status/output command or machine-readable next action with the original identity.

Required regressions: cancel then force; repeated force; terminal race; lost process ownership; valid 100-record page around the measured 1.18 MB case; byte boundary; one large record; cursor stability; no duplicates/skips; malformed/truncated JSON; nonzero transport with valid-looking stdout; redaction and unknown acceptance.

Runtime acceptance: supported controller reads traverse all pages of a bounded fixture history; job-detail lookup retains necessary evidence; terminal counts reconcile. An owned disposable long-running job can be cancelled then force-stopped without touching another process. Verify installed runtime revision/capability before relying on the updated protocol. Submit once with one request ID; observe/reconcile before replay.

## W12 — Give each requested backup its own capture identity

Owned paths: capture context in `sandbox/recovery/materialize.py` and `sandbox/recovery/hosted.py`, the registered WordPress controller in `sandbox/transports/remote_recovery.py`, corresponding typed contracts/tests/docs, and any adapter signature callers. This is distinct from the concurrently owned PostgreSQL schema-verification correction; do not overwrite that work.

1. Carry an immutable backup operation/set identity from publication admission through capture and its receipt. Include the identity and exact artifact/source declaration in the controller request digest. Use a durable admitted identity; a random nonce generated on every retry would break replay safety.
2. Preserve replay of the same admitted backup after transport loss. A different backup set must create a different capture operation even when topology, application revision and runtime revision have not changed.
3. Define how old request-named captures are classified. Keep retained archives intact, and do not retroactively label an old capture as fresh for a new set. Any migration or cleanup requires its existing ownership/retention evidence and authorization.
4. Keep a receipt for capture start/completion, original backup operation, source binding and artifact digest. Source topology stability is useful preflight evidence but is not a database/media freshness fingerprint.
5. Verify this common contract across adapters before release; version public/wire changes and reject unsupported controller capability before capture effects.

Required regressions: publish set A; change only database/media content; publish set B and observe fresh content; retry A and obtain A's original receipt/content; interrupted B capture; two concurrent sets; declaration drift within the same operation; cached empty/malformed archive; output loss and replay without a second identity.

Runtime acceptance: an authorized isolated WordPress fixture with synthetic rows/media. Capture A, change only content, capture B, and restore both into owned disposable targets. A must contain the earlier marker, B the later marker, and same-operation retries must neither recapture nor cross sets. Prove retained original archives and source data are unchanged by verification. Human review is required before release because this changes backup authority and replay behavior. Until accepted, do not claim this controller provides fresh backups solely from newly assigned set names.

## W5 — Make recoverability a pre-effect deployment requirement

Owned paths: hosted plan/apply admission, `_authenticated_machine_identity`, ordinary recovery acceptance, and relevant resource/hosting contracts. This crosses authority boundaries and needs an Astra design decision before edits.

Implementation:

1. Separate authenticated stable identity from optional memory/swap telemetry. Incomplete telemetry must not manufacture identity or silently change authority.
2. Add an explicit plan projection for recovery eligibility and its missing evidence. For the recoverable deploy path, acquire durable job/request/source/config/target authority before effects.
3. Define the compatibility policy for ineligible legacy/non-durable callers. Prefer a typed pre-effect refusal with a concrete supported invocation. If a deliberately nonrecoverable mode remains necessary, document its narrower outcome and require an explicit policy choice; do not silently substitute it.
4. Preserve uncertainty and the original receipt across interruption. Recovery remains observation-bound and must refuse foreign, stale, or unverifiable effects.

Required regressions: authenticated identity plus partial resource data; unknown/malformed identity; dirty or mismatched source; missing durable context; prior active operation; crash immediately before/after receipt persistence; lost acceptance response; later observation recovery from the same request.

Runtime acceptance: an authorized disposable hosted deployment with resource telemetry unavailable still either has a valid supported recovery receipt or stops before effects with the declared reason. Induce a bounded interruption, resume observation through the original request, and prove no duplicate initializer or generation advancement. Human review is required before release.

## W6 — Finish restore verification without weakening it

First dependency: accept the original task's [archive-bound reference plan](https://github.com/alimuzzaman/sandbox/blob/ef5a239dd1f4e443f63ecc546f373d70a50c6adc/docs/postgresql-restore-schema-verification-plan.md#L1) and its implementation evidence. That task owns the PostgreSQL helper edits. Do not duplicate them here.

For its bounded correction, require exact archive/dump/image identity, an isolated independent schema reference, the original capture fingerprint, structural checks, bounded reference lifecycle, preserved retained target/data, and a verified receipt only after every required comparison passes. Include negative controls for changed structure, unavailable/changed legacy source, uncertain reference ownership, and altered archive. Matching masked syntax labels or table counts is insufficient.

Then define a separate coverage contract for verified backup/restore. Inventory defaults, type modifiers/collations, standalone indexes, sequences, triggers/functions, extensions, non-public schemas, grants/RLS where relevant, and row-content requirements. Decide which are guaranteed, deliberately excluded, or unsupported. Version the evidence so older archives retain their exact meaning.

Do not silently broaden the current receipt's claim or reuse a reference-image pass as complete production-data proof. Broader schema/data coverage is material work and needs the scoped Spec Kit chain. Protected restore/production operations retain their own explicit authority and safe-target requirements.

## W7 — Give each requested outcome a complete, queryable receipt

Owned boundaries: command result models, host/image/job projection services and repositories, CLI/MCP presentation, and the adjacent application's deployment attachment contract. Use registered contracts; do not read state JSON directly or add facade consumers.

Proposed receipt fields:

| Dimension | Fields to bind |
|---|---|
| Identity | Operation, request, job, target/project/environment, workspace/instance incarnation |
| Source | Application revision and dirty-source policy; Sandbox source/runtime revision separately |
| Artifact | Signed bundle/receipt, image and config digests, proof/plan identity |
| Lifecycle | Accepted/started/completed time, phase, terminal result, known effect scope, retry/recovery relation |
| Runtime | Exact observed generation/services/images, installed state, declared health evidence and observation time |
| Exposure | Requested hostname, safe route response/redirect proof, TLS result, edge receipt when required |
| Evidence quality | Complete/partial/unknown, bounded/truncated state, reason codes, supported next action |

Implementation:

1. Extend existing service/repository projections after a scoped result-contract design. Keep acceptance, observation, execution, runtime health, and exposure success separate; do not globally reinterpret every `ok` field.
2. Append terminal outcome receipts with bounded retention and explicit history completeness. Keep the strongest existing staging/activation proof as the authority; the new query joins it rather than inventing another activation state machine.
3. Make expose/preview wait for the requested bounded route proof or return a known partial outcome. Confirm the intended backend/revision where the project supports that proof; mere HTTP 200 is not always enough.
4. Provide one supported diagnosis query that shows the current/last complete outcome, exact joined identities, failing stage, known effects, and the next safe command. It must also explain when history is unavailable.
5. Preserve historical unknowns. New receipts can fix future traceability; they cannot certify an old production release retroactively.

Required regressions: accepted but running; terminal failure; route configured but TLS unavailable; wrong backend; query-string/redirect errors; runtime healthy but edge stale; differing control/application revisions; receipt persistence interruption; bounded retention; no-history and partial-history responses; secret-safe CLI/MCP output.

Runtime acceptance: a fresh authorized disposable deployment can be explained after a client reconnect using supported queries alone, with exact source/image/runtime/public-route proof. A failed deployment gives the same clarity without any additional effects or new request identity.

## W8 — Put real outcomes into acceptance, and isolate the fast suite

Implement this alongside Wave 1 so fixes cannot be declared complete on mocks alone.

1. Fix the affected source fixtures: explicit temporary Sandbox homes, injected preflight/process/HTTP/registry/overlay boundaries, and the existing synthetic subprocess environment helper. Assert that source tests neither access the real engine nor create artifacts outside their temporary roots.
2. Add real adapter contract fixtures for Compose hash/image/ps formats. Record tested Compose versions and safe normalized outputs. Test isolation must not turn the fixtures into invented outputs again.
3. Make the default local lifecycle smoke a required gate for relevant core/CLI/proxy/runtime changes. A path filter may save work but must include shared adapters, config, activation, and installer changes that affect startup.
4. Maintain a separate authorized remote acceptance lane for jobs, source transfer, version compatibility, image activation/recovery, and exposure. Give it owned disposable resources, finite deadlines, one request identity, and deterministic cleanup evidence.
5. Require the supported-platform install/update/restart checks for releases. Keep optional runtimes and protected integrations visibly unverified when their lane is unavailable.
6. Record exact source, dirty digest if any, installed runtime, fixture identity, terminal job, exit code, and bounded output. One passing full `./sb selftest` on the candidate plus the affected focused/runtime gates is enough; do not repeatedly run the same suite without a new reason.

Acceptance matrix:

| Lane | Minimum scenarios | Required before |
|---|---|---|
| Isolated source | Changed contracts; malformed/partial results; ownership/races; subprocess boundaries | Commit/push of completed implementation |
| Real Compose contract | Hash/image/ps wire shapes; negative controls | W2/W3 release |
| Linux + macOS local | Fresh/reuse/restart/wake, retained data, clean URL, generic Compose | Startup/proxy/runtime release claims |
| Disposable remote | Exact source transfer, acceptance loss, terminal jobs, paging, cancel escalation | Remote protocol/reliability release |
| Hosted image | Retained exact bundle, initializer once, intended runtime, route/edge, interruption | Hosted deployment release |
| Isolated recovery | Exact archive, independent comparison, replay, preserved target, verified receipt | Recovery release |
| Installer/client | Supported install/update, service restart, CLI and changed MCP path; desktop launch where claimed | Tool/client distribution |

Run long tests through durable jobs with finite timeouts. Retain acceptance identities and use bounded status/output pages. Controller updates use the supported lifecycle command only after the candidate passes and the operator authorizes that update. Independently verify the installed revision afterward. A configured workflow or submitted job does not count as a completed gate.

## W9 — Reduce repeated diagnosis and close feedback with proof

1. Join each confirmed finding to its existing feedback IDs, correction commit, focused regression, runtime acceptance receipt, and remaining limits. Avoid filing duplicates for the same cause.
2. Use the saved 281-record detail index (all 278 high/critical plus three medium) and its substantive dispositions. The initial 70 unreviewed/blocked candidates are a filter, not a complete unresolved count; verified/resolved records still require scope-sensitive evidence interpretation. Re-triage all release-relevant cases against current code and live evidence. Close only the exact accepted issue; retain unsupported/inaccessible cases explicitly. A closure needs the original trigger, correction commit, actual check result, and remaining limits rather than a keyword such as “live.”
3. Use the repeated restore-diagnostic controller updates and the oversized job-history failure as inputs to the supported diagnosis design. The user should not need raw helper output, private state reconstruction, or a controller patch just to identify a failing stage.
4. Keep `AGENTS.md` and skills short: canonical CLI recipe, completion evidence, authority boundary, and concrete next action. Replace outdated command examples only after parser verification. Do not add another long prompt recipe to compensate for a missing deterministic capability.
5. Preserve the useful refusal/no-replay rules. Scope “cannot reproduce → blocked” to a requested runtime fix; source review, bounded diagnostics, and an already-authorized implementation can continue with explicit evidence limits.

Measure on newly recorded operations: first-attempt usable-instance/deployment completion; time to usable URL; time to terminal diagnosis; number of manual recovery steps; incomplete/unknown receipts; and whether final evidence required raw tools. The selected historical snapshots are not a denominator for product-wide success rates. Set performance targets after collecting a representative baseline.

## W10 — Bound the remaining questions before broadening implementation

The ordinary hosted source path is project-scoped while runtime state is environment-scoped. Source sync also uses shared project paths. Same-controller applies hold a shared state lock, so a simple simultaneous apply/apply race is not established. Investigate sync/apply overlap, multiple clients, and live bind-mounted source with deterministic synthetic interleavings and an authorized disposable fixture. Change source isolation only if those checks demonstrate a supported affected case.

Other follow-ups: generic adapter preflight behavior when the engine is unavailable; installer/desktop supported-platform proof; MCP restart/actual wrapper calls; optional runtime parity; and the boundary between application SHA and control-checkout SHA in job history. Keep these as scoped investigations rather than calling them confirmed defects.

Prioritize the piped-stdout creation report `24e6756299d9f7e39e28d87125338f2b` because programmatic callers are a core product path. On the stated macOS/OrbStack environment, compare direct CLI, captured CLI, Node wrapper and isolated preflight with identical inputs. Record only safe stdio/timing/result metadata. The reviewed subprocess helper does not itself prove TTY dependence. Do not bake in a PTY workaround, add retries, or enlarge timeouts until the failing layer is isolated. Add the accepted programmatic entrypoint to W8's creation lane if reproduced.

## Release decision

The first release candidate should contain the verified Wave 1 corrections and their necessary W8 gates. Accept W5–W7 as separate coherent changes after their contracts and negative cases are reviewed. Do not bundle every feedback request or rewrite the whole controller.

For each candidate: review the exact diff, run its required gates, commit/push the non-main branch, and retain its evidence. Merge, controller update, release, deployment, data mutation, and destructive cleanup need their existing explicit authorization. Before asking for such final approval, prepare the exact target, revision, retained artifact, checks, rollback/recovery evidence, and remaining limits. An approval should apply to that concrete result.

The program is complete when the supported commands can create, deploy, diagnose, and recover the declared fixtures with truthful results and durable evidence, without an agent reconstructing private state or guessing which resource belongs to the request.
