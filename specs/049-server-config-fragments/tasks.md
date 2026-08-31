# Tasks: Instance-Scoped Server Configuration Fragments

**Input**: Design documents from `/specs/049-server-config-fragments/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`,
`contracts/`, and `quickstart.md`

**Tests**: Required. Every contract and adapter starts with focused tests that must be
observed failing for the intended missing behavior before implementation begins. Captured
subprocess tests use `tests.subprocess_support.run_test_process` or an explicit
`synthetic_environment`; they never copy, enumerate, unpack, or forward parent
`os.environ`.

**Organization**: Shared foundations come first. Six story phases follow the specification
order. Local/static gates, human security authorization, and disposable live acceptance
come last. No live runtime, feedback closure, merge, deployment, or release is authorized
by checking off a source task alone.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Safe to execute in parallel because it owns different files and has no unmet
  dependency on another marked task.
- **[Story]**: Maps the task to one of the six user stories.
- Every task names the exact file or files it owns.

---

## Phase 1: Setup and Integration Checkpoint

**Purpose**: Start from the current accepted source and prevent Feature 047/048 CLI,
hosting, and recovery work from being overwritten.

- [ ] T001 Integrate the current non-force `origin/latest` only after approved Features 047 and 048 are present, record exact base/feature SHAs and a clean status in `specs/049-server-config-fragments/implementation-evidence.md`, and stop on unresolved overlap rather than implementing on stale `sandbox/cli.py`
- [ ] T002 Audit the post-integration ownership of `sandbox/cli.py`, `sandbox/commands/manifest.py`, `sandbox/commands/net.py`, `sandbox/hosting/`, `tests/test_cli.py`, `tests/test_modularity.py`, and `tests/test_hosting.py`, then record the conflict-resolution map in `specs/049-server-config-fragments/implementation-evidence.md`
- [ ] T003 Run the read-only Spec Kit consistency analysis across `specs/049-server-config-fragments/spec.md`, `specs/049-server-config-fragments/plan.md`, and `specs/049-server-config-fragments/tasks.md`, resolving any blocking artifact defect before source work and recording the verdict in `specs/049-server-config-fragments/implementation-evidence.md`
- [ ] T004 [P] Create non-secret nginx/OpenLiteSpeed valid, invalid, combined-conflict, oversized-boundary, and marker fixtures under `tests/fixtures/server_config/README.md` and `tests/fixtures/server_config/` without copying production credentials, runtime files, or plugin state

**Checkpoint**: Exact integration base is known, Features 047/048 are preserved, and
test fixtures contain data only.

---

## Phase 2: Foundational Types, Input, Storage, and Adapter Registry

**Purpose**: Build the shared security and persistence primitives required by every story.

**Critical**: No user-story implementation starts until the RED checkpoint is observed and
these foundations pass.

### RED tests

- [ ] T005 [P] Write failing canonical identity, fragment/set/evidence model, state-enum, and transaction-transition tests in `tests/test_server_config_models.py`
- [ ] T006 [P] Write failing bounded file/stdin, `O_NOFOLLOW`, stable-read, encoding/control, name, secret-classification, and common `wordpress-cache-v1` policy tests in `tests/test_server_config_policy.py`
- [ ] T007 [P] Write failing owner-only repository, safe-open, atomic generation/receipt/journal, immutable-generation, corruption, and no-cross-incarnation tests in `tests/test_server_config_repository.py`
- [ ] T008 [P] Write failing duplicate-free nginx/litespeed adapter-manifest and unsupported apache/herd registry tests in `tests/test_server_config_adapters.py`
- [ ] T009 [P] Write failing module-boundary tests forbidding new `sandbox_core.py`, raw `COMMANDS`, Hermes facade, MCP helper, registry-JSON, and state-JSON consumers in `tests/test_architecture_boundaries.py` and `tests/test_modularity.py`
- [ ] T010 Run T005-T009 and record the expected missing-module/behavior failures, with no unexpected passing contract, in `specs/049-server-config-fragments/implementation-evidence.md`

### Foundation implementation

- [ ] T011 Implement immutable IDs, fragments, ordered sets, runtime/validation observations, known-good receipts, transactions, phase evidence, terminal outcomes, and bounded projections in `sandbox/server_config/models.py`
- [ ] T012 Implement stable bounded regular-file/stdin ingestion and owner-only exact-output primitives without shell/environment interpretation in `sandbox/server_config/input.py`
- [ ] T013 Implement the common `wordpress-cache-v1` name/content/route/path/header/secret authority and canonical set-conflict checks in `sandbox/server_config/policy.py`
- [ ] T014 Implement verified owner-only directory descriptors, per-incarnation `flock`, atomic bytes/JSON writes, immutable generations, read-only observation, and safe retention in `sandbox/server_config/repository.py`
- [ ] T015 Implement the typed adapter protocol and deterministic nginx/litespeed-only registry in `sandbox/server_config/adapters/base.py` and `sandbox/server_config/adapters/manifest.py`
- [ ] T016 Implement public typed exports and dependency-only composition without policy duplication in `sandbox/server_config/__init__.py` and `sandbox/server_config/context.py`
- [ ] T017 Add safe shared test builders, clocks, runtime observations, fragment sets, and adapter fakes in `tests/server_config_fixtures.py` without inherited environments or live runtime access
- [ ] T018 Run `tests/test_server_config_models.py`, `tests/test_server_config_policy.py`, `tests/test_server_config_repository.py`, `tests/test_server_config_adapters.py`, `tests/test_architecture_boundaries.py`, and `tests/test_modularity.py`; make the foundational RED tests green without weakening assertions and record results in `specs/049-server-config-fragments/implementation-evidence.md`

