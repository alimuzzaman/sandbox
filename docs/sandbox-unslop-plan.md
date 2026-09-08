# Sandbox unslop maintenance plan

Original maintenance baseline: `latest` at `ac65c83a50cf799725b7380a510c01953e93c6eb`, audited 2026-09-07. The original packages and their evidence remain below. The deployment completion amendment at the end records the current task, owners, scope, and acceptance gates; it supersedes the original task's execution assignments. Recheck source identity and concurrent changes before implementation.

The confirmed defects are the activation contract/test mismatch, incomplete activation acceptance, missing automatic Python workflow, broken Makefile alias, contradictory guidance and stale activation selector. Wildcard imports, mirror differences and large modules are candidates, not defects established by their size. The baseline activation run failed 15 of 157 tests; 26 architecture/storage boundary tests passed. No full-suite, installed-controller or production pass is established.

## Ownership and completion rules

One Astra Medium integration owner accepts every package. Astra XHigh owns this plan and any evidence-required revision. Use Astra Medium for activation, complex implementation and integration; Luna High for inventories and bounded checks; Luna Max for mechanical implementation from an accepted package. No Sol. Give each writer exact files and exclusions; serialize overlapping edits.

Use this document as the single maintenance plan. Keep the full Spec Kit workflow for material protocol, persisted-format and authority changes. Routine fixes use a bounded issue or work package and observed checks; they do not require a new PRD/spec/plan/tasks chain. Reconcile existing Feature 051 artifacts through its established workflow. Freeze planning expansion here. Work in the order below, allowing the independent packages to proceed separately. A package is complete only when its stated checks pass at the integrated revision. Keep failed and unrun acceptance visible. Update the plan only when evidence changes a contract, dependency, authority boundary or acceptance requirement; do not create another specification phase for cleanup.

For substantial patches, use separate bounded correctness and product/output reviews under the existing configured `repo-workflow-review` execution contract. Trivial aliases need one review. Handoffs report changed files, commands actually run, terminal results, exact SHA and unresolved limits. Preserve guards, retained evidence, compatibility codecs and meaningful tests. Neither line reduction nor passing weakened assertions counts as acceptance.

## 1. Reconcile activation coverage and decide containment

**Owner:** Astra Medium. **Priority:** P1; precedes activation enablement and a green full-suite claim.

Own `sandbox/hosting/images/activation/v2_service.py` and the affected `tests/test_hosting_image_activation*.py` fixtures/assertions. Inspect callers in `sandbox/commands/hosting.py`, `sandbox/hosting/images/provisioning.py`, `activation/v2_repository.py`, `activation/repository.py`, and `activation/execution_runner.py`; edit shared code only for a demonstrated contract defect. Lenzora's `scripts/deploy-sandbox.sh` is a read-only external caller: its pinned Sandbox revision is not installed-controller proof.

First classify all 15 failures into supported no-init, intentionally refused legacy-init, or graph-capable execution. Repair fixture construction through actual codecs. Legacy init without authenticated ordering must still return `init_mismatch`. No-init replacement/recovery/edge positives must reach their intended phase. A failed initializer must fence dependent work; missing acknowledgements or partial execution must remain uncertain, with no replay or false no-effect result. Add phase-entry assertions so early refusal cannot satisfy crash/edge coverage accidentally.

Then decide the smallest containment for graph-forward effects while graph rollback/recovery acceptance is incomplete. The concrete output is either an independently reviewed pre-effect refusal patch using existing refusal/evidence contracts, or a documented decision that such containment cannot safely preserve retained-state behavior. In the latter case, hold graph enablement/release and assign the blocker to existing Feature 051; finish other packages. Do not invent an acceptance flag or new protocol. Preserve graph decoding, retained candidates, terminal receipts, legacy refusals and the empty-genesis recovery correction. Neither a blanket activation revert nor older `main`/`792f23a` is a verified safe baseline.

