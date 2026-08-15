# Changelog

All notable changes to Sandbox are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.2.2] — 2026-08-13

### Fixed
- Remote resource observation now converts a client-side SSH timeout into
  structured `timed_out` partial evidence instead of leaking a traceback or
  retrying an ambiguous probe.

## [0.2.1] — 2026-08-13

### Fixed
- Feedback list date filters now return RFC 3339 strings in JSON response
  receipts instead of leaking internal `datetime` objects to the encoder.
- Detached `job-start --json` acceptance is flushed as one complete line after
  durable acceptance, while replay-safe request guidance treats missing output
  as unknown rather than success.

## [0.2.0] — 2026-08-13

### Added
- Durable, owner-only workspace identity and migration index with byte-preserved
  legacy metadata, checkout-independent CLI/MCP controls, and plan-bound migration.
- WordPress PHP-extension requirements, deterministic image/package planning,
  four-plane verification, and rollback-safe apply behavior.
- Secret-redacted feedback detail/export/retention controls and conservative
  workspace-aware resource ownership diagnostics.
- A confirmation-gated `remote docker-pool` transaction that backs up and
  validates Docker daemon configuration, restarts Docker, recovers previously
  running containers, and reports only bounded host-capacity evidence.
- Restart-state-backed, confirmation-gated recovery for containers stopped by an
  interrupted Docker-pool transaction.
- A read-only `remote domains` inventory combining instance-registry URLs with
  Sandbox-owned Caddy routes without returning login tokens or route contents.

### Changed
- Remote job and workspace protocols now use canonical project identity, durable
  acceptance receipts, and strict top-level response envelopes.
- Remote deployment and hosting preserve exact nested source roots and immutable
  source provenance.
- Agents must verify local and installed remote revisions before depending on a
  changed CLI/MCP protocol.
- Remote revision receipts now cover every shipped CLI, service, workspace, and
  MCP Python source instead of a small proxy subset.

### Security
- Destructive restore requires explicit confirmation.
- Workspace migration is metadata-only and cannot authorize workspace destruction,
  network cleanup, or ambiguous ownership adoption.

### Fixed
- `job-list --active-only` now applies the active-lifecycle predicate before the
  bounded page, preventing older active jobs from disappearing behind newer
  terminal records.

## [Unreleased] — v1.0.0 target

### Fixed
- Local `job-status --json` now returns a stable `job_not_found` receipt with
  an explicit configured-remote retry hint instead of leaking a registry traceback.
- Confirmed remote provisioning now keeps an owner-only, redacted milestone
  journal and exposes incomplete prior attempts in the next plan instead of
  leaving a receipt-less update opaque.

### Added
- `./sb smoke` — self-test subcommand: boots a fresh instance, verifies WP + REST, tears down.
- `./sb doctor` now audits credential state (FluentBoards reachability, GitHub org set, `.env.local` permissions).
- `./sb doctor` now audits domain/proxy drift: Caddyfile readable inside the proxy container, and configured domain == Caddyfile route for every instance.

### Fixed
- Proxy compose mounts `runtime/proxy` as a directory instead of bind-mounting the
  Caddyfile as a file. The file mount pinned an inode; `regen_caddyfile()` replaces
  the file, so the running container lost `/etc/caddy/Caddyfile` and every
  `caddy reload` failed — `domains setup` reported "proxy container did not start"
  while the container was up, and new instances silently fell back to
  `http://localhost:<port>`.
- `proxy_apply()` self-heals: a failed hot reload now force-recreates the proxy once
  before reporting failure, and surfaces the real stderr instead of guessing at Docker.
- Secure-at-create rollback pops `tld` alongside `domain` and drops the orphaned
  Caddy route, so a failed proxy step no longer leaves an unrepairable half-state.
- `domains setup` wires new routes before deciding each site's URL, so freshly
  assigned domains reach WP's `siteurl`/`home` instead of staying on localhost.
- Snapshot database capture and restore now stream through host-owned `0600`
  files instead of bind-mounting snapshot directories, avoiding remote
  `Permission denied` without chowning paths to MariaDB's `mysql` UID or
  weakening global permissions.

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
- **npm / Homebrew distribution** — `@alimuzzaman/sandbox` package +
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