**Checkpoint**: Common authority, exact identities, safe bytes, durable storage, and the
adapter manifest exist; no server is yet activated.

---

## Phase 3: User Story 1 - Prove and Revert a Server-Owned nginx Cache Hit (Priority: P1)

**Goal**: Apply/list/show/replace/revert one nginx fragment, prove exact-image validation
and target-only readiness, preserve legacy server switching, and support no-op behavior.

**Independent Test**: With fake runtime evidence, apply a valid nginx fragment, observe one
active record and content-free phases, reapply identically with zero validation/reload,
replace by name, and revert to the empty baseline. The later live gate must prove the
nginx hit header without PHP and PHP fallback after revert.

### RED tests for User Story 1

- [ ] T019 [P] [US1] Write failing command-owned grammar, legacy `sb server <type>`/`sb server <instance> <type>`, config apply/list/show/revert, JSON schema, exit-status, and no-op CLI contract tests in `tests/test_server_config_cli.py`
- [ ] T020 [P] [US1] Write failing happy-path apply/list/metadata-show/replace/revert, deterministic ordering, identical reapply, and absent-name healthy no-op service tests in `tests/test_server_config_service.py`
- [ ] T021 [P] [US1] Write failing xSpeed-compatible server-context tokenizer, deny-by-default directive/context, complete-candidate renderer, protected-base-route, duplicate marker/location/variable, and inclusion tests in `tests/test_server_config_nginx.py`
- [ ] T022 [P] [US1] Write failing exact-active-image, network-none synthetic validator, target-only nginx test/reload, pre-activation identity recheck, effective-generation, and unknown-not-ready tests in `tests/test_server_config_nginx_runtime.py`
- [ ] T023 [P] [US1] Write failing instance-specific nginx mount, existing-base-vhost include, unattached-legacy refusal, metadata read-only pre-dispatch, and zero-other-instance-write tests in `tests/test_server_config_lifecycle.py` and `tests/test_server_config_isolation.py`
- [ ] T024 [US1] Run T019-T023 and record expected failures for every CLI and nginx adapter contract before editing production command/service/adapter files in `specs/049-server-config-fragments/implementation-evidence.md`

### Implementation for User Story 1

- [ ] T025 [P] [US1] Move `server` to one feature-owned `CommandSpec`, preserve both legacy switch forms, add the `config` grammar, and remove only the old parser/registration bridge in `sandbox/commands/server.py`, `sandbox/commands/net.py`, `sandbox/commands/manifest.py`, and `sandbox/cli.py`
- [ ] T026 [US1] After T025, implement content-free human/JSON result rendering, stable outcome/phase/error families, exact instance selection, and bounded deadlines in `sandbox/commands/server.py`
- [ ] T027 [US1] Implement apply/list/metadata-show/replace/revert/no-op orchestration with complete-set validation, precondition recheck, activation, reload, readiness, and commit ordering in `sandbox/server_config/service.py`
- [ ] T028 [P] [US1] Implement the reviewed nginx subset tokenizer/parser, common-policy projection, deterministic complete-candidate render, and protected base-vhost composition in `sandbox/server_config/adapters/nginx.py`
- [ ] T029 [US1] Implement exact-image nginx validation with data-free fixtures, fixed argv, cleanup proof, complete inclusion evidence, and content-free native failure classification in `sandbox/server_config/adapters/nginx.py`
- [ ] T030 [US1] Implement target-only nginx generation selection, config test/reload, effective-generation observation, and readiness proof in `sandbox/server_config/adapters/nginx.py`
- [ ] T031 [US1] Add the fixed per-incarnation read-only nginx mount and adapter-owned include without changing Docker/Caddy routing in `sandbox/core/_docker.py` and `config/nginx-sandbox.conf`
- [ ] T032 [US1] Compose the resolved WordPress instance, repository, runtime gateway, clock, redaction boundary, and nginx adapter through typed dependencies in `sandbox/application/context.py` and `sandbox/server_config/context.py`
- [ ] T033 [US1] Make config list and metadata show skip legacy migration/Compose/environment writers while mutations retain normal target resolution and capability checks in `sandbox/commands/server.py` and `sandbox/cli.py`
- [ ] T034 [US1] Run `tests/test_server_config_cli.py`, `tests/test_server_config_service.py`, `tests/test_server_config_nginx.py`, `tests/test_server_config_nginx_runtime.py`, `tests/test_server_config_lifecycle.py`, `tests/test_server_config_isolation.py`, `tests/test_cli.py`, and `tests/test_clean_url_default_policy.py`; record the green story checkpoint in `specs/049-server-config-fragments/implementation-evidence.md`

