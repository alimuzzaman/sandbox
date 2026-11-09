# Tasks: Miu Management and RexEnv Integration

**Input**: `specs/071-miu-rexenv-integration/{spec,plan,research,data-model}.md` and `contracts/operations.md`.
**Organization**: Work sections follow user stories; they are implementation checkpoints inside the single release phase in `plan.md`, not independently releasable increments.
**Verification order**: Finish the agreed Sandbox implementation and matching docs across all stories before acceptance commands or new test authoring. Then run supported workflows on approved disposable targets, write regressions from observations, and run helper-owned gates. Miu’s separate-repo test-first work is recorded separately. No production, installation, App registration or external publication is authorized by this task file.

## Phase 1: Setup and upstream prerequisite

- [x] T001 Implement and contribute scoped Miu API/auth/admission/worker policy in sibling `miu/lib/management-*.mjs`, `miu/bin/qa-worker`, and `miu/test/management-*.test.mjs`; upstream draft https://github.com/akashmdiu/miu/pull/1 at `77d67bc`, 92 management tests passing on Node 24/26.
- [ ] T002 Finish independent review of the final Miu head and record dispositions plus upstream compatibility pin in `specs/071-miu-rexenv-integration/research.md`; do not confuse draft creation with approval or merge.
- [ ] T003 Confirm exact v0.9.7 transport shapes, native execution policy and full-snapshot quiescence mechanism in `specs/071-miu-rexenv-integration/contracts/operations.md`; retain precise unsupported capability states where mechanisms are unavailable.

## Phase 2: Shared foundation

- [ ] T004 Define bounded service/site/build/qualification/proposal/operation models in `sandbox/integrations/models.py` and explicit provider/schema manifest in `sandbox/integrations/manifest.py`.
- [ ] T005 Implement private transactional metadata and unique project-site bindings, idempotent intent admission and unresolved-effect reservations in `sandbox/integrations/repository.py`, referencing authoritative project identity rather than registry JSON.
- [ ] T006 Compose feature dependencies in `sandbox/application/context.py` using public jobs, secrets, resources and recovery contracts; register missing generic mechanisms through their manifests rather than prohibited facades.

**Checkpoint**: Shared foundation exists; no runtime qualification or release readiness follows from this checkpoint.

## Phase 3: US1 — Operate a WordPress project with RexEnv (P1)

**Goal**: Explicit per-project RexEnv lifecycle with truthful native execution and full baseline recovery.
**Independent acceptance**: Two approved disposable projects create/reconcile/start/stop/status/execute/test/snapshot/restore/reset/remove independently; Docker remains default and unrelated sites survive.

- [ ] T007 [US1] Implement bounded supported RexEnv CLI/MCP transport and exact site-id/domain checks in `sandbox/integrations/rexenv/client.py` against the reviewed pin.
- [ ] T008 [US1] Implement `RexEnvAdapter.invoke` lifecycle policy in `sandbox/runtimes/incumbent/rexenv.py`, refusing unknown ownership/platform/build/trust/capability before effects and global service stops.
- [ ] T009 [US1] Implement supported WP argv and separately qualified trusted native execution bridge in `sandbox/integrations/rexenv/execution.py`, reusing execution/process ownership and finite output/time budgets.
- [ ] T010 [US1] Implement named full DB-plus-owned-files capture/restore/reset in `sandbox/integrations/rexenv/snapshots.py`, including quiescence, coverage, quotas, path safety, retained pre-restore point and positive verification; incomplete mechanisms remain unavailable.
- [ ] T011 [US1] Register canonical descriptor selection and backend in `sandbox/config/wordpress_runtime.py`, `sandbox/config/descriptors.py`, `sandbox/runtimes/manifest.py` and `sandbox/application/context.py`, retaining Compose default and registry-owned project resolution.
- [ ] T012 [US1] Register thin `rexenv` CLI/MCP operations in `sandbox/commands/rexenv.py`, `sandbox/commands/manifest.py`, `mcp/wp-server/tools/rexenv.py` and `mcp/wp-server/tools/manifest.py`.

## Phase 4: US2 — Manage Miu service and dashboard (P1)

**Goal**: Supported authenticated service management with reviewed lifecycle proposals and truthful installed provenance.
**Independent acceptance**: Inspect/propose/confirm/install/configure/dashboard/health/upgrade/recover/remove on an approved disposable host, preserving data and neighboring resources as disclosed.

- [ ] T013 [US2] Complete supported installed-revision attestation and nonsecret lifecycle inspection in sibling `miu/lib/management-service.mjs`, `miu/lib/management-api.mjs` and `miu/install.sh`; prohibit moving/unpinned dependency execution.
- [ ] T014 [US2] Implement bounded origin-fixed scoped management client in `sandbox/integrations/miu/client.py`, with secret-use injection, no cookies/private-state access, no redirects or ambient fallback, and explicit API version/provenance handling.
- [ ] T015 [US2] Implement service support/health/dashboard/status/evidence projection in `sandbox/integrations/miu/service.py`, preserving configured/unverified/unknown states.
- [ ] T016 [US2] Implement resource-bound install/configure/upgrade/recover/remove proposal and confirmed application through a fixed host-helper contract in `sandbox/integrations/miu/lifecycle.py`; bind request identity, ownership, builds, consent and recovery limits.
- [ ] T017 [US2] Register thin `miu` CLI/MCP operations in `sandbox/commands/miu.py`, `sandbox/commands/manifest.py`, `mcp/wp-server/tools/miu.py` and `mcp/wp-server/tools/manifest.py` using shared feature services.

## Phase 5: US3 — Connect App and run authorized QA (P1)

