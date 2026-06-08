# Sandbox — MCP-first, per-project rewrite (plan)

Turn Sandbox into an **MCP-first, per-project** WordPress dev/test tool: a plugin
carries its own config, `cd plugin` → the agent drives a single stdio MCP server that
creates/boots an instance on demand and runs the plugin's **`WP_UnitTestCase` /
phpunit** tests — the one capability Sandbox lacks today that the team relies on from
`wp-env`/Herd.

- **Implementation steps:** [`sandbox-mcp-tasks.md`](sandbox-mcp-tasks.md) (follow this to build it).
- **Background + out-of-scope ideas:** [`sandbox-notes.md`](sandbox-notes.md).

---

## Decisions (locked)

| # | Decision |
|---|---|
| 1 | **Interface: MCP-first.** The MCP server is the product; the CLI shrinks to install / launch (`sandbox mcp`) / `doctor` / human+CI escape hatch. |
| 2 | **Transport: stdio.** Claude spawns the server per session; no daemon to keep alive. One stable registration: `claude mcp add --scope user sandbox -- sandbox mcp`. |
| 3 | **Config: per-project `sandbox.config.json` / `sandbox.config.yml`** (canonical, in the plugin repo) + gitignored `sandbox.config.override.{json,yml}`. `.wp-env.json` is **import/fallback only**. |
| 4 | **Instances per directory/worktree.** Registry keyed by canonical project-root path (one worktree = one instance), matching the Herd branch→site model. |
| 5 | **No central catalog.** The `sandbox.yml` `projects[]` catalog is removed; `sandbox.yml` keeps only machine/global defaults. |
| 6 | **Agent passes `projectDir`.** Required param on every tool (its project root if determinable, else cwd); the server discovers the root + reads the config. |
| 7 | **Tests externally provisioned.** Sandbox supplies the WP test suite, phpunit, and polyfills — the plugin's `composer.json` stays clean. |
| 8 | **Single-site.** No separate test environment; tests run against a separate `wp_tests` DB in the one install. |
| 9 | **Default version = `wordpress:latest`.** `phpVersion`/`wpVersion` are opt-in knobs (no implicit pinning). |
| 10 | **Install as npm package / global CLI** (`sandbox` bin), plus the curl installer + a brew tap. |
| 11 | **No installed skills.** Guidance rides on the MCP `instructions` + tool descriptions (+ optional MCP prompt); deeper guidance via the existing `load_skill` tool. |
| 12 | **README rewrite is a post-refactor task.** Document shipped behavior, not the plan. |

---

## Architecture

### One stdio MCP server, resolved per call

Today Sandbox registers **one MCP server per instance**, each baking
`SANDBOX_INSTANCE` into env (`server.py:54`, `_build_mcp_entry` in `sb:2252`). The
rewrite registers **one** `sandbox` server; every tool takes `projectDir` and
resolves the target instance from the **registry** per call. One stable registration,
no per-instance churn, no restart-to-reload.

```
claude mcp add --scope user sandbox -- sandbox mcp
```

### Per-project config (the source of truth)

A plugin repo carries `sandbox.config.json` **or** `sandbox.config.yml` (+ gitignored
`.override`). Schema:

```jsonc
{
  "plugins":   ["."],                  // this repo; sibling slugs/paths for addons
  "mappings":  { "wp-content/plugins/elementor-pro": "/abs/path" },
  "themes":    ["twentytwentyfour"],
  "phpVersion": null,                  // null → wordpress:latest default
  "wpVersion":  null,
  "multisite":  false,
  "server":     "apache",              // apache | nginx | litespeed (herd: backlog)
  "config":     { "WP_DEBUG": true },  // → WORDPRESS_CONFIG_EXTRA constants
  "tests":      { "suite": "auto" }    // auto-detect WP_UnitTestCase vs Brain/Monkey
}
```

`.wp-env.json` is read **only** when no `sandbox.config.*` exists (mapped field-by-field);
`sandbox init` converts it to a native file so there's one source of truth.

### Per-worktree instances + on-disk registry

`sandbox start` (or the first `ensure_instance`) in a plugin dir creates an instance
**keyed by the canonical project-root path** and tracks it in an **on-disk registry**
(`runtime/registry.json` or similar) — `{ projectRoot → { instance, ports, status } }`,
guarded by a `flock` create-lock so concurrent sessions don't race. Sibling plugins in
the same config share that one instance.

### projectDir handshake

The server is a separate process; it can't see the agent's `cd`. So `projectDir` is a
**required** param, the server walks up to find `sandbox.config.*` / `.wp-env.json` /
`.git`, and **allowlists the path** (no `projectDir=/etc`). The `instructions` baseline
(`FastMCP(..., instructions=…)`, `server.py:293`) teaches this.

---

## The test harness (the core value)

Mirror wp-env's mechanism, but Sandbox-owned so the plugin's composer stays clean:

1. **WP suite:** clone `WordPress/wordpress-develop` `tests/phpunit` (sparse, depth 1,
   at the WP version) → mount → `WP_TESTS_DIR`. Defines `WP_UnitTestCase`.
2. **phpunit:** install container-global on PATH (wp-env's `docker-config.js:232` trick).
3. **polyfills:** sandbox-provided + `WP_TESTS_PHPUNIT_POLYFILLS_PATH` (so even this isn't
   in the plugin's composer).
4. **tests DB:** a separate `wp_tests` database + `wptests_` prefix — **never** the dev
   DB (the installer drops every prefixed table).
5. **config:** a sandbox-owned `wp-tests-config.php` matching the sandbox stack
   (`DB_HOST=db`, `DB_NAME=wp_tests`, `DB_USER=wp`), exposed via `WP_TESTS_CONFIG_FILE_PATH`.
6. **run:** `docker compose exec -w <plugin> wp phpunit -c phpunit.xml.dist`.

Support **both** shapes (auto-detected): `WP_UnitTestCase` integration (templately —
needs suite + DB) and **Brain/Monkey** pure-unit (disable-comments — no WP). Surfaces as
`sandbox test [unit|integration]` and the `wp_test` MCP tool (returns pass/fail + failing
names) — the agent runs tests as live evidence.

---

## Distribution

`sandbox` ships as: (a) an **npm package** whose bin shims to the bundled Python `sb`
(`npm i -g`; needs Python 3, fail fast); (b) the existing curl `install.sh`; (c) a brew
tap. Rename the entrypoint to `sandbox` (keep `./sb` alias). A full Node rewrite of `sb`
is out of scope.

---

## Out of scope (moved to [`sandbox-notes.md`](sandbox-notes.md) → Backlog)

Herd as a 4th `server:` option · Xdebug mode selection (coverage/profile/trace) ·
multisite knob · `.wp-env-port` file for host-run Playwright. All real, none required
for the MCP-first per-project test-harness rewrite.
