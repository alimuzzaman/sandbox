---
description: "Task list for In-Instance WP Abilities + MCP Adapter Layer"
---

# Tasks: In-Instance WordPress Abilities + MCP Adapter Layer

**Input**: Design documents from `specs/003-wp-abilities-adapter/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: No unit-test tasks requested; per constitution IV each user story ends with
a **live-stack verification** task (the only proof of done).

## Path Conventions

Host: single-entry `sb` + `sandbox/` package + `mcp/wp-server/`. In-WP surface: a
provisioned mu-plugin under each instance's `wp-content/mu-plugins/`.

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 Create the mu-plugin payload skeleton: `00-sandbox-abilities.php` loader authored at `sandbox/assets/abilities/00-sandbox-abilities.php` (the source the writer copies from). DONE — includes the WP-version gate + enable flag + permission callback. (mcp-adapter vendoring into `sandbox-abilities/` still pending — T002.)
- [x] T002 Vendor `wordpress/mcp-adapter` (v0.5.0) + `wordpress/php-mcp-schema` into `sandbox/assets/abilities/sandbox-abilities/vendor/` (pruned of tests/docs, ~2.3M; isolated from the focused plugin + repo `vendor/`). DONE — the provisioning writer copies the whole payload into the instance.
- [ ] T003 Add the enable-flag plumbing: option `sandbox_abilities_enabled` (default on) + mirror key `instances.<name>.abilities_enabled` read/written via the existing `sandbox.local.yml` helpers.

## Phase 2: Foundational (blocking prerequisites)

- [x] T004 Implement the idempotent mu-plugin writer `_write_abilities_muplugin` in `sandbox/core/_provision.py` (copies the asset into `runtime/wp-<instance>/wp-content/mu-plugins/`); hooked into `cmd_up` ([lifecycle.py](../../sandbox/commands/lifecycle.py)) + the apply/recreate path ([_instances.py](../../sandbox/core/_instances.py)), not herd-gated. **DONE + live-verified**: removed the hand-deployed copy, ran `./sb up`, the writer recreated it and `wp_has_ability('sandbox/execute-php')` returns true.
- [x] T005 In `00-sandbox-abilities.php`: bootstrap `McpAdapter::instance()` + register the `sandbox` MCP server (route `/wp-json/sandbox/mcp`, HttpTransport) on `mcp_adapter_init`, exposing the 5 abilities; gated on `sandbox_abilities_enabled` + Abilities-API presence + the vendored autoload. **DONE + live-verified**: `/sandbox/mcp` route registered; unauth POST `tools/list` → `401` (auth-gated); bogus route → `404`.
- [ ] T006 Implement the shared `permission_callback` (logged-in user AND `manage_options`) and the `resolve_path` ABSPATH jail (rejects symlink final-path escape) in the payload.

## Phase 3: User Story 1 — Run code in the live runtime (P1)

**Goal**: `execute-php` returns a structured result from the live WP runtime.
**Independent test**: call the ability for `return get_option('siteurl');` and get a structured result.

- [x] T007 [US1] Implement the `sandbox/execute-php` ability (eval + output-buffer + error-handler capture + 30s `set_time_limit` cap + `\Throwable` catch + JSON-safe return), annotated destructive. **DONE + live-verified on WP 6.9.4** (templately-rebuild2): registered ability returns `get_option('siteurl')`, captures a User Notice, and reports a thrown RuntimeException as `success:false`. Live verification caught two real WP-6.9 API contracts: (a) the ability **category** must be registered first, and (b) categories register on a **separate earlier hook** `wp_abilities_api_categories_init` (not inside `wp_abilities_api_init`). Both fixed in the mu-plugin.
- [ ] T008 [P] [US1] Add the `wp_eval_live` proxy MCP tool in `mcp/wp-server/tools/abilities.py` (resolves instance, POSTs to the endpoint with app-password auth).
- [ ] T009 [US1] Live verification (quickstart §2): execute-php round-trip via proxy + direct; notice captured in `errors[]`; thrown error returns `success:false` and the site stays up; time-limit cap holds.

## Phase 4: User Story 5 — Off-switch & per-call authorization (P1)

**Goal**: the layer toggles per instance and every ability enforces auth + capability.
**Independent test**: disable → endpoint empty/403; enable → under-privileged caller refused.

- [x] T010 [US5] Implement `./sb abilities on|off|status` in `sandbox/commands/abilities.py` (instance-resolved; sets the `sandbox_abilities_enabled` option; `status` prints endpoint + the "dev/staging only" banner), self-registered + added to `INSTANCE_SCOPED` + imported in cli.py. **DONE + live-verified**: `off` → `wp_has_ability` NOT registered; `on` → registered; `status` prints state/endpoint/banner. (sandbox.local.yml mirror deferred — the WP option is authoritative for the mu-plugin.)
- [ ] T011 [US5] Live verification (quickstart §6): disabled → no abilities exposed + calls 403; enabled → call without valid app password / without `manage_options` is refused.

## Phase 5: User Story 2 — Any MCP client connects directly (P1)

**Goal**: external clients connect to the instance endpoint; discovery includes Sandbox guidance.
**Independent test**: run connect helper, paste config into a fresh client, list + call an ability.

- [ ] T012 [US2] Override `mcp-adapter/discover-abilities` in the payload to append Sandbox environment instructions (focused plugin, instance URL, snapshot reminder) per contracts/abilities.md.
- [ ] T013 [US2] Implement `./sb connect [--instance] [--client …]` in `sandbox/commands/connect.py` (emits endpoint URL + app password [interactive display only] + per-client config; herd → `.test` URL), self-registered.
- [ ] T014 [US2] Live verification (quickstart §3): connect a fresh external MCP client; it lists abilities and calls execute-php; discovery shows the instructions block.

## Phase 6: User Story 3 — Self-sufficient file access (P2)

**Goal**: read/write/edit/list files on the endpoint, jailed; new `.php` confined to sandbox-code/.
**Independent test**: write+read a file; path escape (and symlink) rejected.

- [x] T015 [P] [US3] Implement `sandbox/read-file`, `write-file`, `edit-file`, `list-directory` abilities (ABSPATH-jailed; new `.php` restricted to `wp-content/sandbox-code/`). **DONE + live-verified**: all 4 register; write/read/edit round-trip (hello→world); path escape → `path_outside_base`; `.php` outside sandbox-code → `php_sandbox_required`; `.php` in sandbox-code → created.
- [ ] T016 [P] [US3] Add file-ability proxy MCP tools (`wp_file_read/write/list`) in `mcp/wp-server/tools/abilities.py`.
- [ ] T017 [US3] Live verification (quickstart §4): write/read round-trip **via both the direct endpoint and the `wp_file_*` proxy** (asserts the FR-010 proxy path for files); out-of-ABSPATH + symlink escape rejected; new `.php` outside sandbox-code/ rejected.

## Phase 7: User Story 4 — Persistent AI PHP with crash recovery (P2)

**Goal**: sandbox-code/ loads behind crash recovery; a fatal drops to safe mode.
**Independent test**: write a fatal sandbox file; site stays up in safe mode with a naming notice.

- [x] T018 [US4] Implement the crash-recovery loader (require `wp-content/sandbox-code/*.php`; safe mode skips all on fatal; admin notice names the file; `?sb_safe_mode=1` override). **DONE — hardened beyond the spec**: a `.loading`→`.crashed` marker handshake (write-before-require, clear-after) is the reliable signal because **WP registers its own fatal handler before mu-plugins and pre-empts our shutdown callback** (caught by live verification). The shutdown handler is kept as a fast path.
- [x] T019 [US4] Live verification (quickstart §5): planted a fatal sandbox file → req1 fatals + leaves `.loading`; req2 auto-promotes to `.crashed` and recovers (`alive+abilities`); `.crashed` correctly names `boom.php`; cleanup → normal load.

## Phase 8: Polish & Cross-Cutting

- [ ] T020 [P] Docs-with-code: add the abilities-layer + AGPL-boundary gotcha to `CLAUDE.md`, add the proxy tools to the MCP-surface table + MCP server `instructions`, and document `./sb abilities`/`connect` in `docs/sandbox-config-reference.md`.
- [ ] T021 Idempotency check: re-run `up`/`apply` and confirm the mu-plugin payload is re-written without duplication/corruption (constitution V).
- [ ] T022 Driver parity (quickstart §7): on a herd instance via its `.test` endpoint, repeat execute-php + crash-recovery **and** connect + gating + a file-ability round-trip (confirms app-password auth over the herd SSL endpoint), per analysis C3.
- [ ] T023 [P] Verify the WP-version gate (FR-011): on a sub-minimum WP instance confirm the loader no-ops without fatal and logs the notice (analysis C2).

## Dependencies & Order

- Setup (T001-T003) → Foundational (T004-T006) → user stories.
- User-story order by priority: US1 (T007-T009) → US5 (T010-T011) → US2 (T012-T014) → US3 (T015-T017) → US4 (T018-T019) → Polish (T020-T022).
- US5/US2 depend on Foundational T005/T006; US2's connect (T013) reuses registry/app-pw resolution.
- `[P]` tasks touch distinct files and may run in parallel within their phase.

## MVP scope

US1 (execute-php + proxy + verification, T001-T009 minus file/connect parts) is the
minimal viable increment: an agent can run code in the live runtime.
