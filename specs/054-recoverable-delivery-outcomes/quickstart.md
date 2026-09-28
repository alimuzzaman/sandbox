# Quickstart and Acceptance: Recoverable Delivery Outcomes

These commands describe the interface to exercise after coding. Existing core implementation/evidence is tracked by tasks.md; the US6 commands in sections 7–9 are planned and unrun. Replace /absolute/sandbox, /absolute/app, registered-remote, environment, label and IDs with verified owned values. Do not invent a remote, source commit, operation or ownership identity.

## Execution order and evidence

Finish all production/config/docs work first. Then run supported command exercises below, record observed defects and fix the complete affected production set. Only afterward write focused regressions and run required gates. Planning and source reads do not satisfy runtime acceptance.

Before a remote job, workspace operation or protected deployment, record local Git/runtime revision and installed remote revision through the supported remote lifecycle/status command. If protocol is unsupported or mismatched, stop dependent effects; update only through the supported lifecycle with concrete authorization. Do not use raw SSH, Docker, curl or private-state reconstruction.

Store sanitized acceptance evidence under tmp/054-delivery-acceptance/ while working. Each case records exact candidate/application/control/installed revisions, target, original request/job/operation IDs, timestamps, actual command and exit/terminal result, protected-effect counters or owner snapshots, assertions and explicit missing proof. A deployment or DNS mutation below requires its existing explicit authorization on that owned target; the plan alone is not authorization.

Run candidate job commands with a dedicated SANDBOX_HOME. Startup reconciliation
visits the whole selected ledger; never use the shared active-job ledger when
testing changed identity formats. Use the same isolated home for submission,
status and bounded output. This protects concurrent jobs from older clients.

## 1. Plan and refusal

~~~sh
/absolute/sandbox/sb host plan --project-dir /absolute/app \
  --remote registered-remote --environment staging --json

/absolute/sandbox/sb host apply --project-dir /absolute/app \
  --remote registered-remote --environment staging \
  --request-id f054-direct-refusal-1 --confirm --json
~~~

On an owned acceptance target after implementation, the second command must refuse recovery_context_required before all protected effects even with a request flag. The first reports requires_submission or a concrete ineligible reason; it is never admission.

Observe optional partial memory/swap telemetry with authenticated stable identity separately from a required resource-policy refusal. Then exercise absent/unrecognized identity state, copied context from an unrelated retained job, dirty/mismatched application source, source/config/target drift, existing uncertainty owner, oversized receipt and interrupted/unavailable admission storage. Use disposable owned fixtures and supported fault controls, not edits to a real private authority store.

## 2. Genuine durable ordinary apply

Use the clean application as the durable job project; invoke the separate absolute Sandbox executable. This preserves application and control revisions:

~~~sh
/absolute/sandbox/sb job-start --local --project-dir /absolute/app \
  --timeout 1800 --request-id f054-apply-1 -- \
  /absolute/sandbox/sb host apply --project-dir /absolute/app \
  --remote registered-remote --environment staging --confirm --json
~~~

Retain the returned job_id. This explicit local controller is deliberate: it owns the hosted authority/journal and executes the authenticated deployment to the registered target. A remote controller requires its own registered workspace and verified executable/capability; never transplant the local application/control paths.

~~~sh
/absolute/sandbox/sb job-status ORIGINAL_JOB_ID --json
/absolute/sandbox/sb job-output ORIGINAL_JOB_ID --stream stderr \
  --tail-bytes 8192 --max-bytes 8192
/absolute/sandbox/sb delivery inspect --project-dir /absolute/app \
  --remote registered-remote --environment staging \
  --request-id f054-apply-1 --json
~~~

Require terminal job evidence, admission digest/time before the first effect, exact application revision, runtime/generation/initializer/edge evidence as applicable, and no duplicate initializer. An accepted job or an empty/malformed result is not completion. If acceptance is unknown, use the original request's read-only ledger lookup before any idempotent replay; never submit a second identity.

Interrupt immediately before/after admission and after an effect in an owned fault fixture. Inspect the original request. Use only the separately authorized existing recovery command returned by next_action; do not synthesize a new apply. Original and recovery requests remain distinct and linked.

## 3. Diagnosis and history

~~~sh
/absolute/sandbox/sb delivery inspect --project-dir /absolute/app \
  --remote registered-remote --environment staging --json
/absolute/sandbox/sb delivery inspect --project-dir /absolute/app \
  --remote registered-remote --environment staging \
  --operation-id ORIGINAL_OPERATION_ID --json
/absolute/sandbox/sb delivery inspect --project-dir /absolute/app \
  --remote registered-remote --environment staging --observe --json
~~~

Recorded-only calls must leave authority/journal/job/instance state unchanged. Observe mode may perform finite read-only probes and still leaves records/fences/generation unchanged. Repeat the same selectors through MCP delivery_inspect and compare meaning.

Use retained ordinary and immutable activation fixtures including success, initializer refusal, unknown required runtime revision, stale required edge proof and contradictory identities. Existing immutable authority remains decisive. Do not stage, rebuild or activate an image merely to answer diagnosis.

