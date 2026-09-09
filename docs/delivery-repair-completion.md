# Delivery repair completion

Goal: complete the remaining packages in the [repair plan](audits/2026-09-08-sandbox-delivery-review/plan.md), including their observed acceptance and exact release limits.
The first batch is committed at `eec81ed00463679a010d077938e7f68305d1a2c2`;
its [implementation record](delivery-repair-implementation.md) retains the results.

## Current merged candidate — 9 September 2026

Sandbox candidate `0b9f7a3` combines the delivery/trace package `d21df4f`,
settlement diagnostics and activation preparation with the existing PostgreSQL
verifier work. Lenzora entrypoint/trace package `5a80ea8c9` is merged locally into
its pushed `latest` at `b1d4ea732`. Existing unrelated latest work is preserved.
The first combined Sandbox gate `8abeec459e7c766ee030f445e893020f` ran 5,875
tests and failed with seven failures, five errors and 13 skips. The observed
causes were stale MCP inventory/dependency expectations, missing explicit
project-root and engine/CA fixtures, a host-dependent proxy probe, and the long
isolated home path breaking the real GPG fixture. Production code was unchanged.
The corrected eight-module gate `0c4498baf611847beadc4bf842bf6225` passed all
223 tests in 27.642 seconds, including the real GPG round trip with a short
isolated home. Full replacement job `9c43dc92746beb7b3f21c291c54b18dd` passed: 5,875 tests
in 384.309 seconds, 13 skipped, exit zero and complete output. All source/test
hashes matched the submitted candidate after the run. The installed runtime
update and production continuation remain separate next steps. Earlier
failed gate and preflight records below are historical and remain retained.

The final actual trace exercises passed on isolated CLI/MCP and owner fixtures:
start/record lost-ack replay, immutable parent/revision binding, complete failure
and pending-authority output, bounded events/retention/omissions, and interrupted
main with completed child jobs. Literal W11 preflight produced one JSON result
for success, refusal and unavailable trace service. Synthetic native proof is
kept separate from real remote runtime/edge proof.

Three observed source defects were corrected before regression tests: unsafe
hosting event text, mutable trace parent/source binding, and SQLite read-only
connections writing WAL shared-memory read marks. The private snapshot rerun
`ed2fd4e7f1395158cec6fe9482f0a520` passed 13 cases without changing paused-owner
DB/WAL/SHM bytes; 150 concurrent reads returned 124 coherent results and 26
explicit partial results, with no mixed snapshots. Shared deadline exhaustion
remains a partial outcome; the locked-owner exercise took 5.0413 seconds against
a five-second budget. Other platform and near-cap runtime proof remains open.

The independent correctness reviews found no remaining blocker. Product review
found omitted-event counts missing from human output; the corrected literal CLI
showed 192 omissions and preserved owner bytes, then its regression passed.
Focused trace/model/store tests, snapshot-reader tests and deployment contracts
passed. W11's seven source gates passed, including 110 deployment contract tests;
the combined Lenzora revision also passed all six static checks and 110
contracts. Two default five-second test timeouts in the first combined run
passed on the unchanged source with a bounded 30-second limit (job
`b74ac7898ffdd6a656de107a2546b600`). The installed Cosign accepts its hidden
deprecated flags: preflight now probes parser acceptance and retains the original
signature verification contract. Actual retained bundle checks passed.

Production continuation is separately authorized. It retains the original
uncertain activation identity and signed artifact. A reviewed, fully gated,
clean Sandbox revision must be installed through the supported lifecycle before
continuation; local fixtures do not authorize a new activation identity or build.

## Installed runtime and production checkpoint

The supported service migration completed successfully from the reviewed clean
Sandbox candidate `bf26e579bb9c1b761b66bf651c3901e499b6ddb6`. Independent service
status confirms local and installed runtime `6daeb17dadbeaca2574c78b9` match,
with the service active, authenticated and ownership proven. This closes the
runtime-version prerequisite; it is not application deployment proof.

