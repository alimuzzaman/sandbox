---
description: "Task list for Headless Debugging Tools — Query Monitor, dump/dd, Xdebug"
---

# Tasks: Headless Debugging Tools — Query Monitor, dump/dd, Xdebug

**Input**: Design documents from `specs/007-debugging-tools/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: No unit-test tasks requested; per constitution IV each user story ends with
a **live-stack verification** task.

## Path Conventions

Two provisioned mu-plugins + host CLI/MCP: `mcp/wp-server/tools/`,
`sandbox/commands/`, `runtime/wp-<instance>/wp-content/`.

## Phase 1: Setup (Shared Infrastructure)

- [ ] T001 Confirm the logs are already gitignored (they live under `runtime/wp-<instance>/…`, covered by `runtime/wp-*/`) — no gitignore change needed (analysis H2).
- [ ] T002 [P] Commit a self-contained `symfony/var-dumper` bundle (+ required polyfills + tiny autoloader) under `sandbox/assets/dump-muplugin/`, copied into the instance by the provisioner — never into repo `vendor/` (analysis H3). Fall back to a minimal dependency-free dumper if the bundle is too heavy.

## Phase 2: Foundational (blocking prerequisites)

- [ ] T003 Add idempotent mu-plugin writers (`_write_dump_muplugin`, `_write_qm_muplugin`) to `sandbox/core/_provision.py`, hooked into `cmd_up`/`cmd_install`/`apply`. **Call them OUTSIDE the `server != "herd"` guard** that wraps the mail/dl-cache/snapshot writers — dump + QM are host-filesystem based and must run on herd too (analysis C2).

## Phase 3: User Story 1 — dump/dd to a tailable file (P1)

**Goal**: `dump()`/`dd()` write to a dedicated, tailable file; agent reads it.
**Independent test**: call `dump()` from code, read it via the tail surface.

- [ ] T004 [US1] Author `00-sandbox-dump.php`: global `dump(...$v)` (returns first arg) + `dd(...$v)` (writes + `wp_die`) via VarDumper `VarCloner`+`CliDumper` (colors off) → `wp-content/debug-dump.log` with timestamp + caller header; hard-return unless `WP_DEBUG`/`WP_ENVIRONMENT_TYPE=local`; `function_exists`-guarded.
- [ ] T005 [US1] Extend `tail_log` with a `file ∈ {debug,dump,qm}` selector (default `debug`) in `mcp/wp-server/tools/fs.py`; add `./sb dump [--follow] [--clear]` in `sandbox/commands/dump.py`, self-registered.
- [ ] T006 [US1] Live verification (quickstart §1): `dump()` rendering appears via `tail_log(file="dump")`; `dd()` halts with a pointer; non-local env no-ops.

## Phase 4: User Story 2 — Capture Query Monitor data as JSON (P1)

**Goal**: capture QM collectors for a real request as JSON, headless, no login.
**Independent test**: `qm_capture(url)` returns queries/timing for that request.

- [ ] T007 [US2] Author `00-sandbox-qm.php`: `shutdown`@`PHP_INT_MAX` → `QM_Collectors::init()->process()` → whitelist + `wp_json_encode` each `get_data()` → append to `wp-content/qm.jsonl` (`{ts,url,is_ajax,data}`); drop `hooks` by default; never define `QM_DISABLED`, do define `QM_HIDE_SELF`.
- [ ] T008 [US2] Provision QM installed-but-inactive at instance-create (mappings_inactive style).
- [ ] T009 [US2] Implement `qm_capture(url, collectors=None, *, project_dir)` (auto-activate QM on first call; reuse the `http_fetch` function in `mcp/wp-server/tools/net.py`; read last `qm.jsonl` line; filter) in `tools/net.py`; add `./sb qm [<url>] [--collectors] [--clear] [off]` in `sandbox/commands/qm.py` (`--clear`/`off` take no url — analysis L2). Document that `tail_log(file="qm")` returns raw JSONL while `qm_capture` returns parsed data (analysis M1).
- [ ] T010 [US2] Live verification (quickstart §2): capture returns queries/timing; first call auto-activates; anonymous capture works (no login); `?_envelope` REST path documented.

## Phase 5: User Story 3 — Xdebug on herd + via MCP (P2)

**Goal**: toggle xdebug on herd + from the agent surface.
**Independent test**: xdebug status/toggle on a herd instance + via the tool.

- [ ] T011 [US3] Extract a shared `xdebug_set(instance, state)` core helper (instance-name based) used by BOTH `cmd_xdebug` and the MCP tool (analysis H1). Docker: toggle the container ini (existing behavior). **Herd: do NOT toggle** (shared host PHP); return status + an actionable message that per-instance toggling is unsupported (analysis C1). Replace `cmd_xdebug`'s hard-abort on herd.
- [ ] T012 [US3] Add an `xdebug(action="on|off|status", *, project_dir)` MCP tool calling `xdebug_set` (not duplicating logic).
- [ ] T013 [US3] Live verification (quickstart §3): toggle works on Docker; on herd, status reports + toggle returns the actionable message (no abort); document the `XDEBUG_TRIGGER` requirement.

## Phase 6: Polish & Cross-Cutting

- [ ] T014 [P] Hygiene (quickstart §4): `./sb dump --clear` / `./sb qm --clear` truncate; both gitignored.
- [ ] T015 [P] Docs-with-code: add `qm_capture`, `tail_log` `file` selector, `xdebug` to the CLAUDE.md MCP-surface table + MCP `instructions`; add a "Debugging" common-loop entry; update `skills/wp-debug/SKILL.md` with the dump → QM → Xdebug escalation ladder; document in `docs/sandbox-config-reference.md`.

## Dependencies & Order

- Setup (T001-T002) → Foundational (T003) → stories.
- Priority order: US1 (T004-T006) → US2 (T007-T010) → US3 (T011-T013) → Polish.
- US1 and US2 are independent (distinct mu-plugins); US3 is independent of both.
  `[P]` tasks touch distinct files.

## MVP scope

US1 (T001-T006) — `dump()`/`dd()` to a tailable file — is the smallest standalone
increment; US2 (QM capture) is the next independent slice.
