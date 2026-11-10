# Tasks: Tailnet-Only Hostnames

**Input**: Design documents from `specs/078-host-tailnet-name/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/host-tailnet-name-cli.md, quickstart.md

**Tests**: Required (owner rule: types first, then tests, then implementation).
Tests run only on `xspeed-dev` through `sb job-start`, by Haiku helpers; every
run must print a `Ran N tests` line.

**Repo rule**: placeholders only (`example.test`, `203.0.113.x`, `100.64.0.x`);
no real hostname, address or zone anywhere in code, tests or docs.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [ ] T001 Create the package `sandbox/hosting/tailnet_names/__init__.py` with a module docstring naming the ownership model (Sandbox-owned `sandbox-tailnet-*.conf` files and `/etc/nginx/sandbox-tailnet/<fqdn>/` only)

## Phase 2: Foundational (types first)

- [ ] T002 Define `TailnetNameError` (subclass of `sandbox.core._hosting.HostingRefusal`), the code constants from contracts/host-tailnet-name-cli.md, and the frozen types `TailnetNameConfig`, `TailnetName`, `Upstream`, `RemoteTarget`, `StepOutcome`, `RemoteInventoryEntry`, `DnsResult`, `TailnetNameRecord` with their parsers in `sandbox/hosting/tailnet_names/models.py`
- [ ] T003 Add the `TAILNET_NAMES` capability to the nginx declaration only in `sandbox/hosting/front_door/__init__.py`
- [ ] T004 [P] Tests for the parsers (FQDN rules, zone longest-suffix match, apex and wildcard refusals, upstream ranges, config block errors, tailnet address range, public address resolution) and the capability in `tests/test_host_tailnet_name.py`
- [ ] T005 Implement the parsers until T004 passes in `sandbox/hosting/tailnet_names/models.py`

**Checkpoint**: every argument-level refusal happens with no network call.

## Phase 3: User Story 1 - Publish an app on a tailnet-only HTTPS name (P1) MVP

**Goal**: `add` creates the record, the certificate, the block, reloads under the gate and verifies.

**Independent Test**: With fakes for the remote program, nginx route transaction, broker DNS, broker `lego`, job query and probes, `add` runs every step in order, a rerun skips them all, and each failure point leaves the web server untouched.

### Tests for User Story 1

- [ ] T006 [P] [US1] Render tests: tailnet-only `listen` lines, 308 on port 80, `http2 on;` versus `listen ... ssl http2` by nginx version, proxy headers, `proxy_buffering off`, `proxy_read_timeout 600s`, header comment round trip, never `default_server`, unsafe token refusal in `tests/test_host_tailnet_name.py`
- [ ] T007 [P] [US1] Cloudflare child tests with patched `urlopen`: zone lookup exactly one, `ensure` creates only when absent, identical record is `exists`, other address/proxied/multiple is `conflict` with no write, the token is sent only in the Authorization header and is absent from stdout in `tests/test_tailnet_name_cloudflare_child.py`
- [ ] T008 [P] [US1] Remote program tests run locally against a temp root (paths injected): `csr` creates a 0600 key once and reuses it, `install` refuses a chain whose key or SAN does not match, installs 0644, `inspect` reports held locks, bound address and inventory rows, lock list equals `host_guard.APPLY_TRANSACTION_LOCKS` in `tests/test_tailnet_name_remote_program.py`
- [ ] T009 [P] [US1] Broker tests with a fake `SecretService`: argv holds no token, destination is `CLOUDFLARE_DNS_API_TOKEN`, `lego` argv shape (`run --accept-tos --email --server --dns cloudflare --csr --path`), old outputs cleared before issue, broker refusal maps to `tailnet_name_secret_unavailable`, failure detail is a bounded tail in `tests/test_host_tailnet_name.py`
- [ ] T010 [P] [US1] Verify tests with a local TLS server on 127.0.0.1 using a throwaway self-signed certificate made at test time: fingerprint match passes, other leaf on public probe passes, our leaf on public probe is an exposure, connect refused passes, 502 fails the tailnet probe in `tests/test_host_tailnet_name.py`
- [ ] T011 [US1] Service `add` tests over fake `Deps`: full order, rerun all skipped with no route apply or DNS write, DNS conflict refuses before any remote write, `nginx -t` rejection reports `tailnet_name_nginx_rejected`, busy gate refuses, upstream differs refuses unless replace, exposure removes the block, tailnet probe failure keeps the name, state written after each step, interruption then rerun finishes, token marker absent from every output, state file and fake call argv (SC-004) in `tests/test_host_tailnet_name.py`

### Implementation for User Story 1

- [ ] T012 [P] [US1] Implement the nginx block renderer in `sandbox/hosting/tailnet_names/render.py`
- [ ] T013 [P] [US1] Implement the stdlib Cloudflare child (`ensure`, `inspect`, `delete`) in `sandbox/hosting/tailnet_names/cloudflare_child.py`
- [ ] T014 [P] [US1] Implement the remote program (`inspect`, `csr`, `install`, `remove_cert`) and its controller wrapper over the front door's `_python_program` pattern in `sandbox/hosting/tailnet_names/remote_program.py`
- [ ] T015 [P] [US1] Implement the broker calls (`dns`, `issue`) in `sandbox/hosting/tailnet_names/broker.py`
- [ ] T016 [P] [US1] Implement the TLS probes in `sandbox/hosting/tailnet_names/verify.py`
- [ ] T017 [P] [US1] Implement the controller state file and the per-remote non-blocking lock in `sandbox/hosting/tailnet_names/state.py`
- [ ] T018 [US1] Implement `Deps` and `add` in `sandbox/hosting/tailnet_names/service.py`
- [ ] T019 [US1] CLI tests: `host tailnet-name add` parses, FQDN positional refused on other host actions, `--json` envelope shape and exit codes in `tests/test_host_tailnet_name.py`
- [ ] T020 [US1] Implement the CLI glue in `sandbox/commands/host_tailnet_name.py`, the dispatch in `sandbox/commands/hosting.py` (before the stack branch) and the parser entries in `sandbox/cli.py`

**Checkpoint**: US1 complete and tested with fakes.

## Phase 4: User Story 2 - See what is published (P2)

- [ ] T021 [US2] `list` tests: inventory merged with state, days remaining, `renewal_due`, drift for a recorded name missing on the remote and for a remote name not recorded in `tests/test_host_tailnet_name.py`
- [ ] T022 [US2] Implement `list` in `sandbox/hosting/tailnet_names/service.py` and its text/JSON output in `sandbox/commands/host_tailnet_name.py`

## Phase 5: User Story 3 - Renew certificates (P2)

- [ ] T023 [US3] `renew` tests: not due is skipped, `--force` reissues, `--all` renews only due names, install with reload, `nginx -t` failure restores the previous chain (remote program test in `tests/test_tailnet_name_remote_program.py`), verify after renewal in `tests/test_host_tailnet_name.py`
- [ ] T024 [US3] Implement `renew` in `sandbox/hosting/tailnet_names/service.py` and the reload transaction in `sandbox/hosting/tailnet_names/remote_program.py`

## Phase 6: User Story 4 - Remove a name (P3)

- [ ] T025 [US4] `remove` tests: order block, certificate, DNS; DNS deleted only with the recorded id, address and no proxy (child `delete` test in `tests/test_tailnet_name_cloudflare_child.py`); not owned leaves the record; rerun after a partial remove finishes in `tests/test_host_tailnet_name.py`
- [ ] T026 [US4] Implement `remove` in `sandbox/hosting/tailnet_names/service.py`

## Phase 7: Polish & Cross-Cutting

- [ ] T027 [P] Document the command, configuration block, token handling, `lego` prerequisite, gate, file layout, verification, Host-header allow-list note and the manual-name migration limit in `docs/remote-hosting.md` and `docs/sandbox-config-reference.md`
- [ ] T028 [P] Add the command to `skills/sandbox-cli/SKILL.md` and mirror it byte for byte to `.agents/skills/sandbox-cli/SKILL.md`
- [ ] T029 Run the new suites plus `tests.test_skill_mirrors` and the front door, host exec and CLI suites on `xspeed-dev` (Haiku helper); compare any ownership failure with a baseline run on `latest`
- [ ] T030 Independent review by Luna on `codex_office`; fix or answer every finding; rerun T029
- [ ] T031 Live run on a throwaway name per quickstart.md section 2, only after the orchestrator approves

## Dependencies & Execution Order

- Phase 1 → Phase 2 → US1 → (US2, US3, US4 in any order) → Polish.
- Within US1: T006-T011 before T012-T018; T012-T017 in parallel (separate files); T018 after T012-T017; T019 before T020.
- US2-US4 each extend `service.py`, so they run one after another.

## Parallel Example: User Story 1

```text
T006 render tests | T007 child tests | T008 remote program tests | T009 broker tests | T010 verify tests
T012 render.py    | T013 cloudflare_child.py | T014 remote_program.py | T015 broker.py | T016 verify.py | T017 state.py
```

## Implementation Strategy

MVP is US1 (publish one name end to end). US2-US4 are maintenance of what US1
creates and ship in the same release phase: a name without `renew` breaks in
90 days, so the four stories are one coherent outcome and are not released
apart.