The original Lenzora production activation remains uncertain at generation zero
with `effect_entered=true`. Its read-only settlement observation reports
`not_quiescent` / `helper_activity_present`. The first exact containment plan
covered 17 container IDs, 14 running and three stopped, preserving all volumes.
Its apply refused `evidence_changed`; the subsequent inventory retained the same
IDs, running states and restart policies. A newly requested plan itself then
refused `container_binding_changed` during its two-sample comparison, so no
second apply was issued. No container stop or restart-policy change was observed.

This is a controller evidence fence, not permission to bypass identity checks or
claim a separate mutator was identified. Production runtime, health and public
route evidence remain unavailable or unknown. No new activation identity or
image build was created. Further recovery needs a stable owned-container
observation or a separately reviewed repair for the demonstrated limitation.
Sanitized feedback is `24a93c6607de088bb74e247acf92d4c6`; the exact supported
command evidence is retained in `tmp/054-delivery-acceptance/live-prod-final/`.

## Execution and ownership

Finish implementation before exercising real features. After those runs, write
regressions from the observed behavior and run the required gates. Read-only
source and existing evidence guide coding. Root owns integration and acceptance.
The PostgreSQL and Lenzora tasks retain their active files and runtime authority.

The user confirmed on 9 September 2026: deployments unable to create a recovery
receipt must stop before changes. There will be no nonrecoverable bypass mode.

| Package | Remaining outcome | Current state |
|---|---|---|
| Snapshot follow-up | Capture from the exact existing instance without web recreation or source-mount changes | Stopped refusal and running-instance capture accepted locally; the selected fixture was explicitly stopped after capture |
| W1 | Exact ownership receipt and two-instance URL/data isolation | Receipt and two-label URL owner checks passed with synthetic external ports; real two-instance runtime acceptance remains |
| W2 | Hosted initializer runs once across convergence/reconnect and rejects foreign evidence | Observer accepted locally; full hosted lane remains |
| W3/W8 | Supported local lifecycle/platform, multisite, wake and clean URL acceptance | macOS first lane passed; broader lanes remain |
| W4 | Updated remote history pagination and cancellation with complete terminal evidence | Local lane passed; matching-controller lane remains |
| W12 | Backup A/B content freshness, original replay, independent isolated restore | Source correction landed; isolated runtime lane remains |
| W5/W7 | Pre-effect recovery admission and complete queryable outcomes | Feature 054 read-only exercises and 46-test focused gate passed; compatibility gate passed with 43 tests; full selftest failed and protected runtime acceptance remains |
| W6 | Archive-bound independent PostgreSQL restore verification | Eight-file correction pushed; actual native canary, 250 focused tests and 5,711-test full gate passed; controller and original target verification remain |
| W9 | Close exact feedback cases with accepted source/runtime evidence | Await acceptance; retain partial and unknown cases |
| W10 | Resolve bounded isolation, preflight and client/platform questions | Local owner/transport lane reproduced a final mixed publication and lock-order asymmetry; remote, reader, client and fix lanes remain |
| W11 | Supported Lenzora shell entrypoint, Node bootstrap and deployment preflight | Dedicated Lenzora branch codex/delivery-entrypoint-20260909; source/review corrections complete, actual dev/prod preflight refusals observed; source gates complete with the documented 4 GB type-check OOM; package pushed |

The [PostgreSQL proof coverage contract](postgresql-restore-proof-coverage.md)
separates checked fields from omitted schema and row-content properties. It does
not broaden existing receipts or substitute for the owner's runtime acceptance.

## New evidence

Amar Sonar's editor deployment attempted `43adaef6139c85130a17e86eeda6c08659f5fb06`
through a direct `host apply` with a request ID but no durable wrapper. It failed
on foreign initializer evidence. Generation 11 recorded the requested/staged
revision, while recorded/deployed stayed at `30b2ec2` and edge evidence remained
pending. Five healthy services and the exact served editor asset did not complete
the deployment. The prior task retained its evidence and submitted feedback
`4eff41cf119c72c9a02f442cf09acbc7`; it did not retry or fabricate recovery authority.
This is a required negative scenario for W5/W7, not authorization to alter that
production stack.

## Acceptance and release

