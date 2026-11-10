# Tasks: Stack Lifetime and Per-Copy Stack Identity

**Input**: Design documents from `specs/077-stack-lifetime/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: requested (user policy: types first, then tests, then implementation). Tests
run only on xspeed-dev through `sb job-start --remote xspeed-dev`, by a Haiku helper.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [x] T001 Create package `sandbox/hosting/lifetime/__init__.py` with a module docstring naming spec 077

## Phase 2: Foundational (blocks every story)

- [x] T002 Types in `sandbox/hosting/lifetime/hoststore.py` (stdlib only, no package imports, Python 3.8 syntax): Lifetime/LifetimeSource/RecordState literals, Owner, StackIdentity, MergeEvidence, ReapInfo, TargetRecord, Policy dataclasses; `parse_record(data, file_name) -> TargetRecord | Invalid`, `record_to_dict` preserving unknown keys; `parse_ttl("14d")`; RFC3339 helpers
- [x] T003 Pure decisions in `sandbox/hosting/lifetime/hoststore.py`: `eligibility(record, now)`, `decide_claim(existing_records, draft, ...)`, `check_limits(records, policy, free_bytes, owner, is_new)`
- [x] T004 Tests for T002-T003 in `tests/test_target_lifetime.py`: validation failures are protected, TTL bounds, eligibility table (temporary expired/merged/not; hosted; permanent; reaped; invalid), limits, claim decisions (same copy, other copy, other owner, explicit copy with matching/different owner, project taken, hosted taken, expired without new TTL, reaping, legacy)
- [x] T005 Host store in `sandbox/hosting/lifetime/hoststore.py`: `Store(home)` with flock, atomic writes (0600/0700), audit lines; ops `list`, `check`, `claim`, `update`, `release`, `record_merge`, `classify`, `policy`; `main()` reads one JSON request from stdin and prints one JSON line
- [x] T006 Tests for T005 in `tests/test_target_lifetime.py`: run ops in-process and as `python3 -c <source>` subprocess against a temp `SANDBOX_HOME`; two concurrent claims of one slug (multiprocessing) produce exactly one winner; unknown keys survive an update; invalid file never rewritten by claim
- [x] T007 Controller client in `sandbox/hosting/lifetime/client.py`: ship `hoststore.py` source over `remote.ssh_run(..., input_data=...)`, parse the response, map refusals to `HostingRefusal` codes; injectable runner for tests
- [x] T008 [P] Naming in `sandbox/hosting/lifetime/naming.py`: owner label (flag or `git config user.name`; an email is refused), normalized repo identity from `remote.origin.url` (no credentials), `generate_slug(ref_branch, commit, owner, taken, pattern)`; tests in `tests/test_target_lifetime.py`
- [x] T009 Template `stack.dev_branch` (optional, default `dev`) in `sandbox/hosting/stack_template.py`; registry fields in `sandbox/hosting/stack_registry.py`; test in `tests/test_host_stack.py`

## Phase 3: User Story 2 - Many copies, never taken over (P1)

**Independent test**: two owners claim one slug on one fake remote; the second gets `stack_slug_taken`; two unnamed `up`s from one controller get distinct generated slugs.

- [x] T010 [US2] Tests in `tests/test_host_stack.py`: `up --plan/--confirm` runs `check`/`claim` before worktree creation; refusal leaves no worktree, manifest or registry change; generated name reported; owner and copy in the result; re-up from the holding copy updates in place; ports and hostnames held by remote records of other stacks are refused
- [x] T011 [US2] `sandbox/commands/host_stack.py` `_up`: lifetime flags (`--lifetime`, `--ttl`), owner, `--copy`, optional `--name` with generated slug and claim retry, remote records feed port allocation and hostname ownership, claim before worktree, registry mirror
- [x] T012 [US2] CLI flags and actions in `sandbox/cli.py` and dispatch in `sandbox/commands/hosting.py` (stack actions `ttl|sweep|reap|limits`; host action `classify`)

## Phase 4: User Story 1 - PR stacks clean themselves up (P1)

**Independent test**: an expired temporary record and its fake target on a temp `SANDBOX_HOME` are torn down by the reaper with manifest intents first; hosted/permanent/unclassified untouched.

- [x] T013 [US1] Extract the edge-cleanup command builder in `sandbox/hosting/destroy.py` (`edge_cleanup_command(name, front_door)`), `_remove_edge` uses it; existing destroy tests stay green
- [x] T014 [US1] Tests in `tests/test_target_lifetime.py` for the reaper: fake shell runner records commands; intents precede commands; record goes `active -> reaping -> reaped`; resume from `reaping`; skip when the apply transaction is active or the reclaim guard is held; Caddy front door skipped; invalid and unclassified untouched; re-check under lock skips a record whose TTL was extended after planning
- [x] T015 [US1] Reaper in `sandbox/hosting/lifetime/reaper.py`: `plan(home, now)`, `run(home, now, run_id, runner, manifest_path)`; list containers/volumes/networks/own image tags by label, intents, `destroy._remote_teardown_command`, nginx `transaction_command(remove=True)`, `edge_cleanup_command`, source dir when no environment remains
- [x] T016 [US1] Routine pass in `sandbox/resources/cleanup_routine/run.py`: run the reaper under the reclaim guard after the busy checks, count its removals in the run record, then continue with the workspace plan; tests in `tests/test_target_lifetime.py`
- [x] T017 [US1] `sb host stack reap --here|--remote` in `sandbox/commands/host_stack.py`; `--remote` runs `<remote_sb_path> host stack reap --here ... --json` and maps an unknown action to `stack_reap_unavailable`; `sb resources cleanup --tier safe|all --confirm` runs it after the probe cleanup in `sandbox/commands/resources.py`
- [x] T018 [US1] Merge sweep in `sandbox/hosting/lifetime/merge.py` and `stack sweep`; automatic sweep after a confirmed canonical up; tests with a temp Git repo (merged, not merged, missing commit, fetch failure)

## Phase 5: User Story 3 - See and manage everyone's stacks (P2)

- [x] T019 [US3] Tests in `tests/test_host_stack.py`: list merges remote records and local registry (`mine`, `remote_record`), `ttl` owner check and override audit, `down` owner check, `down` of a reaped stack
- [x] T020 [US3] `_list`, `_down`, `_ttl` in `sandbox/commands/host_stack.py`

## Phase 6: User Story 4 - Dev stack and long-lived apps never reaped (P2)

- [x] T021 [US4] Tests: hosted only for dev ref; one canonical per repo; classify/unclassify an existing target; classify a missing target refused
- [x] T022 [US4] `sandbox/commands/host_classify.py` (`sb host classify`)

## Phase 7: User Story 5 - Copies cannot fill the server (P3)

- [x] T023 [US5] Tests: per-owner cap, total cap, free-disk minimum, re-run not counted; `stack limits` show/confirm
- [x] T024 [US5] `stack limits` in `sandbox/commands/host_stack.py`

## Phase 8: Polish

- [x] T025 Docs: `docs/remote-hosting.md` (lifetimes, identity, reaping, limits), `skills/sandbox-cli/SKILL.md` and its `.agents` mirror, `CLAUDE.md` gotcha (record store, no-record-is-protected, reaper needs migrate)
- [x] T026 Run the suites on xspeed-dev (Haiku helper) and compare failures with an `origin/latest` baseline
- [x] T027 Luna review of the final head on `codex_office`; fix or answer every finding
- [x] T028 Hand-back note to the orchestrator (head SHA, files, tests, review, limits, panel field status, stray-container finding)

## Dependencies

Phase 2 blocks all. US2 (T010-T012) before US1's `stack reap` CLI (T017) and US3/US4/US5 CLI work (shared files). T013-T016 can proceed after Phase 2 in parallel with US2. Polish last.

## Parallel examples

- T008 with T005-T007 (different files).
- T013-T015 (destroy, reaper) with T010-T011 (host_stack).

## Implementation strategy

MVP = Phase 2 + US2 + US1 (claim, identity, reaping). US3-US5 complete the release; all ship together in one release cycle.
