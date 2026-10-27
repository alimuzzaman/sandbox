---
description: "Task list for feature 069: Secret Profiles and Create-Only Manager Migration"
---

# Tasks: Secret Profiles and Create-Only Manager Migration

**Input**: Design documents from `specs/069-secret-profiles/` (spec.md FR-001..FR-053 / SC-001..SC-013, plan.md, research.md R1-R13, data-model.md, contracts/, quickstart.md) and `.specify/memory/constitution.md`.
**Interfaces**: every signature comes from `contracts/python-interfaces.md`; do not invent public names. On-disk formats from `contracts/journal-approval.md`; wire frames from `contracts/worker-protocol.md`; HTTP from `contracts/provider-infisical-v4.md`; config grammar and digests from `contracts/config-schema.md`; CLI grammar, refusal codes and exit codes from `contracts/cli.md`.

**Ordering override (operator-directed, repo override of test-first)**: ALL code and docs first (Phases 1-9), then supported synthetic CLI acceptance (Phase 10), then focused regression tests are WRITTEN and RUN (Phase 11). No test is written or run before Phase 9 is complete. Tests run only through Haiku 5.5 agents using `tests/subprocess_support.synthetic_environment` / `run_test_process`; every report states the exact command and outcome; any single test with duration >= 5 s is reported as a failure to investigate; never the full suite.

**Workspace rules for every implementer**: shared repo on the active `latest` checkout; no branch, worktree, commit, push, tag, remote action or workflow dispatch; no `git stash`, `git reset`, `git checkout -- .`; never edit another owner's files. No real credential, env file, `secrets.asb.bd` tenant, provisioning or production migration until a human security review signs off (SC-013). Synthetic values only.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on an incomplete task)
- **[Story]**: US1..US6 from spec.md
- **Owner tags** in the description: **(A)** models/config/classify/continuity/source_view, `sources.read_with_stat`, `config/secrets.py`, `authority.py`, `context.py`; **(B)** manifest/protocol/worker/broker/evidence, `credentials/base.py` locator; **(C)** journal/approval/migrate/cleanup/scoped_run, `writer.py` remover, `commands/secrets.py`, `audit.py`, docs.

## Ownership map (single owner per file)

| Owner | Files |
|---|---|
| A | `sandbox/secrets/ownership/{__init__,models,config,classify,continuity,source_view}.py`, `sandbox/secrets/sources.py` (only `read_with_stat`), `sandbox/config/secrets.py`, `sandbox/secrets/authority.py`, `sandbox/secrets/context.py` |
| B | `sandbox/secrets/ownership/{manifest,protocol,worker,broker,evidence}.py`, `sandbox/secrets/credentials/base.py` (only `locator_for_ownership`) |
| C | `sandbox/secrets/ownership/{journal,approval,migrate,cleanup,scoped_run,cleanup_progress}.py`, `sandbox/secrets/writer.py` (only `remove_keys`), `sandbox/commands/secrets.py`, `sandbox/secrets/audit.py`, `docs/secret-inspection.md`, `skills/secret-inspection/SKILL.md`, `CLAUDE.md` gotcha 32, `docs/research/secret-ownership-wire-evidence.md` |

---

## Phase 1: Setup

- [X] T001 (A) Create package directory `sandbox/secrets/ownership/` with `__init__.py` that re-exports only the public names of `models.py` (re-exports of B/C names are added in T041 after they exist), stdlib + `sandbox.secrets.*` imports only.
- [X] T002 (A) Record the frozen import rule at the top of `sandbox/secrets/ownership/__init__.py` docstring: no import of `sandbox_core`, `sandbox.registry`, `sandbox.hermes`, MCP `app`, `writer.opaque_revision`, `writer.load_revision_key`; no read of `registry.json` (plan Constraints; enforced later by T078).

---

## Phase 2: Foundational (blocking; A freezes models first, then B and C run in parallel)

**Gate**: T003 must be merged into the shared tree and announced frozen before any B or C task starts. After T003, A's remaining foundational tasks and every B/C task may proceed in parallel on disjoint files.

