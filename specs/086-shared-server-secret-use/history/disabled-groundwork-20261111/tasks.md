# Tasks: Shared server secret use

**Input**: Design documents in `specs/086-shared-server-secret-use/`.
**Prerequisites**: [spec.md](spec.md), root-reviewed [plan.md](plan.md), [research.md](research.md), [data-model.md](data-model.md), both [source](contracts/source-groundwork.md) and [future](contracts/future-owner-broker.md) contracts, and [quickstart.md](quickstart.md).
**Status**: T001-T016 source/docs implemented; T019-T026 regression authoring complete but UNEXECUTED. Supplied CLI helper report: 47 PASS / 0 FAIL / 2 SKIP (44 actual CLI cases); T017-T018 remain partial because default/explicit personal-local full CLI validation was skipped at the legacy startup state/writer boundary. Root authorized isolated caller compatibility authoring after reviewing that limit. No new tests, typechecks or gates have run. T027 onward remain pending; B1-B8 remain blocked.

## Format and path conventions

Checklist format is `- [ ] Tnnn [P?] [USn?] Description with file path`. `[P]` means independent files with satisfied dependencies. Paths are repository-relative unless absolute. Proposed test files below do not exist yet. External evidence lives in `/Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use/`.

The implementation owner integrates all source/docs changes. An approved personal helper owns execution and complete logs of CLI checks, tests and gates. The main implementer runs no tests. An independent personal Codex Sol high reviewer owns the final source review. This document does not launch helpers or change providers; unavailable approved helpers block execution, never authorize substitution.

## Scope and execution gates

Implement explicit credential selection, pure static validation, unavailable inventory and unconditional early refusal only. Keep personal-local local and existing remote behavior. Keep all existing admitted operator/ControlGate rights and approved sudo semantics. Current private installation layout remains unchanged. The future owner/broker contract is PROPOSED/UNAVAILABLE; no operational credential consumer, admission constructor, issuer, ledger, transport or capability-true escape hatch belongs in this slice.

Finish **all** implementation and matching docs, T003-T016, before feature CLI checks, regression authoring or tests. Then have the approved helper exercise supported local synthetic CLI scenarios, T017-T018. Only afterward author regressions, T019-T026, and have the helper run T027-T028. This deliberately overrides the template's test-first examples under the explicit task instruction and AGENTS.md critical execution order. An unexpected failure returns to implementation/docs, followed by affected CLI checks and regression/gate runs. No deferred check counts as passed.

This checklist is executable for source groundwork only. Blocked integration outcomes appear separately below and cannot be checked off, counted as skipped success or implemented using fake admission fixtures. Constitution IV and full feature acceptance remain BLOCKED. No secret access or metadata, runtime/jobs, installation changes, network, real provider operations, migration, self-SSH, self-remote or `--here` is authorized by these tasks. Synthetic checks must isolate state and disable controller auto-update rather than touch live configuration.

## Phase 1: Setup

**Purpose**: Confirm ownership and acceptance inputs without changing source behavior.

- [X] T001 Confirm the immutable implementation baseline, active feature branch and unrelated dirty-file ownership against `specs/086-shared-server-secret-use/plan.md`; record owned paths and the resolved root reservation (ours 086, sibling hosted-site-baselines 085) in `/Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use/implementation-report.txt`. Keep sibling files and the relocated 086 feature pointer untouched; numbering is resolved by root reservation.
- [X] T002 Read existing host registration, parser and test conventions in `sandbox/commands/manifest.py`, `sandbox/cli.py`, `tests/test_hosting.py`, `tests/test_host_stack.py` and `tests/subprocess_support.py`; record synthetic CLI fixture/check commands and forbidden-boundary inventory in `/Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use/implementation-report.txt` without authoring tests or running acceptance.

## Phase 2: Foundational declarations

**Purpose**: Establish the smallest pure records and public argument contract.