Record exact candidate hashes, installed runtime, fixture ownership, one request
identity, terminal job and complete bounded output for each real lane. Keep the
shared controller's current owner and revision visible. Prepare a concrete reviewed
candidate, target and recovery plan before requesting protected integration or
release authority. Do not certify historical incomplete operations retroactively.

## Retained gate results

The first isolated Feature 054 focused job `f019304588af57769e767766faacee7d`
completed with exit 0, complete output, and 46 tests passing. The expanded
pre-correction compatibility run `129890de7774ead3c13cf194edc7e45d` failed on
five old job-service/scheduler expectations. After the ownership and stale
heartbeat corrections, the dedicated 43-test job
`6511be651fe77074105696163dba482c` completed with exit 0 and complete output.

The first full `sb selftest` job `e9b7a91f2b693b558f396922478af73c` was
deliberately cancelled with exit `-15` before source changes. Its replacement
`87ae44f557933efcba7b7190779c50d9` reached a terminal failed result with exit 1
and complete output: 5,749 tests ran, with 24 failures, 43 errors, and 21
skips. Retained failure output includes the public command/MCP inventory still
expecting 91 entries while delivery registers 92, snapshot tests still
expecting the old empty-export error instead of the new
`snapshot_runtime_unavailable` refusal, existing hosting/preview/activation and
remote test errors, old job health/reconciliation expectations where unresolved
ownership now remains `unknown`, and the built-in `sandbox-cli` skill mirror
mismatch. This is a failed repository gate; no full-gate pass is claimed.

## Snapshot and workflow follow-ups

The snapshot correction skips global predispatch regeneration for capture and
inventory, and uses a bounded observation of the selected running database before
`wpcli --no-deps` exports. Explicit rebaseline propagates failures; provisioning
baseline capture keeps its existing best-effort behavior. Snapshot replacement
retains the old artifact until the new one is published. An interruption may leave
a visible `*-previous-*` restore point; this is not an atomic directory swap.

The negative retained fixture was stopped before and after the probe with no
containers and `reachable=false`. Its snapshot path refused with
`snapshot_runtime_unavailable`; the retained full-gate output records that no
`db.sql` was created and the stack was not reconciled. The selected running
fixture was then captured by the supported command
`sb snapshot delivery-selector-running-20260909 --db-only --instance
wordpress-i-codex-delive` in job
`64102611ddf8cfc6f941c035fbb61687` (request
`delivery-snapshot-selector-capture-20260909-01`). It finished with exit 0,
complete bounded output, and a saved `db.sql`; the output shows the
`wpcli --no-deps -T db export` path.

The before/after running status observations retained the same four container
IDs, normalized labels and mounts, instance incarnation
`inc_43fa4f152997ce77179e545be847de1b`, and server-config mount digest
`sha256:8ee7d85f48d4881b7806909596e2fbb334a1978b5c93abd4f308537bc84de4a4`.
The fixture was subsequently stopped by supported job
`76f00d12e738a988851a9799f79cba3d` (exit 0, complete output), and the final
status has no containers, `reachable=false`, and the same incarnation. No web
recreation or source-mount change was observed. This proves the local
selector/capture behavior; it does not prove a production snapshot or release.

The repository-local PRD extension still encoded a retired Sol-only readiness
check after the primary skills adopted the active model policy. Its command,
hook, template, and README now require the actual independent review verdict
under that policy. Historical PRD receipts remain unchanged.

## Bounded W10 investigation

The approved local owner/transport lane completed in durable job
`311a51eab69e99ca2e9f10fe6b01ed6d` (request
`f054-w10-local-observe-20260909-1`, exit 0, complete output, 1.469 seconds).
Both environments selected the same project source directory. Serial A then B
published complete A/A and B/B generations. In the controlled interleave, A
paused after replacing its first file, B completed B/B and published its
manifest, and A then replaced its second file and published A's manifest. The
final source was B/A, matching neither complete generation. Both transport
calls returned `status=accepted` and `restarted=false`; unknown-file and Git
sentinels survived.

