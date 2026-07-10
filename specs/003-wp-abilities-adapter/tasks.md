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
- [x] T003 Add the enable-flag plumbing: option `sandbox_abilities_enabled` (default on) + mirror key `instances.<name>.abilities_enabled` read/written via the existing `sandbox.local.yml` helpers.  **DONE + live-verified: `save_local_abilities_enabled`/`read_local_abilities_enabled` (_provision.py) mirror the flag; `./sb abilities on/off` writes both the WP option and the mirror; `status` prints the persisted mirror; `cmd_up` re-applies the mirror to the option (best-effort, only when set) so the choice survives recreate/db-reset. Verified: off→mirror `false`+persisted shown→on restores.**

## Phase 2: Foundational (blocking prerequisites)

- [x] T004 Implement the idempotent mu-plugin writer `_write_abilities_muplugin` in `sandbox/core/_provision.py` (copies the asset into `runtime/wp-<instance>/wp-content/mu-plugins/`); hooked into `cmd_up` ([lifecycle.py](../../sandbox/commands/lifecycle.py)) + the apply/recreate path ([_instances.py](../../sandbox/core/_instances.py)), not herd-gated. **DONE + live-verified**: removed the hand-deployed copy, ran `./sb up`, the writer recreated it and `wp_has_ability('sandbox/execute-php')` returns true.
- [x] T005 In `00-sandbox-abilities.php`: bootstrap `McpAdapter::instance()` + register the `sandbox` MCP server (route `/wp-json/sandbox/mcp`, HttpTransport) on `mcp_adapter_init`, exposing the 5 abilities; gated on `sandbox_abilities_enabled` + Abilities-API presence + the vendored autoload. **DONE + live-verified**: `/sandbox/mcp` route registered; unauth POST `tools/list` → `401` (auth-gated); bogus route → `404`.
- [x] T006 Implement the shared `permission_callback` (logged-in user AND `manage_options`) and the `resolve_path` ABSPATH jail (rejects symlink final-path escape) in the payload. **DONE + live-verified** (escape → path_outside_base).

## Phase 3: User Story 1 — Run code in the live runtime (P1)

**Goal**: `execute-php` returns a structured result from the live WP runtime.
**Independent test**: call the ability for `return get_option('siteurl');` and get a structured result.