**Goal**: Human-consented App identity and grants, supported QA status/verdict/sites/evidence, no implicit external writes.
**Independent acceptance**: Explicitly selected App/repository identity, trusted pinned PR, report-only run and separate publication authority; absent/revoked grants never fall back.

- [ ] T018 [US3] Add supported pure verified App identity/grant inspection and distinct human create/adopt flows in sibling `miu/lib/ghapp.mjs`, `miu/lib/management-api.mjs` and `miu/bin/qa-dashboard`; no secret/setup-code capture by Sandbox.
- [ ] T019 [US3] Implement App proposal/consent/connection status in `sandbox/integrations/miu/service.py`, binding exact identity, callback origin, repositories and external-write scope.
- [ ] T020 [US3] Implement idempotent pinned QA submit/read/list/cancel/result/evidence in `sandbox/integrations/miu/service.py`, resolving the original identity after lost replies and retaining unknown outcomes.
- [ ] T021 [US3] Bind QA principal scopes, owner-approved code trust and separately recorded publication authorization in `sandbox/integrations/models.py` and `sandbox/integrations/miu/service.py` before launch.

## Phase 6: US4 — Recover interrupted operations (P1)

**Goal**: Lost replies/restarts/cancellation retain identity, resources and cleanup authority until effects are proven.
**Independent acceptance**: Interrupt create, QA, snapshot, restore and remove; inspect original state; no duplicate operations or premature release.

- [ ] T022 [US4] Implement read-only reconciliation and partial-effect projections in `sandbox/integrations/repository.py` and provider services, with no stale PID/time takeover or arbitrary retry.
- [ ] T023 [US4] Bind lifecycle/snapshot jobs and recovery-set references through public shared services in `sandbox/application/context.py`, `sandbox/integrations/miu/lifecycle.py` and `sandbox/integrations/rexenv/snapshots.py`; uncertain import or child cessation retains leases.
- [ ] T024 [US4] Implement reviewed upgrade/reset/removal recovery plans and successful-state verification in `sandbox/integrations/miu/lifecycle.py` and `sandbox/integrations/rexenv/snapshots.py`, preserving selected restore points and unowned resources.

## Phase 7: US5 — Coexist with host services (P2)

**Goal**: Separate DNS/routing/TLS/listener/service/data ownership and finite host capacity with no implicit takeover.
**Independent acceptance**: Conflicting and neighboring services are unchanged; proposals disclose capacity and affected/preserved ownership.

- [ ] T025 [US5] Read `docs/clean-url-default.md` and implement opt-in ingress/resolver conflict preflight through existing domain/ingress services in `sandbox/application/context.py`, retaining Docker/Caddy defaults and separately scoped ownership.
- [ ] T026 [US5] Implement capacity/attribution and managed/adopted/unowned/unknown host-resource projections in `sandbox/integrations/miu/lifecycle.py` and `sandbox/integrations/models.py`, using public `sandbox/resources/service.py` contracts without unmeasured savings claims.

## Phase 8: Documentation, acceptance and final gates

All implementation T004–T026 and matching documentation must be finished before acceptance/tests under the repository execution-order rule.

- [ ] T027 Document actual registered commands, descriptor schema, trust, secret-use provisioning, resource ownership and recovery limits in `README.md`, `docs/miu-rexenv.md` and applicable `skills/sandbox-cli/SKILL.md`; keep release/revision evidence aligned with public protocol changes.
- [ ] T028 Obtain task-specific approval for disposable Ubuntu 24.04 target/builds and human App consent, then capture supported CLI plus applicable MCP acceptance evidence for US1–US5 following `specs/071-miu-rexenv-integration/quickstart.md`; record unavailable proof precisely.
- [ ] T029 Write focused observed-behavior regressions in `tests/test_rexenv_runtime.py`, `tests/test_rexenv_snapshots.py`, `tests/test_miu_client.py`, `tests/test_miu_lifecycle.py` and `tests/test_miu_commands.py`; use synthetic subprocess environments and bounded owned fixtures only.
- [ ] T030 Run focused and required gates through the Office test helper; record exact commands/counts/logs and independent current-head review in `specs/071-miu-rexenv-integration/quickstart.md` evidence references.
- [ ] T031 Verify exact selected builds, two-project isolation, full DB/files recovery, interruption/replay, identity/write audit and neighbor preservation against FR-001–025 and SC-001–008; update `specs/071-miu-rexenv-integration/plan.md` readiness only with observed evidence.
- [ ] T032 Commit/push completed reviewed work to the active non-main branches; keep Related PRs current, obtain authority before Sandbox PR creation/merge/release/deploy, and block publication of the incomplete phase.

## Dependencies and parallel opportunities

T001–T003 establish prerequisites; T004–T006 precede all story implementations. US1 and US2 transports can progress independently after foundations. US3 depends on US2 plus upstream App interfaces. US4 integrates both provider operation paths. US5 constrains all host changes. T027 must finish with implementation; T028 precedes new regressions T029; T030–T032 close the assembled outcome. Unavailable host or mechanism proof leaves the corresponding task open.

Potential parallel shards, when helper budget permits: the RexEnv transport versus Miu client; CLI versus explicit MCP wrappers after service interfaces stabilize; final independent source review versus separate helper test execution. Each shard owns distinct files. Do not parallelize mutations to one site, service, database or browser.

## Implementation strategy

Implement one complete feature outcome, review the upstream prerequisite independently, and assemble both provider tracks before qualification. There is no separately releasable MVP or flag-based partial release. Upstream PR review/merge and disposable-host authorization are external gates, not evidence that Sandbox capabilities already exist.