**Acceptance:** discover the whole `test_hosting_image_activation*.py` family; retain count, exit status and intended-phase evidence. A containment patch must demonstrate zero runtime/edge entry on rejected requests and unchanged classification of retained uncertain state. Exercise supported wrapper/controller contracts separately. Completion of graph execution, disposable topology, provider acceptance and T156–T167 remains the existing [Feature 051](../specs/051-immutable-activation-recovery/tasks.md) work, not a promise made by this cleanup plan.

## 2. Make Python acceptance automatic and discoverable

**Owner:** Luna Max, accepted by Astra Medium. **Priority:** P1. Can start independently; green acceptance depends on resolved suite failures.

Own new `.github/workflows/python.yml`, `tests/README.md`, and `specs/051-immutable-activation-recovery/quickstart.md`; update `README.md` only where it describes the gate. Reuse `./sb selftest` from `sandbox/commands/debug.py`, which runs full unittest discovery. Do not introduce a competing runner or substitute plugin-oriented `sb test`.

Run on pull requests and `latest` pushes without path filters that omit Python-only changes. Use a finite timeout, supported Python/PyYAML setup, read-only workflow permissions and exact revision output. Ensure architecture and owned-storage suites are selected by full discovery. Keep desktop and disposable runtime jobs separate. Replace the quickstart's hand-maintained activation list with discovery by filename; add architecture/storage checks explicitly for a focused run. Report optional-environment skips as limits.

**Acceptance:** prove a Python-only diff selects the job; deliberately inject a temporary failing activation assertion in an isolated validation copy and observe nonzero gate failure, then remove it. The integrated full suite must pass without exclusions. Local validation is not hosted CI proof: hosted execution remains pending until observed. Keep existing red evidence visible meanwhile. Use finite durable Sandbox jobs with stable request IDs for long local checks; malformed acceptance is unknown, requiring ledger inspection before replay.

## 3. Repair the Makefile entry point

**Owner:** Luna Max. **Priority:** P2; independent.

Own `Makefile` only. Change the two `./sandbox` invocations and two header comments to `./sb`; retain the thin forwarding behavior. The callers are users invoking `make help` or a forwarded CLI command.

**Acceptance:** `make help` and harmless `make guide` succeed and reach the corresponding CLI behavior. Unknown commands must still fail. No new framework or dedicated test file is warranted.

## 4. Correct canonical guidance and model routing

**Owner:** Luna Max with Astra Medium review. **Priority:** P2; independent of activation.

Own the relevant rules in `AGENTS.md`; `skills/{bug-repro,fix,speckit-refine,speckit-specify,speckit-implement}/SKILL.md` and their `.agents/skills/` mirrors; `.specify/templates/prd-template.md`; `.specify/workflows/speckit/workflow.yml`; `tests/test_speckit_refine.py`; and affected entries in `.specify/integrations/{claude,speckit}.manifest.json`.

Scope plugin-only write boundaries to plugin work. Distinguish source/tooling reproduction from runtime reproduction, so a broken Makefile does not require a live WordPress stack. Replace obsolete mandatory Terra/Sol routing with a reference to current model policy. Narrow the unconditional full Spec Kit recipe to material or ambiguous features; keep routine maintenance on the short path defined above. Preserve PRD ownership, independent readiness review, downstream artifact boundaries and workflow ordering. Replace tests that enforce obsolete model strings with checks of those useful contracts and policy-consistent routing. Refresh manifest digests only for touched files, using an existing update command if one owns them; preserve unrelated entries and historical feature evidence. Do not edit personal policy or vendor caches.

**Acceptance:** `tests.test_speckit_refine` and `tests.test_skill_mirrors` pass; targeted searches find no contradictory active routing in owned files. A tooling bug can proceed with source evidence; a runtime bug still requires runtime evidence. Safety and authority controls remain intact.

## 5. Validate one structural cleanup pilot

**Owner:** Luna High collects; Astra Medium selects and integrates. **Priority:** P3, after acceptance repairs.

Inspect `sandbox/commands/cache.py` as the first wildcard-import pilot, using `sandbox/commands/manifest.py`, `tests/test_command_composition.py` and `tests/test_architecture_boundaries.py` to trace registration and exports. Change only that command's imports after locating existing mechanism owners. Require unchanged command/error/confirmation behavior, fewer wildcard consumers and no new compatibility-facade consumers. Skip the pilot if resolving dependencies requires new layers or broad extraction.

