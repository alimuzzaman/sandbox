# Feature Specification: Headless Debugging Tools — Query Monitor, dump/dd, Xdebug

**Feature Branch**: `feat/agent-tooling-specs`

**Created**: 2026-06-22

**Status**: Draft

**Input**: "Integrate Query Monitor, create CLI/MCP tools. Do we have dump/dd functions for
quick-and-dirty debugging? Xdebug?"

## Summary

An AI agent debugs by reading files and JSON, not by looking at an admin-bar
panel. Give it three headless debugging surfaces, in increasing weight:

1. **dump / dd** (new) — `dump()`/`dd()` globals that write structured output to a
   dedicated, tailable file. Quick-and-dirty inspection; nothing exists today.
2. **Query Monitor** (new) — capture QM's collected data (DB queries, PHP errors,
   hooks, timing, HTTP, assets, request) as JSON from a real page request,
   without a browser and without QM's capability gate.
3. **Xdebug** (extend) — already shipped via `./sb xdebug on|off|status`
   ([sandbox/commands/debug.py:24](../../sandbox/commands/debug.py#L24)); the gap
   is herd (host) instances + an MCP toggle + clearer agent docs.

All three are local/dev-only and gitignored at runtime.

## Clarifications

### Session 2026-06-22

- Q: How is Query Monitor activated on an instance? → A: Provision QM **installed-but-inactive** at instance-create time (the `mappings_inactive` pattern — present, not activated), and **auto-activate on first `qm_capture`** (idempotent). The `qm.jsonl` capture mu-plugin is always present regardless of QM's active state, so normal requests carry no QM overhead until a capture is requested.
- Q: How does the agent read `dump()`/`dd()` output? → A: Add a **file selector to the existing `tail_log`** (`tail_log(file="dump")` → `debug-dump.log`); no new MCP tool. CLI: `./sb dump`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — dump/dd to a tailable file (Priority: P1)

A dev or agent drops `dump($thing)` in plugin code and reads the result from a
clean file — not buried in `debug.log`.

**Acceptance**:
1. **Given** an instance with `WP_DEBUG` or `WP_ENVIRONMENT_TYPE=local`, **When**
   plugin code calls `dump($var)`, **Then** a faithful, ANSI-free rendering (with
   recursion/depth handling) is appended to `wp-content/debug-dump.log` with a
   `=== dump HH:MM:SS file:line ===` header.
2. `dd($var)` writes then `wp_die()`s with a pointer to the file.
3. **Given** production / non-local, **When** the mu-plugin loads, **Then** it
   no-ops (hard return) and defines nothing.
4. The agent reads it via `tail_log(file="dump")` (MCP) / `./sb dump --follow` (CLI).
5. `dump`/`dd` are `function_exists`-guarded so they never collide with Symfony's
   or another plugin's definitions.

### User Story 2 — Capture Query Monitor data as JSON (Priority: P1)

An agent profiles a page/REST request and gets QM's data structured.

**Acceptance**:
1. **Given** QM active + the Sandbox QM mu-plugin, **When** the agent calls
   `qm_capture(url)`, **Then** the stack fires an HTTP request to `url` and
   returns parsed JSON for that request: `{db_queries, php_errors, hooks, timing,
   http, assets_scripts, assets_styles, conditionals, request, block_editor}`
   (selectable via a `collectors=[…]` filter; default trims the huge `hooks` set).
2. Capture works for **anonymous** requests (no `view_query_monitor` cap, no QM
   cookie) — because the mu-plugin reads `QM_Collectors` directly on `shutdown`,
   bypassing dispatchers/auth.
3. **When** the agent only needs REST-route data, **Then** `wp_rest(path +
   ?_envelope)` as an app-password user returns the `qm` envelope key (the 6 raw
   collectors) with no extra setup.

### User Story 3 — Xdebug on herd + via MCP (Priority: P2)

**Acceptance**:
1. `./sb xdebug on` works (or fails with a clear, actionable message) on a herd
   instance — today it hard-aborts ("not wired for herd").
2. An MCP `xdebug(action="on|off|status")` tool toggles it without shelling out.
3. Docs state the trigger requirement (gotcha #7): requests need
   `XDEBUG_TRIGGER` (cookie/GET/env) or they won't break.

## Requirements

### dump / dd
- **FR-1** mu-plugin `00-sandbox-dump.php` (+ vendored `symfony/var-dumper`)
  written into every instance's shared bind-mount at provision time, alongside
  the mail/dl-cache mu-plugins.
- **FR-2** Defines global `dump(...$v)` (returns first arg) and `dd(...$v)`
  (writes + `wp_die`). Engine: VarDumper `VarCloner` + `CliDumper`
  (`setColors(false)`, `DUMP_STRING_LENGTH`). Output: `wp-content/debug-dump.log`,
  append + `LOCK_EX`, each entry prefixed with timestamp + caller `file:line`.
- **FR-3** Hard `return` unless `WP_DEBUG` or `wp_get_environment_type()==='local'`;
  `function_exists` guards; slug-prefixed internal helper.
- **FR-4** Extend the existing `tail_log` MCP tool with a `file` selector
  (`tail_log(file="dump", lines=…, *, project_dir)` → `debug-dump.log`; default
  `file="debug"` keeps current behavior) + `./sb dump [--follow] [--clear]`. No
  new MCP tool (avoids an extra registration/restart surface).

### Query Monitor
- **FR-5** mu-plugin `00-sandbox-qm.php`: on `shutdown` priority `PHP_INT_MAX`,
  if `QM_Collectors` exists, `QM_Collectors::init()->process()` then iterate
  collectors, `wp_json_encode` each `get_data()`, append one line to
  `wp-content/qm.jsonl` with `{ts, url, is_ajax, data:{…}}`. Whitelist collector
  ids (drop `hooks` by default — it's huge). Never define `QM_DISABLED`; **do**
  define `QM_HIDE_SELF`.
- **FR-6** Provisioning installs QM at instance-create time **installed-but-inactive**
  (the `mappings_inactive` pattern — present, not activated), so it doesn't pollute
  the focused plugin's deps and adds no per-request overhead until used.
- **FR-7** MCP `qm_capture(url, collectors=None, *, project_dir)`: if QM is
  inactive, **auto-activate it on this first call** (idempotent), then fire
  `http_fetch(url)` (reuse the existing tool internals), read the **last**
  `qm.jsonl` line, return parsed + filtered JSON. CLI: `./sb qm <url>
  [--collectors db_queries,timing,php_errors]`; `./sb qm off` to deactivate.
- **FR-8** Document the `?_envelope` REST path as the zero-config alternative for
  REST-scoped debugging.
- **FR-9** `qm.jsonl` / `debug-dump.log` are runtime, gitignored; `./sb qm
  --clear` / `./sb dump --clear` truncate them.

### Xdebug
- **FR-10** Extend `cmd_xdebug` to support herd (host PHP): toggle the host
  instance's `php<MM>` xdebug ini, or emit a precise "enable it this way on herd"
  message if host-managed PHP can't be toggled by us.
- **FR-11** MCP `xdebug(action, *, project_dir)` wrapping the CLI.
- **FR-12** Docs: trigger requirement + how the agent sets `XDEBUG_TRIGGER` on a
  `visit`/`http_fetch` probe.

## Design notes

- **Why not `wp eval` for QM?** QM short-circuits under the `WP_CLI`/CLI SAPI, so
  QM data must come from a real **web** request (`http_fetch`/`wp_rest`) →
  mu-plugin writes `qm.jsonl` → agent reads it. `qm_capture` automates that
  round-trip.
- **Direct-collector read beats both built-in headless surfaces.** QM's REST
  envelope only carries 6 `raw` collectors and the header dispatch only 3; reading
  `QM_Collectors` on shutdown gives the full set and ignores the auth gate. (See
  research: `output/raw` = cache/conditionals/db_queries/http/logger/transients;
  `output/headers` = overview/php_errors/redirects.)
- **dump engine:** VarDumper over `print_r`/`var_export` for object/recursion
  fidelity; `CliDumper` (not `HtmlDumper`/`ServerDumper`) because the sink is a
  file the agent tails, not a browser or a TCP server.
- **Borrowed contracts:** model `qm_capture`'s output on `wp profile --format=json`
  (stage/hook/time/query breakdown) and a future `./sb doctor`-style checks list on
  `wp doctor`'s `{name, status, message}` shape. Xdebug stays the heavyweight
  escalation tier.
- Optional sugar (from Kint): `dump_wp_query()`, `dump_post()` helpers atop the
  VarDumper writer. Nice-to-have, not v1.

## Integration points

- mu-plugin writers next to `_write_mail_muplugin` in the CLI; hook into
  `cmd_up`/`cmd_install`/`apply` (idempotent rewrite).
- MCP: `qm_capture`, `tail_log` `file` selector, `xdebug` in
  `mcp/wp-server/tools/` (reuse `http_fetch` internals, `_project_instance`,
  `tail_log`). New tools ⇒ Claude Code restart (gotcha #4).
- CLI: `qm` + `dump` command modules; extend `debug.py` (`cmd_xdebug`) for herd.
- Docs: CLAUDE.md MCP-surface table + a new "Debugging" common-loop entry +
  `skills/wp-debug/SKILL.md` (teach the agent dump → QM → Xdebug escalation),
  `docs/sandbox-config-reference.md`.

## Tasks

1. `00-sandbox-dump.php` + vendored var-dumper + writer; provisioning hook.
2. `tail_log` `file="dump"` selector + `./sb dump` CLI.
3. `00-sandbox-qm.php` shutdown→`qm.jsonl` reader; provision QM installed-inactive; `qm_capture` auto-activates on first use.
4. `qm_capture` MCP + `./sb qm` CLI (+ document `?_envelope`).
5. Xdebug: herd support in `cmd_xdebug` + `xdebug` MCP tool.
6. Live verification: `dump()` from plugin code → read via `tail_log(file="dump")`; `qm_capture`
   on a slow page shows queries+timing; xdebug status on docker + herd.
7. Docs + `skills/wp-debug` escalation ladder.