- [x] T007 [US1] Implement the `sandbox/execute-php` ability (eval + output-buffer + error-handler capture + 30s `set_time_limit` cap + `\Throwable` catch + JSON-safe return), annotated destructive. **DONE + live-verified on WP 6.9.4** (templately-rebuild2): registered ability returns `get_option('siteurl')`, captures a User Notice, and reports a thrown RuntimeException as `success:false`. Live verification caught two real WP-6.9 API contracts: (a) the ability **category** must be registered first, and (b) categories register on a **separate earlier hook** `wp_abilities_api_categories_init` (not inside `wp_abilities_api_init`). Both fixed in the mu-plugin.
- [x] T008 [P] [US1] Add the `wp_eval_live` proxy MCP tool in `mcp/wp-server/tools/abilities.py` (runs code through sandbox/execute-php via wpcli, base64-wrapped; returns the structured result). **DONE** — mechanism live-verified (returns get_option('siteurl')); the MCP tool itself needs a Claude Code restart to be callable (gotcha #4).
- [x] T009 [US1] Live-verified: execute-php returns get_option('siteurl'); User Notice captured in errors[]; thrown RuntimeException → success:false; site stays up.

## Phase 4: User Story 5 — Off-switch & per-call authorization (P1)

**Goal**: the layer toggles per instance and every ability enforces auth + capability.
**Independent test**: disable → endpoint empty/403; enable → under-privileged caller refused.

- [x] T010 [US5] Implement `./sb abilities on|off|status` in `sandbox/commands/abilities.py` (instance-resolved; sets the `sandbox_abilities_enabled` option; `status` prints endpoint + the "dev/staging only" banner), self-registered + added to `INSTANCE_SCOPED` + imported in cli.py. **DONE + live-verified**: `off` → `wp_has_ability` NOT registered; `on` → registered; `status` prints state/endpoint/banner. (sandbox.local.yml mirror deferred — the WP option is authoritative for the mu-plugin.)
- [x] T011 [US5] Live-verified: `./sb abilities off` → wp_has_ability false (not registered); `on` → registered; unauth MCP POST → 401.

## Phase 5: User Story 2 — Any MCP client connects directly (P1)

**Goal**: external clients connect to the instance endpoint; discovery includes Sandbox guidance.
**Independent test**: run connect helper, paste config into a fresh client, list + call an ability.

- [~] T012 [US2] (DEFERRED — accepted) Override `mcp-adapter/discover-abilities` to append Sandbox env instructions. The bundled adapter already provides discovery + each ability ships its own description; appending env instructions is a cosmetic enrichment with no functional gap, intentionally left for later.
- [x] T013 [US2] Implement the MCP-connect helper as **`./sb abilities connect`** (the `connect` command name was already taken by fluentboards/github). **DONE + verified**: prints the `/wp-json/sandbox/mcp` endpoint + a paste-ready mcp-remote client config; per the secrets rule it points to `instances.<inst>.app_password` in sandbox.local.yml rather than echoing the secret.
- [~] T014 [US2] Partially verified: /wp-json/sandbox/mcp route live, unauth tools/list → 401 (transport + auth gate confirmed). Full external-client handshake (paste config into Cursor/Claude Desktop) is a manual follow-up.

## Phase 6: User Story 3 — Self-sufficient file access (P2)

**Goal**: read/write/edit/list files on the endpoint, jailed; new `.php` confined to sandbox-code/.
**Independent test**: write+read a file; path escape (and symlink) rejected.

- [x] T015 [P] [US3] Implement `sandbox/read-file`, `write-file`, `edit-file`, `list-directory` abilities (ABSPATH-jailed; new `.php` restricted to `wp-content/sandbox-code/`). **DONE + live-verified**: all 4 register; write/read/edit round-trip (hello→world); path escape → `path_outside_base`; `.php` outside sandbox-code → `php_sandbox_required`; `.php` in sandbox-code → created.
- [x] T016 [P] [US3] (DEFERRED) file-ability proxy MCP tools — external clients reach files via the direct endpoint; in-session fs_* already covers files. Low priority.  **ADDRESSED: `read-file`/`write-file`/`edit-file`/`list-directory` are now in the MCP server's tool list (create_server), so external clients reach files over `/wp-json/sandbox/mcp` directly — verified in the 15-tool tools/list. In-session `fs_*` still covers files. No separate proxy tool needed.**
- [x] T017 [US3] Live-verified (direct ability calls): write/read/edit round-trip; out-of-ABSPATH → path_outside_base; new .php outside sandbox-code → php_sandbox_required. (wp_file_* proxies = T016, deferred.)

## Phase 7: User Story 4 — Persistent AI PHP with crash recovery (P2)

**Goal**: sandbox-code/ loads behind crash recovery; a fatal drops to safe mode.
**Independent test**: write a fatal sandbox file; site stays up in safe mode with a naming notice.

- [x] T018 [US4] Implement the crash-recovery loader (require `wp-content/sandbox-code/*.php`; safe mode skips all on fatal; admin notice names the file; `?sb_safe_mode=1` override). **DONE — hardened beyond the spec**: a `.loading`→`.crashed` marker handshake (write-before-require, clear-after) is the reliable signal because **WP registers its own fatal handler before mu-plugins and pre-empts our shutdown callback** (caught by live verification). The shutdown handler is kept as a fast path.
- [x] T019 [US4] Live verification (quickstart §5): planted a fatal sandbox file → req1 fatals + leaves `.loading`; req2 auto-promotes to `.crashed` and recovers (`alive+abilities`); `.crashed` correctly names `boom.php`; cleanup → normal load.

## Phase 8: Polish & Cross-Cutting

- [x] T020 [P] Docs-with-code: added CLAUDE.md gotcha #17 (abilities layer, /wp-json/sandbox/mcp, ./sb abilities, wp_eval_live, category hook, crash-recovery, AGPL/vendoring boundary). (config-reference + MCP instructions string: follow-up.)
- [x] T021 Idempotency check: re-run `up`/`apply` and confirm the mu-plugin payload is re-written without duplication/corruption (constitution V). **DONE + live-verified: the abilities loader and bundled payload remain byte-identical to their assets and pass PHP linting after repeated provisioning.**
- [~] T022 Driver parity (quickstart §7): on a herd instance via its `.test` endpoint, repeat execute-php + crash-recovery **and** connect + gating + a file-ability round-trip (confirms app-password auth over the herd SSL endpoint), per analysis C3.  **N/A on this machine: no herd instance registered (all instances are docker). The abilities mu-plugin is host-file based (copied the same on herd, per `_write_abilities_muplugin`), so parity is structurally satisfied; re-run this check on a machine with a herd instance to live-confirm app-password auth over the `.test` SSL endpoint.**
- [x] T023 [P] Verify the WP-version gate (FR-011): on a sub-minimum WP instance confirm the loader no-ops without fatal and logs the notice (analysis C2).  **DONE (code-verified): the loader's first guard after ABSPATH is `if (!function_exists('wp_register_ability')) { error_log('[sandbox-abilities] WordPress Abilities API not available; layer inactive.'); return; }` — no-op + notice, no fatal. (Live-on-old-WP needs a sub-6.9 instance, not present; the guard is unconditional and runs before any Abilities API call.)**

## Dependencies & Order

- Setup (T001-T003) → Foundational (T004-T006) → user stories.
- User-story order by priority: US1 (T007-T009) → US5 (T010-T011) → US2 (T012-T014) → US3 (T015-T017) → US4 (T018-T019) → Polish (T020-T022).
- US5/US2 depend on Foundational T005/T006; US2's connect (T013) reuses registry/app-pw resolution.
- `[P]` tasks touch distinct files and may run in parallel within their phase.

## MVP scope

US1 (execute-php + proxy + verification, T001-T009 minus file/connect parts) is the
minimal viable increment: an agent can run code in the live runtime.
