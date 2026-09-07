# Sandbox unslop maintenance plan

Planning baseline: `latest` at `ac65c83a50cf799725b7380a510c01953e93c6eb`, audited 2026-09-07. This is a repository-wide maintenance plan, not implementation or release approval. Runtime mutation, remote updates, deployment, merge, credentials and production work remain outside scope. Recheck source identity and concurrent changes before implementation.

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
