# Changelog

All notable changes to Sandbox are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] — v1.0.0 target

### Added
- `./sb smoke` — self-test subcommand: boots a fresh instance, verifies WP + REST, tears down.
- `./sb doctor` now audits credential state (FluentBoards reachability, GitHub org set, `.env.local` permissions).

---

## [0.1.0] — 2025-06 (per-project rewrite)

First feature-complete release. All major subsystems rewritten to the
per-project model: one `cd` + one `./sb init` gives any WP plugin repo
a live stack, isolated test DB, and a single registered MCP server.

### Added
- **Per-project config loader** — walks up from `project_dir` to find
  `sandbox.config.json` / `.wp-env.json`; deep-merges `.override.*`.
- **On-disk instance registry** (`runtime/registry.json`) — maps project
  root → instance (ports, server, version pins). `flock`-guarded writes;
  survives process restart. `registry_get/put/list/remove` API.
- **`ensure_instance(project_dir)`** — create-if-missing boot cycle:
  picks free ports, generates compose, boots, installs WP, wires plugins
  from the project config, records in the registry. Idempotent.
- **Single MCP server** (`sandbox mcp` / `./sb mcp`) — one registration
  replaces per-instance `sandbox-<name>` servers. Every tool takes a
  required `project_dir`; `ensure_instance` tool exposed.
- **External PHP test harness** — sparse-clones `wordpress-develop`
  tests at the instance's WP version, downloads phpunit 9 + polyfills,
  creates an isolated `wp_tests` DB. `./sb test` + `run_tests` MCP tool.
- **`./sb init`** — scaffold `sandbox.config.json` (or convert
  `.wp-env.json`), boot instance, provision test harness in one command.
- **Version knobs** — `phpVersion`/`wpVersion` in config resolve to
  server-aware image tags (apache, nginx-fpm, lsphp). WP CLI image
  follows the PHP pin.
- **npm / Homebrew distribution** — `@wpdeveloper/sandbox` package +
  `bin/sandbox.js` shim + `packaging/homebrew/sandbox.rb` formula.
- **`docs/sandbox-config-reference.md`** — full schema reference.
- **Web dashboard** (`./sb web`) — instance list, focus/server/start/
  stop/restart, per-project pointer for new instances.
- **Curses TUI** (`./sb dashboard`) — keyboard-driven instance manager.

### Changed
- Central `projects:` catalog removed; `./sb projects|pick|use|add` gone.
  Old code preserved at commit `25fc409` for reference.
- `instance create` removed from CLI (now `./sb init` per-project).
- `focus_set` / `focus_resolve` tools removed from MCP surface.
- README and CLAUDE.md rewritten to the per-project model.

### Fixed
- `instance delete` now cleans the registry entry (no more stale
  "ready" records pointing at deleted stacks).
- `cmd_update` / `cmd_doctor` / `cmd_status` re-pointed to the registry
  (no longer die on `main` not being in the catalog).
- `pretty permalinks` — `AllowOverride All` patch already in compose.
- Symlink depth — plugins land at `wp-content/plugins/<slug>` (depth 1).
- Bind-mount path — arbitrary-root plugin projects get their own mount
  so absolute symlinks resolve inside the container.