Retain success A then failed B on an authorized fixture. Require B as latest_attempt and A as latest_retained_complete_success. Check different application/control revisions, missing/expired detail, terminal/history bounds, pinned-record capacity, invalid/cross-target/expired cursors and post-effect journal failure. After detail and target metadata eviction, retry the original key with the same and changed intent: require expired/conflict with zero new operation/effects. Fill the permanent guard cap and require pre-effect refusal for new keys while existing lookups still work. A current healthy runtime must not manufacture older success.

## 4. Deploy exposure and preview

Configure the declared application contract from contracts/routes.md; WordPress may use its documented REST metadata default. Verify the exact requested primary and aliases.

~~~sh
/absolute/sandbox/sb deploy --project-dir /absolute/app \
  --remote registered-remote --source-ref EXACT_APPLICATION_COMMIT \
  --ensure --expose --domain primary.example.test \
  --alias alias.example.test --request-id f054-expose-1 \
  --verify-timeout 120 --json

/absolute/sandbox/sb preview create --project-dir /absolute/app \
  --remote registered-remote --name f054-preview \
  --request-id f054-preview-1 --verify-timeout 120 --confirm --json
~~~

The domains above are placeholders, not live acceptance targets. Use explicitly authorized public DNS names for real public proof. Retain configured/verified distinction, all host checks, elapsed aggregate time, exact instance/incarnation and cleanup effects.

Controlled negative cases: missing/wrong DNS, invalid TLS, another redirect origin, redirect loop, dropped/changed/repeated query, generic 200 without application marker, authentication rejection, wrong backend/release, one failed alias and stale required edge. Each remains non-success even when configuration and another route pass. At deadline the worker is reaped and result is incomplete. Optional unavailable release identity is visible; required unavailable identity refuses or remains non-complete.

Inspect deploy/preview with --label EXACT_RETURNED_LABEL. Reuse the original request for lookup; a duplicate request must return retained status without replay. A conflicting payload must refuse.

## 5. Creation and partial URL effects

Use two owned labelled instances with distinct sentinel data and public URLs. Observe new creation and reuse, lost response after the pending registry commit, nonterminal original child, changed incarnation and only one successful home/siteurl write. Also change each frozen intent field under the same request, evict receipt detail, exhaust guard capacity and interrupt after guard reservation but before registry commitment in owned fixtures. Require conflict/expired/unknown with no replay; guards remain discoverable independently of the current instance row. Use supported ensure/deploy/preview and owner readback; no inventory difference or private JSON editing.

Require exact request/project/label/incarnation joins, typed partial URL result, zero unrelated data/URL changes and no deletion authority for reused or unproved instances. Preview's existing exact route/DNS cleanup may be observed when already authorized; retain removed/cleanup_failed/unknown evidence. Never delete an instance to prove a diagnostic requirement.

## 6. Regressions and gates, after actual command exercises

Author tests/test_delivery_models.py, test_delivery_repository.py, test_delivery_admission.py, test_delivery_service.py, test_delivery_routes.py and test_delivery_cli_mcp.py from the observed cases. Extend the relevant existing hosting recovery, job registry, deploy, preview and instance tests for integration boundaries. New/changed captured subprocesses use tests.subprocess_support.run_test_process or synthetic_environment; never copy or enumerate os.environ.

Use finite durable jobs for focused and full suites. Example local full gate, after resolving the actual repository Python path:

~~~sh
/absolute/sandbox/sb job-start --local --project-dir /absolute/sandbox \
  --timeout 3600 --request-id f054-full-tests-1 -- \
  /usr/bin/env PYTHONPATH=/absolute/sandbox \
  /absolute/sandbox/.cli-venv/bin/python -m unittest discover -s tests -v
~~~

Read terminal status and bounded output by the returned job_id. Run existing required manifest/CLI/MCP coverage and runtime-revision checks included by the repository gate; any additional gate discovered in current repository guidance must be named and recorded. Compare failures against the unchanged baseline using the same environment. A configured test lane, accepted job or unrun optional runtime is not a pass.

Final evidence distinguishes source checks, actual ordinary-host admission/recovery, immutable recorded diagnosis, real deploy/preview public routes, controller capability/revision and unavailable protected/optional-runtime proof. Independent correctness and product reviewers inspect that evidence and the stable diff before final acceptance.

## 7. US6 literal preflight and one trace query

Complete all T061–T070 production/docs work first. Use the verified W11 checkout from plan.md, its required Node 24.18.0, an isolated Sandbox home and the same candidate executable for creation/query. Configure owned synthetic external ports through the existing supported test seams; never edit real private records or fetch credentials to satisfy a fixture.

~~~sh
./deploy dev --preflight --json

/absolute/sandbox/sb delivery inspect --project-dir /absolute/w11 \
  --trace-id ORIGINAL_TRACE_UUID --json
~~~