- [X] T003 (A) Implement `sandbox/secrets/ownership/models.py` exactly as `contracts/python-interfaces.md` [A] models block: `Domain`, `Origin`, `Capability`, `NameState`, `BlockReason`, `Existence`, `Destination` (with `gateway: Literal["none","cloudflare-access"]="none"` default so direct mode keeps existing defaults), `OwnershipProfile`, `MapEntry`, `OwnershipConfig.from_normalized`, `ClassificationReport.counts/as_dict`, `SourceContinuity.same_as` (all fields except `reliable`/`unreliable_reason`), `Grant`, `StorageAdmission` (`attestation="operator_attested"` only), `Approval` (`surface="cli_tty"`), `CreateOutcome`, `RevocationOutcome`, `ScopedRunRequest`. Frozen dataclasses; no extra public fields. FREEZE: later changes require parent approval.
- [X] T004 [P] (A) Add generic `SourceRegistry.read_with_stat(self, alias) -> tuple[SafeSource, os.stat_result]` in `sandbox/secrets/sources.py`: one descriptor, read through it, final `fstat` from the same fd; no ownership import in `sources.py`; existing `read(alias)` byte-for-byte unchanged (FR-051).
- [X] T005 [P] (A) Implement `normalize_ownership(project_block, machine_block, sources, root)` in `sandbox/secrets/ownership/config.py` per `contracts/config-schema.md` Normalization: exactly one layer; allowed keys only; reject secret-like metadata and value-named fields; `undecided` not a profile name; profile requires domain + destination; destination origin checks reuse `_normalize_infisical_source` rules; `gateway` enum `none` (default) | `cloudflare-access`; `timeoutSeconds` 1..120 default 30; `readClientId != createClientId`; cross-domain profiles must not share projectId/readClientId/createClientId; `origin: destination` requires `readClientId`; consumer not in `_DANGEROUS_DESTINATIONS`; ownership decisions (undecided, multiply assigned, duplicate destination, other-domain) kept verbatim for classify; entries sorted `(source, key, profile, name)`; canonical JSON `sort_keys, (",",":"), ensure_ascii`.
- [X] T006 (A) Wire `ownership` into `sandbox/config/secrets.py`: `_SECRET_KEYS = {"sources","useProfiles","ownership"}`; call `normalize_ownership` after sources normalize; return dict gains `ownership` key ONLY when a block existed, otherwise identical object shape (FR-051, SC-008). Depends on T005.
- [X] T007 [P] (A) Implement `ownership_digest(profile_name, ownership, capability, *, project_root) -> "o1-<sha256>"` in `sandbox/secrets/authority.py` with payload per config-schema (including the complete canonical normalized ownership block hash, domain, destination incl. gateway, clientId by capability, entries, sorted names for create); extend `_fingerprint(..., ownership_consumers=())` and `bind_provider_authorities(..., ownership=None)` so that with no consumers every existing `a1-` digest is byte-identical; each ownership consumer carries `ownershipSha256` of the complete map. No value-derived input.
- [X] T008 [P] (A) Implement `capture(alias, info, names, *, now_ns)` in `sandbox/secrets/ownership/continuity.py`: stat fields + `names_sha256` (names only, never values); `reliable=False` with reason when ctime is not strictly older than `now_ns` granularity or the filesystem reports unusable fields (R8); no `opaque_revision`.
- [X] T009 (A) Implement `read_names` and `read_values` in `sandbox/secrets/ownership/source_view.py` over `SourceRegistry.read_with_stat` only (never reopens raw paths): registered file/dotenv aliases only, any other kind/format -> `source_unsupported`, unregistered -> `source_unregistered` with registration guidance (FR-050); `read_values` checks continuity before and after on the same fd (`source_changed` / `continuity_unreliable`); values returned as `bytearray`; caller wipes. Depends on T004, T008.
- [X] T010 (A) Pass normalized ownership into `SecretService` via `sandbox/secrets/context.py` (attribute only; unused and absent when no block; no behaviour change, FR-051). Depends on T006.
- [X] T011 [P] (B) Implement `sandbox/secrets/ownership/protocol.py`: frame encode/decode (`J` canonical ASCII JSON <= 64 KiB, `V` raw bytes <= `MAX_PROVIDER_VALUE_BYTES`, `E` ASCII code), 1-byte type + 4-byte big-endian length, reject unknown type/oversize/out-of-order; open frame `version: 2`; shared by broker and worker.
- [X] T012 [P] (B) Add `locator_for_ownership(grant) -> CredentialLocator` in `sandbox/secrets/credentials/base.py`: service namespace `sandbox-ownership`, account `f"{profile}:{capability}:{authority_digest}"`; no listing/deletion helpers (FR-039).
- [X] T013 [P] (C) Implement controller identity + runtime root in `sandbox/secrets/ownership/journal.py` (module-level helper used by journal/approval/CLI): root `$SANDBOX_HOME/runtime/secret-ownership/` dirs 0700 / files 0600, owner=euid, wrong mode/owner refuses; `controller-id` 32 hex created once with `O_EXCL`; approval id `ap_<controller>_<16hex>`, run id `run_<controller>_<16hex>` (controller-qualified, guidance only, no authority). See analysis gap G7 for the missing public name.
- [X] T014 (C) Implement `Journal` in `sandbox/secrets/ownership/journal.py` per contract: `open` with `flock(LOCK_EX|LOCK_NB)` (`journal_locked`), header controller check (`journal_foreign_controller`, names owning controller from header), `append` = full `os.write` + `fsync(fd)` + `fsync(dirfd)` on first create, `states()`; torn tail: only an incomplete final fragment lacking `\n` is truncated under the exclusive lock before append; any earlier corruption -> `journal_corrupt` refuse; a surviving final `attempted` resolves to `acceptance_unknown`, never `pending`. Depends on T003, T013.
- [X] T015 (C) Implement `ApprovalStore` core in `sandbox/secrets/ownership/approval.py`: `load`, `require_current` (expiry, operation, profile, map_sha256, destination_sha256, authority_digest, every `SourceContinuity.same_as`; codes `approval_expired`/`approval_mismatch`/`source_changed`/`authority_mismatch`/`continuity_unreliable`); never extends or rewrites an approval (FR-034). Depends on T003, T013.

