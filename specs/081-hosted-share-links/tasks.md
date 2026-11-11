# Tasks: Hosted share links

**Input**: `specs/081-hosted-share-links/` (plan, spec, research, data-model, contracts)

**Tests**: Required. The owner's rule is types first, then tests, then code. Tests run
only on xspeed-dev through `sb job-start --remote xspeed-dev` in Haiku helpers, compared
with an `origin/latest` baseline.

## Phase 1: Setup

- [x] T001 Record the `origin/latest` baseline: run the touched test files on xspeed-dev at the merge base, and save the report under `/Users/alim/Sites/git/.scratch/sandbox/share-links/baseline.txt`

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
- [x] T024 Run the full touched-test set on xspeed-dev and compare it with the baseline
- [x] T025 Self-review `git diff origin/latest..HEAD` and trace token recipients, verification semantics and races
- [x] T026 Fresh Sol review on `codex_office` (`gpt-6.1-sol`, high, read-only); fix until it says safe
- [x] T027 Push `feat/hosted-share-links`, check `@{upstream}`, and write the handback

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

## Phase 8: Convergence

- [x] T028 Add the share types (`ShareCreated`, `ShareList`, `ShareRevoked`, `ShareRefused`) to `src/web/src/types.ts` and the import-free helpers in `src/web/src/ui/sharelabels.ts` (refusal help, expiry and uses text, button rule) per FR-032, R12 (missing)
- [x] T029 Write `tests/test_web_share.py` first: gate refusals (no Origin, foreign Origin, loopback Origin from another port, form content type, missing panel header, cross-site `Sec-Fetch-Site`, oversized body), body validation, create/list/revoke mapping with `caller="panel"`, refusal 409 mapping, generic 500, `no-store`, the link absent from `GET /api/hosting` and from stdout/stderr per FR-030, FR-031, SC-007, SC-008 (missing)
- [x] T030 Add `_share_request_allowed` and `_web_share` plus `POST /api/hosting/share` to `sandbox/commands/ui_dash.py` per contracts/panel-http.md, FR-031, FR-034 (missing)
- [x] T031 Add `postShare` to `src/web/src/api.ts` (JSON, `X-Sandbox-Panel: share`, `cache: "no-store"`) per R10 (missing)
- [x] T032 Add `src/web/src/pages/share.ts`: confirm, one-shot link dialog outside the app root with copy fallback, live-links dialog with per-id revoke; wire `sb.shareStack` and `sb.shareLinks` in `src/web/src/main.ts` and `SbApi` per FR-029, FR-030, FR-033, R11 (missing)
- [x] T033 Add "Share this stack" and "Live links" to stack cards in `src/web/src/pages/hosting.ts` per FR-029, R12 (missing)
- [x] T034 Add `src/web/tests/sharelabels.test.ts` and register it in `tests/test_web_poller.py`'s node suite per FR-032 (missing)
- [x] T035 Rebuild `config/sandbox-web.js` and `config/sandbox-web.css` with the repo scripts (typecheck clean) per plan: panel follow-up (missing)
- [x] T036 Update `docs/login-service.md` and the sandbox-cli skill in `skills/` and `.agents/skills/` (byte-identical) for the panel button per Constitution V (missing)
- [x] T037 Run the touched suites on xspeed-dev against an `origin/latest` baseline, and check the dialog against a placeholder fixture per quickstart.md (missing)

## Phase 9: User Story 7 - Short share links (follow-up, 2026-11-11)

**Goal**: New links read `https://<gate>/s/#<token>` with one 128-bit token; old links
keep working until they expire (FR-035 to FR-041, R13 to R16).

**Independent test**: Mint a link and check its form; open it; open an old-form link;
post every bad-token case and compare the 404s byte for byte; check the list id is the
hash prefix and not the token.

Tests first:

- [x] T038 [P] [US7] Gate runtime tests in `tests/test_share_gate_runtime.py`: the new form round-trips (`GET /s/` opener for any state, `POST /s/` with the token gives 303 and the cookie); old-form `/s/l-<32 hex>` links still open and revoke by old id; wrong, unknown, expired, revoked, spent, malformed and old-form tokens posted to `/s/` give byte-identical 404s with identical headers; a short id that is not the hash prefix and a duplicate short id are refused on mint; the opener script accepts 22- and 43-character fragments and its CSP hash matches; the token is absent from the gate log; a stack page with a 12-hex `kid` mints a valid WordPress link
- [x] T039 [P] [US7] Controller tests in `tests/test_host_share.py`: `share_url(host, token)` is `https://<host>/s/#<token>` with a 22-character token; `link_id` is `l-` + `sha256(token)[:12]` and differs from every token substring; the CLI/MCP `--json` result carries the new form; the canary token is absent from audit lines, stderr, progress output and feedback; revoke accepts `l-<12 hex>`, `l-<32 hex>`, `s-<32 hex>` and `all`; `render_key_plugin` accepts a 12-hex `kid`; a share declaration with gate hostname `s.<zone>` validates
- [x] T040 [P] [US7] 075 site-link tests in `tests/test_host_login_service.py` and `tests/test_login_service_runtime.py`: `host login-url --service <site entry>` returns the new form and opens through `/s/`
- [x] T041 [P] [US7] Panel tests in `tests/test_web_share.py` and `src/web/tests/sharelabels.test.ts`: the panel create response carries the new form once, `GET /api/hosting` never holds it, and revoke accepts `l-<12 hex>` and old-form ids

Implementation:

- [x] T042 [US7] `sandbox/hosting/share/__init__.py`: 128-bit `new_token`, `short_link_id(token)`, `share_url(hostname, token)`, link id and revoke rules for both forms; drop old-form minting; `sandbox/hosting/share/stack_page.py`: `kid` of 12 or 32 hex
- [x] T043 [US7] `sandbox/hosting/login_service/runtime.py`: `GET|HEAD /s/` constant opener, `POST /s/` hash scan with constant-time compare and one 404, short-id checks on load and mint, both id forms for sessions, stacks and revoke, opener script for 22 or 43 characters; the old `/s/l-<32 hex>` path marked `# LEGACY(081 short links)`
- [x] T044 [US7] Callers: `sandbox/commands/host_share.py`, `sandbox/commands/hosting.py` (075 site link), `sandbox/commands/ui_dash.py` (revoke id rule), `src/web/src/ui/sharelabels.ts`; rebuild `config/sandbox-web.js` with `scripts/build-web-js.sh`
- [x] T045 [US7] Docs: `docs/login-service.md` (new form, short gate hostname `s.<zone>`, the hostname is not a secret, old links until expiry), CLAUDE.md gotcha 36, the sandbox-cli skill in `skills/` and `.agents/skills/` (byte-identical)

Close-out:

- [ ] T046 [US7] Run the touched suites on xspeed-dev (`sb job-start --remote xspeed-dev --cancel-on-stall --profile unit`) through Haiku helpers and compare with an `origin/latest` baseline
- [ ] T047 [US7] Sol review on `codex_office` (`gpt-6.1-sol`, high, read-only) of `git diff origin/latest..HEAD`; fix every finding with a test until it says safe; record that human sign-off is needed before release
- [ ] T048 [US7] Push `feat/short-share-links` by name and write the handback to `/Users/alim/Sites/git/.scratch/sandbox/handbacks/feat-short-share-links.md`

Follow-up (not in this change):

- [ ] T049 Remove the old-form link path (`/s/l-<32 hex>`, 43-character tokens, 32-hex link ids and `kid`s) from `sandbox/hosting/login_service/runtime.py`, `sandbox/hosting/share/__init__.py`, `sandbox/hosting/share/stack_page.py`, `sandbox/commands/ui_dash.py` and `src/web/src/ui/sharelabels.ts`, with its tests, no earlier than 30 days after the short form reached every gate (search `LEGACY(081 short links)`)

**Dependencies (US7)**: T038-T041 come first and fail before T042-T044. T042 blocks
T043 and T044. T045 can run with T043. T046 needs T042-T045; T047 needs T046; T048 last.

**Parallel (US7)**: T038, T039, T040 and T041 are in different files.