**Checkpoint**: nginx behavior is implemented and locally contract-tested. This is a
technical MVP demonstration, not releasable proof until unsafe-input, recovery, isolation,
live acceptance, and human review gates pass.

---

## Phase 4: User Story 2 - Prove and Revert OpenLiteSpeed Cache Behavior (Priority: P1)

**Goal**: Support the same named lifecycle on canonical server type `litespeed`, using an
exact-image, network-none, data-free boot and behavior canary that cannot silently accept
ignored rules.

**Independent Test**: With fake runtime/container evidence, render a complete OLS vhost,
validate it in the exact active image with no live mounts/network/secrets, prove inclusion
and canary behavior, activate/restart only the target, and revert to origin/PHP baseline.

### RED tests for User Story 2

- [ ] T035 [P] [US2] Write failing OpenLiteSpeed vhost-local rewrite/cache subset, global/listener/admin/external-processor denial, complete-render, and ignored-directive tests in `tests/test_server_config_openlitespeed.py`
- [ ] T036 [P] [US2] Write failing exact-image, `--network none`, read-only root, bounded tmpfs, no-live-volume/data/secret/environment, loopback canary, cleanup, and capability-unavailable tests in `tests/test_server_config_openlitespeed_runtime.py`
- [ ] T037 [P] [US2] Write failing target-only OLS generation selection/restart, runtime/image/mount recheck, effective-vhost identity, readiness, and rollback-adapter operation tests in `tests/test_server_config_openlitespeed_activation.py`
- [ ] T038 [P] [US2] Write failing OLS apply/list/show/revert and complete-set service integration tests in `tests/test_server_config_service_openlitespeed.py`
- [ ] T039 [US2] Run T035-T038 and record every expected OLS contract failure before editing `sandbox/server_config/adapters/openlitespeed.py` in `specs/049-server-config-fragments/implementation-evidence.md`

### Implementation for User Story 2

- [ ] T040 [P] [US2] Implement the deny-by-default OLS vhost cache/rewrite parser, common-policy projection, baseline-preserving renderer, inclusion markers, and canary fixtures in `sandbox/server_config/adapters/openlitespeed.py`
- [ ] T041 [US2] Implement the exact-active-image isolated OLS boot/probe/cleanup validator with fixed argv and content-free evidence in `sandbox/server_config/adapters/openlitespeed.py`
- [ ] T042 [US2] Implement target-only OLS generation selection, graceful restart/reload, effective-vhost observation, and unknown-not-ready proof in `sandbox/server_config/adapters/openlitespeed.py`
- [ ] T043 [US2] Add the fixed per-incarnation OLS vhost mount/inclusion without overwriting plugin/WordPress `.htaccess` or host-global config in `sandbox/core/_docker.py` and `sandbox/core/_provision.py`
- [ ] T044 [US2] Compose and expose only the registered `litespeed` adapter while keeping apache/herd explicit refusals in `sandbox/server_config/adapters/manifest.py`, `sandbox/server_config/context.py`, and `sandbox/application/context.py`
- [ ] T045 [US2] Run all four OLS suites plus `tests/test_server_config_service.py`, `tests/test_lifecycle.py`, and `tests/test_clean_url_default_policy.py`; record the green story checkpoint in `specs/049-server-config-fragments/implementation-evidence.md`

**Checkpoint**: Both minimum adapters satisfy local contracts; live OLS cache/purge proof
is still required.

---

## Phase 5: User Story 3 - Refuse Unsafe Input Without Disrupting Service (Priority: P1)

**Goal**: Fail before activation for malformed, wrong-server, unsafe-source, oversized,
secret-like, protected-route, and out-of-authority content while both target and control
remain unchanged and ready.

**Independent Test**: Run the adversarial file/stdin, common-policy, nginx, OLS, CLI, and
combined-set matrix and assert zero live pointer/reload/repository commit, unchanged active
set, bounded reason, and no content/path leak.

### RED tests for User Story 3