The lock probes show the boundary precisely. While sync A's callback held its
actual target lease, a different-environment apply B acquired its transaction
and shared-state lock. Same-environment apply A waited until sync released the
target lease. When apply B held the actual shared-state lock, sync A's callback
did not enter until apply released it. This is a launch-order asymmetry: the
local locks did not serialize the cross-environment source publication.

This is a reproduced final publication mismatch at the hosted transport boundary,
not a historical production incident and not authorization for an isolation fix.
The run did not exercise full supported `host sync`/`host apply`, a real remote
transport, bind-mounted application readers, independent controllers, equal-ID
reachability, or the direct/captured/Node-wrapper piped-stdout comparison. It
also did not run source fixes or regression gates. See
`tmp/054-delivery-acceptance/w10/results.md` and
`sandbox/transports/remote_sync.py`.

Generic Compose ensure checks declared services before starting them, but has no
separate engine observation. An unavailable-engine fixture must distinguish a
configuration failure from a daemon failure before implementation is broadened.
A safe local `sb delivery inspect` comparison completed in isolated durable job
`aef667f1bdc13d9a98f0d5b59b9ab454`, exit 0 with complete output. Direct PTY,
captured pipe and Node child wrapper all returned matching semantic JSON after
removing only query-time `recorded_at`; the PTY's extra byte was its final CRLF.
This did not reproduce pipe failure. The original macOS/OrbStack `sb ensure`
report remains untested: this lane exercised no live instance or engine.

Sandbox job source fields describe the submitted checkout. Lenzora's hosted job
uses its control checkout and carries the application release separately. Job
history alone cannot prove the application/control revision join. Feature 054's
diagnostic contract must preserve both identities without rewriting historical
job metadata. The W10 job used an independent Sandbox home and no network,
registration, credentials, containers, production files or source changes.

## W6 PostgreSQL canary boundary

The PostgreSQL proof-coverage contract remains a source and scope document. The
owner checkout at `/Users/alim/Sites/git/sandbox` completed the eight owned
PostgreSQL verifier files at pushed commit
`4bd622d3581c3097f4f1d274b4e9d236daed66ab`. Root independently compared all eight
file hashes with the accepted canary metadata; they match.

The first two local native canaries failed during setup/cleanup. Subsequent
fixture corrections preserved the production ownership checks. The owner's
native canary `70a72f1a6861fa87427c80daefb238d8` then passed real capture/restore,
raw schema mismatch detection, nine negative controls, three mutations after
inspection, original-request locking, independent archived-schema reference
cleanup and receipt replay. Source identity and sentinel rows were preserved.
The 250 focused recovery tests and full gate passed. Full job
`5d0a5dd9bb29764775f96f2c7c9631b0` exited zero with 5,711 tests passing and 13
skipped. Root read the retained final summary. Independent review found no
acceptance blocker. This is local native canary evidence, not a controller update,
deployment or production restore. The unknown resource from the first setup
failure remains preserved; known failed-target cleanup used exact ownership
checks. The changed recovery acceptance evidence requires human review before release.

## Lenzora entrypoint observation

After all W11 source and review corrections, the actual `./deploy dev --preflight
--json` and `./deploy prod --preflight --json` commands reached Node 24.18.0 and
returned all eleven checks. Durable jobs `529a7adaceb9cf5d36efc34f1b3dfbe1` and
`6d169430349a05103b4cc60066283709` ended with exit 1 and complete output. Both
reported dirty control/Sandbox checkouts and unavailable required Cosign flags;
the installed Cosign 3.1.3 help lacks `--offline` and `--new-bundle-format`.
Dependent checks were blocked. These are launcher and refusal observations,
not a passing deployment-readiness result. No deployment was performed.

The subsequent W11 regression gates passed 99 contract tests and 292 unit tests.
Unit job `15d3d5835bbf2a3a3d2ded1e704845b3` exited zero with complete output;
only the observed slow cases received bounded per-test timeouts. The package is pushed at
`63a74f95c129cac249a2d143fa886a31bb2e5f6b`; root verified a clean worktree and
matching local/tracking refs. Lint, Prisma manifest, feature registry, architecture
and API contract checks passed. Candidate type-checking passed with an 8 GB heap;
the official 4 GB run ran out of memory without TypeScript diagnostics, while the
clean baseline passed. That resource limit remains explicit.

