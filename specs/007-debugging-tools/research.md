# Research: Headless Debugging Tools

## Decision: extract QM by reading `QM_Collectors` on shutdown

- **Decision**: A `00-sandbox-qm.php` mu-plugin hooks `shutdown` at `PHP_INT_MAX`, calls `QM_Collectors::init()->process()`, iterates collectors, `wp_json_encode`s each `get_data()`, and appends one line to `wp-content/qm.jsonl` (`{ts,url,is_ajax,data:{…}}`). Whitelist collector ids; drop the huge `hooks` set by default.
- **Rationale**: QM's two built-in headless surfaces are partial — the REST envelope carries only 6 `raw` collectors, the header dispatch only 3. Reading `QM_Collectors` directly gives the full set AND bypasses the `view_query_monitor` capability gate (works for anonymous requests).
- **Alternatives considered**: `?_envelope` REST (documented as the zero-config path for REST-scoped data, but partial); HTML panel (browser-only).

## Decision: capture via a real web request, not `wp eval`

- **Decision**: `qm_capture(url)` fires `http_fetch(url)` (reusing the tool internals) → the mu-plugin writes `qm.jsonl` → read + return the last line, filtered to requested collectors.
- **Rationale**: QM short-circuits under the `WP_CLI`/CLI SAPI, so QM data must come from a real web request.

## Decision: QM installed-inactive, auto-activate on first capture

- **Decision**: Provision QM at instance-create installed-but-inactive (mappings_inactive style); `qm_capture` activates it on first use (idempotent); `./sb qm off` deactivates.
- **Rationale**: zero collector overhead on normal requests until a capture is requested. (Clarification 2026-06-22.)

## Decision: dump/dd via Symfony VarDumper `CliDumper` → dedicated file

- **Decision**: `00-sandbox-dump.php` (vendoring `symfony/var-dumper`) defines global `dump(...$v)` (returns first arg) + `dd(...$v)` (writes + `wp_die`), rendering via `VarCloner` + `CliDumper` (`setColors(false)`) to `wp-content/debug-dump.log` with a `=== dump HH:MM:SS file:line ===` header. Hard return unless `WP_DEBUG`/`WP_ENVIRONMENT_TYPE=local`; `function_exists` guards.
- **Rationale**: faithful object/recursion rendering beats `print_r`/`var_export`; a dedicated file keeps dumps out of the noisy general debug log; CliDumper (not Html/Server) suits a tailed file.
- **Vendoring mechanism (analysis H3)**: `symfony/var-dumper` pulls `symfony/polyfill-*` deps — not a one-file drop. Commit a **self-contained bundle** (var-dumper + required polyfills + a tiny autoloader) under `sandbox/assets/dump-muplugin/`, which the provisioner copies into `wp-content/mu-plugins/sandbox-dump/`. Never touch repo `vendor/` (constitution boundary; wiped on `composer install`). If the bundle proves heavy, fall back to a minimal dependency-free dumper.
- **Alternatives considered**: `error_log(print_r())` (mangles objects, pollutes debug.log); Ray/Kint (GUI/browser sinks).

## Decision: read dump via `tail_log` file selector (no new tool)

- **Decision**: Extend `tail_log` with `file ∈ {debug (default), dump, qm}`; `tail_log(file="dump")` → `debug-dump.log`. CLI `./sb dump`.
- **Rationale**: avoids an extra MCP tool/registration-restart surface. (Clarification.)

## Decision: shared xdebug core helper; herd = status + message (analysis C1, H1)

- **Decision**: Extract the xdebug logic into a core `xdebug_set(instance, state)` helper called by BOTH `cmd_xdebug` (CLI) and the new `xdebug(action)` MCP tool (the MCP process resolves via `_project_instance`/`_compose`, the CLI via `args.resolved_instance`/`compose` — so a shared instance-name-based helper avoids duplicated, drifting logic). On **Docker** it toggles the container's xdebug ini (existing behavior). On **herd** it does NOT toggle: Herd's PHP is a shared host install, so flipping the global ini would affect every Herd site and need a restart we can't do per-instance — the helper returns status + an actionable message. Document the `XDEBUG_TRIGGER` requirement (gotcha #7).
- **Rationale**: closes the gap honestly without claiming an infeasible per-instance herd toggle; keeps one source of truth (constitution III).

## Open questions

None — QM activation + dump-read mechanism resolved (clarifications).
