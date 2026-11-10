# Tasks: Login service site gate

**Input**: Design documents from `specs/075-login-site-gate/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Requested. The owner asked for types first, then tests, then implementation, plus canary tests for link leakage.

**Organization**: Tasks are grouped by user story. US1, US2 and US4 are all P1 and share one runtime, so US2 (no access without a session) and US4 (no leak) are proven against the code US1 adds.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [x] T001 Confirm the branch base and the spec directory: `git log -1` on `feat/login-site-gate`, `specs/075-login-site-gate/` present, `.specify/feature.json` points at it

## Phase 2: Foundational (types and constants)

- [x] T002 Add `site_gate` to `ENTRY_MODES` and add the constants `SITE_MAX_USES=20`, `SITE_MAX_LINKS=32`, `SITE_ATTEMPTS_PER_MINUTE=30`, `SITE_LINK_SCHEMA`, `SITE_SESSION_SCHEMA` and `SITE_DEFAULT_PATH_PREFIX`; allow `upstream` in `_ENTRY_FIELDS` in sandbox/hosting/login_service/__init__.py
- [x] T003 Add the matching runtime constants and the storage-path helpers (`sites/<entry>/{links,used,sessions}`) in sandbox/hosting/login_service/runtime.py

## Phase 3: User Story 1 - Share a private mockup (P1) MVP

**Goal**: A minted link opens a session, and the session browses the upstream, websocket included.

**Independent Test**: The runtime suite mints a record, POSTs the token, gets a 303 with the cookie, and then GETs a proxied page and a websocket echo through the fake upstream.

### Tests for User Story 1

- [x] T004 [P] [US1] Parse tests in tests/test_host_login_service.py:
  - accepted: a valid `site_gate` entry; capture optional when every entry is `site_gate`; the default `path` and `landing_path`
  - refused: an upstream that is the primary, `sandbox-login`, an unknown service, or carries a path, query or credentials; the primary hostname as gate; a shared gate hostname; `requires_email: true`; `basic_auth` combined with a `site_gate` entry
- [x] T005 [P] [US1] Render and preflight tests in tests/test_host_login_service.py: no `depends_on` capture and no credential when capture is absent; `runtime_config` carries the upstream; preflight refuses `upstream_port_published` and `upstream_not_in_project`; the existing preflight JSON stays accepted
- [x] T006 [P] [US1] Runtime tests in tests/test_login_service_runtime.py:
  - the link page needs a live link; POST with a valid token gives a 303 to `landing_path` with `__Host-sandbox_site_<entry>`, `Path=/`, `Secure`, `HttpOnly`, `SameSite=Lax`, `Max-Age` and `Referrer-Policy: no-referrer`
  - a session GET reaches the upstream with `Host` set to the gate hostname and without the gate cookie
  - a websocket echo works with a session
  - `--uses 3` gives three sessions and then 404
  - a concurrent single-use race gives exactly one 303

### Implementation for User Story 1

- [x] T007 [US1] Parse `site_gate` entries (`_parse_site_gate`), make capture optional, and enforce one entry per gate hostname in sandbox/hosting/login_service/__init__.py
- [x] T008 [US1] Make `runtime_config`, `render_service` and `status_view` handle a missing capture, and add the upstream to the config in sandbox/hosting/login_service/__init__.py
- [x] T009 [US1] Extend `preflight_command`/`check_preflight` with upstream checks, and add `site_command(prefix, verb, entry, *args)` in sandbox/hosting/login_service/__init__.py
- [x] T010 [US1] Refuse `basic_auth` with a `site_gate` entry in sandbox/core/_hosting.py, next to the login declaration parse
- [x] T011 [US1] Add `SiteStore` (mint, find, claim, session create/read, list, revoke, prune) in sandbox/hosting/login_service/runtime.py
- [x] T012 [US1] Add the site link page (auto-submit script, CSP hash), link POST handling, session-gated proxy and tunnel with an upstream target, and make `main()` skip the capture wait when there is no capture in sandbox/hosting/login_service/runtime.py
- [x] T013 [US1] Add `site mint|list|revoke` handling in `main()` (argv after the source), reading the record from stdin for mint, in sandbox/hosting/login_service/runtime.py
- [x] T014 [US1] Pass the site_gate upstreams to the apply preflight in `_preflight_login_service` in sandbox/commands/hosting.py

**Checkpoint**: The runtime suite passes for US1.

## Phase 4: User Story 2 - Nothing visible without a session (P1)

**Goal**: Every unauthenticated or malformed request answers 404 and reaches nothing.

**Independent Test**: The runtime suite counts requests that reach the fake upstream; it stays 0 across every variant.

- [x] T015 [P] [US2] Runtime tests in tests/test_login_service_runtime.py:
  - with no session: every method, a websocket upgrade, and a forged or invalid cookie each get 404
  - expired, replayed, revoked, unknown and malformed tokens give identical 404 bytes
  - absolute-form, `//`, a wrong Host and duplicate Host get 404 even with a session
  - after `enabled_until`, everything gets 404
  - 429 after 30 attempts
  - the upstream hit count stays 0
