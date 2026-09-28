# Why Sandbox does not reliably deliver a working instance or deployment

Review date: 8 September 2026. Source baseline: `fd7d650f8bfbb1760d931e2484cc01fa0badf546`.

Sandbox has many of the needed mechanisms, but several handoffs lose the identity or evidence needed to finish safely. A successful command can mean a process exited, a request was accepted, or a route was written. It does not consistently mean the intended instance or release is usable. Other paths change the runtime before discovering that their proof cannot be accepted. Agents then reconstruct the missing evidence through repeated status, log, and controller checks.

This review found **nine defects reproduced at synthetic source boundaries or through a measured read-only transport call**, an additional deployment-entrypoint failure observed in six real attempts, and six actionable gaps in recovery, completion evidence, tests, and release gates. They explain concrete ways the product can fail. They are not a measured failure rate for all Sandbox use. Later evidence also establishes a successful Amar Sonar reconciliation after separately owned initializer fixes; the pinned source findings remain distinct from that corrected runtime.

## Findings, in priority order

P1 means fix before relying on the affected path for important deployment/data work. P2 means a bounded reliability or evidence defect that should follow in the same repair program. These priorities do not authorize runtime changes.

| ID | Priority | Finding | Evidence level |
|---|---|---|---|
| F01 | P1 | Failed deploy cleanup can delete another caller's new instance | Reproduced with synthetic interleaving |
| F15 | P1 | Separate backup sets can reuse an earlier cached content capture | Real coordinator/request calculation and generated cache branch reproduced with synthetic content |
| F02 | P1 | Preview URL updates can target the existing default instance | Command construction and real selector reproduced; historical report corroborates |
| F16 | P1 | Default ensure JSON can expose an installation login link before the final result | Actual progress expression and CLI boundary reproduced with a synthetic marker |
| F03 | P1 | Hosted initializer proof compares incompatible Compose identity formats | Generated proof reproduced; upstream format verified; historical report corroborates |
| F04 | P1 | WordPress ensure can return ready after HTTP readiness times out | Fresh and multisite control flow reproduced |
| F14 | P1 | The promised Lenzora deployment command never reaches deployment | Six actual terminal attempts, dev and prod; adjacent application entrypoint |
| F05 | P1 | Ordinary host apply can start without a recoverable operation receipt | Confirmed source path; historical reports corroborate |
| F06 | P1 | Default acceptance does not exercise the promised create/deploy outcomes | CI and test-entrypoint inspection |
| F07 | P1 | PostgreSQL restore verification blocks a real drill and has a narrow schema contract | Retained real drill plus source inspection; semantic equivalence remains unproved |
| F08 | P2 | Generic Compose status reports ready from empty or malformed output | Adapter reproduced |
| F09 | P2 | Force cancellation fails after ordinary cancellation | Real cancellation method reproduced with fake process identity |
| F10 | P2 | Job history exceeds the client envelope and hides the cause | Live size/shape measurement and synthetic boundary probe |
| F11 | P2 | Expose/preview success does not prove the public URL works | Confirmed completion path and route helper inspection |
| F12 | P2 | Deployment evidence cannot answer the last successful exact release | Live read-only status plus provenance inventory |
| F13 | P2 | Some source tests depend on real Docker and write outside their fixtures | Observed test invocation plus fixture and adapter inspection |

### F01 — Cleanup infers ownership from absence in an earlier inventory

