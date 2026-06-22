# Feature Specification: In-Instance WordPress Abilities + MCP Adapter Layer

**Feature Branch**: `feat/agent-tooling-specs`

**Created**: 2026-06-22

**Status**: Draft

**Input**: Novamira parity #1 — "Ride the official WP Abilities API + `wordpress/mcp-adapter`.
Our 23 tools are hand-rolled custom MCP. Their abilities are discoverable WP-natively,
work with *any* MCP client, and the ecosystem standard is forming around exactly this."
Plus parity #5 — the crash-recovery sandbox-loader pattern.

## Summary

Add a **second, optional MCP surface that lives inside each provisioned
instance**: a Sandbox mu-plugin that registers WordPress **Abilities** (the core
`wp_register_ability` API, WP 6.9+) and exposes them over MCP via the official
`wordpress/mcp-adapter` Composer package. This is exactly Novamira's architecture
([CLAUDE.md](file:///tmp/novamira-review/CLAUDE.md): "Abilities API + MCP Adapter").

This does **not** replace the Python MCP server (`mcp__sandbox__*`). The two have
distinct jobs:

| Surface | Owns | Lives |
|---------|------|-------|
| Python MCP (`mcp__sandbox__*`) | Provisioning, lifecycle, snapshots, multi-instance routing, `visit`, mail, cache | Host process, routes by `project_dir` |
| In-instance Abilities (new) | Things best done *inside* WP: `execute-php` with the full runtime, native ability discovery | The instance's own `/wp-json` MCP endpoint |

**Ecosystem signal (added after the spec-005 deep-dive):** this bet is now
corroborated by Elementor itself — Elementor core's `composer.json` pins
`wordpress/mcp-adapter ^0.5.0` and `modules/mcp/module.php` registers real WP
abilities served at `/wp-json/elementor/mcp` (behind a hidden WP-7.0 experiment).
The reference third-party project `msrbuilds/elementor-mcp` and Novamira both ride
the same Abilities-API + mcp-adapter stack. The WordPress ecosystem is
consolidating on exactly this path (Abilities API landed in Core 6.9;
`Automattic/wordpress-mcp` is deprecated in favor of `WordPress/mcp-adapter`).
Note: of WPDeveloper's own plugins, **none** (EA, EB, Elementor Pro is 3rd-party)
expose abilities yet — see [005 research](../005-editor-authoring/research.md).

**Why add it:** (1) MCP-client portability — Cursor, Windsurf, Cline, Claude
Desktop can connect *directly* to an instance's MCP endpoint with zero Sandbox-
specific glue, because the endpoint is standards-based. (2) `execute-php` (eval in
the live WP process, with `$wpdb` and every loaded plugin API) is strictly more
powerful than `wp eval-file` for interactive inspection and is the foundation
specs 005/006 build on. (3) We ride the forming ecosystem standard instead of a
bespoke tool list.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Run PHP in the live WP runtime (Priority: P1)

An agent executes arbitrary PHP against a running instance and gets back the
return value, echoed output, captured warnings/notices, and timing — without
writing a file.

**Acceptance**:
1. **Given** a running instance with abilities enabled, **When** the agent calls
   the `execute-php` ability with `return get_option('siteurl');`, **Then** it
   returns `{success:true, return_value:"http://…", output:"", errors:[],
   execution_time_ms:…}`.
2. **When** the code emits a notice, **Then** it appears in `errors[]` (type,
   message, file, line) — captured, not fatal.
3. **When** the code throws, **Then** `{success:false, error_message, error_class}`
   — the request survives.
4. **When** the code runs >30s, **Then** it is cut by the `set_time_limit` cap.

### User Story 2 — Any MCP client connects directly (Priority: P1)

A developer points Cursor/Windsurf/Cline/Claude Desktop at an instance's MCP
endpoint and the abilities show up as tools.

**Acceptance**:
1. `./sb connect <instance>` (or the web dashboard "Use with Claude" block)
   prints the endpoint URL + an Application Password and a ready-to-paste client
   config. The client lists the abilities and can call `execute-php`.
2. Discovery returns the ability list **plus** Sandbox environment instructions
   (Novamira overrides `mcp-adapter/discover-abilities` for exactly this —
   [discover-abilities.php](file:///tmp/novamira-review/includes/abilities/discover-abilities.php)).

### User Story 3 — Persistent AI-written PHP with crash recovery (Priority: P2)

An agent writes a persistent mu-style PHP file into a sandbox folder; if it
fatals, the site auto-recovers into safe mode instead of white-screening.

**Acceptance**:
1. New `.php` written via the write ability lands only in
   `wp-content/sandbox-code/` (path-jailed).
2. A fatal in a sandbox file writes a `.crashed` marker; subsequent requests skip
   **all** sandbox files (safe mode) and wp-admin shows a dismissable-blocked
   notice naming the file. `?sb_safe_mode=1` forces safe mode manually. (Direct
   port of [sandbox-loader.php](file:///tmp/novamira-review/includes/sandbox-loader.php).)

### User Story 4 — Off by default, gated (Priority: P1)

The ability layer is inert until explicitly enabled, and every ability requires
auth + capability.

**Acceptance**:
1. With the layer disabled, the MCP endpoint exposes nothing and abilities 403.
2. Every ability's `permission_callback` requires a logged-in user **and**
   `manage_options` (Novamira's `novamira_permission_callback`), over an
   Application Password on HTTPS-or-`WP_ENVIRONMENT_TYPE=local` (our gotcha #1).

## Requirements

- **FR-1** Ship a Sandbox mu-plugin (`00-sandbox-abilities.php` + `sandbox-abilities/`
  payload) written into every instance's shared bind-mount during provisioning,
  alongside the existing mail/dl-cache/autologin mu-plugins
  (`_write_*_muplugin` pattern in the CLI).
- **FR-2** Bundle `wordpress/mcp-adapter` (vendored into the mu-plugin payload,
  not the user's plugin) and register an MCP server exposing only abilities with
  `meta.mcp.public = true`.
- **FR-3** Register core abilities: `sandbox/execute-php` (eval + capture, the
  Novamira `novamira_execute_php` implementation verbatim in spirit),
  `sandbox/read-file` / `write-file` / `edit-file` / `list-directory` (ABSPATH-
  jailed via a `resolve_path` that rejects symlink escape — Novamira's
  `novamira_resolve_path`). WP-CLI abilities are **deferred** — the Python MCP
  `wp_cli` + spec 004 already cover that surface from the host.
- **FR-4** Override `mcp-adapter/discover-abilities` to append Sandbox
  environment instructions (focused plugin, instance URL, snapshot reminder).
- **FR-5** Master enable flag, default **on for Sandbox instances** (they're
  disposable + local) but instance-scoped and toggleable: `./sb abilities
  on|off|status <instance>`. Requires WP 6.9+; on older WP the layer no-ops with
  a logged notice.
- **FR-6** Crash-recovery sandbox loader for persistent AI PHP, jailed to
  `wp-content/sandbox-code/`, with `.crashed` safe-mode + `?sb_safe_mode=1`.
- **FR-7** `execute-php` annotated `destructive:true, readonly:false,
  idempotent:false`; file/list reads annotated `readonly:true`.
- **FR-8** Connection helper: `./sb connect <instance>` + web-dashboard block
  emit the endpoint + app-password + per-client config (npx-mcp-remote / direct
  HTTP), like Novamira's Connect page.

## Design notes

- **Prefix** everything `sandbox_*` / `sandbox/` (ability names, options,
  hooks) per the plugin-code non-negotiables — never `novamira_*`.
- **License**: `wordpress/mcp-adapter` is GPL-compatible; vendoring it in our
  mu-plugin is fine. We are *not* copying Novamira's AGPL code — we re-implement
  the (small, mechanical) ability callbacks against the same public WP APIs.
- **Relationship to the Python MCP**: keep both. The Python MCP can even *proxy*
  to the in-instance endpoint for `execute-php` so existing Sandbox users get it
  through the familiar `mcp__sandbox__*` namespace (add a thin `wp_eval_live`
  tool that POSTs to the instance ability). Decide proxy-vs-direct in `plan.md`.
- **Herd**: the mu-plugin is host-file-based, so it works on herd unchanged; the
  MCP endpoint is just the herd `https://<instance>.test/wp-json/...` URL.

## Integration points

- CLI: new `abilities` + `connect` command modules
  ([sandbox/commands/](../../sandbox/commands/)); mu-plugin writer alongside
  `_write_mail_muplugin`.
- Provisioning: hook the writer into `cmd_up` / `cmd_install` / `apply` so it's
  idempotently (re)written, like the other mu-plugins.
- Docs: CLAUDE.md (new gotcha: abilities layer + the AGPL boundary), MCP-surface
  table, `docs/sandbox-config-reference.md`.

## Open questions (resolve in plan.md / via clarify)

1. Proxy through the Python MCP, or expose the instance endpoint directly to
   clients, or both? (Recommend: both — direct for portability, a proxy tool for
   in-session convenience.)
2. Do we want the file-CRUD abilities at all, given the Python MCP `fs_*` +
   native Read/Write already cover files? (Recommend: ship only `execute-php`
   first; add file abilities only if external-client parity demands it.)
3. Enable default — on (disposable instances) vs off (safety). Recommend on, with
   the flag + a clear "dev/staging only" banner.

## Tasks

1. mu-plugin scaffold + `wordpress/mcp-adapter` vendoring + enable flag/option.
2. `sandbox/execute-php` ability (eval + ob + error-handler + timeout + JSON-safe
   return), permission callback, MCP meta.
3. `discover-abilities` override with Sandbox instructions builder.
4. Crash-recovery sandbox loader + safe-mode notice + `?sb_safe_mode=1`.
5. `./sb abilities on|off|status`, `./sb connect`, provisioning hooks.
6. (Optional) Python MCP `wp_eval_live` proxy tool.
7. Live verification: connect Claude Desktop + Cursor to one instance; run
   `execute-php`; trip a fatal in a sandbox file and confirm safe-mode recovery.
8. Docs.