- [ ] T046 [US3] Extend `tests/test_server_config_policy.py` with failing empty/262144/262145-byte, stdin deadline, symlink/directory/device/socket/FIFO, unstable-read, BOM/NUL/control/non-UTF8, conflicting-source, invalid/traversal, credential-like-name, high-confidence secret-like-content, clean near-match, and content-free classification-error cases
- [ ] T047 [US3] Extend `tests/test_server_config_policy.py` with failing listener/vhost/include/proxy/upstream/process/module/program/TLS/DNS/Caddy/autologin/health/login/outside-docroot/header/log/unknown-directive and combined-conflict cases for both adapters
- [ ] T048 [US3] Extend `tests/test_server_config_cli.py` with failing wrong-server, unsupported apache/herd, legacy unattached mount, bounded basename-only error, raw-content/native-stderr/path/secret omission, and `mutated:false` refusal cases using synthetic subprocess environments
- [ ] T049 [US3] Extend `tests/test_server_config_nginx.py`, `tests/test_server_config_openlitespeed.py`, and `tests/test_server_config_isolation.py` with failing invalid-native/ignored-rule refusal, zero validator-to-live leakage, zero reload, unchanged target/control set/runtime/readiness cases
- [ ] T050 [US3] Run T046-T049 and record the newly failing adversarial assertions before policy/input/adapter hardening in `specs/049-server-config-fragments/implementation-evidence.md`

### Implementation for User Story 3

- [ ] T051 [US3] Harden stable input and output-source validation to satisfy every boundary without TOCTOU adoption or shell/environment interpretation in `sandbox/server_config/input.py`
- [ ] T052 [US3] Complete common deny-by-default authority, high-confidence name/content secret classification with fail-closed false-positive behavior, content-free refusal, protected-route/path/header/log rules, and deterministic combined-set conflict refusal in `sandbox/server_config/policy.py`
- [ ] T053 [US3] Complete native unknown/context/server mismatch and ignored-directive refusal before live activation in `sandbox/server_config/adapters/nginx.py` and `sandbox/server_config/adapters/openlitespeed.py`
- [ ] T054 [US3] Enforce bounded content-free refusals, phase evidence, structured output, and zero-reload/zero-commit semantics in `sandbox/commands/server.py` and `sandbox/server_config/service.py`
- [ ] T055 [US3] Run the full adversarial matrix plus `tests/test_secret_leaks.py` and `tests/test_clean_url_default_policy.py`; record the green refusal checkpoint in `specs/049-server-config-fragments/implementation-evidence.md`

**Checkpoint**: Unsafe input is refused before activation. Syntax-invalid refusal is not
misreported as automatic rollback.

---

## Phase 6: User Story 4 - Recover a Failed or Interrupted Activation (Priority: P1)

**Goal**: Retain exact prior/candidate evidence, restore the prior known-good generation
with at most one recovery activation, and block later mutation on ambiguity.

**Independent Test**: Inject failures/interruption at every durable phase; prove exact
rollback and readiness when possible, truthful `recovery_needed` otherwise, no recency
selection, bounded concurrency/deadlines, and write-free degraded inspection.

### RED tests for User Story 4

- [ ] T056 [P] [US4] Write failing durable requested/prepared/validated/activating/reloading/observing/committed/terminal transition and exact prior/candidate binding tests in `tests/test_server_config_transactions.py`
- [ ] T057 [P] [US4] Write failing interruption-before/after every phase, committed-receipt interruption, missing-generation, corrupt-journal, runtime drift, no-recency-choice, and later-mutation reconciliation tests in `tests/test_server_config_recovery.py`
- [ ] T058 [P] [US4] Write failing post-validation activation/reload/readiness fault, exact-prior restore, one recovery activation, rolled-back nonzero result, rollback-timeout, and recovery-needed tests in `tests/test_server_config_rollback.py`
- [ ] T059 [P] [US4] Write failing per-incarnation lock contention, re-read-after-wait, whole-operation/phase deadline, cleanup timeout, and no duplicate retry tests in `tests/test_server_config_concurrency.py`
- [ ] T060 [P] [US4] Write failing stopped/unknown/degraded/recovery-needed list/show projection and zero-persistent-write tests in `tests/test_server_config_inspection.py`
- [ ] T061 [US4] Run T056-T060 and record the intended recovery/rollback/concurrency failures before changing journal/service behavior in `specs/049-server-config-fragments/implementation-evidence.md`

### Implementation for User Story 4

- [ ] T062 [P] [US4] Implement the durable transaction phase API, exact referenced-generation retention, terminalization, and no-recency recovery reads in `sandbox/server_config/repository.py` and `sandbox/server_config/models.py`
- [ ] T063 [US4] Implement mutation-start reconciliation for interrupted pre/post-activation states and fail-closed corrupt/drifted evidence in `sandbox/server_config/service.py`
- [ ] T064 [US4] Implement exact prior-generation restoration, at-most-one target recovery activation, readiness proof, rolled-back result, and recovery-needed blocking in `sandbox/server_config/service.py`
- [ ] T065 [US4] Enforce one monotonic 180-second operation deadline, 60-second phase/rollback ceilings, bounded lock wait, and timeout propagation through `sandbox/server_config/service.py`, `sandbox/server_config/repository.py`, and `sandbox/server_config/adapters/base.py`
- [ ] T066 [US4] Implement read-only healthy/stopped/degraded/recovery-needed/unsupported/absent projection without repair, pruning, timestamp writes, or lock-file creation in `sandbox/server_config/service.py` and `sandbox/server_config/repository.py`
- [ ] T067 [US4] Add test-only injected activation/readiness/rollback fault adapters that are never registered or reachable from production CLI/environment in `tests/server_config_fixtures.py` and `tests/test_server_config_rollback.py`
- [ ] T068 [US4] Run all transaction/recovery/rollback/concurrency/inspection suites plus service/adapter regressions and record the green recovery checkpoint in `specs/049-server-config-fragments/implementation-evidence.md`

