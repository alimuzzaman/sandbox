# Tasks: Secrets unset and remote copy

**Input**: `specs/073-secrets-unset-copy/` (plan.md, spec.md, research.md, data-model.md, contracts/cli.md)

**Tests**: requested (owner brief: types first, then tests, then implementation). Tests run only in Haiku helpers.

## Phase 1: Setup

- [x] T001 Create the scratch log directory `/Users/alim/Sites/git/.scratch/sandbox/secrets-unset-copy/` (outside the worktree)

## Phase 2: Foundational (types first)

- [x] T002 Add `CopyAction`, `CopyIntent`, `RemoteSetRequest` (with validation and `destination`), `RemoteSetResult` and the `RemoteSecretSink` protocol in `sandbox/secrets/remote_copy.py`
- [x] T003 Add the optional `destination` audit field (emitted only when set; refused for ownership operations) in `sandbox/secrets/audit.py`, and thread `destination=` through `SecretService._operate` in `sandbox/secrets/service.py`
- [x] T004 Add `skip_unchanged` to `update_source` returning `("already_current", current_revision)` without a write in `sandbox/secrets/writer.py`

## Phase 3: User Story 1 - unset (P1)

**Goal**: remove one key from a local dotenv source. **Independent test**: temp three-key source, unset middle key, two others byte-identical, audit intent+outcome, rerun `key_missing`.

- [x] T005 [US1] Write failing service tests for unset (happy path bytes and mode preserved, rerun `key_missing`, stale `--if-revision`, provider refusal before audit, non-dotenv refusal, invalid key, audit events, value-free payload and audit) in `tests/test_secret_unset_copy.py`
- [x] T006 [US1] Write failing CLI tests for `unset` parse and dispatch (`--json`, human output, refusal exit) in `tests/test_secret_unset_copy.py`
- [x] T007 [US1] Implement `SecretService.unset` using `read_with_stat`, `continuity.capture` and `writer.remove_keys` with the R1 error mapping in `sandbox/secrets/service.py`
- [x] T008 [US1] Add the `unset` parser, `ACTIONS` entry and dispatch in `sandbox/commands/secrets.py`

## Phase 4: User Story 2 - copy (P2)

**Goal**: copy one value to a remote source over SSH stdin. **Independent test**: fake sink records value only on its input; actions map; canary absent from argv, env, output, audit.

- [x] T009 [US2] Write failing tests for `set --skip-unchanged` (service and CLI; identical value no write and same revision; different value updates; create-only identical converges) in `tests/test_secret_unset_copy.py`
- [x] T010 [US2] Write failing service tests for `copy_to_remote` with a fake sink (created/updated/already_current, remote refusal codes pass through, unknown and unprovisioned remote refused before intent, missing local key, line-break and oversize values refused before the sink, provider refusal, audit `destination` and `reason_code`, value-free payload and audit) in `tests/test_secret_unset_copy.py`
- [x] T011 [US2] Write failing tests for `SshRemoteSecretSink` with `ssh_run` patched (command string quoted and value-free, value only in `input_data`, default and explicit project dir, intent flags, exit 255/2/other, timeout, JSON refusal, oversized or malformed stdout, stderr never surfaced) in `tests/test_secret_unset_copy.py`
- [x] T012 [US2] Write failing CLI tests for `copy` parse and dispatch with the sink patched in `tests/test_secret_unset_copy.py`
- [x] T013 [US2] Pass `skip_unchanged` through `SecretService.set` and `copy_reference` in `sandbox/secrets/service.py`, and add `--skip-unchanged` to the `set` parser and dispatch in `sandbox/commands/secrets.py`
- [x] T014 [US2] Implement `SshRemoteSecretSink` and the bounded response parser in `sandbox/secrets/remote_copy.py`
- [x] T015 [US2] Implement `SecretService.copy_to_remote` in `sandbox/secrets/service.py`
- [x] T016 [US2] Add the `copy` parser, `ACTIONS` entry, remote lookup and dispatch in `sandbox/commands/secrets.py`

## Phase 5: Polish

- [x] T017 [P] Document unset, copy and `--skip-unchanged` in `docs/secret-inspection.md`
- [x] T018 [P] Update `skills/secret-inspection/SKILL.md` and the secrets lines of `skills/sandbox-cli/SKILL.md`
- [x] T019 Run the secrets test modules and a byte-compile check in a Haiku helper; logs under the scratch root
- [x] T020 CLI smoke of `unset` against a temp source in a temp `SANDBOX_HOME` in a Haiku helper
- [x] T021 Luna non-author review of the head; fix or answer every finding
- [x] T022 Commit, push `feat/secrets-unset-copy` with `-u`, verify upstream

## Dependencies

T002-T004 block both stories. US1 (T005-T008) does not depend on US2. US2 needs T002-T004. T017/T018 can run in parallel with T019.

## Implementation strategy

MVP is US1 (local only). US2 needs the remote's runtime migrated before real use.

## Evidence (2026-11-10)

- `tests.test_secret_unset_copy`: 37 run, 37 passed.
- Four existing secret modules (`test_secret_commands`, `test_secret_service`,
  `test_secret_ownership_cli_acceptance`, `test_secret_ownership_review_regressions`):
  220 run, 218 passed, 2 errors. The base commit `faeb419` has the same 2 errors
  (`test_context_check_requires_no_descriptor_or_source_file`,
  `test_allowed_and_denied_offload_contexts`, project-root allowlist).
- CLI smoke against a temp `SANDBOX_HOME`: 10 of 10 expectations held. The removed
  fixture value appears 0 times in the output and 0 times in the audit.
- Non-author review (GPT-6 Luna, high effort, Codex Office) of `4a12190`: no findings.
- Not run: `copy` against a real remote (the brief forbids it).
