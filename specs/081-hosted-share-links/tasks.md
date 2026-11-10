# Tasks: Hosted share links

**Input**: `specs/081-hosted-share-links/` (plan, spec, research, data-model, contracts)

**Tests**: Required. The owner's rule is types first, then tests, then code. Tests run
only on xspeed-dev through `sb job-start --remote xspeed-dev` in Haiku helpers, compared
with an `origin/latest` baseline.

## Phase 1: Setup

- [ ] T001 Record the `origin/latest` baseline: run the touched test files on xspeed-dev at the merge base, and save the report under `/Users/alim/Sites/git/.scratch/sandbox/share-links/baseline.txt`

## Phase 2: Foundational (types)

- [x] T002 Define share types and constants (`ShareDeclaration`, `LinkRequest`, scope rules, TTL parsing, id and token generation, `ShareRefusal`, audit record builder) in `sandbox/hosting/share/__init__.py`
- [x] T003 Define the gate port rule (`gate_loopback_port(port) = port + 3000`) and the gate-hostname set for a declaration in `sandbox/hosting/login_service/__init__.py`

## Phase 3: US1 + US3, agent-made links, random ids, private by default (P1)

**Goal**: `sb host share create` on an opted-in target returns a link; a private target answers 404 except through the gate.

**Independent test**: `tests/test_host_share.py` and `tests/test_share_gate_runtime.py` pass.

### Tests

- [x] T004 [P] [US1] Manifest tests: the `share` short form, the merge with `autologin.login_service`, reserved names, refusals, and `private` preflight; in `tests/test_host_share.py`
- [x] T005 [P] [US1] CLI tests: target selection, `agent_mint` on and off (terminal path, MCP refusal), refusal inside a durable job, JSON shape, canary that the token appears only in the result (not in audit, state, remote argv or stdin records), and audit fields; in `tests/test_host_share.py`
- [x] T006 [P] [US3] Renderer tests: the override publishes the gate port, and Caddy and nginx route gate hostnames to it and 404 the rest under `private`; in `tests/test_host_share.py`

### Implementation

- [x] T007 [US1] Parse `share` and `indexing` in `validate_loaded`, and merge `share` into the login-service declaration, in `sandbox/core/_hosting.py` and `sandbox/hosting/login_service/__init__.py`
- [x] T008 [US3] Publish the gate port in `compose_override`, and extend the preflight to allow exactly that mapping and to check the primary service under `private`, in `sandbox/core/_hosting.py` and `sandbox/hosting/login_service/__init__.py`
- [x] T009 [US3] Caddy gate routing and the `private` 404 in `caddyfile` in `sandbox/core/_hosting.py`
- [x] T010 [US3] nginx gate routing and the `private` 404 in `sandbox/hosting/front_door/nginx.py`
- [x] T011 [US1] `sb host share create|list|revoke` with audit, in the new `sandbox/commands/host_share.py`; dispatch in `sandbox/commands/hosting.py`; parser in `sandbox/cli.py`
- [x] T012 [P] [US1] MCP `share` group in `mcp/wp-server/tools/share.py` and `mcp/wp-server/tools/manifest.py`

## Phase 4: US2, the Agent Notes link model (P1)

**Independent test**: `tests/test_share_gate_runtime.py` passes over real sockets.

- [x] T013 [US2] Runtime tests: `/s/<id>` opener only for a live id, POST swap and 303 to scope, cookie attributes, several sessions per cookie, scope and traversal refusals, use limits and the unlimited case, revoke ending sessions and tunnels, rate limit, uniform 404, link-file recheck; in `tests/test_share_gate_runtime.py`
- [x] T014 [US2] Share store v2 (links, sessions, scope) and `/s/<id>` handling in `sandbox/hosting/login_service/runtime.py`; update 075 site-link minting in `sandbox/commands/hosting.py` to the v2 record and URL
- [x] T015 [US2] Update the 075 site-gate tests to the v2 URL and record in `tests/test_login_service_runtime.py` and `tests/test_host_login_service.py`

## Phase 5: US4, nothing hosted is indexed (P1)

- [x] T016 [US4] Route-coverage test for every renderer branch (basic auth with and without bypasses, robots, redirects, private, gate, wildcard, ACME-only port 80) on both front doors, plus the gate's own responses; in `tests/test_hosted_noindex.py`
- [x] T017 [US4] Deferred header in `caddyfile`, and `add_header ... always` plus `proxy_hide_header` in nginx, with the `robots` default and `robots: allow` refusal, in `sandbox/core/_hosting.py` and `sandbox/hosting/front_door/nginx.py`
- [x] T018 [US4] Gate header and meta robots on every gate response and page, and gate `/robots.txt`, in `sandbox/hosting/login_service/runtime.py`

## Phase 6: US5, the stack page (P2)

- [x] T019 [US5] Tests: stack link minting (registry lookup, refusal without `share`), the WordPress key mu-plugin render, the stack page view (fresh links per view, health, scope confined to the page), and revoke removing keys; in `tests/test_host_share.py` and `tests/test_share_gate_runtime.py`
- [x] T020 [US5] Stack record and WordPress key mu-plugin in `sandbox/hosting/share/stack_page.py`
- [x] T021 [US5] Stack page serving, health probes and per-view minting in `sandbox/hosting/login_service/runtime.py`
- [x] T022 [US5] `--stack` in `sandbox/commands/host_share.py`, and the panel hook `create_share`

## Phase 7: Polish

- [x] T023 Docs: `docs/login-service.md` (share section, owner view through the tailnet, the stack page, the panel button), the `indexing` field in `docs/remote-hosting.md`, a CLAUDE.md gotcha, and the sandbox-cli skill
- [ ] T024 Run the full touched-test set on xspeed-dev and compare it with the baseline
- [x] T025 Self-review `git diff origin/latest..HEAD` and trace token recipients, verification semantics and races
- [ ] T026 Fresh Sol review on `codex_office` (`gpt-6.1-sol`, high, read-only); fix until it says safe
- [ ] T027 Push `feat/hosted-share-links`, check `@{upstream}`, and write the handback

## Dependencies

T002-T003 block everything. US1/US3 (T004-T012) and US2 (T013-T015) share the runtime and
the store: do T014 before T011's end-to-end canary. US4 is independent of US1-US3 except
for the shared renderers, so do T017 after T009-T010. US5 needs US1-US3.

## Parallel opportunities

T004/T005/T006 (test files), T012 (MCP), and T016 (noindex tests) can be written in
parallel with the implementation in other files.

## MVP

Phases 2-5: agent links, private gate routing, the link model and no indexing. The stack
page follows.