Separately classify the eight differing mirrors: `sandbox-release`, `share-build`, `skill-creator`, `speckit-agent-context-update`, `speckit-constitution`, `speckit-implement`, `speckit-plan` and `wp-pilot/lib/runner.js`. The first seven differences are `SKILL.md` files. `sandbox/commands/skill.py` and `mcp/wp-server/app.py` load canonical `skills/`; Hermes registers both trees and copies absent mirrors. Check `sandbox/core/_hermes.py` and integration manifests, record an explicit intentional-difference allowlist before synchronizing, and preserve agent-only skills. Extend existing mirror checks only for confirmed contracts. Serialize any `speckit-implement` edit after package 4.

`mcp/wp-server/app.py`, `sandbox/commands/hosting.py`, `sandbox/core/_hermes.py`, `_remote.py` and native credential helpers remain a queue of inspection candidates. Promote one only for a demonstrated repeated mechanism or failure with named callers and parity tests. Do not split modules or remove tests on appearance. Credential/security-control changes require human review before release.

## 6. Measure one possible performance improvement

**Owner:** Luna High measures; Astra Medium decides. **Priority:** P3; optional and independent of structural refactoring.

Start with a bounded CLI help/import workload through `sb` and `sandbox/cli.py`, under a fixed synthetic environment and identical interpreter, fixture, machine and repetition count. Record baseline wall-time distribution, memory where available, exit code and output contract. Profile before naming an implementation file; choose at most one measured hotspot and state its useful improvement threshold before editing. Repeat the same workload afterward and run affected behavior tests. No reliable gain, output drift or weaker validation means reject the candidate, not broaden the rewrite. Preserve cold/warm distinctions and measurement noise.

