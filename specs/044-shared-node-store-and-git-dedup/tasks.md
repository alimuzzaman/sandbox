# Tasks: Shared node store and hardlinked Git workspaces

**Input**: [spec.md](./spec.md), [plan.md](./plan.md), [research.md](./research.md),
[data-model.md](./data-model.md), [quickstart.md](./quickstart.md), and
[contracts/](./contracts/)

**Status**: locally actionable implementation is complete pending independent review.
Unchecked rows remain remote or human-confirmation gates. Completing a static task or local
test does not waive the live-remote evidence gates in T016–T018.

## Phase 1 — Shared checkout foundation (User Story 1, P1)

**Goal**: replace the duplicate-history copy with one shared, private-metadata materializer
while retaining an explicit plain-copy fallback.

**Independent test**: `tests/test_workspace_git_dedup.py` materializes a real Git fixture,
mutates/reset/discards/tests/builds the workspace, and proves source bytes, metadata, refs,
tracked status, and `git fsck --full` remain unchanged.

- [x] T001 [US1] Define `MaterializationPlan`, `CheckoutMaterializationReceipt`, safe path/lock validation, and the public `plan_materialization()` / `materialize()` seams in `sandbox/workspaces/checkout.py`; acceptance: the API rejects same-path, symlink, out-of-bound, and malformed-label inputs before mutation and exposes only the receipt fields in `data-model.md`.
- [x] T002 [US1] Implement staged worktree copy, private Git metadata copy, eligible loose-object/pack hard links, atomic `.git` publication, source lock, and complete plain-copy fallback in `sandbox/workspaces/checkout.py`; acceptance: `EXDEV`, `EOPNOTSUPP`, `ENOTSUP`, `EINVAL`, `ENOSYS`, `EPERM`, and `EACCES` discard partial staging, report `history_mode=copied` with a bounded reason, and leave the prior workspace/source intact on copy failure.
- [x] T003 [US1] Adapt `workspace_refresh_command()` in `sandbox/transports/remote_jobs.py` to render the shared plan with shell-safe quoting, exact staged-runtime `PYTHONPATH`, existing top-level bind-mount inode preservation, lock/error receipts, and no source-side write; acceptance: remote single-job and matrix preparation use one command path and no raw caller shell fragment or broad source deletion appears in the generated command.
- [x] T004 [US1] Replace receipt-backed `shutil.copytree` reset in `sandbox/application/workspace_service.py` with the shared Python materializer while retaining the legacy workspace branch; acceptance: new receipts reset through the lock/atomic/fallback contract, old-layout records still reset without migration, and failed reset marks the existing lifecycle indeterminate rather than deleting its source.
- [x] T005 [P] [US1] Add the real-filesystem regression suite in `tests/test_workspace_git_dedup.py`; acceptance: tests cover loose and packed objects, inode separation for every mutable Git entry, workspace reset/ref/index/config/untracked/object writes, valid marker rewrite, malformed marker refusal, missing history, injected hard-link failures, canonical lock contention, atomic remote reset/overlay, remote renderer, and local-reset delegation.

## Phase 2 — Explicit family-scoped node store (User Story 2, P2)

**Goal**: opt-in generic Compose projects to one named family store and an in-volume
dependency-tree location without changing legacy projects or build caches.

**Independent test**: `tests/test_compose_node_store.py` compares normalized descriptors and
generated overlays for source/workspace pairs, different projects, and an absent/false opt-in.

- [x] T006 [US2] Extend `ComposeSchemaProvider.resolve()` in `sandbox/config/compose.py` to normalize `compose.nodeStore` as a strict boolean through project/override precedence; acceptance: absent/false become `node_store=false`, true becomes `node_store=true`, every other type fails before an overlay write or Docker process, and no package file/script is inspected. Evidence boundary: T006 only returns this normalized flag; it does not derive a family identity or create/share/materialize a named store (T007 remains pending).
- [x] T007 [US2] Add pure family derivation and opted-in overlay generation in `sandbox/runtimes/compose.py`; acceptance: one exact `sandbox-nodestore-<family>` named volume mounts read-write at `/sandbox-node`, exports the shared store plus distinct `SANDBOX_NODE_MODULES=/sandbox-node/node_modules/<canonical-runtime-id>`, preserves collision suffixes/current resource entries, and never adds a host bind or changes BuildKit caches.
- [x] T008 [P] [US2] Add descriptor, family, overlay, fallback, permission, and reclaim-safety tests in `tests/test_compose_node_store.py`; acceptance: siblings share one store family but distinct dependency trees, malformed/repeated markers do not strip, distinct canonical projects differ, absent/false bytes are legacy-identical, unsupported consumers still start, and no automatic/broad reclaim is emitted.

