# Tasks: Local Images for Hosted Deployments

**Input**: Design documents in `specs/068-host-local-images/`

**Execution rule**: Complete all source and documentation implementation before running the real CLI acceptance. Only after that acceptance may focused regressions be written and gates run.

## Phase 1: Setup

- [x] T001 Verify feature branch, clean ownership boundaries, and ignore coverage without modifying unrelated `docs/secret-inspection.md`
- [x] T002 Record existing protocol/shape revision and public hosting command documentation baselines in `specs/068-host-local-images/research.md`

## Phase 2: Foundational Contracts

- [x] T003 Define closed local/remote receipt models, canonical identities, bounds, and exact image comparator in `sandbox/hosting/local_images.py`
- [x] T004 Define hermetic Compose projection and clean exact-revision materialization in `sandbox/hosting/local_images.py`
- [x] T005 Define fixed registered-remote observe/stage/load/publish repository contract in `sandbox/transports/remote_local_images.py`
- [x] T006 Register new remote helper/receipt shapes and protocol evidence in `sandbox/remote_runtime/shapes.py` and `sandbox/remote_runtime/protocol.py`

## Phase 3: User Story 1 — Build Exact Hosted Images Locally (P1)

**Goal**: Produce exact `linux/amd64` hosted images and a bounded non-secret local receipt from one clean revision.

**Independent test**: The completed supported CLI builds the tiny throwaway project and emits an exact receipt without leaking a secret canary.

- [x] T007 [US1] Implement local Docker/Compose/BuildKit capability and platform admission in `sandbox/hosting/local_images.py`
- [x] T008 [US1] Implement service classification, shared-image conflict refusal, external digest requirements, build/save/inspect, and owner-only persistence in `sandbox/hosting/local_images.py`
- [x] T009 [US1] Implement approved BuildKit secret-only injection and secret-safe progress/error handling in `sandbox/hosting/local_images.py`
- [x] T010 [US1] Add `host image build-local` parser/help and orchestration in `sandbox/cli.py` and `sandbox/commands/hosting.py`

## Phase 4: User Story 2 — Transfer and Verify Receipt-Bound Images (P1)

**Goal**: Safely load only missing exact artifacts and publish immutable remote verification evidence.

**Independent test**: Transport acceptance fakes prove load, exact skip, external verification, remote publication, and interruption-as-unknown.

- [x] T011 [US2] Implement registered target/machine/daemon/platform observations and exact image comparison in `sandbox/transports/remote_local_images.py`
- [x] T012 [US2] Implement bounded owner-only archive staging, size/hash verification before load, storage admission, and cleanup in `sandbox/transports/remote_local_images.py`
- [x] T013 [US2] Implement aggregate/per-artifact deadlines, fresh post-load observation, replay skips, an SSH-held remote-engine lock, and unknown outcomes in `sandbox/transports/remote_local_images.py`
- [x] T014 [US2] Implement atomic digest-addressed remote receipt publication/readback in `sandbox/transports/remote_local_images.py`
- [x] T015 [US2] Add confirmation-gated `host image push-local` parsing/orchestration and bounded output in `sandbox/cli.py` and `sandbox/commands/hosting.py`

## Phase 5: User Story 3 — Apply Only Verified Local Images (P1)

**Goal**: Bind ordinary no-build apply to an exact remote receipt and refuse every mismatch before Compose mutation.

**Independent test**: Apply orchestration admits one exact receipt and refuses source/render/service/image/platform/target/daemon mismatch before `_run_compose`.

- [x] T016 [US3] Add `--local-images` parser validation and closed durable apply argument binding in `sandbox/cli.py` and `sandbox/hosting/recovery/policy.py`
- [x] T017 [US3] Reconstruct/compare the hermetic render contract and reject `hostingImages` or `compose.build: true` in `sandbox/commands/hosting.py`
- [x] T018 [US3] Retrieve remote receipt and freshly verify target/daemon/platform/service/image identities before Compose mutation in `sandbox/commands/hosting.py`
- [x] T019 [US3] Add receipt-mode `--pull never` while preserving existing receipt-free Compose behavior in `sandbox/commands/hosting.py`
- [x] T020 [US3] Bind remote receipt identity into hosting admission/application evidence and preserve source/topology/initializer/health/edge/recovery behavior in `sandbox/commands/hosting.py`