## Job compatibility correction

An actual legacy job observation exposed a reconciliation hazard: hostname-based
boot records could be treated as lost after the observer adopted kernel UUIDs.
Startup, status health and scheduler lease handling now preserve unresolved
ownership. An owned isolated exercise confirmed that the legacy job stayed
running with unknown health, retained its lease, and refused an overlapping job.
The focused job-service, scheduler and compatibility gate ran 42 tests successfully
in durable job `6c156048a0ab3e971a7823bfa6bf06ef`, with exit zero and complete
output. All candidate job commands use a separate Sandbox home to avoid
reconciling concurrent user jobs. Independent review then found a second-probe
race in stale-heartbeat reconciliation. Status now uses the classifier's
explicit ownership observation; an unavailable later probe cannot make the job
terminal. A real process exercise retained the running job and lease. The updated
43-test gate passed in job `6511be651fe77074105696163dba482c`.
Full `selftest` job `e9b7a91f2b693b558f396922478af73c` was deliberately cancelled
before changing source (terminal cancelled, exit -15). Its replacement,
`87ae44f557933efcba7b7190779c50d9`, terminated failed with exit 1 and complete
output (5,749 tests: 24 failures, 43 errors, 21 skipped); see [retained gate
results](#retained-gate-results).

This correction does not repair a previously interrupted operation receipt.
Ordinary apply still lacks a retained, request-bound remote-effect drain proof.
Changing recovery eligibility from failed to interrupted would not establish
that proof and is not accepted. Positive interrupted recovery needs an execution
owner capability and complete original pre-effect and phase authority.

## Current regression follow-up

Grouped job `6bccf76e79c7e322c1dd2cc0328dd871` ran 783 tests and ended failed
with five failures and five errors. Snapshot, job and most command contracts
passed. The failures exposed remaining hosting fixtures and a creation-codec
mismatch: delivery accepted original namespaced request IDs while creation
context/receipt/URL-result codecs rejected them. Request-specific codecs now
preserve those bounded IDs. The actual creation-owner exercise also found that
a failed startup replay reported success merely because its receipt existed.
Compose and WordPress now return success only for a succeeded receipt. The
corrected isolated durable job `cb40c988290cba795e26ccaa0b43efe7` passed all ten
cases, including original IDs, failed/pending/unknown replay, guard interruption,
intent drift, receipt eviction, guard capacity, and two-label URL ownership.
External runtime and WordPress ports were synthetic; real containers and remote
creation were not exercised. Regression gates are still pending. No full-pass or release claim is made from this grouped run.


## Nested-source acceptance

The corrected local owner lane passed four actual supported host command cases
with a real nested Git checkout and local Git receiver. Positive job
`0943a107ecd0098af64716a49605c53f` retained original job commit C, published the
prepared subtree T, preserved the sibling manifest selector, and completed the
configuration/runtime/delivery joins using T. The negative jobs proved dirty
source refusal before admission (`25b389080b6eed11b386385aeb99e720`), a mismatched
publication return stopping before reset/Compose (`75fcfe6557479ea194882c4dda91d911`),
and an observed outer commit C blocking delivery success and edge work
(`1593b861f9d31298c15ceaa8c1c45eed`). All harness jobs exited zero with complete
output; expected negative host commands exited one. External identity, broker,
Compose and edge ports were synthetic. This is not remote/runtime release proof.

The observed creation failure also has twelve focused regressions, passed in
job `b71a618d7ed929e6d1896f4a2835884c` (4.541 seconds, complete output). Hosting,
recovery and artifact join regressions are being included in the next focused gate.

Focused hosting/recovery job `5e7db2d156c5590c2efa1f2e36e23375` ran 469 tests and
failed with eight failures and two errors. The causes were stale or incorrect
test expectations (sanitized admission projection, CLI output shape, malformed
revision text and authenticated activation-input paths), plus a gate temporary
path under a public ancestor rejected by the existing runtime ownership guard.
Those fixture/runner corrections do not weaken the production contracts. The
replacement gate uses Python 3.12 and an explicit private temporary directory.