**Checkpoint**: Post-validation failure restores exactly once or remains visibly blocked;
read-only inspection never repairs.

---

## Phase 7: User Story 5 - Preserve Instance and Lifecycle Isolation (Priority: P2)

**Goal**: Bind fragments to an immutable instance incarnation, preserve them only across
safe same-server stop/start, gate switching/deletion, and prove no control-instance change.

**Independent Test**: Mutate one of two instances, exercise switch/delete/stop/start and
same-name recreation, and compare incarnation, set/generation, runtime/image, response
marker, and readiness of the control before/after every operation.

### RED tests for User Story 5

- [ ] T069 [P] [US5] Write failing incarnation mint/preserve/apply/relocate/delete/recreate-same-name and legacy-unattached identity tests in `tests/test_server_config_instance_identity.py`
- [ ] T070 [P] [US5] Write failing distinct mount-root/source/guest-path, target-only Compose/service call, no host-global/Caddy change, and no cross-instance fragment adoption tests in `tests/test_server_config_isolation.py`
- [ ] T071 [P] [US5] Write failing active/unresolved/degraded/recovery-needed server-switch and ordinary-delete refusal plus exact confirmed-state deletion tests in `tests/test_server_config_lifecycle.py`
- [ ] T072 [P] [US5] Write failing stop/stopped/start, ensure/apply/reconcile/relocation, committed-generation restoration, image/mount drift, and readiness-before-dependent-use tests in `tests/test_server_config_restart.py`
- [ ] T073 [P] [US5] Write failing target/control before-after evidence comparison tests for apply/replace/revert/refusal/rollback/recovery in `tests/test_server_config_control_instance.py`
- [ ] T074 [US5] Run T069-T073 and record the expected identity/lifecycle/isolation failures before changing instance and lifecycle code in `specs/049-server-config-fragments/implementation-evidence.md`

### Implementation for User Story 5

- [ ] T075 [US5] Mint an opaque server-config incarnation on create, preserve it through apply/reconcile/relocation, expose only typed projections, and prevent display-name/project-identity adoption in `sandbox/core/_instances.py` and `sandbox/application/context.py`
- [ ] T076 [US5] Render and attest one incarnation-specific read-only mount per nginx/OLS Compose service without reading fragment JSON in `sandbox/core/_docker.py` and `sandbox/server_config/context.py`
- [ ] T077 [US5] Reconcile only the committed generation before ready on start/ensure/apply/relocation and refuse ambiguous/stopped state in `sandbox/core/_provision.py`, `sandbox/commands/lifecycle.py`, and `sandbox/server_config/service.py`
- [ ] T078 [US5] Gate legacy server switching before YAML/Compose writes while preserving switch behavior for an empty healthy set in `sandbox/commands/server.py` and `sandbox/server_config/service.py`
- [ ] T079 [US5] Gate managed deletion, bind explicit fragment-state confirmation to exact incarnation/set/transaction identity, and disassociate before safe cleanup in `sandbox/commands/instances_cmd.py`, `sandbox/core/_instances.py`, and `sandbox/server_config/service.py`
- [ ] T080 [US5] Preserve state across ordinary stop/start while requiring exact server/image/mount/generation readiness reconciliation in `sandbox/commands/lifecycle.py` and `sandbox/server_config/service.py`
- [ ] T081 [US5] Return bounded target/control identity and readiness evidence without raw container/host details in `sandbox/server_config/models.py`, `sandbox/server_config/service.py`, and `sandbox/commands/server.py`
- [ ] T082 [US5] Run every identity/isolation/lifecycle/control test plus `tests/test_lifecycle.py`, `tests/test_cli.py`, `tests/test_clean_url_default_policy.py`, and instance deletion regressions; record the green story checkpoint in `specs/049-server-config-fragments/implementation-evidence.md`

**Checkpoint**: No fragment can cross an incarnation/server/instance boundary, and
lifecycle mutations fail before writes when state is unsafe.

---

## Phase 8: User Story 6 - Inspect Exact Content Deliberately (Priority: P3)

**Goal**: Return metadata by default, emit exact bytes only through explicit human stdout
or a safe owner-only destination, and keep all routine channels content-free.

**Independent Test**: Store recognizable non-secret bytes, compare list/default show/JSON/
errors/logs/exact stdout/file output, and verify only explicitly selected destinations
receive exact bytes.

### RED tests for User Story 6