**Checkpoint**: models frozen; config/digests stable; journal and approval primitives exist. B and C may proceed in parallel.

---

## Phase 3: User Story 1 - Metadata-only dry-run and fail-closed classification (P1) MVP

**Goal**: credential-free `classify` reports names/counts/reason classes; blocking names stop migration before any write.
**Independent test**: quickstart 1.1, 1.2, 1.7 on a synthetic dotenv source.

- [X] T016 [US1] (A) Implement `classify(config, profile, source_names, existence=None)` in `sandbox/secrets/ownership/classify.py`: states `would_create`, `pre_existing`, `unchecked` (no existence), `unknown`; blocking reasons `unmapped` (names present in any source referenced by the profile's entries that no entry maps), `undecided`, `multiply_assigned`, `duplicate_destination` (full endpoint/project/environment/path/name tuple), `other_domain` (scope conflict, not a bare equal name across isolated projects), `unregistered_source`, `source_unsupported` (incl. destination names failing `^[A-Z_][A-Z0-9_]*$`); duplicate consumer within one profile blocks; mapped key absent from source blocks by name; pure, no I/O, sorted names only.
- [X] T017 [US1] (C) Add `ownership` to `ACTIONS` in `sandbox/commands/secrets.py` and implement `ownership classify --profile P [--json]`: `read_names` per alias -> `classify`; existence `unchecked` without `--check-destination`; JSON shape per cli.md; exit 2 when blocking; `ownership_not_configured` / `profile_unknown`; never reads values. `cmd_secrets(cfg, args, *, ownership_adapter=None)` keyword-only seam. Depends on T009, T016.
- [X] T018 [P] [US1] (C) Add operation names `ownership_classify|ownership_approve|ownership_migrate|ownership_cleanup|ownership_run` to the allowed list in `sandbox/secrets/audit.py` (schema_version stays 1; names and counts only).

**Checkpoint**: US1 usable without any provider credential.

---

## Phase 4: User Story 2 - Approved create-only migration with a durable journal (P1)

**Goal**: one profile per run, create-only, journaled before send, verified in memory, sanitized summary.
**Independent test**: quickstart section 2 via fake adapter; live proof is human-gated (Phase 12).

**Hard dependency D1**: the PUBLIC-source V2 selection predicate (`V2_SELECTION_QUERY`, research R6) is being looked up by a separate agent. B MUST NOT research or duplicate it. T024 consumes the delivered query; Phase 4 (and B) is NOT complete until T024 lands a non-None, source-cited constant. No always-refuse empty placeholder may ship.

- [X] T019 [P] [US2] (B) Implement `OWNERSHIP_MANIFEST` and `load_ownership_adapter(name="infisical-v4")` in `sandbox/secrets/ownership/manifest.py`: capabilities `("read","create","revoke")`, surfaces `("cli",)`, `contract_confirmed` derived from evidence readiness (T024); create requested while unconfirmed -> `provider_contract_unconfirmed`; capability check before any side effect.
- [X] T020 [US2] (B) Implement `sandbox/secrets/ownership/worker.py` (stdlib only): `-I` child; reads bootstrap credential via `locator_for_ownership(grant)` from the native store; `gateway: none` -> opaque client secret; `gateway: cloudflare-access` -> exact JSON bundle `{clientSecret, gatewayClientId, gatewayClientSecret}` (any other shape -> `credential_scope_invalid`), sends only fixed `CF-Access-Client-Id`/`CF-Access-Client-Secret` headers; fixed honest `User-Agent: sandbox-secret-ownership/1`; one login; `BoundedHTTPTransport` semantics (exact HTTPS origin, no redirects, ambient proxies ignored, no retries, 1 MiB cap, per-request timeout <= min(timeoutSeconds, deadline)); `infisical._decode_json`; ops `exists`/`policy`(returns null)/`create` (ready -> wait for `go`/`abort`)/`readback`/`read`/`close` exactly as worker-protocol.md; names subset of `grant.names`; create/readback only for capability create, read only for read; classification table from provider-infisical-v4.md and R4/R5 (hidden-value parse, 404 JSON -> absent, `{"approval":...}` -> unresolved, 400 already-exists -> conflict, timeout/5xx/malformed -> acceptance_unknown, pre-connect -> failed); SIGTERM handler attempts revoke within deadline; wipe buffers; no list/search/export endpoint, no Cloudflare administration. Depends on T011, T012.
- [X] T021 [US2] (B) Implement `validate_create_inputs(grant, values)` on the adapter (pure): reject non-lossless UTF-8 and any value whose leading/trailing code point is in the exact 25-code-point ECMAScript TrimString set; never normalize; errors carry name + reason only. Also re-run per value inside `create` defensively.
- [X] T022 [US2] (B) Implement `BrokerOwnershipAdapter` and its `OwnershipSession` in `sandbox/secrets/ownership/broker.py`: spawn per worker-protocol.md (env allowlist = `BoundedProviderBroker._environment()`, `start_new_session=True`, stderr DEVNULL, close_fds); `__exit__` always sends `close`, then `terminate_process_group(grace 0.5, kill 0.5)`; `revocation()` -> `revoked` only on worker-confirmed acknowledgment, else `cleanup_unknown` with next action (token expiry time; gateway tokens and bootstrap entries operator-managed, not confirmed); `token_expires_at` from `opened`; fixed safe error messages, never interpolate bodies/exceptions/lengths. Depends on T020.
- [X] T023 [P] [US2] (B) Implement `admit_evidence(path, destination, *, now)` in `sandbox/secrets/ownership/evidence.py` per storage-evidence.md: exact kind/version/keys; destination binding (origin + projectId); `capturedAt <= now < expiresAt`, window <= 3600 s; producer exact; SQL byte-equal to shipped constants; Q1 row match and cast-tolerant indexdef parse (until `EXPECTED_INDEXDEF` pinned); version strings, bare booleans, source excerpts rejected by shape; returns `StorageAdmission(attestation="operator_attested")`; never the word "verified" for storage.
- [X] T024 [US2] (B) BLOCKED ON D1: set `V2_SELECTION_QUERY` in `sandbox/secrets/ownership/evidence.py` and its output predicate from the parent-delivered public-source result, with a source citation comment (provider tag, file); flip manifest `contract_confirmed` accordingly. If D1 resolves as "not determinable from public source", STOP and report to the parent; do not ship a None/always-refuse constant.
- [X] T025 [US2] (C) Implement `run_migration(service_ctx, profile, approval_id, *, confirm, timeout_seconds, adapter=None)` in `sandbox/secrets/ownership/migrate.py` following the normative 12-step algorithm in python-interfaces.md: classify -> dry-run plan (exit 0, existence `unchecked`) without `--confirm`; `require_current`; `admit_evidence` on the stored copy bound by sha256; manifest capability; `Journal.open`; `open_session(Grant(create))`; `policy_requires_approval() is True` -> `provider_policy_requires_approval`; `exists(all)`: any UNKNOWN -> `existence_unknown`, fresh journal + any PRESENT -> `pre_existing` whole batch, 0 writes; `read_values` (continuity re-check); batch `validate_create_inputs` on the ENTIRE batch before the first create; per name `create(before_send=journal attempted+fsync)`, stop on conflict/unresolved/acceptance_unknown, `readback_equals` -> verified/unresolved; deadline = min(approval effective expiry, token expiry, `--timeout-seconds` <= 3600) checked before each name; exit session, wipe values, write CompletionRecord (T036). Exit codes 0/2/3/4/5/6 per cli.md. Depends on T014, T015, T022, T023.
- [X] T026 [US2] (C) Add CLI `ownership migrate --profile P --approval ID [--confirm] [--timeout-seconds 1..3600]` and `ownership status --profile P` (journal + last run record, names/states only) in `sandbox/commands/secrets.py`. Depends on T025.
- [X] T027 [P] [US2] (C) Add CLI `ownership evidence-check --profile P --evidence FILE` (calls `admit_evidence`, prints `operator_attested` admission report, no provider call) and copy admitted evidence to `R/evidence/<sha256>.json` (0600) in `sandbox/commands/secrets.py`. Depends on T023.
- [X] T028 [US2] (C) Add CLI `ownership credential-set --profile P --capability read|create --stdin [--create-only|--replace-only]` in `sandbox/commands/secrets.py`: secret from TTY or stdin pipe only (never argv/env), stored via `credentials/manifest.create_credential_store` at `locator_for_ownership(Grant)`; for `gateway: cloudflare-access` profiles the input must be the exact 3-key JSON bundle (validation seam: see analysis gap G1); never lists or deletes store entries. Depends on T012.
- [X] T029 [US2] (C) Add `classify --check-destination` in `sandbox/commands/secrets.py`: value-free `exists` via a READ grant session (`would_create`/`pre_existing`/`unknown`), never a create grant, never fetches a value. Depends on T022, T017.

**Checkpoint**: migration end-to-end against the fake adapter; real provider still gated.

---

## Phase 5: User Story 3 - Human-only approval, expiry and same-authority resume (P2)

**Goal**: human-only TTY grants bound to operator/controller/profile/source state/full map/destination/authority, <= 1 h, never past evidence or token; resume skips confirmed names.
**Independent test**: quickstart 1.3, 1.5 and section 2 token-expiry case.

- [X] T030 [US3] (C) Implement `ApprovalStore.grant_interactive(...)` in `sandbox/secrets/ownership/approval.py`: refuse `approval_human_only` unless stdin AND stdout are TTYs and the process is in the foreground process group (covers MCP and non-interactive callers); display profile, names, destination, authority digest, `storage_attestation: operator_attested`; require typed confirmation; `minutes` 1..60; `expires_at <= min(created+3600, evidence expiresAt)`; bind `SourceContinuity` per alias, `map_sha256`, `destination_sha256`, `authority_digest`, `evidence_sha256`; persist 0600 canonical JSON. Depends on T015.
- [X] T031 [US3] (C) Add CLI `ownership approve --profile P --operation migrate|cleanup [--evidence FILE] [--minutes 1..60]` in `sandbox/commands/secrets.py`: `migrate` requires `--evidence` (calls `admit_evidence`, copies into `R/evidence/<sha256>.json`); `cleanup` forbids `--evidence`. Calls `grant_interactive`. Output shows `approval_id`, expiry, states `storage_attestation: operator_attested`. Depends on T030.
- [X] T032 [US3] (C) Implement resume logic in `sandbox/secrets/ownership/migrate.py`: load journal, re-verify approval currency via `require_current`; terminal `verified`/`reconciled` revalidated against destination metadata without create; `attempted`/`acceptance_unknown` re-checked: if absent -> pending, if matches expected value -> journal `reconciled`, if mismatch -> journal `unresolved` (stop); abort if journal has ANY foreign controller ID or missing local owner guidance.
- [X] T033 [US3] (C) Implement token-expiry binding and check: clamp run deadline to `min(approval.expires_at, session.token_expires_at)`; check remaining time before each name create.

**Checkpoint**: approval cannot be forged by non-TTY; resume is same-authority and idempotent.

---

## Phase 6: User Story 4 - Destination-origin secrets and scoped command execution (P2)

**Goal**: `sb secrets run --ownership-profile P --ownership-key K ... -- ARGV` delivers declared consumer env variables from mixed source/destination origins under exclusive read grants.
**Independent test**: quickstart 1.6 and section 3 scoped-run tests.

- [X] T034 [US4] (C) Add ownership selectors to `run` in `sandbox/commands/secrets.py`: `--ownership-profile P` and repeated `--ownership-key K`; reject mixing with `--source`, `--key`, `--secret`, `--destination`, `--session`, `--lifetime-seconds` (`option_conflict`); require at least one `--ownership-key` (`ownership_keys_required`); enforce same safe-environment / escalation policy as existing `run`.
- [X] T035 [US4] (C) Implement `resolve_scoped_bindings(service_ctx, request, adapter=None)` in `sandbox/secrets/ownership/scoped_run.py`: validate request keys subset of profile; verify no cross-domain mixing (`other_domain`); for `source` origin entries call `source_view.read_values`; for `destination` origin entries open `Grant(read)` session and call `session.read(names)`; enforce consumer name policy; return `{consumer: value_str}`; wipe buffers after injection; child launch propagates existing runner exit code.

**Checkpoint**: scoped command execution works with destination and source entries.

---

## Phase 7: User Story 5 - Explicit verified-only cleanup of local source keys (P3)

**Goal**: human-confirmed, verified-only atomic removal of migrated source keys with a pre-removal read-grant RAM check.
**Independent test**: quickstart section 3 cleanup tests.

- [X] T036 [US5] (C) Implement `remove_keys(path, keys, *, expected)` in `sandbox/secrets/writer.py`: dotenv only; descriptor-safe write to atomic temp file in same directory, mode/owner preserved, `fsync(fd)`, `os.replace`, `fsync(dirfd)`; check continuity against `expected`; returns new `SourceContinuity`; no `opaque_revision` used.
- [X] T037 [US5] (C) Add CLI `ownership cleanup --profile P [--approval ID] [--confirm]` in `sandbox/commands/secrets.py`: without `--confirm`: dry-run plan of eligible keys; with `--confirm`: requires valid cleanup approval; for each key performs pre-removal `readback_equals` via read grant session; calls `remove_keys`; marks approval consumed on first removal; exit codes per cli.md.
- [X] T038 [US5] (C) Implement cleanup orchestration in `sandbox/secrets/ownership/cleanup.py`: eligibility requires journal state `verified` or `reconciled` and destination entry presence; any `unresolved`/`conflict`/`acceptance_unknown` in journal refuses whole cleanup (`cleanup_ineligible`).

**Checkpoint**: cleanup cannot touch unverified keys or run without explicit approval.

---

## Phase 8: User Story 6 - Audit trail, status and settled-state retention (P3)

**Goal**: audit records match schema_version 1; status summarizes runtime state; retention purges completed runs on confirm.

- [X] T039 [US6] (C) Implement CompletionRecord serialization in `sandbox/secrets/ownership/migrate.py`, `cleanup.py`, `scoped_run.py`: written to `R/runs/<run_id>.json` (0600); non-secret metadata only; records `counts`, `names`, `revocation`, `next_action`, `storage_attestation`.
- [X] T040 [US6] (C) Add CLI `ownership retention --profile P [--confirm]` in `sandbox/commands/secrets.py`: dry-run by default; `--confirm` removes completed/settled journals and run records; refuses retention if ANY journal entry is non-terminal (`attempted`, `acceptance_unknown`, `unresolved`, `conflict`); never deletes lock files or native store credentials.
- [X] T041 (A) Export public interfaces in `sandbox/secrets/ownership/__init__.py`.

---

## Phase 9: Documentation, Skills and Polish

- [X] T042 (C) Update `docs/secret-inspection.md` with ownership profiles, CLI workflows, exit codes, security properties and limitations.
- [X] T043 (C) Update `skills/secret-inspection/SKILL.md` with the ownership decision tree and command forms.
- [X] T044 (C) Add CLAUDE.md gotcha 32 amendment: ownership rules, human approval requirements, zero value exposure.
- [X] T045 (C) Document wire evidence and provider verification status in `docs/research/secret-ownership-wire-evidence.md`.
- [X] T046 (C) Verify AST import boundaries and static checks across all new modules.

---

## Phase 10: Synthetic CLI Acceptance (real CLI against synthetic fixtures)

- [X] T047 Write the CLI synthetic acceptance test in `tests/test_secret_ownership_cli_acceptance.py` (quickstart section 1): baseline preserved (FR-051, SC-008), classify blocking rules (US1), approve TTY gate (FR-032), migrate unconfirmed / evidence refusal, value-only edit mtime detection (FR-035), scoped run isolation (SC-007), leak scan over stdout/stderr/journals (SC-001).
- [X] T048 Write the in-process fake-adapter acceptance in `tests/test_secret_ownership_cli_acceptance.py` (quickstart section 2): clean run, initial pre-existing (SC-002), post-preflight conflict (SC-005), interruption -> `acceptance_unknown` -> reconcile to exactly one (SC-003), token expiry and re-approval with wider authority refused (SC-004), approval/malformed bodies (SC-006), revocation failure -> `cleanup_unknown` exit 6 (SC-009), last-batch-entry TrimString rejection causes 0 writes.
- [X] T049 Haiku agent runs T047/T048 via `run_test_process` (`python -m unittest tests.test_secret_ownership_cli_acceptance -v --durations 0`), reports command, pass/fail counts and any test >= 5 s as a failure.

---

## Phase 11: Focused regression tests (written and run only after Phase 10)

- [X] T050 [P] Write `tests/test_secret_ownership_config.py` (normalization rejects, sort/canonical JSON, gateway enum, single layer, `ownership` key absent when no block).
- [X] T051 [P] Write `tests/test_secret_ownership_classify.py` (every BlockReason, duplicate tuple rule, cross-project equal names allowed, uppercase name rule).
- [X] T052 [P] Write `tests/test_secret_ownership_continuity.py` (`read_with_stat` same-fd stat, value-only edit with restored mtime detected, unreliable path, dotenv-only `source_unsupported`).
- [X] T053 [P] Write `tests/test_secret_ownership_worker.py` (frames, out-of-order/oversize, op/capability gating, fake transport classification table, fixed UA, gateway headers only for `cloudflare-access`, no proxy/redirect, revoke outcomes).
- [X] T054 [P] Write `tests/test_secret_ownership_journal.py` (fsync order, lock contention, foreign controller, torn final fragment truncated and attempted -> acceptance_unknown, mid-file corruption refused, retention refusal).
- [X] T055 [P] Write `tests/test_secret_ownership_approval.py` (TTY/foreground gate, 1 h and evidence caps, every binding mismatch code, qualified ids).
- [X] T056 [P] Write `tests/test_secret_ownership_migrate.py` (normative step order, batch validation before first create, deadline per name, exit codes).
- [X] T057 [P] Write `tests/test_secret_ownership_cleanup.py` (eligibility, per-name final check outcomes, `remove_keys` atomic + continuity).
- [X] T058 [P] Write `tests/test_secret_ownership_scoped_run.py` (refusals before value read, mixed origins, read grant only, selector exclusivity).
- [X] T059 [P] Write `tests/test_secret_ownership_boundaries.py`: AST gate that `sandbox/secrets/ownership/*` never imports `opaque_revision`, `load_revision_key`, `sandbox_core`, `sandbox.registry`, `sandbox.hermes`, MCP `app`; `sources.py` does not import `ownership`; worker imports stdlib + minimal sandbox.secrets authority, credential, transport and protocol helpers only; no controller/CLI/core imports; no `registry.json` string.
- [X] T060 [P] Add an `a1-` digest regression to `tests/test_secret_ownership_config.py` (or the authority section of it): every existing fixture with `ownership=None` equals pre-change digests (SC-008).
- [ ] T061 Haiku agents (one per module group, fresh per run) run T050-T060 modules and then the focused existing set `tests/test_secret_config.py tests/test_secret_commands.py tests/test_secret_service.py tests/test_secret_provider_broker.py tests/test_secret_credentials.py tests/test_secret_mcp.py tests/test_mcp.py` via `run_test_process` with `--durations 0`; never the full suite; report command and outcome; >= 5 s per test = failure.

---

## Phase 12: Human-gated (NOT executed by this task list)

- [ ] T062 Human: approve a concrete disposable-instance test plan (never `secrets.asb.bd`), provision a separate synthetic Infisical instance and identities, then run the R4/R5/R6/R7 live proofs and pin `EXPECTED_INDEXDEF`; until recorded, mark those proofs "unverified".
- [ ] T063 Human: security review of write authority, credential lifetime, revocation, gateway bundle and the CLAUDE.md gotcha 32 amendment; Linux + macOS controller acceptance (SC-012); only then any real migration (SC-013).

---

## Phase 13: Independent Review Findings Implementation (R1-R4)

- [X] T064 (R1 - Blocking) Wire `before_send` re-check of source continuity across all approval sources in `sandbox/secrets/ownership/migrate.py`:
  - Fresh `read_names()` on all `approval.sources` aliases inside `before_send` callback before appending `ATTEMPTED`.
  - Validate `store.require_current` across all approval sources, profile, config, destination, and authority.
  - Re-verify clock bounds; abort with `source_changed` without releasing `GO` or writing `ATTEMPTED` if modified.
- [X] T065 (R2 - Blocking) Durable token expiry sidecar and enforcement in `sandbox/secrets/ownership/approval.py` & `migrate.py`:
  - Added `bind_token_expiry` creating private 0600 `<approval_id>.token_expiry.json` bound to controller, operator, map, destination, authority, and token expiry.
  - Enforced `effective_expires_at = min(existing_effective, expires_at, approval.expires_at)`.
  - `ApprovalStore.require_current` enforces token expiry sidecar, failing closed with `approval_expired` if expired, or `approval_mismatch` if corrupt/mismatched.
  - Migration immediately binds token expiry upon opening worker session in `_reconcile` and `run_migration`.
- [X] T066 (R3 - Should-fix) Durable per-name cleanup progress, crash recovery, and writer hook:
  - Added optional `before_replace` callback to `sandbox/secrets/writer.py:remove_keys`, invoked with staged `SourceContinuity` before atomic replace.
  - Implemented `CleanupProgress` in `sandbox/secrets/ownership/cleanup_progress.py` managing versioned `cleanup.json` sidecar in journal directory.
  - Recorded `prepare` with staged inode metadata before replacement; recorded `checkpoint_removed` after replacement.
  - Implemented crash recovery promoting prepared intent to removed on matching staged inode; failed closed on unexpected missing keys.
  - Integrated `proven_removed` parameter in `sandbox/secrets/ownership/classify.py` to prevent false `SOURCE_KEY_MISSING` blocking.
- [X] T067 (R4 - Should-fix) Trusted derived token follow-up on uncertain revocation:
  - Added `revocation_outcome` and `revocation_status` helpers in `sandbox/secrets/ownership/migrate.py`.
  - In `_finish`, formatted token expiry time and operator-managed bootstrap notice in `next_action` on `cleanup_unknown`. Retained primary failure code.
  - In `scoped_run.py`, preserved `revocation_outcome` and attached guidance on read failure when revocation is unconfirmed.
  - In `commands/secrets.py`, formatted trusted next action in `classify --check-destination` and sanitized dispatch remedies.

---

## Dependencies & Execution Order

- Phase 1 -> T003 (models freeze, A) -> {A foundational T004-T010} || {B T011-T012} || {C T013-T015}.
- US1 (Phase 3) needs T009 + T016. US2 needs Phase 2 + B worker/broker/evidence + C journal/approval core. US3 approve CLI is required before any `migrate --confirm` acceptance; US2 dry-run does not need it. US4 needs T009 + T022 (read session). US5 needs T020/T025. US6 needs T030, T036, T038.
- **D1**: T024 (and B completion) blocks on the parent-delivered public-source V2 predicate; Phase 9 T046 cannot close until T024 is done.
- Phase 9 complete -> Phase 10 -> Phase 11. Phase 12 is human-only.
- Phase 13 implements review findings R1-R4 across C and writer hooks.

### Parallel opportunities
- After T003: A (T004, T005, T007, T008), B (T011, T012, T019, T023), C (T013, T018) run concurrently on disjoint files.
- Within C, CLI edits to `sandbox/commands/secrets.py` (T017, T026-T029, T031, T034, T037, T040) are serialized (one file, one owner).
- Phase 11 test files T050-T059 are independent and may be written in parallel.

## Implementation Strategy

MVP = Phase 1-3 (credential-free classify). Then US2+US3 together (migration is not usable without approval), then US4, US5, US6, then Phase 9 docs/integration, then acceptance and focused regressions. No release, tag, deploy or real migration is authorized by this list.

## Final local verification evidence (2026-10-27)

The current composite focused result is 455/455 cases across 22 modules, using the latest applicable r20/r21/r24/r25 reports. No individual test reached 5 seconds; the slowest was 4.118 seconds. Independent source review r5 cleared R1–R15. Detailed evidence is in the external scratch report `final-local-verification.txt`. No commits or pushes were made. T061 remains open for its broader `tests.test_mcp` requirement, which was outside the exact authorized focused list; the eight requested legacy secret modules passed. T062/T063 remain human-gated and unverified. Actual Linux execution, native stores and disposable-provider proofs are not supplied by synthetic tests.