- [x] T016 [US2] Add the exact-one-Host check, the uniform 404 on every link failure, and the per-entry rate limiter in sandbox/hosting/login_service/runtime.py

## Phase 5: User Story 4 - The link never leaks (P1)

**Goal**: The link value shows on the operator's terminal once and nowhere else.

**Independent Test**: Canary tests patch the token generator and search every captured channel.

- [x] T017 [P] [US4] Command tests in tests/test_host_login_service.py:
  - mint with `--json` and in human mode, using a fake terminal
  - the canary token is absent from stdout, stderr, saved state, every remote command and remote stdin, and present on the terminal exactly once
  - with no terminal: `terminal_required` and no remote call
  - `--uses` 0 and 21 refused; `site_gate_only`; `site_options_conflict`
- [x] T018 [P] [US4] Runtime canary in tests/test_login_service_runtime.py: the gate log lines and the `site list` output never hold the token, the session secret or either hash
- [x] T019 [US4] Implement `_cmd_host_site_link` (terminal open before mint, writer lock, remote `site mint` over stdin, JSON without the URL) and the routing from `_cmd_host_login_link` in sandbox/commands/hosting.py
- [x] T020 [US4] Add `--uses`, `--list` and `--revoke` to the `host` subparser's login-url arguments in sandbox/cli.py, and let `--list` skip `--confirm` in the `login-url` dispatch in sandbox/commands/hosting.py

## Phase 6: User Story 3 - List and revoke (P2)

**Goal**: The operator sees live links and sessions and cuts any of them off.

**Independent Test**: In the runtime suite, a revoked session or link answers 404 on the next request, and a tunnel closes after the revoke check.

- [x] T021 [P] [US3] Runtime tests in tests/test_login_service_runtime.py: `site list` shape; revoking a session, a link (which also kills its sessions) and `all`; a revoke racing a consume leaves no session; an unknown id refused; an open websocket closes after the revoke check
- [x] T022 [P] [US3] Command tests in tests/test_host_login_service.py: `--list` and `--revoke` build the right remote command and print the JSON shape; `site_link_unknown` and `site_entry_unknown` pass through
- [x] T023 [US3] Add the tunnel's `still_allowed` re-check, with select timeouts bounded at 30 s, in sandbox/hosting/login_service/runtime.py
- [x] T024 [US3] Implement list and revoke in `_cmd_host_site_link` in sandbox/commands/hosting.py

## Phase 7: Polish

- [x] T025 [P] Document `site_gate` in docs/login-service.md: declaration, mockup example with placeholder hosts, edge forwarding, contract, CLI, limits
- [x] T026 [P] Update skills/sandbox-cli/SKILL.md and .agents/skills/sandbox-cli/SKILL.md with mint, list and revoke
- [ ] T027 Run both suites, plus `tests.test_hosting`, on the remote through a Haiku helper; compare any failure with the baseline controller checkout
- [ ] T028 Luna review of the head (link leakage, replay, cookie scope, open proxy and Host tricks, websocket without a session); fix or answer every finding, then re-review
- [ ] T029 Commit, `git push -u origin feat/login-site-gate`, and verify the upstream is `origin/feat/login-site-gate`

## Dependencies

- Phase 2 blocks everything.
- US1 (T007-T014) blocks US2, US4 and US3, because they test and extend the same handlers.
- US2 and US4 are independent of each other once US1 is in.
- US3 depends on US1's `SiteStore` and US4's command routing (T019).
- Polish comes last; T028 follows T027.

## Parallel Opportunities

- The test tasks marked [P] within a story touch different test classes and can be written together.
- T025 and T026 (docs) run in parallel with T027.

## Implementation Strategy

MVP is US1 plus US2 plus US4: a link that works, a gate that shows nothing otherwise, and a
link that never leaks. US3 (list and revoke) ships in the same release, because the
owner asked for it and a forwarded link otherwise has no kill switch before expiry.