[`deploy.py:67–125`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/commands/deploy.py#L67) compares instance names before and after a failed ensure. If exactly one new name has the default label, it deletes that name. The deletion helper removes the named instance and its Docker data. See [`_remote.py:1551`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/core/_remote.py#L1551).

Trigger: caller A inventories an empty target; caller B creates its default instance; A's ensure fails or its completion becomes unknown; A's cleanup sees B as the unique new instance. The probe records deletion of `created-by-caller-b`. No request, incarnation, creator receipt, or proof that A has stopped is required by this cleanup decision.

Impact: another caller's data can be removed, or cleanup can race an ensure that is still running after transport loss. No real deletion was attempted in this review.

Fix: require an exact creation receipt and terminal ownership proof before cleanup. A name difference is not deletion authority. On unknown completion, retain the candidate and reconcile the original request. Add the interleaving and timeout cases to the existing deployment tests.

### F15 — Capture replay identity does not distinguish separate backup sets

[`ScopedMaterializer.publish:51`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/recovery/materialize.py#L51) receives a backup `set_id` but does not pass it to the capture adapter at line 82. [`HostedRecoveryMaterializer._request_id:102`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/recovery/hosted.py#L102) hashes the remote, source binding, and artifact declaration. The registered WordPress controller's binding covers topology, mounts, repository and runtime revision, not database/media content. See [`remote_recovery.py:139`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/transports/remote_recovery.py#L139).

Two separately requested backups after a content-only edit can therefore use the same controller request. The generated [`capture program:244`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/transports/remote_recovery.py#L244) returns the existing request-named archive before taking another database dump or filesystem capture.

The probe executes two real coordinator publications with distinct synthetic set IDs, a fixed synthetic source binding and a pre-existing synthetic cached capture. Both use the same controller request and publish the old content. The actual generated cache program exits successfully without reaching a capture subprocess. Native tar validation and an actual live content update were not exercised. This corroborates feedback `66035a8ff701c7010103f76ab117d838`; no stale production backup is asserted.

Impact: a newly named backup can preserve an earlier content state while appearing newly published. Fix: bind capture replay identity to a durable backup operation/set identity. A retry of that same operation must reuse its receipt, while a distinct requested backup must capture current content. Include content-only changes, same-set retry, interrupted capture, and cross-set isolation in acceptance. Do not delete retained captures as an improvised fix.

### F02 — Preview loses the instance identity immediately before changing WordPress URLs

[`preview.py:182`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/commands/preview.py#L182) passes the preview label through ensure and apply and checks their instance identity. At line 199 it calls a URL helper that takes no instance or label. [`_remote.py:1585`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/core/_remote.py#L1585) then runs unqualified `sb wp option update home` and `siteurl` from the shared project directory.

With a default instance and a preview instance registered for that root, the actual selector returns the default record when no label is supplied. See [`_instances.py:335`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/core/_instances.py#L335) and [`cli.py:1408`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/cli.py#L1408). The probe selected `existing-default`; an explicit preview label selected `new-preview`.

Impact: the existing site can start redirecting to the preview hostname and its admin can break. Feedback `b572b3d55192b8bc9e7c70dec0722d9f` reports this outcome, but the present review reproduced only the source boundary. Existing tests mock the URL helper and miss its constructed command.

Fix: carry the exact returned instance through both option updates, validate it against the project/label, and verify both results on that identity. Apply the same correction to remote deploy.

### F16 — Final JSON redaction does not cover earlier installation output

[`cmd_ensure:520`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/commands/instances_cmd.py#L520) invokes the runtime before printing the final redacted JSON. Fresh ensure calls [`cmd_install:1401`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/core/_instances.py#L1401), which unconditionally prints the newly created login URL in its [`completion message:1298`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/commands/lifecycle.py#L1298). Later plugin activation can still fail.

The probe extracts that exact progress expression, substitutes a synthetic login marker, and runs it through the actual `cmd_ensure` failure boundary. With JSON enabled and reveal-login false, the marker remains in earlier stdout; the final error JSON omits it and exits 1. No real credential was accessed or printed. Feedback `6704dc49fbf1d94c5578a7857e34010e` reports the corresponding disposable-runtime failure.

Impact: default command capture/logging can retain a usable login link despite the opt-in reveal contract. The existing final formatter is a useful partial safeguard, but does not fix this path. Fix output at the producer and carry reveal authority explicitly; keep all default progress/error output secret-free, including failures before the final envelope. Test the complete stdout/stderr stream, not just its last JSON line. This needs human review before release.

### F03 — Valid initializer evidence is rejected after Compose has changed the runtime

[`hosting.py:922–941`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/commands/hosting.py#L922) compares the entire `compose config --hash SERVICE` output to the bare config-hash label. It also compares quiet Compose image output directly to Docker inspect's image identity.

Compose emits the service name followed by its hash, and quiet image output removes the algorithm prefix. These formats differ from the label and inspect values. Verified against the [upstream hash writer](https://raw.githubusercontent.com/docker/compose/main/cmd/compose/config.go) and [quiet image writer](https://raw.githubusercontent.com/docker/compose/main/cmd/compose/images.go), inspected on this review date.

The generated proof program returns `foreign` for these valid formats and `succeeded` for the invented matching formats used by the tests. Each mismatch independently causes refusal. The foreign-project negative control remains refused. [`test_hosting.py:956`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/tests/test_hosting.py#L956) is the fixture gap.

Compose convergence happens before this check, at [`hosting.py:1066`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/commands/hosting.py#L1066). The later refusal prevents duplicate initializer execution, which is necessary, but leaves the deployment unfinished. Feedback `0f0960d8c6ce36083fb195fb4a9fb3de` corroborates the format problem.

Fix: parse one exact service/hash record and canonicalize full image identity at the adapter boundary. Keep project, service, creation epoch, ambiguity, terminal exit, and no-replay checks. Validate with real Compose output before releasing the fix.

A separately owned Amar Sonar deployment supplied an additional failure after the format-only fix `0942d14`: `wp-init` still returned foreign evidence. Its retained diagnosis shows five healthy services, requested/staged application `30b2ec2`, recorded `4802dad`, observed revision unavailable, and edge/runtime pending. Successful editor/public-index markers are component evidence, not completion of the refused apply. See the [Amar Sonar evidence index](research/asb-deployment-evidence.md).

The owner traced the remaining mismatch to `env_file` resolution on Compose 5.4.0. That version's [hash implementation](https://raw.githubusercontent.com/docker/compose/v5.4.0/cmd/compose/config.go) builds the model without resolving service environment, unlike current upstream main's explicit resolution step. The saved resolved-hash capture now matches all six initializer labels. Source correction [c7ed709](https://github.com/alimuzzaman/sandbox/commit/c7ed709ba35ef2485c4e3d71ccb80ad09207be2c) was independently inspected here: it passes resolved JSON privately over stdin before hashing and retains identity/time/exit guards. Its 180-test result and installed runtime revision are supplied owner evidence. Normal second-pass interpolation is required because Compose JSON already escapes dollar signs; the owner's earlier `--no-interpolate` proposal was corrected.

Later saved final apply/status evidence establishes successful reconciliation: generation 10, requested/staged/recorded/observed application revision `30b2ec2`, five healthy services, and runtime/source/edge ready. Final browser markers also pass. This reconciled the existing runtime; it does not prove a fresh initializer rerun, and the exact editor bundle digest is an owner assertion rather than a digest printed in those files. W2 should review/reuse the existing correction and this acceptance evidence, then fill only the remaining declared test gaps. The Lenzora entrypoint failures remain separate.

### F04 — A failed WordPress HTTP wait does not prevent ready state

[`_wait_http`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/core/_instances.py#L608) returns false on timeout. Fresh ensure ignores that result at [`_instances.py:1406`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/core/_instances.py#L1406). The multisite restart path also ignores `_wait_reachable` at line 1442. If later plugin/theme and snapshot steps succeed, line 1475 records `ready`.

The synthetic fresh and multisite probes both observed `pending → ready` after false readiness results. WordPress installation through CLI does not establish that the web tier can serve the user. This is a control-flow defect; a dead live web tier was not created for this review.

Fix: require bounded backend, installed-state, and advertised-route evidence before ready. On failure, keep an owned resumable state and return the failing stage and a concrete retry command. Preserve the backend-only wake probe so validation does not recursively enter activation middleware.

### F14 — The promised deployment command fails before image selection

The parent task supplied six actual dev/prod attempts from approximately 17:14–17:17Z. Every attempt exited 1 before image selection or activation. The [attempt evidence](research/deployment-attempts.md) preserves the safe log index and provenance.

| Invocation, repeated for dev and prod | Observed stopping point |
|---|---|
| `pnpm deploy dev` / `pnpm deploy prod` | `ERR_PNPM_NOTHING_TO_DEPLOY`; pnpm's built-in deployment command runs |
| `pnpm run deploy dev` / `pnpm run deploy prod` | `ERR_PNPM_UNSUPPORTED_ENGINE`; required Node 24.18.0, host v26.5.0 |
| `bash scripts/run-with-repository-node.sh pnpm run deploy dev` / `prod` | Node 24.18.0 selected; application preflight rejects Sandbox runtime mismatch |

The [Lenzora runbook](https://github.com/alimuzzaman/lenzora/blob/9021d63f7e93357230321a037a044023dc35664d/docs/runbooks/hosted-production-images.md#L3) promises the first invocation. Its [package script](https://github.com/alimuzzaman/lenzora/blob/9021d63f7e93357230321a037a044023dc35664d/package.json#L25) cannot override pnpm's existing command. pnpm documents that [script shorthand only works without a built-in name collision](https://pnpm.io/10.x/cli/run); [`deploy` is already a built-in command](https://pnpm.io/10.x/cli/deploy).

The Node wrapper is inside the package script, so it cannot fix an engine rejection that happens before the script starts. Moving the wrapper outside pnpm reaches the intended CLI, but then [`defaults.ts:61`](https://github.com/alimuzzaman/lenzora/blob/9021d63f7e93357230321a037a044023dc35664d/scripts/hosted-deployment/defaults.ts#L61) correctly refuses client/controller skew. The independently observed revisions were local `b1c44aad3fde1cf9f42d05a6` and installed `e0949074e80c469bcff36c12`; the logs themselves do not print those hashes.

The parent records clean Lenzora `9021d63` and clean Sandbox `ef5a239` during all attempts, with unfinished verifier files separately preserved and restored. These are provenance assertions in the supplied summary; the failure logs independently establish the stopping gates. No build, deployment job, controller update, or production mutation was reported.

Fix: test the exact public shell entrypoint, put runtime selection before package-manager engine admission, and surface all prerequisite mismatches in one pre-effect check. The exact `pnpm deploy dev/prod` acceptance requirement remains unmet. A package script rename or documenting `pnpm run` is not proof that the original literal command works. Do not bypass the valid Sandbox revision guard to make the command proceed.

### F05 — Recovery eligibility is discovered too late

Ordinary host apply catches a missing stable-machine projection and substitutes `machine_identity=None`. `_accept_hosting_operation` then returns no receipt, while apply continues into source staging and runtime effects. See [`hosting.py:2995`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/commands/hosting.py#L2995) and [`hosting.py:1801`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/commands/hosting.py#L1801).

The ordinary identity path requires complete resource evidence; image staging has a separate identity-only allowance. See [`hosting.py:1696`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/commands/hosting.py#L1696). Thus an optional resource observation can leave an ordinary apply without later recovery authority. Missing durable context or an ineligible source can do the same.

This compatibility behavior is explicit in the code. The product gap is that an important apply can begin without making that limitation a pre-effect result. Feedback `29031eb6f69e28ef4aea7ec5fdad2b3c` and `3126f1d654a039c32481c0522d831870` report the resulting recovery dead end.

Fix: expose recovery eligibility in the plan, and require the promised receipt before effects for the recoverable deployment path. Separate authenticated identity from optional resource telemetry without accepting unknown identity. Do not repair this by weakening recovery refusals or inventing request identities.

### F06 — The normal gate tests components, not the promised user outcomes

[Python CI](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/.github/workflows/python.yml#L1) runs `./sb selftest` on pull requests and pushes to `latest`. [Runtime smoke](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/.github/workflows/smoke.yml#L1) is manual. Hosting tests substitute remote execution, Compose, health, and edge collaborators. Installer and desktop checks are largely source/packaging checks; desktop CI uploads an unsigned package.

This gap has concrete examples: F03's fake wire formats pass while the generated proof rejects real formats; F04's timeout result is not checked; F08's generic status test accepts `started` as its process output. The retained real lifecycle smoke initially failed the restart clean-URL check after 22 other checks passed. A later wake-fix run passed all 23 checks. That is evidence of both a real missed integration defect and an effective runtime acceptance test.

Fix: make a disposable local lifecycle lane a required gate for affected changes, plus a separate authorized remote/image/recovery release lane. Retain the fast source suite. Bind every acceptance result to the exact source, runtime, fixture, and terminal job. Open release gates are already documented in [`release-readiness.md`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/docs/release-readiness.md#L18).

### F07 — Restore verification cannot yet establish the required result

The retained Lenzora development drill matches all 307 table counts, 4,264 ordered column records, the migration checksum, and other recorded comparisons. It still reports `all_match=false` because 19 constraint definitions differ. The successful `restore_inspected` envelope means inspection succeeded; it does not mean restore verification succeeded.

[`postgres_helper.py:30`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/recovery/postgres_helper.py#L30) fingerprints reconstructed constraint text and public column type/nullability. PostgreSQL documents that [`pg_get_constraintdef` reconstructs a command](https://www.postgresql.org/docs/16/functions-info.html), so textual inequality alone is not a general semantic comparison. The masked diagnostic shapes do not prove that these 19 differences are harmless.

The same projection excludes column defaults and type modifiers, standalone indexes, sequence state, triggers, and non-public schemas. Some constraints imply indexes, but that does not cover all indexes. Matching table counts also does not prove every row value is equal. These are limits of the present evidence contract, not a demonstrated corrupt restore.

The original task has already committed an [archive-bound reference-restore correction plan](https://github.com/alimuzzaman/sandbox/blob/ef5a239dd1f4e443f63ecc546f373d70a50c6adc/docs/postgresql-restore-schema-verification-plan.md#L1). Its implementation was concurrently dirty when inspected and is not accepted by this review. Use that owner for the bounded correction. Separately specify the schema/data coverage that a future “verified backup” claim guarantees. Never normalize away parentheses/casts, accept counts alone, or alter retained/source data to make the comparison pass.

### F08 — Generic Compose status treats missing evidence as ready

[`compose.py:353–385`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/runtimes/compose.py#L353) parses dictionary rows only. When no rows parse and the command exits zero, it deliberately returns ready for compatibility.

The probe returns `ok=true`, `status=ready`, and observation freshness `live` for empty output, `[]`, malformed text, and an array containing an exited service. A dictionary row for that exited service correctly returns stopped. Empty output can also occur when no relevant containers exist.

Fix: support the declared Compose JSON formats, distinguish no container from malformed/unavailable evidence, and require the selected service's positive observation for ready. Preserve compatibility through an explicit adapter/version contract, not a success fallback.

### F09 — Force escalation is unreachable once cancellation has begun

[`JobService.cancel:975`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/application/job_service.py#L975) always transitions a nonterminal job to cancelling before signaling. The [transition table](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/jobs/models.py#L107) disallows cancelling → cancelling.

In the probe, normal cancellation records synthetic signal 15. A subsequent force request raises `invalid job lifecycle transition: cancelling -> cancelling` before signal 9 can be recorded. Feedback `5a30b5cda8378a014d0ae9b15cdf826d` and `aaa61571984fb074981f7608e6423aeb` report the same failure class.

Fix: make repeated cancel/force requests state-aware and idempotent. Revalidate process ownership before any signal and handle a concurrent terminal transition. Add normal-then-force, repeated-force, terminal-race, and stale-process controls. No real signal was sent here.

### F10 — A valid job-list page is too large for the client

At `2026-09-08T17:01:43Z`, a read-only 100-job request returned one valid JSON line of **1,181,179 bytes**, with `ok=true` and 100 records. The client's limit is **1,048,576 bytes**. [`_last_json:242`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/transports/remote_jobs.py#L242) rejects the entire response; [`control:780`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/transports/remote_jobs.py#L780) falls back to “remote exit code 0.” The CLI exits 1. A three-job page succeeded in the earlier bounded check.

The [measurement wrapper](evidence/inspect_job_page.py) records only lengths, validity, count, and parser acceptance; it neither prints nor saves the raw jobs. [Diagnostic output](evidence/remote-job-page-diagnostic.json) preserves the result. Client/controller revision skew was also observed, but it is not needed to explain this failure.

Fix: use a compact, byte-bounded job summary page with an exposed continuation cursor and separate detail lookup. Preserve output bounds. Return a typed oversize/truncation error with counts/limits, not a misleading successful process exit. Large single records must also have a defined bounded representation.

### F11 — Writing a route is treated as completing exposure

The [route helper](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/core/_remote.py#L1479) writes Caddy configuration, validates it, enables the service, and reloads it. [`deploy.py:323–374`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/commands/deploy.py#L323) then assigns the public URL and returns success. Preview similarly completes after DNS/route/option writes.

Neither path independently observes DNS resolution, the TLS handshake, the public route, or the intended application's response before claiming completion. A valid configuration and successful reload cannot establish those facts. This finding concerns deploy/preview exposure; it does not claim that the separate immutable-host activation protocol lacks its own edge checks.

Fix: expose separate configured and verified states and make the requested exposed-site outcome depend on bounded public-route evidence. Include redirects, query strings, wrong-backend responses, and certificate failures in acceptance. On verification failure, keep the original request and known effects visible.

### F12 — Deployment history is spread across stores and lacks a complete public join

The live production host observation saved in [production-host-status.json](evidence/production-host-status.json) has null requested/staged/recorded/observed/deployed revisions, generation 0, unknown runtime/edge, unavailable health, and 18 unknown service states. The separate [image activation status](evidence/production-image-status.json) retains an uncertain activation with `effect_entered=true`, five results, two recoveries, and no current generation digest.

These are successful observations of unknown/uncertain state. They do not prove an outage, a failed historical deployment, or a currently healthy exact release. They do show that the supported records cannot answer “what was the last successful production deployment?” from the inspected evidence.

The [provenance inventory](research/provenance-inventory.md) maps local release files, staging ledgers, activation state, ordinary host receipts, jobs, and instance records. Ordinary apply keeps the latest operation; job records can identify the control checkout rather than the application release; local release attachments retain other parts of the join. No single retained public record joins all completion evidence and timestamps.

Fix: provide a compact append-only outcome receipt and a supported query that joins the target, application source, Sandbox runtime, artifact/proof, request/job, generation, runtime health, and route/edge evidence. Historical unknown state must remain unknown; do not backfill success from a current healthy container.

### F13 — Test isolation and fixture fidelity need their own gate

The 88-test lifecycle run passed in 2.205 seconds. Its command set only `PYTHONPATH=repo`. Ready WordPress fixtures did not patch daemon preflight and reached the real read-only [`docker info` path](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/core/_docker.py#L662). Generic Compose fixtures used temporary source roots but allowed [`_overlay`](https://github.com/alimuzzaman/sandbox/blob/fd7d650f8bfbb1760d931e2484cc01fa0badf546/sandbox/runtimes/compose.py#L212) to write under inherited Sandbox home.

This review therefore did create test-generated Compose overlays outside the checkout through existing tests. No instance start/stop/delete, database change, or deployment was performed. The exact inherited home was not captured; no broad cleanup was attempted. The later custom probes explicitly fake preflight and overlay boundaries.

Impact: source checks depend on machine state, can leave artifacts, and can pass without proving the behavior their output appears to summarize. Fix the affected fixtures to use explicit temporary homes and fake system boundaries. Keep separate real adapter contract tests so isolation does not reproduce F03's unrealistic wire fixtures.

## What the broader evidence says

The full feedback pagination captured 748 unique records in eight pages: 101 unreviewed, 109 blocked, 268 resolved, 112 verified, 73 duplicate, 82 not applicable, and three invalid. Keyword families include 136 deployment/activation records, 97 jobs/transport, 76 startup, 34 recovery, and 29 clean-URL records. They overlap. All 166 high/critical records matching those families were examined in detail: 43 unreviewed, 27 blocked, 54 resolved, 33 verified, seven duplicate, and two not applicable. The initial 70-candidate filter means unreviewed/blocked, not a complete count of unresolved defects. Status labels and words such as “live” in a closure are not independent acceptance evidence.

The initial summary filter missed several direct ensure/host-apply reports, so detail coverage was expanded to **all 278 high/critical records plus three selected medium records**, 281 successful reads with zero failures. The remaining 467 medium/low records have metadata coverage only. The substantive [feedback review](feedback-review.md) distinguishes current reproduced defects, source-fixed but stale open records, scoped prior fixes, and unverified operational leads. It added F15 and F16. For example, the old large-page error-tail leak was correctly closed within its documented scope; that closure explicitly did not claim a successful large-page replay. The remaining pagination defect is F10, not proof that the earlier closure was false.

The retained full-suite job is terminal successful: 5,678 tests, 13 skipped, exit 0. Its inspected record does not bind that result to a source commit. Retained runtime evidence includes the later 23-check lifecycle pass and a real reopen canary with an explicit source commit plus a dirty-source digest. The earlier lifecycle failure is not reported as an unfixed current regression. These results show useful mechanisms working; they do not establish production activation or every supported platform.

The observed pattern is therefore specific: **contract mistakes at real adapter boundaries, incomplete completion checks, missing ownership in older paths, and acceptance gates that stop before those boundaries are exercised together**. Controller/version churn and sparse diagnostics increase recovery work once a failure occurs. They are contributors, not a substitute explanation for the concrete defects above.

## Coverage and limits

The inventory spans core Python, CLI/MCP, local WordPress and generic Compose lifecycle, activation/hosting/recovery, remote jobs, tests/CI, installers, desktop/web, instructions, feedback, and adjacent Lenzora deployment entrypoints. Deep source review and probes concentrate on create/deploy/recover outcomes. The code inventory contains roughly 159k core LOC and 138k test LOC; this is not a claim that every line received substantive review. See [coverage inventory](research/coverage-inventory.md).

New evidence consists of supported read-only status/job queries, the instrumented page-size observation, nine synthetic probe families, and the 88 focused tests with the isolation limits above. Existing operational artifacts were inspected and indexed separately. No new full suite, browser session, local instance lifecycle, remote deployment, image build, restore, credential operation, or release was run by this review. Six actual entrypoint attempts were supplied by the separately authorized parent task; all failed before image selection or activation. Amar Sonar's separately owned final reconciliation succeeded with the limits stated in F03. Successful inspection, accepted jobs, passing source tests, image staging, and deployment completion remain separate evidence categories.

The review worktree stayed pinned to `fd7d650`. The original checkout advanced to `ef5a239` with a schema-verification plan and had concurrent recovery source edits. Those files were preserved. None of the proposed repairs in this report has been implemented or accepted by this review.

The [research record](research.md) preserves candidate dispositions, instruction/workflow analysis, sources, and all sanitized inventories. The [repair plan](plan.md) defines ownership, order, tests, and release evidence for each work package.