The literal first command runs from the W11 checkout and emits exactly one JSON document. Passing preflight means command_result=succeeded and deployment_result=not_started; it creates no workload job or activation. Failed/blocked checks retain a trace when bootstrap owner access works. A missing launcher/runtime/default executable explicitly reports unavailable, never a fabricated retained ID. Compare the query with MCP delivery_inspect(project_dir,trace_id), including early stage times, check results and command/deployment distinction. No manual private-file joins are allowed.

For uncertain start acknowledgment, use the persisted original invocation request:

~~~sh
/absolute/sandbox/sb delivery inspect --project-dir /absolute/w11 \
  --trace-request-id ORIGINAL_TRACE_REQUEST_UUID --json

/absolute/sandbox/sb delivery inspect --project-dir /absolute/w11 \
  --trace-id ORIGINAL_TRACE_UUID --mutation-id ORIGINAL_MUTATION_UUID --json
~~~

The first resolves the stored scope from its project/request locator, including before producer/target resolution. The second returns the original accepted mutation receipt. Reuse original IDs and payload only after a conclusive absent lookup; ambiguous reads stop effects. A changed target/producer under the same locator must conflict. Query must work after terminal/detail expiry with explicit guard coverage and no state creation.

## 8. US6 retained-image owner pipeline and reconnect

Run the supported W11 normal command with --json under an owned fixture whose existing release/artifact/native transport ports return fixed validated retained-bundle evidence. Exercise actual cli.ts, state.ts, job.ts, worker.ts and native.ts paths plus Sandbox CLI/service owners; stub only external network/runtime boundaries. This local fixture is synthetic workflow proof. Running the real ./deploy dev against an actual remote requires separate concrete deployment/credential authority and is not authorized by this quickstart.

Require the following observations from one returned trace query:

| Case | Required output / invariant |
|---|---|
| Retained bundle through prepare/stage/activation/runtime/public checks | Requested release/source/policy, exact artifact/config/plan/proof/generation, observed runtime source/images/health and initializer/routes/edge evidence with original times; zero image builds. |
| Job roles | Parent, parent-prepare job, parent-stage request, and parent-activate job/request in distinct roles. Wrapper control revision never becomes application release revision; original worker argv/submission bytes stay identical. |
| Pre-effect worker trace | Parent-keyed stage-enter is committed/read back before each native protected phase. Failed/unknown trace publication stops the dependent effect while retaining original job authority. |
| Main disappears after child completion | Invocation command result stays unknown; native child outcome appears separately with exact proof. No second job or initializer is submitted. |
| Reconnect/new invocation | New trace may attach the same stable parent. Stale snapshots cannot erase known links or terminal outcomes; unobserved earlier invocation stages remain unavailable. |
| Legacy diagnostic export | Explicit producer read uses legacy_projection and existing owner validators; early history remains unavailable, existing private run/terminal bytes are unchanged, and query runs no project code. |
| Failed original followed by authorized recovery fixture | Original failure/digest remains fixed; related child is separate, inherited fields are labelled original deployment metadata, and omitted/expired recovery coverage is explicit. |

Use original parent/publication IDs with trace-owner-status for lost worker acknowledgment. Replay the same publication only after conclusive absence; CAS conflicts read current sequence and preserve identical content/ID if the monotonic transition remains valid. Never add a trace argument to worker argv or mint a workload retry request. Existing stage/activation recovery fences remain decisive.

## 9. US6 fault, output and gate evidence

Exercise these through actual CLI/MCP/producer adapters on owned fixtures before writing regressions: wrong parent/role/project/control source/application source/target/artifact proof; unknown or changed executing runtime; dirty preflight; same-ID changed intent; start/record/publication lost acknowledgments; crash/store failure before versus after effects; terminal replay/conflict; trace/parent expiry; 4,096 shared guards; protected/terminal/receipt/byte capacities; bounded 640-row recovery scan; >32 valid native images; query deadline and output elision; strict old preflight report compatibility; synthetic secret redaction. Every refusal retains original safe selectors and makes zero unauthorized downstream calls.

Check original delivery output too: complete failed gives explicit failure/stage without a missing-proof loop; complete nonterminal without a usable child read gives authority_pending; active original job or incomplete immutable owner may offer a real read command without erasing failure context. Observe actual writer events and unchanged legacy terminal hashes. CLI/MCP payloads carry role evidence detail, not only IDs. Omitted required proof cannot retain joined success.

Capture actual maximum elapsed time and bounded decode/serialization overrun for the cooperative five-second query budget. Hash/count existing fixture state before/after read-only queries and require no create/migration/prune/reconciliation or child execution. Owner-supplied full proof must survive the closed query codec; optional omission remains valid JSON under 256 KiB with explicit coverage.

After these actual observations, author focused trace/W11 regressions and complete outstanding core regressions, then run finite durable Sandbox focused/full gates plus W11's current required typecheck/lint/Prisma-manifest/feature-registry/architecture/API-contract/targeted-deployment scripts. Record the actual Node/heap limit and current baseline. Independent correctness/product review follows the stable diff and observed evidence. Unrun real remote/runtime/public deployment remains unverified even when the synthetic retained-image pipeline passes.