- [ ] T083 [P] [US6] Write failing exact `show --content` stdout-only, no-added-newline/progress/heading, missing/degraded pre-emission refusal, and `--content --json` incompatibility tests in `tests/test_server_config_content_show.py`
- [ ] T084 [P] [US6] Write failing owner-only regular destination, safe-parent, symlink/special/non-owner/type-swap, atomic replacement, basename-only JSON, and no-state-write tests in `tests/test_server_config_content_export.py`
- [ ] T085 [P] [US6] Write failing recognizable-marker leak scans across list/default show/JSON/errors/phase evidence/log captures/native failures and synthetic subprocess environments in `tests/test_server_config_content_leaks.py`
- [ ] T086 [US6] Run T083-T085 and record every expected content-output/leak failure before modifying exact-content behavior in `specs/049-server-config-fragments/implementation-evidence.md`

### Implementation for User Story 6

- [ ] T087 [P] [US6] Implement healthy-committed-fragment exact reads and safe owner-only atomic export with no unproven candidate disclosure in `sandbox/server_config/input.py` and `sandbox/server_config/repository.py`
- [ ] T088 [US6] Implement `show --content` stdout-only mode, JSON incompatibility, `--output` result metadata, and pre-emission error ordering in `sandbox/commands/server.py`
- [ ] T089 [US6] Enforce content-free logs/exceptions/phase evidence/native diagnostics and safe basename-only output across `sandbox/server_config/service.py`, `sandbox/server_config/adapters/nginx.py`, and `sandbox/server_config/adapters/openlitespeed.py`
- [ ] T090 [US6] Run all content tests plus CLI/policy/secret-leak regressions and record the green story checkpoint in `specs/049-server-config-fragments/implementation-evidence.md`

**Checkpoint**: Exact bytes appear only where explicitly requested; metadata inspection
remains read-only.

---

## Phase 9: Documentation, Integration Gates, Live Acceptance, Review, and Feedback Closure

**Purpose**: Keep docs/code/revision evidence together, prove both real server paths and a
control instance, obtain human security approval, then close feedback truthfully.

### Documentation and local/static gates

- [ ] T091 [P] Document public commands, bounds, server support, phases/outcomes, no-op/rollback/recovery meanings, legacy mount remedy, lifecycle gates, and unsupported Apache/Herd in `docs/sandbox-config-reference.md`
- [ ] T092 [P] Document the CLI-first safe apply/inspect/revert workflow, exact-content warning, no raw Docker/SSH route, and live evidence requirements in `skills/sandbox-cli/SKILL.md` and `.agents/skills/sandbox-cli/SKILL.md`
- [ ] T093 [P] Update feature discoverability, security boundary, compatibility, and no-MCP/no-host-global scope in `README.md` and `CLAUDE.md` without weakening `docs/clean-url-default.md`
- [ ] T094 Record exact public CLI/schema/policy/adapter revisions, changed files, test matrix, known unsupported cases, and evidence placeholders in `specs/049-server-config-fragments/implementation-evidence.md`
- [ ] T095 Run every Feature 049 focused suite from `specs/049-server-config-fragments/quickstart.md`, preserve RED-before-green evidence, and record exact counts/durations in `specs/049-server-config-fragments/implementation-evidence.md`
- [ ] T096 Run Feature 047/048 hosting/recovery, command manifest, CLI, lifecycle, architecture, modularity, secret, and clean-URL regression suites named in `specs/049-server-config-fragments/plan.md`; fix only Feature 049 regressions and record results in `specs/049-server-config-fragments/implementation-evidence.md`
- [ ] T097 Run `py_compile` on every changed Python file, `git diff --check`, package/release-prune checks excluding `.specify/`, and a secret/content marker scan; record the clean static gate in `specs/049-server-config-fragments/implementation-evidence.md`
- [ ] T098 Verify command help/JSON/docs/error-code parity and public runtime-revision evidence across `sandbox/commands/server.py`, `docs/sandbox-config-reference.md`, `skills/sandbox-cli/SKILL.md`, and `specs/049-server-config-fragments/contracts/cli.md`
- [ ] T099 Refresh and integrate current `origin/latest` by normal non-force history after local gates, re-resolve Feature 047/048 overlaps without restoring legacy parser behavior, rerun T095-T098, and record the exact final candidate SHA in `specs/049-server-config-fragments/implementation-evidence.md`

### Human security authorization before live mutation

- [ ] T100 Prepare a content-free security review package covering authority grammar, path/header/log controls, exact-image validators, OLS network/data isolation, argv/environment safety, durable rollback, lifecycle/deletion, content output, and test evidence in `specs/049-server-config-fragments/implementation-evidence.md`
- [ ] T101 Obtain explicit independent human security approval for the consequential server-config path and authorization for disposable live acceptance, record reviewer/verdict/scope in `specs/049-server-config-fragments/implementation-evidence.md`, and stop before live mutation if approval is absent or conditional blockers remain