- [X] T003 Add immutable closed personal-local/server selection and static/refusal result records in `sandbox/hosting/credential_selection.py`; require explicit selected remote, project, source/profile ids and pinned revisions for server scope. Omitted scope stays personal-local; reject mixed/missing/malformed refs with bounded syntax and fixed non-echoing errors. Add no operational authority types or environment/state dependencies.
- [X] T004 Add immutable exact provider/generated mapping records in `sandbox/hosting/credential_selection.py`; preserve source key, destination, intended consumer and kind with deterministic ordering and duplicate/ambiguous-destination rejection. Reuse the smallest existing pure declaration checks; never invoke effectful full validation, `_secret_status` or stack preview. Keep provider and generated mappings separate.
- [X] T005 Add the six selection options from `specs/086-shared-server-secret-use/contracts/source-groundwork.md` to the existing registered host parser in `sandbox/cli.py`; keep `sandbox/commands/manifest.py` ownership intact. Add an optional typed preconfiguration dispatch hook to `CommandSpec` in `sandbox/registry.py`, registered by the host owner. Invoke it immediately after host argument normalization in `sandbox/cli.py`, before migration, configuration/instance/registry reads and compatibility writers; handled server requests return, personal-local declines unchanged. `predispatch_policy` alone cannot satisfy this. Help labels the options disabled groundwork; accept no actor/grant/owner-context/raw-command/enablement options. Invalid arbitrary values must not leak through argparse choice/error rendering.

**Checkpoint**: Selection and mapping records carry declarations only. No server registration, presence or authenticated binding has been established.

## Phase 3: User story 1, honest capability result, P1

**Goal**: Validate nonsecret intent without laptop keys and refuse unsupported execution before legacy work.
**Independent check**: T017 and T019-T022 prove static success and effect-free fixed refusal with synthetic input. They cannot prove installed dispatch.

- [X] T006 [US1] Implement the registered host preconfiguration hook in `sandbox/commands/hosting.py` for all server or mixed reference selections before CLI configuration/state access; retain the `cmd_host` defense before dispatch. Permit only the dedicated static-validation and unavailable-inventory branches; all other actions return the fixed unsupported result before existing local key/remote lookup, lifetime/claim/lease, source export/worktree/staging, target/job or effect boundaries. Cover apply, stage, image, sync and other dispatch shortcuts.
- [X] T007 [US1] Implement dedicated server `host validate` in `sandbox/commands/hosting.py` using `sandbox/hosting/credential_selection.py`; read only bounded ordinary nonsecret declarations, validate required/generated mapping shape and reject inline provider `secrets.values` with a fixed diagnostic before existing error rendering can echo it. Valid output is `server_credentials_static_validated`, availability `unverified`, execution `disabled`, exit zero. Do not use `_secret_status`, provider clients or effectful plan helpers.
- [X] T008 [US1] Apply the same selection/refusal guard at direct public stack/host handler entry points in `sandbox/commands/host_stack.py` and `sandbox/commands/hosting.py`, including `_up`, `cmd_host_stack` and directly callable apply/stage/image/sync handlers. Refuse `--plan`, `--confirm` and generation requests before `_entry`, lifetime store creation/read/list/check, checkout/ref export, source, claims or later secret gates. A caller bypassing `cmd_host` must not bypass refusal.
- [X] T009 [US1] Integrate fixed JSON/text envelope and exit semantics in `sandbox/commands/hosting.py` and `sandbox/commands/host_stack.py`: `server_credentials_invalid_selection`, `server_credentials_unsupported`, static validation and inventory codes per the source contract. Refusal/unavailable exits nonzero. Never append untrusted fields, child output, presence claims or fabricated request/grant/admission ids. Keep refusal unconditional without flags, environment switches or injectable production enablement.

## Phase 4: User story 2, intended server consumers, P1

**Goal in this slice**: Explain the blocked outcome and preserve current rights without adding a consumer.
**Independent check**: Source review confirms the documented dependency and absence of a newly enabled path. Actual clean-laptop consumption, actors and sudo acceptance are B1/B2/B6 below and remain blocked.