## Phase 3 — Compatibility, migration, and rollback (User Story 3, P3)

**Goal**: preserve all pre-feature workspaces, document a reversible opt-in migration, and
keep old storage until a named plan is explicitly confirmed.

**Independent test**: point the new runtime at an old-layout workspace, run start/status/reset,
then rehearse opt-in and rollback on a disposable family without deleting unrelated data.

- [x] T009 [US3] Extend `tests/test_workspace_git_dedup.py` and `tests/test_compose_node_store.py` with legacy and rollback cases; acceptance: old workspaces start/reset/report status without migration, `nodeStore=false` restores byte-identical legacy overlay behaviour, hard-link/valid-marker fallback remains usable, malformed markers refuse, and no ensure/status/destroy path removes old workspaces or volumes.
- [x] T010 [US3] Document the explicit `compose.nodeStore` declaration, project-owned per-runtime dependency-tree change, old-layout compatibility, rollback order, and measured migration checklist in `docs/remote-hosting.md`; acceptance: docs name the exact volume/mount/env contract, state that Sandbox never infers opt-in, and contain no broad prune or automatic deletion instruction.
- [x] T011 [US3] Add the shared-store rationale, legacy behaviour, and bounded named-reclaim warning to `README.md`; acceptance: the public overview distinguishes build-time cache from the opt-in runtime store and requires a read-only plan plus explicit confirmation before exact-name removal.
- [x] T012 [US3] Update the Spec-Kit/runtime guidance subsection in `CLAUDE.md`; acceptance: guidance points to the two contracts, requires source-integrity and legacy gates, and forbids automatic/broad reclaim, unapproved remote actions, and claims based on estimates.

## Phase 4 — Evidence and release gates (all stories)

**Goal**: collect falsifiable local and real-remote evidence without claiming completion in
the planning package.

- [x] T013 [US1] Add a bounded evidence harness or test-only command under `tests/test_workspace_git_dedup.py` that records source revision, used-space observations, history mode, hard-link counts, and before/after `git status --porcelain`, `git diff --exit-code`, and `git fsck --full`; acceptance: the harness creates/removes exactly one disposable workspace and never mutates/deletes the source checkout.
- [x] T014 [US2] Add a bounded evidence harness or fixture under `tests/test_compose_node_store.py` that emits `docker compose config`/mount/environment observations for two sibling labels and a non-opted-in control; acceptance: evidence proves one family volume, distinct per-runtime dependency trees, legacy byte identity, and no host bind without running inferred package scripts.
- [x] T015 [US3] Define and register the supported named-store reclaim surface in `sandbox/commands/resources.py`, `mcp/wp-server/tools/resources.py`, and corresponding seams; acceptance: plan/apply requires confirmation, rechecks mounts and immutable engine identity locally/remotely, and rejects recreated volumes, inferred families, wildcards, broad prune, automatic cleanup, and unconfirmed apply.
- [ ] T016 [US1] Execute the real remote history gate from `specs/044-shared-node-store-and-git-dedup/quickstart.md` against the configured remote after implementation; acceptance: preserve finite-timeout job IDs and read-only output showing the measured SC-001 used-space delta, source integrity after reset/discard/unpack/test/build, source survival after temporary workspace removal, and any fallback reason. If evidence is absent, report the gate as unverified rather than successful.
- [ ] T017 [US2] Execute the real remote node-store/permission gate from `specs/044-shared-node-store-and-git-dedup/quickstart.md`; acceptance: preserve two-family-workspace and control overlays, exact named-volume/mount/env observations, concurrent-install exit statuses, and ordinary-operator preparation/removal evidence. No deployment, DNS/ACME, secret access, or unrelated cleanup is allowed.
- [ ] T018 [US3] After T015 exists and its tests pass, run the supported Sandbox CLI/MCP read-only named-store plan and, only after independent human confirmation, its exact disposable-volume apply described in `specs/044-shared-node-store-and-git-dedup/contracts/node-store-overlay.md`; acceptance: the plan records exact family/name, size/existence, and running mounts; apply rejects missing confirmation/raced mounts and never invokes `docker volume prune`, wildcard removal, or automatic cleanup. Leave this task unchecked until confirmation evidence exists.