This lane adapts [Theo's suggestions](https://x.com/theo/status/2095966874010046621) about useful tests, wrappers, verification and performance. [Viticci's NotesCTL example](https://x.com/viticci/status/2096133624181432439) is anecdotal input, not Sandbox evidence. [OpenAI's model guidance](https://developers.openai.com/api/docs/guides/latest-model) informs execution; none of these sources authorizes changes or establishes a defect.

## Source and evidence references

Sources checked 2026-09-07. X blocked direct retrieval; Theo and Viticci were read through [Theo’s mirror](https://zamantika.com/theo/status/2095966874010046621) and [Viticci’s mirror](https://zamantika.com/viticci/status/2096133624181432439). [The Decoder’s September 5 article](https://the-decoder.com/openai-shares-prompting-tips-for-gpt-6-astra-including-a-blocklist-of-slop-words/) summarizes the official guidance on instruction conflicts and proportional tests. Practitioner reports are not measured Sandbox results.

Local audit evidence at the planning baseline:

- Activation durable job `7634ebfb9317d495f2b8ca1fe2c2491f`: 157 tests, 15 failures, exit 1. Selector: `test_hosting_image_activation*.py`.
- Architecture/storage durable job `d9d3c7baa082113f527701a10637e488`: 26 tests passed, exit 0. Modules: `tests.test_architecture_boundaries`, `tests.test_owned_storage_architecture`.
- `make help`: exit 2 because `./sandbox` is a directory.

Implementation status (2026-09-07): packages 1–5 are complete at the integrated
revision. The activation fixtures now exercise the authenticated graph path and
retain pre-effect refusal coverage for legacy-init and graph rollback; the
production activation service was not changed in this maintenance pass. Package
6 was intentionally rejected: a fixed 20-repetition CLI/import benchmark did
not meet the 10% improvement threshold, so no performance edit was made. Local
acceptance is green (`./sb selftest`: 5,489 tests, 21 skipped); hosted CI,
disposable topology, provider, installed-controller, edge, deployment, and
production gates remain separate and unobserved. Do not treat this plan or its
local tests as release or activation approval.

Hosted deployment follow-up (2026-09-07): the active maintenance branch also
normalizes Compose secret targets before candidate materialization and hardens
ordinary `host apply` initializer handling. Initializer images are built before
the first runtime `up`; a bounded proof of the current project, service, config
hash, image, container creation time, and terminal exit decides whether the
explicit one-shot run is skipped. Absent evidence runs once; failed, running,
foreign, stale, malformed, or ambiguous evidence refuses replay. This is a
hosted-apply guard, not immutable activation completion. File-backed secret
ownership for non-root application users remains a separate Linux-canary blocker;
no permission widening or readiness-timeout relaxation is authorized by this
maintenance pass.

## Deployment completion amendment — 2026-09-07

The user requests a detailed plan by the current agent, four Luna implementation
agents starting at lower effort, deployment repairs with Lenzora production first,
and review/port of the remaining branch into `latest`. The current agent owns
planning, integration, and final acceptance. Four Luna High agents own the bounded
packages below. Escalate a package to XHigh or Max only for demonstrated reasoning
or review needs. Do not start an Astra agent or task. These task-level choices
supersede the older role assignments above.

### Outcome and current evidence

Finish the existing deployment contract without another broad rewrite. A successful
production outcome means one exact signed application revision activated through
the supported Lenzora package command, with complete initializer, image, service,
edge, and public-route proof. A green source suite alone does not meet that outcome.

Inspected baselines: Sandbox `latest`/`origin/latest` at `ccd4119`, clean; Lenzora
`latest`/`origin/latest` at `fa945494a20852a46697fc1f105efe4233895c44`, clean.
The remaining Sandbox branch is `codex/activation-preparation-fix` at `c380e3e`.
Its `private_inputs.py` production change is already identical to `latest`.
Its remaining test changes switch graph cases to legacy zero-init and remove newer
coverage. Preserve the graph cases; do not cherry-pick these superseded migrations.
The three affected current test modules passed 38 tests during this audit.
Keep the branch and worktree; this review is not deletion authority.

The referenced task, **Merge sapan-dev into latest**, reports repeated failures:
private artifact rejection, incomplete initializer execution, `effect_unknown`,
controller revision skew, and finally staging `request_conflict`. Its latest
retained report says production was generation 0 with no activated revision.
Current `host status` independently confirms generation 0, null revision fields,
and unavailable/partial runtime observation; it does not prove absence of old
containers or effects. A previous recovery plan retained an uncertain transaction
and worker effects, so generation 0 cannot establish a clean first deployment.
Development's last retained report has healthy runtime but `provider_auth` on
required edge purge; that is historical, not a fresh development acceptance.

Current remote service status is authenticated and active but mismatched:
local runtime `56fa80e0495ec7539a04ad07`, installed
`fab9fdea93229996b1587a61`. Do not rely on new protocol behavior until a reviewed
source revision is installed through the supported lifecycle and checked again.

### Package A — replay the exact staging operation

Owner: Luna High branch/replay agent. Own
`sandbox/hosting/images/{provisioning,staging_repository,staging_v2_service}.py`
and their existing provisioning/staging tests. The current agent owns required
dispatch edits in `sandbox/commands/hosting.py` and matching public docs.

Trace the wrapper's stable stage request ID through stage-bundle provisioning,
returned generation, `StageRequestSet`, repository acceptance, and terminal replay.
Reproduce any generation/policy drift with real codecs and a temporary repository.
The expected behavior is that an exact retry returns retained evidence without
another broker use, image pull, or generation advance. A different plan, target,
policy, or ambiguous effect remains refused. An expired credential does not justify
changing an existing request. A new request identity must never be an automatic
escape from conflict. Inspect retained requests through their repository owner;
never edit or directly parse machine ledgers.

Before implementation, identify which immutable request fields survive and where
the caller currently loses them. Keep any fix in the existing custody/replay
mechanism. If a public selector or wire contract must change, update its manifest,
docs, tests, and revision evidence together. Tests must disprove accidental replay
across changed authority, and prove that unknown/in-progress results cannot start
a second operation.

### Package B — make private secret inputs usable without leaking them

Owner: Luna High private-input agent. Initially own
`sandbox/hosting/images/activation/private_inputs.py` and
`tests/test_hosting_image_activation_private_source.py`; reassign additional
transport files explicitly if the actual mechanism lives there.

Reproduce Linux file access for a non-root container consuming candidate-owned
Compose secrets. Preserve the registered source, `_FILE` application contract,
exact candidate HMAC binding, atomic publication, and owner-only source custody.
Do not make private source files world-readable, silently inject secrets into
public environment/state, or assume Compose honors file-backed uid/gid options.
Choose the smallest supported runtime projection only after proving its ownership
and cleanup behavior. Missing ownership proof must refuse before application
effects. Replay verifies the same bytes and ownership; partial publication leaves
the active/prior candidates intact. Source tests use synthetic values only.

Acceptance requires a real disposable Linux container reading the intended secret
as the intended non-root user, a different user denied access where the contract
requires it, and no secret bytes in public output. Local mocked filesystem checks
are useful regressions, not the Linux acceptance result.

### Package C — finish the bounded activation execution gaps

Owner: Luna High activation agent. Initially own
`sandbox/hosting/images/activation/{v2_service,v2_runtime,init_runner}.py` and
`tests/test_hosting_image_activation_init.py`; assign graph/recovery files only
after the agent maps actual gaps in T156–T164. Preserve the existing graph codecs
and progress mechanism instead of adding a competing executor.

Before changes, map current source against required behavior: exact queue
prerequisite readiness; migrate, storage-init, topology-gate order; inspect before
start; durable effect entry; terminal exit persisted before cleanup; dependent
services fenced on failure; bounded final readiness; complete evidence before edge
and generation commit. Name a reproducible missing behavior and its causal path.
Implement established-contract defects only. Do not disable graph rollback guards
or make an incomplete initializer chain recoverable merely to permit a deploy.

Regression cases must cover delayed healthy readiness, nonzero init exit, lost
start acknowledgement, crash after exit before cleanup, changed container/config
identity, and replay after possible effect. Assert which later phases never run.
Retained uncertain state must stay uncertain. Graph rollback/recovery and operator
settlement remain separate gates unless their full existing contracts are proven.

### Package D — repair the Lenzora caller

Owner: Luna High Lenzora agent. Own Lenzora `scripts/deploy-sandbox.sh`, its
existing deployment tests, and `docs/runbooks/hosted-production-images.md`.
Do not edit snapshot/comparison core code or start image builds.

Trace exact application revision A, control/wrapper revision D, and Sandbox
revision S. Preserve the retained signed images for A. Identify whether the current
wrapper can use a reviewed D/S without requiring an application rebuild; implement
only the established A/D/S contract in Feature 051. Reject source drift, a bundle
for another A, an unreviewed S, malformed/uncertain results, and changed target
generation. Coordinate staging changes with package A. Change the runtime pin only
after the current agent provides an accepted commit and installed-runtime evidence.

Keep the supported `pnpm run deploy production` path, whole-second deadlines,
private bounded failure capture, complete terminal activation proof, and the seven
public route checks. Tests must exercise actual wrapper branches with synthetic
CLI responses, including same-request retry, conflict, partial status, and an exact
successful result. No full application test/build sweep by this agent.

### Integration and release sequence

1. Review each agent's causal finding before authorizing its patch. Keep one writer
   per path. Record remaining Feature 051 gaps without declaring old unchecked
   tasks done based on a source skim. Update this amendment when findings change
   the contracts or file ownership.
2. Accept bounded patches only with a meaningful failing-before/passing-after
   check, diff review, and matching docs. Reuse the four agents for independent
   correctness and operator-output review after their own patches stabilize;
   reviewers do not approve their own implementation. The current agent resolves
   findings and checks cross-package invariants.
3. Run the combined activation/staging/hosting, architecture, storage, subprocess
   environment, and affected Lenzora wrapper gates. Run required full Sandbox
   discovery once the integrated source is stable. Long checks use finite durable
   jobs with one retained request ID/job ID. Report optional skips and failures.
   Commit/push accepted work on `latest`; preserve concurrent work and `main`.
4. Prove a disposable Linux topology before production: cold start, three ordered
   initializers, queue prerequisite, non-root secret readers, delayed health,
   failure/no-replay, and supported recovery boundaries. Use supported Sandbox
   controls and explicit synthetic inputs. Retain exact source and terminal job
   evidence. A unavailable canary is an open gate, never a simulated pass.
5. Refresh the exact production transaction/staging owner and preservation evidence
   through supported observation. Do not equate an empty/partial status with
   permission to replace possibly effected containers. If legal two-observation
   recovery cannot close an old transaction, prepare the existing Feature 051
   settlement work and its concrete data/preservation decision for human review.
   Do not bypass it by deleting state, renaming requests, or adopting a partial run.
6. Review the concrete A/D/S tuple and consequential security/production changes
   before release. Use the supported runtime update, then independently verify
   installed revision/capability. Preserve required edge purge; diagnose provider
   refusal through registered controls. Credential changes, destruction, or data
   settlement need their own exact authority and are not inferred from this plan.
7. Revalidate the retained signed bundle and reuse exact staging custody where
   legal. Submit one supported production activation. Require all three initializer
   exits, exact image identities for 17 healthy services, edge/purge receipt,
   unchanged post-edge proof, committed generation, and fresh `/api/health`,
   `/product`, `/features`, `/demo`, `/pricing`, `/docs`, `/integrations` evidence.
   Observe bounded stability and report any remaining provider/runtime failure.
8. Address development separately after production's critical path: refresh its
   current SHA/owner/edge receipt, then use admitted recovery or an exact approved
   deployment. Do not let production authority modify unrelated hosted apps.

### Anti-slop and completion rule

No module splitting, broad import cleanup, timeout increases, permission widening,
new dependencies, or redundant test frameworks without a demonstrated need.
Keep fixed refusal reasons useful and secret-free. Preserve failure history rather
than counting repeated partial tests as progress. Stop repeating a failed approach
after two attempts; change the diagnosis and continue independent work.

This amendment is a plan, not a completed implementation or production receipt.
Completion requires the source, disposable-runtime, installed-controller,
incident-authority, and production/public gates above. Report exact outstanding
gates if one requires an external decision; finish all independent authorized work
before requesting that decision.

### Execution evidence — 2026-09-07

The staging CLI now selects a retained successful v2 proof before opening the
credential source, preserving the original request binding when the caller repeats
the request with the resulting generation. Changed plans/policies and uncertain
operations remain fenced. Reconciliation holds the hosting target owner.
Activation now persists runtime proof and the prepared edge request together;
historical v2 runtime-only records cannot promote without terminal edge evidence.
Independent review found no actionable defects in these changes.

Local durable job `74a9cd246d52a573ed31a222dd5bc3fc` completed `./sb selftest`:
5,509 tests, 13 skipped, exit 0. The focused image suite passed 347 tests.
These are source checks, not installed-controller or production acceptance.

Disposable Linux job `368aaf34e04768cf83abc5b13e447ba8` reproduced the private
file blocker using the already-present signed worker image: host owner UID 1001,
application UID/GID 1000, candidate mode 0600, application read denied. No real
secret was used. The candidate-v2 work below addresses that ownership boundary.
Production remains generation 0 with no verified activation; do not retry it on
the strength of the local suite.

### Candidate-v2 implementation boundary

The environment-source canary passed on Linux Compose 5.4.0 in job
`8e056610cc1153bab736830d6ff540e4`. However, Compose injects these files during
start, not create, and refuses them for read-only root filesystems. Merely
changing `file` to `environment` cannot satisfy the existing initializer path,
which verifies a stopped container then starts it directly.

Implement a distinct `candidate-v2` input contract under FR-052–FR-054. Preserve
candidate-v1 decoding and retained recovery. New snapshot/preparation identity
and configuration HMAC must include the new contract, so an old candidate is
never silently reused with different runtime semantics. Keep source secret
files owner-only; retain exact captured bytes and deterministic source mappings.

For candidate-v2, represent sources as private generated environment references
in the effective Compose document. Supply their captured values only to the
private Compose process. Create exact containers without starting them. Through
the private Docker archive channel, prepare `/run/secrets` with exact declared
UID/GID/mode and bytes, then independently read back and verify the bounded
archive before application start. Refuse symlinks, extra secret files, unsafe
parent ownership/modes, changed container state, undeclared sources, and
read-only services. Do not overwrite an existing mismatched secret directory.
Default ownership/mode follows Compose (root/root, 0444); explicit mappings must
remain exact. Values and unkeyed hashes must never enter public evidence.

Initializers use their existing create/inspect/start receipt sequence. Persistent
graph phases replace `up` with create, verify each exact selected container,
prepare/read back private files, then start only those container IDs. Keep the
existing durable effect boundary and uncertainty fencing; no replay restarts an
uncertain operation. Verify the private files again during readiness/observation.

Ownership: private-input agent owns the private archive helper, candidate source
materialization and helper tests; activation agent owns graph integration and
graph tests; current agent owns models, provisioning, transport wiring and final
integration. The branch agent independently reviews the completed boundary.
Acceptance requires malicious archive/unit cases, candidate-v1 regression parity,
an actual stopped-container Linux prepare/readback/start canary, the focused
activation suite and full selftest. Security-control review is required before
this contract is released or used on production.

Stopped-container Linux acceptance passed in durable job
`292832616a0eae8299bfb9dd059e70fd`, using the already-present signed worker image
and synthetic data: prepare, exact archive readback, app UID/GID 1000 read,
wrong-UID denial, and exact-container cleanup. Plain tar-stdin `docker cp`
preserved numeric ownership; `--archive` remapped it to the container user and
was rejected by readback. The helper uses the observed passing form.

The candidate-v1/v2 focused image suite passed 365 tests. Independent review
identified a blocking stdin write outside the graph deadline. The corrected
nonblocking command port passed 21 graph/runtime/topology tests, including
unread large-input and short-read failures. Final Linux archive job
`0f36678534c230a625949ca9a146a780` passed against that corrected source.
Lenzora's wrapper suite passed 261 tests, with 27 skipped, for source/control
checkout separation, rotated-policy refusal, and opt-in v2 capability routing.
These checks do not establish installed-controller parity or production recovery.

Final full job `0c65582ace1b105159e2080a7d20422b` ran 5,527 tests with 13 skips
and one stale modularity-count failure (247 expected, 248 observed from the new
legacy-replacement refusal). Corrected that inventory expectation and reran
the modularity/architecture modules; no production code changed afterward.
The original full job remains recorded as failed. Lenzora's final wrapper run
again passed 261 tests with 27 skips after the v2-value capability check.


### Recovery review follow-up — 2026-09-08

The complete disposable Linux graph passed in job
`d8a94b26bd3f01aefe0e352307e0caa6` (exit 0): cold prerequisite readiness,
three ordered initializers, two dependent consumers with delayed health,
synthetic private inputs, initializer failure fencing, refusal to replay an
uncertain execution, and exact owned-container cleanup. This used the existing
signed worker image, not a new application build. Earlier job
`68e55c62049bf2dad49f350747bc844c` exposed Docker's explicit `none` network
identity. The validator now checks both that identity and HostConfig.NetworkMode;
other special network modes remain refused. A separate real Compose defect was
fixed: candidate-v2 uses supported `up --no-start --no-deps --no-build --pull
never --force-recreate`, then exact stopped-container verification and private
preparation before direct start. `compose create --no-deps` is unsupported.

`host image status` now reads the activation repository under its shared locks
without opening stage custody, acquiring credentials, or observing the runtime.
It reports retained schema/request/transaction/generation selectors; corrupt or
unavailable state reports `state_unavailable`, never an invented empty target.
Activation errors retain only fixed public codes and bounded diagnostic enums.

The live production owner remains an uncertain, effect-entered transaction at
generation 0. Ordinary observation recovery refused with `topology_mismatch`;
it did not clear that owner or create runtime receipts. No deployment is justified
by the disposable graph check. The separate FR-061/062 settlement boundary remains
incomplete: its closed value, signature-verification and pure candidate helpers
are internal foundations only, with no CLI apply route or custody release.
Do not wire settlement into activation until fresh process/container/data
observations, installed approval, durable terminal custody and separate forward
compatibility admission are implemented and independently accepted together.

The Lenzora source audit found no production backup receipt or restore evidence
for application `fa945494a20852a46697fc1f105efe4233895c44`. Its backup/restore
runbook and business-readiness register still require an assigned owner, provider
backup identity and isolated database-plus-artifact restore proof. A backup
receipt and reviewed initialization compatibility decision have been requested.
They cannot be synthesized from source tests, healthy containers or a canary.


Final source acceptance for this follow-up passed in local durable job
`ddb93d29c9daa438cd146f65f806c0c8`: 5,556 tests, 13 skipped, exit 0 in
635.784 seconds. The 23 focused settlement model/policy/candidate tests also
passed, including a real retained graph transaction, historical generation
preservation, original-result tampering, request collision and SSH namespace
refusals. No production source changed after the full suite began. This closes
the earlier full-suite failure; it does not close T165, install a settlement
command, update the remote runtime, or establish production recovery.


### Active image-deployment goal: settlement integration package

The goal covers successful image-based deployment of both Lenzora environments,
not only a reusable secret-copy primitive. The next implementation package closes
T165 before any production owner is released. One current-agent owner performs
implementation and integration; no new delegates are needed.

1. Complete the optional settlement state extension through ActivationRepository
   and its existing shared host-state port. Preserve legacy state bytes when no
   settlement exists. Retain full original uncertainty, exact approved plan and
   approval, and a bounded ordered terminal chain. Commit before proof-custody
   release. A lost commit or release acknowledgement must replay only the exact
   durable result/release; it must not observe or deploy again.
2. Implement a separate plan/install-approval/apply command. Planning and apply
   use retained target/Compose selectors, fresh bounded observations of the
   machine/daemon, exact stopped container identities and preserved volume/layer
   identities, and explicit reviewed backup/data-assessment input. Apply performs
   two fresh matching observations under target ownership and rechecks installed
   approval before commit. No runtime, data, provider or deletion effect is an
   allowed settlement operation. Missing quiescence remains a refusal.
3. Store each reviewed approval immutably using the existing owner-only artifact
   installer. Verify the exact plan with a dedicated SSH signing namespace; a
   separately installed forward approval uses another namespace and binds the
   next request ID, target, generation, plan/proof/snapshot, application revision,
   prior settlement and reviewed data decision. Do not add an automatic signer
   to deployment or settlement apply.
4. Add an optional signed forward subject to v2 activation requests and retained
   graph evidence without changing legacy request/digest bytes. Immediately after
   settlement, refuse missing/mismatched/expired approval before custody or runtime
   effects. Only a new graph activation can consume that authority; adoption,
   rollback and replay of the original incident cannot. Preserve the approval
   through transaction, generation and recovery evidence.
5. Prove actual shared-repository persistence and custody crash boundaries, CLI
   confirmation/selector/refusal behavior, signature separation, observer drift,
   corrupt history, and forward-admission failures. Run the focused affected
   suites and full selftest before supported controller rollout.

After settlement integration, complete the image-based development route using
its own host configuration, target authority, secrets and exact build/application
identity. Preserve the signed production application bundle and do not relabel
its inputs as a different build. Final acceptance requires successful durable
image activation, all required runtime services at the selected source/image
identity, and the public routes for each environment. Backup/approval evidence
and any needed containment remain explicit operational prerequisites, not values
that tests or synthetic canaries may manufacture.

Settlement integration full gate `3167c20c75b14672c0b83709811b7b13` ran 5588
tests in 636.493 seconds. Its sole failure was the historical broad `kind`
conditional inventory: 251 observed versus 248 expected. The three additions
are approval-record guards in `settlement_store.py` (`_path`, `_read`, and the
approval decoder selector); they do not add runtime-kind dispatch. The inventory
is updated to 251. A passing rerun is still required before controller rollout.