- [X] T010 [US2] Document custody and operator-rights boundaries in `README.md`: all admitted operators retain full current owner rights and initiating ControlGate actor identity; profile purpose does not restrict membership. Link the proposed future contract, describe broker-only intended delivery, and state that no authenticated dispatch/admission or credential-consuming server path is enabled. Preserve generic secret-run escalation guards and current layout.

## Phase 5: User story 3, safe refusal and recovery, P1

**Goal in this slice**: Distinguish stateless unsupported refusal from the future retained missing-key outcome.
**Independent check**: Source/docs review and T022 show repeatable refusal without stores or reservations. Missing-key persistence and replay acceptance are B3/B4 below and remain blocked.

- [X] T011 [US3] Document in `README.md` that disabled refusal never reserves an identity or performs missing-key preflight. Explain the proposed resolution-before-reservation, frozen retained no-effect refusal, known-terminal linked new-id retry, original lookup for uncertainty and committed-admission prerequisite by linking `specs/086-shared-server-secret-use/contracts/future-owner-broker.md`. Mark them blocked integration obligations, not implemented recovery behavior.

## Phase 6: User story 4, identity and intent, P1

**Goal in this slice**: Preserve exact nonsecret declarations and reject client authority claims.
**Independent check**: T023-T024 prove immutable declaration distinctions and isolated pure results only. Authenticated binding and grant enforcement remain B1/B2/B4/B5.

- [X] T012 [US4] Complete closed declaration decoding in `sandbox/hosting/credential_selection.py`: reject unauthorized actor/grant/owner-context and unsupported fields without echo. Ensure selected server, source key, pinned source/profile revisions, destination and consumer changes remain distinguishable even with identical destination-key sets. Do not turn declaration equality/hash or parsed context into authenticated intent, reservation or admission authority.

## Phase 7: User story 5, inventory and compatibility, P2

**Goal**: Return honest unavailable inventory while preserving personal behavior and generated/provider distinctions.
**Independent check**: T018/T025-T026 cover source inventory refusal and local compatibility. Supported admitted inventory and postadmission generation remain B6/B7.

- [X] T013 [US5] Add server `host secrets` unavailable result in `sandbox/commands/hosting.py` with `server_credentials_inventory_unavailable`, inventory `unavailable` and availability `unverified`. Perform no source metadata, local/server key, remote registry, provider, provisioning or generation access. Never infer absence or provider validity.
- [X] T014 [US5] Preserve default and explicit personal-local dispatch in `sandbox/commands/hosting.py` and `sandbox/commands/host_stack.py`, including existing personal-local remote operations and generated-runtime rules. Server selection must never fall through on any outcome; `--generate` cannot satisfy provider requirements or bypass disabled refusal. Add no new server-reference requirement to working personal paths.

## Phase 8: Complete docs before checks

- [X] T015 Update `skills/secret-inspection/SKILL.md` with explicit scope selection, pure static validation, unverified availability, unavailable inventory, fixed early refusal and personal compatibility. State that source evidence gives no credential authority; keep existing lowest-disclosure procedures and guards unchanged.
- [X] T016 Complete `README.md` CLI examples and local release/revision evidence for the new options/schema/result behavior, linking the source and proposed future contracts. Record supplied versus actually observed revision separately in `/Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use/implementation-report.txt`; advertise no installed protocol/capability or runtime revision. Keep wire protocol unchanged because this slice has no transport. Self-review all implementation/docs before T017.

## Phase 9: Supported local synthetic CLI checks, helper only

**Global barrier**: T003-T016 complete. Use existing support, disposable fixtures under shared scratch, finite timeouts and explicit synthetic environments. These are actual supported CLI invocations through the approved helper, prior to regression authoring. No remote execution or real credential access. The helper reports command, actual pass/fail/skip counts, first failure excerpt at most 40 lines, and full log path. Main reads reports. Local finite durable execution is allowed only if separately approved and supported; these tasks confer no runtime/job authority.