### Disposable live acceptance through Sandbox only

- [ ] T102 Using only supported `./sb` and Sandbox tools, create/reconcile disposable nginx target/control and OpenLiteSpeed target/control projects, prove exact Git/installed-runtime/image/incarnation/mount/readiness baselines, and record content-free evidence in `specs/049-server-config-fragments/implementation-evidence.md`
- [ ] T103 Have a fresh agent use only the published guide and prepared fixture to execute the nginx apply/list/show/warm/server-hit-without-PHP/reapply-no-op/replace/revert/PHP-fallback sequence with no undocumented command or infrastructure access, prove the control instance unchanged after every mutation, require each injected failure to identify its phase and one safe next action, and record SC-001/SC-006/SC-007/SC-011 evidence in `specs/049-server-config-fragments/implementation-evidence.md`
- [ ] T104 Have a fresh agent use only the published guide and prepared fixture to execute the OpenLiteSpeed origin/PHP/warm/server-hit-without-PHP/plugin-purge/non-hit-with-PHP/rewarm/hit/revert/origin sequence with no undocumented command or infrastructure access, prove exact-image isolated validation and unchanged control, require each injected failure to identify its phase and one safe next action, and record SC-002/SC-006/SC-011 evidence in `specs/049-server-config-fragments/implementation-evidence.md`
- [ ] T105 Execute both-server invalid/out-of-authority/wrong-server/unsafe-source/oversized refusal, idempotency, read-only inspection, controlled post-validation rollback, rollback-timeout recovery-needed, switch/delete/stop/start/name-reuse, and content-leak acceptance from `specs/049-server-config-fragments/quickstart.md`; record SC-003-SC-010/SC-012 evidence without raw Docker, SSH, curl, or runtime edits in `specs/049-server-config-fragments/implementation-evidence.md`
- [ ] T106 Reconcile all live/local evidence against FR-001-FR-050 and SC-001-SC-012, mark any unproven item open rather than inferred, and record the complete coverage verdict in `specs/049-server-config-fragments/implementation-evidence.md`

### Final review and feedback closure

- [ ] T107 Obtain independent final human/security review of the exact final candidate SHA and live evidence, record release/adoption verdict and any required follow-up in `specs/049-server-config-fragments/implementation-evidence.md`, and do not merge/deploy/release while verdict is not approved
- [ ] T108 After T107 approval only, review feedback `0df918a754a862fb10667b3b0d3f6855` as `verified` with bounded evidence through `./sb feedback review`, retain `80d1ef1465068665f33bf6afe97c4ef3` as the separate already-fixed LiteSpeed bootstrap record, and record the redacted feedback receipts plus final branch/SHA/status in `specs/049-server-config-fragments/implementation-evidence.md`

---

## Dependencies and Execution Order

### Phase dependencies

- **Phase 1** has no source dependency but must wait until Features 047/048 are approved
  and integrated into `latest`; otherwise T001 stops the feature.
- **Phase 2** depends on Phase 1 and blocks every user story.
- **US1** depends on Phase 2 and establishes the command/service/nginx happy path.
- **US2** adapter RED tests may start after Phase 2; its service integration and completion
  depend on US1's shared service/command contracts.
- **US3** adversarial RED tests may start after Phase 2 and can run beside US1/US2 tests;
  its completion is required before any live acceptance.
- **US4** depends on the Phase 2 journal primitives and US1 activation service.
- **US5** depends on US1 activation plus US4 recovery meanings because lifecycle gates
  must distinguish active, interrupted, degraded, and recovery-needed states.
- **US6** depends on US1 metadata show and Phase 2 safe storage; it can otherwise proceed
  beside US4/US5.
- **Phase 9** local/docs work depends on all six stories. Live work additionally depends
  on T101 authorization. Feedback closure depends on final approved review T107.

### User story dependency graph

```text
Setup -> Foundation -> US1 nginx ---------> US2 OpenLiteSpeed
                    |       |             |
                    |       +-> US4 recovery -> US5 lifecycle/isolation
                    |       +-> US6 exact inspection
                    +-> US3 unsafe refusal

US1 + US2 + US3 + US4 + US5 + US6
  -> local/static gates -> security authorization -> live acceptance
  -> final review -> feedback closure
```

### Within each story

1. Write every listed RED test.
2. Run and retain the intended failure evidence.
3. Implement only the behavior covered by those tests.
4. Make the story tests green without weakening assertions.
5. Run named compatibility gates and record the checkpoint.

---

## Parallel Execution Examples

### User Story 1

After Phase 2, separate owners may write T019 (CLI), T020 (service), T021 (nginx
policy/render), T022 (nginx runtime), and T023 (lifecycle/isolation) in parallel. After
T024, T025 and T028 may proceed in parallel; T026 follows T025, and T027/T029-T033 then
integrate them.

### User Story 2

T035-T038 are independent RED files and may run in parallel. After T039, T040 can run
beside the runtime harness preparation for T041; T042-T044 integrate sequentially.