## Phase 6: User Story 4 — Preserve Existing Hosting Recovery Truth (P2)

**Goal**: Ensure staging evidence never claims deployment and existing apply failure/recovery semantics remain authoritative.

**Independent test**: Transfer-only, pre-mutation refusal, and post-admission failure retain separate receipt and delivery states.

- [x] T021 [US4] Keep image receipts outside hosted deployment state and expose only bounded staging summaries in `sandbox/commands/hosting.py`
- [x] T022 [US4] Preserve durable failure/recovery projections for receipt-bound apply in `sandbox/hosting/recovery/policy.py` and `sandbox/commands/hosting.py`

## Phase 7: Documentation and Release Evidence

- [x] T023 [P] Document build/transfer/apply authority, exact commands, cross-controller handoff, limits, and failure semantics in `docs/remote-hosting.md`
- [x] T024 [P] Update public command overview and examples in `README.md` and `skills/sandbox-cli/SKILL.md`
- [x] T025 [P] Add the public CLI/schema/protocol change to `CHANGELOG.md`

## Phase 8: Real Supported CLI Acceptance

- [x] T026 Create the committed-source tiny throwaway project under gitignored `tmp/` and exercise `./sb host image build-local` with local Docker, exact `linux/amd64`, ambient drift, and fake secret canary checks

## Phase 9: Focused Regressions and Gates

- [x] T027 [P] Add receipt/build regressions observed from acceptance in `tests/test_hosting_local_images.py`
- [x] T028 [P] Add bounded remote transport/replay/interruption regressions in `tests/test_remote_local_images.py`
- [x] T029 Add CLI/apply/recovery/no-build/protocol compatibility regressions in `tests/test_hosting.py`, `tests/test_host_recovery_cli.py`, and `tests/test_remote_runtime_protocol_shapes.py`
- [x] T030 Run focused unittest modules with per-test durations, reject any test over 5 seconds, then run `./sb selftest` once and record results

## Phase 10: Review and Shipping

- [x] T031 Perform a Greptile-style source/security review, resolve supported findings, and scan the staged diff for secrets and unrelated files
- [ ] T032 Commit and push the feature branch with required co-author/release evidence, excluding unrelated `docs/secret-inspection.md`
- [ ] T033 Confirm no active Mac Lenzora host-apply jobs; only when idle merge into `latest` and push without force
- [ ] T034 Fast-forward the clean Mac deploy checkout, run remote service migration plan, report `replaces_revision` impact, and pause for the required warning before confirm
- [ ] T035 Run authorized migration confirm, verify runtime revision match, and report exact Lenzora local-image commands to the release thread

## Dependencies

- T003–T006 block all stories.
- US1 (T007–T010) produces the receipt required by US2.
- US2 (T011–T015) produces remote evidence required by US3.
- US3 (T016–T020) precedes US4 truth-preservation integration.
- T023–T025 complete before any acceptance/test execution.
- T026 must complete before T027–T030 by repository rule.
- Shipping tasks run only after all implementation/review gates pass; T033–T035 additionally obey the user's live-release timing gates.

## Parallel Opportunities

- T023, T024, and T025 touch separate documentation files after CLI behavior stabilizes.
- T027 and T028 cover separate completed mechanisms after real CLI acceptance.

## Implementation Strategy

Implement one complete source/docs slice first. Exercise the actual local-build CLI next. Derive focused regressions from that behavior, run gates, review, and ship the feature branch. Treat merge, Mac update, and remote migration as a separately timed release phase that must wait for Lenzora host applies and the explicit migration-impact warning.