- [ ] T017 Have the approved personal helper run the implemented `./sb host validate` syntax from `specs/086-shared-server-secret-use/quickstart.md` with ordinary synthetic hosting declarations and empty laptop credentials, then server apply/stage/image/sync and stack-up `--plan`/`--confirm` refusal invocations in JSON/text. Use `tests/subprocess_support.py::run_test_process` or `synthetic_environment`, synthetic home/state, `SANDBOX_AUTO_UPDATE=0` and finite timeouts. Capture actual code/status/exit outcomes in `/Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use/source-cli.log`; stop dependent work on unexpected boundary access.
- [ ] T018 Have the approved personal helper exercise malformed/mixed/missing refs, absent/wrong selected-server declarations, unavailable inventory, generated/provider distinctions and default/explicit personal-local safe validation using the same isolation. Well-formed unverified references remain unverified, never registered/authenticated. Capture fixed non-echoing JSON/text results and commands in `/Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use/source-cli.log` and summarize observed limits in `/Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use/implementation-report.txt`.

## Phase 10: Story regressions after observed CLI behavior

**Global barrier**: Root accepted the understood T017-T018 partial CLI observations for regression authoring. Full personal-local CLI validation remains skipped/unverified; isolated caller-contract comparisons cover only its bounded source portion. Author tests now, never before implementation. Use only synthetic declarations/sentinels, no real source metadata. Captured children always use `tests/subprocess_support.py::run_test_process` or `synthetic_environment`; never copy, enumerate, unpack or pass parent `os.environ`. Spies prove forbidden calls were not made; they must fail if a guarded boundary is invoked. Fake committed admissions must never authorize production effects.

### User story 1 regressions

**Authoring status**: T019-T026 checkmarks record authored tests only. All are UNEXECUTED pending the separately approved T027-T028 helper; no green regression or integration claim. Report: `/Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use/regression-report.txt`.

- [X] T019 [P] [US1] Add declaration/static validation cases in `tests/test_host_credential_selection.py` for clean client, malformed/mixed/missing bounded refs, wrong/missing server selector, required/generated overlap and inline provider values. Assert exact fixed results and unverified availability without local key access.
- [X] T020 [P] [US1] Add dispatch and direct-handler regressions in `tests/test_host_server_credentials.py` with meaningful forbidden-boundary spies for key reads/status, remote lookup, lifetime stores/read/list/check, claims/leases, checkout/export/worktree/staging, jobs, provider/generation/target/effects. Cover stack `--plan`/`--confirm` and apply/stage/image/sync entry, not just a mocked top-level guard; prove no production enablement path from context/manifest/revision declarations.
- [X] T021 [P] [US1] Add captured CLI JSON/text/exit regressions in `tests/test_host_server_credentials_cli.py` from T017-T018 observations. Include fixed no-echo diagnostics for invalid flags/choices and synthetic sentinel-bearing malformed refs/declarations, clean isolated state and unavailable inventory. Exercise the entire public CLI main invocation with traps for attempted auto-migration, load_config, registry/instance resolution and compatibility writers, plus zero process/network/write calls beyond the CLI subprocess itself and explicitly permitted declaration reads. Cover malformed/mixed selection, static validate, unavailable inventory and effectful server actions. An empty synthetic home alone does not prove absence of attempted access; retain handler-level traps too.
- [X] T022 [US1] Extend `tests/test_host_server_credentials.py` for repeated valid/invalid refusals and malicious sentinel output paths; assert no secret-derived rendering, raw child output, reservation/admission ids, stores or side effects. State explicitly that this is stateless refusal evidence, not durable replay or provider-consumer leak proof.

### User story 4 regressions

