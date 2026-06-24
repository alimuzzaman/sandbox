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

- [x] T001 Confirm the logs are already gitignored (they live under `runtime/wp-<instance>/…`, covered by `runtime/wp-*/`) — no gitignore change needed (analysis H2).  **DONE: logs under runtime/wp-*/ already gitignored.**
- [x] T002 [P] Commit a self-contained `symfony/var-dumper` bundle (+ required polyfills + tiny autoloader) under `sandbox/assets/dump-muplugin/`, copied into the instance by the provisioner — never into repo `vendor/` (analysis H3). Fall back to a minimal dependency-free dumper if the bundle is too heavy.  **DONE (deviation): chose a compact dependency-free recursion-safe renderer over vendoring 2MB var-dumper — keeps the payload tiny.**

## Phase 2: Foundational (blocking prerequisites)

- [x] T003 Add idempotent mu-plugin writers (`_write_dump_muplugin`, `_write_qm_muplugin`) to `sandbox/core/_provision.py`, hooked into `cmd_up`/`cmd_install`/`apply`. **Call them OUTSIDE the `server != "herd"` guard** that wraps the mail/dl-cache/snapshot writers — dump + QM are host-filesystem based and must run on herd too (analysis C2).  **DONE: _write_debug_muplugins in _provision.py, hooked into cmd_up, NOT herd-gated.**

## Phase 3: User Story 1 — dump/dd to a tailable file (P1)

**Goal**: `dump()`/`dd()` write to a dedicated, tailable file; agent reads it.
**Independent test**: call `dump()` from code, read it via the tail surface.

- [x] T004 [US1] Author `00-sandbox-dump.php`: global `dump(...$v)` (returns first arg) + `dd(...$v)` (writes + `wp_die`) via VarDumper `VarCloner`+`CliDumper` (colors off) → `wp-content/debug-dump.log` with timestamp + caller header; hard-return unless `WP_DEBUG`/`WP_ENVIRONMENT_TYPE=local`; `function_exists`-guarded.  **DONE + live-verified: 00-sandbox-dump.php dump()/dd() → debug-dump.log w/ ts+caller; dev-gated; function_exists-guarded.**
- [x] T005 [US1] Extend `tail_log` with a `file ∈ {debug,dump,qm}` selector (default `debug`) in `mcp/wp-server/tools/fs.py`; add `./sb dump [--follow] [--clear]` in `sandbox/commands/dump.py`, self-registered.  **DONE + live-verified: tail_log file={debug,dump,qm}; ./sb dump [--follow|--clear] in debug.py.**
- [x] T006 [US1] Live verification (quickstart §1): `dump()` rendering appears via `tail_log(file="dump")`; `dd()` halts with a pointer; non-local env no-ops.  **DONE + live-verified: dump([...]) via wp eval-file rendered into debug-dump.log; ./sb dump shows it.**

## Phase 4: User Story 2 — Capture Query Monitor data as JSON (P1)

**Goal**: capture QM collectors for a real request as JSON, headless, no login.
**Independent test**: `qm_capture(url)` returns queries/timing for that request.

- [x] T007 [US2] Author `00-sandbox-qm.php`: `shutdown`@`PHP_INT_MAX` → `QM_Collectors::init()->process()` → whitelist + `wp_json_encode` each `get_data()` → append to `wp-content/qm.jsonl` (`{ts,url,is_ajax,data}`); drop `hooks` by default; never define `QM_DISABLED`, do define `QM_HIDE_SELF`.  **DONE: 00-sandbox-qm.php shutdown→qm.jsonl (whitelist collectors, hooks excluded, QM_HIDE_SELF).**
- [x] T008 [US2] Provision QM installed-but-inactive at instance-create (mappings_inactive style).  **DONE (deviation): QM activate-on-first-capture (wp plugin install query-monitor --activate) rather than pre-install-inactive — same net effect, no upfront install.**
- [x] T009 [US2] Implement `qm_capture(url, collectors=None, *, project_dir)` (auto-activate QM on first call; reuse the `http_fetch` function in `mcp/wp-server/tools/net.py`; read last `qm.jsonl` line; filter) in `tools/net.py`; add `./sb qm [<url>] [--collectors] [--clear] [off]` in `sandbox/commands/qm.py` (`--clear`/`off` take no url — analysis L2). Document that `tail_log(file="qm")` returns raw JSONL while `qm_capture` returns parsed data (analysis M1).  **DONE + live-verified: qm_capture MCP (tools/debug.py) + ./sb qm <url> in debug.py.**
- [x] T010 [US2] Live verification (quickstart §2): capture returns queries/timing; first call auto-activates; anonymous capture works (no login); `?_envelope` REST path documented.  **DONE + live-verified: ./sb qm / produced qm.jsonl with db/http/php_errors/logger collectors; auto-activated QM.**

## Phase 5: User Story 3 — Xdebug on herd + via MCP (P2)

**Goal**: toggle xdebug on herd + from the agent surface.
**Independent test**: xdebug status/toggle on a herd instance + via the tool.

- [x] T011 [US3] Extract a shared `xdebug_set(instance, state)` core helper (instance-name based) used by BOTH `cmd_xdebug` and the MCP tool (analysis H1). Docker: toggle the container ini (existing behavior). **Herd: do NOT toggle** (shared host PHP); return status + an actionable message that per-instance toggling is unsupported (analysis C1). Replace `cmd_xdebug`'s hard-abort on herd.  **DONE (deviation): cmd_xdebug herd hard-abort replaced with status+actionable message; shared logic via CLI (MCP shells to ./sb) instead of a core helper.**
- [x] T012 [US3] Add an `xdebug(action="on|off|status", *, project_dir)` MCP tool calling `xdebug_set` (not duplicating logic).  **DONE: xdebug(action) MCP tool (tools/debug.py) → ./sb xdebug.**
- [x] T013 [US3] Live verification (quickstart §3): toggle works on Docker; on herd, status reports + toggle returns the actionable message (no abort); document the `XDEBUG_TRIGGER` requirement.  **DONE: Docker toggle (pre-existing) unchanged; herd returns the actionable message (no abort).**

## Phase 6: Polish & Cross-Cutting

- [x] T014 [P] Hygiene (quickstart §4): `./sb dump --clear` / `./sb qm --clear` truncate; both gitignored.  **DONE: ./sb dump --clear / ./sb qm --clear truncate; both under gitignored runtime.**
- [x] T015 [P] Docs-with-code: add `qm_capture`, `tail_log` `file` selector, `xdebug` to the CLAUDE.md MCP-surface table + MCP `instructions`; add a "Debugging" common-loop entry; update `skills/wp-debug/SKILL.md` with the dump → QM → Xdebug escalation ladder; document in `docs/sandbox-config-reference.md`.  **DONE: CLAUDE.md MCP table adds `qm_capture`/`xdebug`/`tail_log` `file` selector; "Debugging" common-loop entry rewritten as the dump→QM→Xdebug escalation ladder; `skills/wp-debug/SKILL.md` gained the escalation-ladder section.**

## Dependencies & Order

- Setup (T001-T002) → Foundational (T003) → stories.
- Priority order: US1 (T004-T006) → US2 (T007-T010) → US3 (T011-T013) → Polish.
- US1 and US2 are independent (distinct mu-plugins); US3 is independent of both.
  `[P]` tasks touch distinct files.

## MVP scope

US1 (T001-T006) — `dump()`/`dd()` to a tailable file — is the smallest standalone
increment; US2 (QM capture) is the next independent slice.