### User Story 3

T046-T049 can be split by input/common policy, CLI/redaction, and server adapters as long
as one owner serializes the shared `tests/test_server_config_policy.py` edits. T051 and
T053 can proceed in parallel before T054 integration.

### User Story 4

T056-T060 are separate RED suites and may run in parallel. T062 and T067 own different
production/test files and can proceed together; T063-T066 integrate sequentially.

### User Story 5

T069-T073 are separate RED suites and may run in parallel. T075, T076, and T081 own
different primary files and may proceed together after T074; lifecycle tasks T077-T080
must serialize overlapping core/command edits.

### User Story 6

T083-T085 are separate RED suites and may run in parallel. T087 may proceed before T088;
T089 integrates leak protection after both.

### Final phase

T091-T093 are parallel documentation lanes. T095-T098 are bounded local gates that can be
split only when they do not run competing heavy/live workloads. T103 and T104 use distinct
disposable target/control pairs but should run serially on a constrained host; T105 follows
both so it can compare the complete evidence model.

---

## Requirements and Success-Criteria Coverage

| Requirement group | Primary tasks |
|---|---|
| FR-001-FR-002 command and exact instance resolution | T019, T025-T027, T032-T034 |
| FR-003-FR-005 authority and forbidden scope | T006, T013, T046-T055 |
| FR-006-FR-008 servers, non-translation, names | T008, T015, T035-T045, T069, T075 |
| FR-009-FR-010 bounded data input | T006, T012, T046, T051 |
| FR-011-FR-016 content-free/read-only inspection | T019, T023, T033, T048, T054, T060, T066, T083-T090 |
| FR-017-FR-027 complete validation, activation, no-op, replace, revert | T020-T034, T035-T045 |
| FR-028-FR-035 rollback, recovery, locking, bounds, readiness | T056-T068 |
| FR-036-FR-042 incarnation/lifecycle/isolation/no raw infrastructure | T069-T082, T102-T105 |
| FR-043-FR-050 live evidence, compatibility, redaction | T091-T108 |
| SC-001 nginx live behavior | T103, T106 |
| SC-002 OpenLiteSpeed live behavior | T104, T106 |
| SC-003 refusal before activation | T046-T055, T105-T106 |
| SC-004-SC-005 rollback bounds and exact restoration | T058-T068, T105-T106 |
| SC-006 control-instance invariance | T073, T081-T082, T103-T106 |
| SC-007 idempotency/replacement | T020, T027, T034, T103, T105-T106 |
| SC-008 read-only zero writes | T023, T033, T060, T066, T083-T090, T105-T106 |
| SC-009 content appears only explicitly | T048, T054-T055, T083-T090, T105-T106 |
| SC-010 interruption/drift/lifecycle/name reuse | T056-T082, T105-T106 |
| SC-011 deterministic fresh-agent documented workflow | T091-T094, T103-T106 |
| SC-012 five/180-second bounds | T059, T065, T095, T105-T106 |

Every FR and SC has a source/test task and an evidence/review task. A checked source task
never substitutes for its live or human gate.

---

## Implementation Strategy

### Technical MVP

US1 after Setup/Foundation is the smallest demonstrable increment: named nginx
apply/list/show/reapply/replace/revert through the real command/service boundary. Stop and
validate it independently at T034.

### Releasable minimum

Do not release the technical MVP. Minimum support promised by the specification requires
US1-US5, including OpenLiteSpeed, unsafe-input refusal, recovery, and isolation. US6 is P3
but remains required before declaring the complete Feature 049 scope done because exact
content show is part of the accepted public contract.

### Incremental delivery

1. Integrate Features 047/048 and complete RED-first foundations.
2. Deliver/test nginx US1 without claiming release readiness.
3. Add exact-image OpenLiteSpeed US2.
4. Close the adversarial US3 and recovery US4 gates.
5. Close lifecycle/isolation US5 and exact inspection US6.
6. Land docs and pass all local/static compatibility gates.
7. Obtain human security authorization, run serial disposable live acceptance, obtain
   final approval, then update feedback truthfully.

---

## Task Counts

- Setup/integration: 4
- Foundational: 14
- User Story 1: 16
- User Story 2: 11
- User Story 3: 10
- User Story 4: 13
- User Story 5: 14
- User Story 6: 8
- Documentation/gates/live/review/feedback: 18
- **Total: 108 tasks**

## Notes

- `[P]` never authorizes concurrent edits to the same file or competing live workloads.
- Preserve user/concurrent changes and stop on overlap not already resolved by T001/T099.
- Commit and push verified logical units on the active non-`main` branch only; never
  force-push, deploy, release, merge to `main`, or close feedback without its explicit gate.
- No raw Docker, SSH, curl, direct state/runtime editing, or secret reveal is an acceptance
  substitute.
- Exact fragment bytes and native diagnostics never enter evidence, logs, routine JSON,
  comments, commits, or feedback.