- [X] T023 [US4] Extend `tests/test_host_credential_selection.py` with each source key, selected server, source/profile revision, destination and consumer mutation, including unchanged destination sets; assert distinct immutable declarations and rejection of actor/grant/owner-context substitutions. Describe these as pure declaration checks, not authenticated grant enforcement.
- [X] T024 [US4] Extend `tests/test_host_credential_selection.py` with concurrent/repeated PURE decoding and validation of different synthetic selections/mappings; assert isolated deterministic results and zero shared mutable state or effects. Do not construct reservations, fake grants or operational concurrency fixtures.

### User story 5 regressions

- [X] T025 [US5] Add focused compatibility cases in `tests/test_hosting.py` and `tests/test_host_stack.py` comparing omitted and explicit personal-local scope for existing local and personal-local remote call contracts without actual connections. Preserve generated-runtime distinctions using existing synthetic fixtures and captured-child support; no fallback from server scope.
- [X] T026 [US5] Extend `tests/test_host_server_credentials.py` for unavailable inventory without metadata probes, provider presence versus validity claims and `--generate` disabled before presence/generation. Keep missing generated credentials distinct in personal compatibility cases; do not claim actual server key preflight or admitted generation ran.

## Phase 11: Polish, gates and source handoff

- [ ] T027 Have the approved personal helper run focused `python -m unittest tests.test_host_credential_selection tests.test_host_server_credentials tests.test_host_server_credentials_cli tests.test_hosting tests.test_host_stack` with isolated synthetic child environments and finite timeout; retain actual command/counts/first-failure excerpt and full `/Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use/focused-tests.log`. Main runs none. Failures block review/handoff until addressed and affected checks rerun.
- [ ] T028 Have the approved personal helper run the repository-required Python acceptance gate `./sb selftest` from `.github/workflows/python.yml` with finite bounds and approved local synthetic isolation; report actual pass/fail/skip counts, command, first-failure excerpt and `/Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use/gates.log`. Use the supported local execution route; do not trigger GitHub Actions, remote jobs, installs or unrelated app/runtime gates. If a required gate needs unapproved execution, record it blocked rather than substitute a green result.
- [ ] T029 Supply an independent personal Codex gpt-6.1-sol high source reviewer the immutable current diff/head, supplied base `e46777b`, branch, full acceptance/blocked matrix and actual T017-T028 counts/log paths in `/Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use/review-input.txt`. Require correctness/security/early-boundary/compatibility/docs review with severity, file:line, concrete failure, confidence, falsifier, needs-testing and verdict. Reviewer runs no tests/builds/apps/network; no provider fallback. Save review in `/Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use/source-review.txt`.
- [ ] T030 Address findings only in the owned source/docs/test paths named above and record dispositions in `/Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use/implementation-report.txt`; finish revised implementation/docs before repeating affected helper CLI checks, then regressions/gates. Any consequential authentication/authorization/custody/production-path change requires human review before release; no runtime acceptance inferred.
- [ ] T031 Obtain a fresh independent personal Sol high source recheck of the final immutable input after fixes, with actual updated evidence in `/Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use/source-review.txt`. Review until no blocking findings remain; record unresolved needs-testing as blocked, never verified. No reviewer reruns checks. If no fixes were needed, retain the initial reviewed exact immutable input and verdict.
- [ ] T032 After required gates and independent review, recheck ownership/branch/upstream and commit only completed relevant source work, then push the single named feature ref `git push -u origin feat/shared-server-secret-use`; record exact final SHA and check/review evidence in `/Users/alim/Sites/git/.scratch/sandbox/shared-server-secret-use/implementation-report.txt`. Preserve unrelated dirty work. No shared-branch push, PR, merge, tag, deploy or runtime-authority claim. Any source change after reviewed input requires affected checks and fresh review before push.

## Blocked integration outcomes, outside executable checklist