2026-08-29 live-gate review: T016-T018 remain open. The current T016 helper does not
exercise the required reset/discard/unpack/test/build sequence, and the T017 fixture is
static overlay evidence rather than two live sibling workspaces with concurrent installs
and ordinary-operator permission proof. Host memory was reported at 96.93%, so no heavy
overlapping remote fixture was improvised. T018 had neither a proven disposable family
from T017 nor an exact independently confirmed apply plan; no reclaim plan or apply ran.
See `live-gate-blockers-2026-08-29.md`.
- [x] T019 Run focused tests plus `git diff --check` on `sandbox/workspaces/checkout.py`, `sandbox/transports/remote_jobs.py`, `sandbox/application/workspace_service.py`, `sandbox/config/compose.py`, `sandbox/runtimes/compose.py`, `sandbox/commands/resources.py`, `mcp/wp-server/tools/resources.py`, `tests/test_workspace_git_dedup.py`, `tests/test_compose_node_store.py`, `docs/remote-hosting.md`, `README.md`, and `CLAUDE.md`; acceptance: no unchecked task is marked complete by automation, no claim of implementation/measurement/performance appears without evidence, and unresolved failures are reported with their bounded cause.

## Dependencies and execution order

### Phase dependencies

- Phase 1 is the prerequisite for remote workspace materialization and blocks the history
  evidence gate.
- Phase 2 depends on the descriptor/overlay seams in Phase 1 only for shared test fixtures; it
  does not require node-store opt-in for the history story.
- Phase 3 depends on the fallback/legacy branches from Phases 1–2 and must be complete before
  migration or reclaim evidence.
- Phase 4 depends on all implementation and focused-test tasks. Remote tasks are evidence
  collection only and cannot be replaced by static tests.

### Story dependencies

- **US1 (P1)**: T001 → T002 → T003/T004 → T005. This is the MVP slice and can ship only after
  the real remote history gate T016.
- **US2 (P2)**: T006 → T007 → T008. It requires a hosted project to consume the env/mount
  contract; Sandbox alone cannot prove dependency-tree linking.
- **US3 (P3)**: T009 → T010/T011/T012 → T015 → T018. Existing workspaces remain usable throughout;
  migration is not a prerequisite for ordinary operation.

### Parallel opportunities

- T005 can run after the checkout API is agreed while T006–T007 proceed in disjoint config/
  runtime files; T008 is independent of T005 once the descriptor shape is fixed.
- T010, T011, and T012 touch disjoint documentation files and can be drafted in parallel,
  then reviewed together for the exact command/volume names.
- T013 and T014 are disjoint evidence fixtures; T016 and T017 must remain serial per remote
  to avoid confusing measurements, and T018 must remain confirmation-gated after T015 and
  before the final static review T019.

## Implementation strategy

1. Deliver the P1 history materializer and its regression suite first; stop if source integrity
   or atomic/fallback tests fail.
2. Add the strict opt-in and overlay only after the P1 helper is stable; verify byte-identical
   legacy output before changing any hosted project declaration.
3. Publish migration/rollback documentation and prove old workspaces continue to run before
   considering a real project opt-in.
4. Collect remote evidence with finite deadlines and retain raw bounded receipts. Treat missing,
   partial, or estimated evidence as unverified.
5. Keep named store reclaim a separate read-only-plan/explicit-confirmation operation. Never
   add automatic cleanup, broad prune, or an implicit deletion to this feature.
