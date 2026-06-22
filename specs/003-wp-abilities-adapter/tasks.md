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

- [ ] T001 Create the mu-plugin payload skeleton: `00-sandbox-abilities.php` loader + `sandbox-abilities/` dir (callbacks + vendored adapter) authored under a host template dir in `sandbox/` (e.g. `sandbox/assets/abilities/`), the source the writer copies from.
- [ ] T002 Vendor `wordpress/mcp-adapter` (^0.5.x) into the payload `sandbox-abilities/vendor/` (isolated from the focused plugin and repo `vendor/`).
- [ ] T003 Add the enable-flag plumbing: option `sandbox_abilities_enabled` (default on) + mirror key `instances.<name>.abilities_enabled` read/written via the existing `sandbox.local.yml` helpers.

## Phase 2: Foundational (blocking prerequisites)

- [ ] T004 Implement the idempotent mu-plugin writer `_write_abilities_muplugin` in `sandbox/core/muplugins.py` (or the existing mu-plugin writer module), copying the payload into `runtime/wp-<instance>/wp-content/mu-plugins/`; hook it into `cmd_up` / `cmd_install` / `apply` alongside the mail/dl-cache/autologin writers.
- [ ] T005 In `00-sandbox-abilities.php`: bootstrap the bundled mcp-adapter, register the MCP server exposing only abilities with `meta.mcp.public=true`, gated on `sandbox_abilities_enabled` AND WP Abilities-API presence (no-op + logged notice otherwise).
- [ ] T006 Implement the shared `permission_callback` (logged-in user AND `manage_options`) and the `resolve_path` ABSPATH jail (rejects symlink final-path escape) in the payload.

## Phase 3: User Story 1 — Run code in the live runtime (P1)

**Goal**: `execute-php` returns a structured result from the live WP runtime.
**Independent test**: call the ability for `return get_option('siteurl');` and get a structured result.

- [ ] T007 [US1] Implement the `sandbox/execute-php` ability (eval + output-buffer + error-handler capture + `set_time_limit` cap + `\Throwable` catch + JSON-safe return) per contracts/abilities.md, annotated destructive.
- [ ] T008 [P] [US1] Add the `wp_eval_live` proxy MCP tool in `mcp/wp-server/tools/abilities.py` (resolves instance, POSTs to the endpoint with app-password auth).
- [ ] T009 [US1] Live verification (quickstart §2): execute-php round-trip via proxy + direct; notice captured in `errors[]`; thrown error returns `success:false` and the site stays up; time-limit cap holds.

## Phase 4: User Story 5 — Off-switch & per-call authorization (P1)

**Goal**: the layer toggles per instance and every ability enforces auth + capability.
**Independent test**: disable → endpoint empty/403; enable → under-privileged caller refused.

- [ ] T010 [US5] Implement `./sb abilities on|off|status` in `sandbox/commands/abilities.py` (instance-resolved; sets option + mirrors to `sandbox.local.yml`; `status` prints endpoint + WP-support + the "dev/staging only" banner per FR-006), self-registered in `sandbox/registry.py`.
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

- [ ] T015 [P] [US3] Implement `sandbox/read-file`, `write-file`, `edit-file`, `list-directory` abilities (ABSPATH-jailed; new `.php` restricted to `wp-content/sandbox-code/`) per contracts/abilities.md.
- [ ] T016 [P] [US3] Add file-ability proxy MCP tools (`wp_file_read/write/list`) in `mcp/wp-server/tools/abilities.py`.
- [ ] T017 [US3] Live verification (quickstart §4): write/read round-trip **via both the direct endpoint and the `wp_file_*` proxy** (asserts the FR-010 proxy path for files); out-of-ABSPATH + symlink escape rejected; new `.php` outside sandbox-code/ rejected.

## Phase 7: User Story 4 — Persistent AI PHP with crash recovery (P2)

**Goal**: sandbox-code/ loads behind crash recovery; a fatal drops to safe mode.
**Independent test**: write a fatal sandbox file; site stays up in safe mode with a naming notice.

- [ ] T018 [US4] Implement the crash-recovery loader in the payload: require `wp-content/sandbox-code/*.php` behind a shutdown handler that writes `.crashed` on fatal; safe mode skips all sandbox files; admin notice names the file; `?sb_safe_mode=1` manual override.
- [ ] T019 [US4] Live verification (quickstart §5): fatal sandbox file → site up in safe mode, `.crashed` present, notice shown; remove marker → normal load.

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