These are acceptance coverage and prerequisites, not nominally runnable implementation tasks. Root and sibling owners must jointly accept the future contract, retain the resolved root reservation (ours 086, sibling hosted-site-baselines 085) and active 086 metadata, establish installed authenticated dispatch/admission, canonical installation identity beyond machine/uid/home, complete lifecycle/source-writer/target-fence composition, accepted durable atomic storage/retention and finite policy, then obtain separate runtime/credential authorization. No task here enables those prerequisites. Absence keeps capability disabled. Evidence goes into the separately authorized integration package linked from `contracts/future-owner-broker.md`.

| Block | Story / requirements | Required later observed outcome |
| --- | --- | --- |
| B1 | US2/US4; FR-004, FR-005, FR-006, FR-011, FR-022, FR-023; SC-003, SC-005, SC-009 | Real ControlGate initiating actor preserved through server-created typed committed admission, operation and grants. Two admitted operators retain equal current owner and approved sudo rights. Client actor/context/reservation/preflight cannot mint authority. Wrong canonical installation/runtime/target/copy/source artifact refuses; current private layout preserved without migration. |
| B2 | US2/US4; FR-006, FR-008, FR-012, FR-013, FR-014, FR-017; SC-003, SC-005, SC-007 | Trusted consumer uses exact bound source/profile/mapping/destination/consumer with grant/revision/deadline/cancel revalidation; subsequent effects check current fences. Source-writer publication authority is enforced. Concurrent equal-rights actors cannot swap grants. Clean laptop succeeds without ambient/dummy/local/alternate-host fallback. |
| B3 | US3/US5; FR-007, FR-008, FR-009, FR-010, FR-019, FR-020; SC-004 | Nonsecret resolution precedes atomic scoped reservation. All required provider keys preflight before staging, lifetime claims, generation or effects. Missing/unavailable key yields retained terminal names/status-only no-effect refusal, including generation requests. Repair keeps original frozen; only explicit new-id retry linked to KNOWN terminal refusal reviews fresh intent. Provider rejection remains distinct. |
| B4 | US3/US4; FR-007, FR-010, FR-015, FR-016, FR-017; SC-006 | Disconnect during reservation/refusal/admission retains uncertainty until original-id lookup. Same accepted identity/intent returns retained result without effects; changed intent refuses. Concurrent identical requests bind once; cross-actor requests remain separate; cancellation/expiry are inspection-only. Retention/tombstones never reopen identity. |
| B5 | US4; FR-006, FR-011, FR-012, FR-015, FR-023; SC-005, SC-009 | Every installation/actor/runtime/target/copy/incarnation/committed-source/artifact/source-profile-revision/source-key/destination/consumer mutation between reservation, preflight, admission, staging and consumption stops subsequent unauthorized use. Successful preflight without committed admission gives zero effects; stale fence/cancel/deadline between effects stops the next effect. |
| B6 | US2/US3/US5; FR-013, FR-014, FR-018, FR-019, FR-020; SC-002, SC-007, SC-008 | Success/provider rejection/consumer failure/timeout leak zero values or derived metadata into transport, argv, output/log/poll/job/artifact/cache/staged/retained storage. Unsupported persistent delivery refuses without a file. Supported admitted inventory exposes bounded names/status without effects or validity claims. Separately authorized postadmission generation delivers only to intended stack consumer and never replaces provider keys. |
| B7 | US5; FR-020, FR-021; SC-008 | Integrated default/explicit personal-local local/remote behavior and existing generated-runtime delivery remain compatible across success/failure/cancel/timeout. Server failures never fallback. Pure compatibility checks T025-T026 cover only the source portion. |
| B8 | All; FR-022, FR-023, FR-024; SC-009, constitution IV | Independent installed client/controller revision and versioned capability agreement, all B1-B7 evidence, matching docs/release evidence and human security review plus separately authorized live acceptance. Only then review enablement in a new integration task set. |

## Coverage and acceptance accounting

| Requirement group | Runnable source work | Still blocked |
| --- | --- | --- |
| FR-001, FR-002, FR-003 | T003-T009, T012, T017, T019-T024 | Installed dispatch enablement B8 |
| FR-004, FR-005 | T010 preserves/document rights; T006/T008 refuse before authority | Actual actor/admission/sudo B1/B2 |
| FR-006 | T004/T012/T023-T024 exact pure declarations | Authenticated complete binding B1/B2/B5 |
| FR-007, FR-008, FR-009, FR-010, FR-011 | T006/T008/T011/T020/T022 zero calls/authority only | Reservation, preflight, refusal retention and admission B1/B3/B4/B5 |
| FR-012, FR-015, FR-016, FR-017 | T006/T008/T012/T020/T022/T024 refusal/pure isolation only | Live grants/fences/lifetime/replay/concurrency B2/B4/B5 |
| FR-013, FR-014, FR-019 | T007/T009/T010/T021-T022/T026 decoder/no-fallback checks | Real consumer custody/provider failure B2/B6 |
| FR-018 | T013/T018/T021/T026 unavailable inventory | Supported admitted inventory B6 |
| FR-020, FR-021 | T004/T014/T018/T025-T026 personal/provider/generated boundaries | Integrated generation/compatibility B3/B6/B7 |
| FR-022, FR-023, FR-024 | T010/T015-T016/T029-T032 honest source/revision reporting; no layout change | Complete prerequisites and live acceptance B1/B8 |

SC-001 and unsupported SC-002 can be evaluated by source CLI/regressions once actually run. SC-003, SC-004, SC-005, SC-006 and SC-007 are blocked integrated outcomes. SC-008 has source compatibility coverage only; its integrated portion remains blocked. SC-009 is a disabled gate obligation pending B8, not a passed enablement claim. No acceptance scenario is erased because it cannot yet run.

## Dependencies and execution order

`T001-T002 -> T003-T005 -> T006-T016 -> T017-T018 -> T019-T026 -> T027-T028 -> T029 -> T030/T031 -> T032`.

Foundation precedes all story implementation. US1 supplies the common early guard used by US5; US2/US3 supply required honest docs, not working server consumers/recovery. US4 pure mapping/decoder work completes before the shared check barrier. All story implementation/docs is one source increment before checks, despite separate story headings. Source review follows helper checks; fresh analyze precedes implementation and is outside this generation task. Blocked B1-B8 cannot be scheduled by traversing this graph.

## Parallel opportunities and examples per story

Only T019-T021 have `[P]`: their files are separate and all depend on T018. Within each file the later tasks are serial. No spawning is authorized by this document; these are scheduling opportunities for a separately authorized implementation owner.

- US1: after T018, T019 in `tests/test_host_credential_selection.py`, T020 in `tests/test_host_server_credentials.py`, and T021 in `tests/test_host_server_credentials_cli.py` can be authored independently. T022 follows T020.
- US2: T010 is serial with README work; no source implementation parallelism or operational acceptance is available.
- US3: T011 is serial with T010 in `README.md`; retained-state acceptance stays blocked.
- US4: T023 then T024 follow T019 in the same file; do not parallelize writes. Pure concurrent calls tested by T024 are not parallel agent work.
- US5: T025 and T026 can be authored alongside US4 regressions after T022, with separate file owners; within T025 one owner handles both existing test files. Shared browser/runtime resources are not used.

## Implementation strategy and notes

The suggested MVP is the US1 static/refusal slice plus the mandatory US4 exact declaration and US5 compatibility/unavailable-inventory safeguards and all matching docs. A US1-only feature-complete or deployment claim is invalid. Incremental delivery ends at reviewed source groundwork on the named feature branch. A future accepted integration package owns B1-B8; full feature completion remains blocked.

Use the current registered modules, no new compatibility-facade consumers or direct registry/state JSON reads. Keep `sb` as the entry file, runtime/vendor untouched and working personal paths intact. New captured test children use synthetic environments even if existing neighboring tests use another pattern. Reports distinguish commands planned, commands actually run, actual counts/logs, source review and unavailable installed/live proof. Never mark a blocked outcome accepted or source checks as fixing remote credential use.
