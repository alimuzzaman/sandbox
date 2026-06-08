# Sandbox MCP-first rewrite — implementation tasks

Follow this top to bottom. Plan: [`sandbox-improvement-plan.md`](sandbox-improvement-plan.md).
Reference: [`sandbox-notes.md`](sandbox-notes.md).

**Conventions for the implementing agent**
- Verify on the live stack, not by reading. A task is done when its **Verify** step
  passes against a running instance — not when the code "looks right".
- Touch only `sb`, `mcp/wp-server/server.py`, `docs/`, and new files. Do **not** edit
  `runtime/wp/`, `vendor/`, or generated compose.
- Keep one shared core: put reusable logic (config load, registry, ensure-instance,
  test-harness) in importable functions called by **both** the CLI and the MCP tools —
  don't duplicate.
- Don't `git commit`/`push` unless the user says so.
- Land docs in the same change as the code (except the Phase 3 README rewrite).

Legend: `[ ]` todo · `[~]` in progress · `[x]` done.

---

## Phase 0 — Per-project config + single MCP server + registry (backbone)

### [x] T0.1 — Per-project config loader
- **Files:** `sb` (new `load_project_config(project_dir)`), shared with `server.py`.
- **Do:**
  - Walk up from `project_dir` to find the project root (nearest `sandbox.config.json`
    / `sandbox.config.yml` / `.wp-env.json` / `.git`). Reject paths outside an allowlist
    (home / configured roots) — no `/etc`.
  - Load `sandbox.config.{json,yml}`; deep-merge `sandbox.config.override.{json,yml}` on top.
  - If neither exists, import `.wp-env.json` (map `core`→`wpVersion`, `phpVersion`,
    `plugins[]`, `themes[]`, `mappings{}`, `config{}`→`WORDPRESS_CONFIG_EXTRA`, `port`,
    `multisite`; ignore `testsPort`/`testsEnvironment`).
  - Defaults when versions absent: `wordpress:latest` (no implicit pin).
- **Verify:** `load_project_config` on the templately worktree returns its plugins +
  multisite from `.wp-env.json`; on a dir with a hand-written `sandbox.config.yml` returns
  that, with `.override` applied.

### [x] T0.2 — On-disk instance registry + create-lock
- **Files:** `sb` (new `registry.py`-style functions, or a section in `sb`); state file
  e.g. `runtime/registry.json`.
- **Do:** map `canonical project-root path → { instance, ports, server, status, wp_version }`.
  Reads/writes guarded by a `flock` lockfile per project root. Provide `registry_get(root)`,
  `registry_put(...)`, `registry_list()`, `registry_remove(root)`.
- **Verify:** two rapid `ensure_instance` calls for the same root produce **one** instance
  (second returns the first); registry survives process restart.

### [x] T0.3 — `ensure_instance(project_dir)` core (create-if-missing)
- **Done:** `ensure_instance` + `cmd_ensure` (`./sb ensure --project-dir DIR --json`) in `sb`;
  derives a per-dir instance name, picks ports, writes the instance block, boots via
  `cmd_up`/`cmd_install`, wires plugins/mappings from the project config, records in the
  registry. Verified: disable-comments booted on :8190 (54s), 2nd call returns in 1.2s,
  registry + reachability + plugin activation confirmed.
- **Follow-ups (scoped out, tracked):** (a) map the config's `config{}` constants →
  `WORDPRESS_CONFIG_EXTRA` (not yet applied — see T2.2); (b) when the project root is
  **outside** `defaults.plugins_home`, add its own bind mount or absolute symlinks 404
  in-container (see T0.4).
- **Files:** `sb` — extract today's `cmd_instance` create path (`sb:3754`) into a callable
  `ensure_instance(root, config)` returning `{ instance, url, ports, status }`.
- **Do:** if registry has a ready instance for `root` → return it. Else: pick free ports
  (`_pick_instance_ports`), generate compose, `up`, `install` (reuse `cmd_install`), wire
  plugins/mappings from config, set focus, persist to registry. Instance name derived from
  the dir (sanitised, deduped). Stream progress (for MCP: progress notifications).
- **Verify:** in a fresh plugin dir, `ensure_instance` boots a reachable
  `http://localhost:<port>` and records it; a second call returns instantly.

### [x] T0.4 — Collapse to a single MCP server; tools take `project_dir`
- **Done (hard cut):** removed `SESSION_INSTANCE` + the env-baked WP_URL/WP_APP_PASSWORD/
  MAILPIT_URL globals; added `_core()` + `_project_instance(project_dir)` resolving via the
  registry; converted every stack tool (`wp_cli`/`wp_exec`/`wp_rest`/`db_query`/`tail_log`/
  `fs_*`/`mail_*`/`activate_plugin`/`deactivate_plugin`/`import_content`) to a required
  keyword-only `project_dir`; added the `ensure_instance` tool (shells `./sb ensure --json`);
  rewrote `focus_get` to read the project root directly; **removed** the deprecated
  cross-instance `focus_set`/`focus_resolve`; `visit` drops the unused instance + passes admin
  creds. Fixed a latent `_compose` bug (missing `--project-directory` made relative WP mounts
  miss). Verified end-to-end against the live disable-comments instance (wp_cli/wp_rest/focus_get
  green; no-instance dir returns an actionable error).
- **Still open:** the arbitrary-root **bind mount** below (projects under `plugins_home` work;
  outside it needs the extra mount), and the 2 cosmetic `SESSION_INSTANCE` mentions left in
  docstrings (lines ~19, ~168).
- **Files:** `mcp/wp-server/server.py`.
- **Do:**
  - Drop the `SANDBOX_INSTANCE` env binding (`server.py:54`) as the routing mechanism.
  - Add a required `project_dir: str` param to every `@mcp.tool()` (wp_cli, wp_exec,
    wp_rest, db_query, tail_log, fs_*, mail_*, focus_*, activate/deactivate, import_content).
    Replace `_resolve_instance(instance)` calls with `_resolve_project(project_dir)` →
    registry → instance cfg (calls `ensure_instance` when missing, or errors clearly if the
    caller wants no auto-create — pick per tool; read-only tools should not auto-create).
  - Add new tools: `ensure_instance(project_dir, config?)`, `instance_status(project_dir)`,
    `stop_instance` / `destroy_instance`, `apply_config(project_dir, override?)`.
  - Keep `load_skill`/`load_context`/`load_workflow` as-is (already correct).
  - **Bind mount for arbitrary roots:** when a project root is outside
    `defaults.plugins_home`, the compose web/cli services must also mount that root at
    its identical host path (like the existing `{plugins_host}:{plugins_host}` mount),
    else the absolute plugin symlink 404s in-container. Add a per-instance mount derived
    from the registry's project root.
- **Verify:** with one registration, `wp_cli(project_dir=<templately>, command="plugin list")`
  hits templately's instance and `wp_cli(project_dir=<disable-comments>, …)` hits a different
  one — no `SANDBOX_INSTANCE` env set.

### [x] T0.5 — `sandbox mcp` CLI entrypoint + single registration
- **Done:** `./sb mcp` (`cmd_mcp`) execs the venv stdio server; verified as a real MCP server
  (initialize → "sandbox", tools/list → 20 tools incl. ensure_instance/wp_cli). Register with
  `claude mcp add --scope user sandbox -- ./sb mcp`.
- **Still open:** update the *auto*-registration (`register_claude_user_scope` /
  `write_claude_mcp_config`, `sb:2252+`) so `./sb setup` / `instance create` register the single
  `sandbox` server instead of per-instance `sandbox-<name>` ones. Manual registration works today.
- **Files:** `sb` (new `cmd_mcp` + subparser; update `register_claude_user_scope`/
  `_build_mcp_entry` `sb:2252-2386`).
- **Do:** `sandbox mcp` execs `MCP_VENV/bin/python mcp/wp-server/server.py` (stdio). Replace
  per-instance registration with **one** entry: `claude mcp add --scope user sandbox --
  sandbox mcp` (no `SANDBOX_INSTANCE`, no per-instance `.mcp.json`). Remove the per-instance
  `write_claude_mcp_config` fan-out.
- **Verify:** `claude mcp list` shows a single `sandbox`; `claude mcp get sandbox` connects;
  tools work from any directory.

### [x] T0.6 — Teach the handshake in `instructions`
- **Done:** rewrote `SANDBOX_INSTRUCTIONS` to the project-handshake (always pass `project_dir`;
  call `ensure_instance` first; one dir ↔ one instance) as part of T0.4.
- **Files:** `server.py` `SANDBOX_INSTRUCTIONS` (the `FastMCP(..., instructions=…)` baseline,
  `server.py:293`).
- **Do:** state: always pass `project_dir` = the project root if known, else cwd; call
  `ensure_instance` first when you need a URL; never invent an instance name.
- **Verify:** a fresh session, given only the server, can boot + query a plugin with no human
  hint beyond "test this plugin."

---

## Phase 1 — PHP test harness (the core value)

### [x] T1.1 — `ensure_test_harness(project_dir)`
- **Done:** `./sb test` provisions a cached, external harness under `runtime/`: sparse-clones
  `wordpress-develop` `tests/phpunit` (trunk for latest, tag for pinned `wpVersion`), downloads
  `phpunit.phar` (9.6.34) + `composer.phar` (UA-bearing download — phar.phpunit.de 403s the
  default UA), clones `yoast/phpunit-polyfills`. Idempotent (7.5s warm). Verified all artifacts.
- **Still open:** ensure container-global phpunit on PATH is replaced by mounting `phpunit.phar`
  at run time (T1.3); WP-version match uses trunk for "latest" pending a real version probe.
- **Files:** `sb` (core fn) + `server.py` (tool).
- **Do (per [`sandbox-notes.md`] mechanism):**
  - Clone `WordPress/wordpress-develop` `tests/phpunit` (sparse, depth 1, at the instance's
    WP version; latest/trunk when unpinned) into a cached host dir; mount at
    `/wordpress-phpunit`; set `WP_TESTS_DIR`.
  - Ensure container-global phpunit on PATH (or a sandbox-managed `vendor/bin/phpunit`).
  - Provide `yoast/phpunit-polyfills` + export `WP_TESTS_PHPUNIT_POLYFILLS_PATH`.
  - Idempotent + cached (don't re-clone if present at the right version).
- **Verify:** after running it, `wp_exec(container=wp, command="which phpunit")` resolves and
  `/wordpress-phpunit/includes/bootstrap.php` exists in the container.

### [x] T1.2 — Separate tests DB + sandbox `wp-tests-config.php`
- **Done:** creates the isolated `wp_tests` DB (+ grant) on the instance's mariadb via root
  (`MARIADB_ROOT_PASSWORD=root`); generates a sandbox `wp-tests-config.php` (host `db`, `wp_tests`,
  user `wp`, `wptests_` prefix) and places it inside the suite dir so the WP bootstrap
  auto-discovers it (confirmed via `includes/bootstrap.php` lines 6–16). Verified `wp_tests` exists.
- **Files:** `sb` core; a shipped `mcp/.../wp-tests-config.php` (or generated under `runtime/`).
- **Do:** create a `wp_tests` database on the `db` service; ship a config with `DB_HOST=db`,
  `DB_NAME=wp_tests`, `DB_USER=wp`, `DB_PASSWORD=wp`, `$table_prefix='wptests_'`,
  `WP_TESTS_DOMAIN`=instance host; expose via `WP_TESTS_CONFIG_FILE_PATH`.
- **Verify:** running the WP test installer creates `wptests_*` tables in `wp_tests`; the dev
  `wp` DB is **untouched** (check `db_query` table lists before/after).

### [x] T1.3 — `sandbox test` command + `run_tests` MCP tool
- **Done:** `./sb test [--project-dir DIR] [--provision-only] [-- <phpunit args>]` provisions
  the harness, runs `composer install` for the plugin's OWN dev deps (falls back to
  `composer update --no-plugins` when the lock is stale/PHP-incompatible, with
  `COMPOSER_HOME`/`COMPOSER_ALLOW_SUPERUSER`), then runs `php phpunit.phar` in the bind-mounted
  plugin dir with the suite (`/wordpress-phpunit`) + polyfills + `wp-tests-config.php`
  (defining `WP_TESTS_PHPUNIT_POLYFILLS_PATH`). Added the `run_tests` MCP tool (21 tools total)
  returning `{ok, passed, summary, output}`.
- **Follow-ups:** `sandbox test` mutates the plugin's `composer.lock` when the lock is
  incompatible (acceptable, but note it) and leaves `.phpunit.result.cache`; both live in the
  plugin repo. A `--testsuite unit|integration` selector and a no-WP (pure Brain/Monkey) fast
  path are still TODO.
- **Files:** `sb` (new `cmd_test` + subparser) + `server.py` (`run_tests` tool).
- **Do:** `sandbox test [unit|integration] [-- <phpunit args>]`. Auto-detect shape from the
  plugin's bootstrap (`WP_UnitTestCase` → run harness; Brain/Monkey → just phpunit at cwd).
  Exec `docker compose exec -w <plugin> wp phpunit -c phpunit.xml.dist <args>` with the env
  from T1.1/T1.2. Use `lsphp` on litespeed instances. `run_tests` returns
  `{ passed, failed, failures[] }`.
- **Verify:** see T1.4.

### [x] T1.4 — Validate against both shapes (acceptance)
- **Done (WP_UnitTestCase path proven green):** a smoke `WP_UnitTestCase` test on the
  disable-comments instance passed — `OK (3 tests, 4 assertions)` — exercising WP load, the
  plugin-under-test load, and the WP post factory against the isolated `wp_tests` DB, with
  **zero WP-testing deps in the plugin's composer** (sandbox supplied suite + phpunit +
  polyfills). The `run_tests` MCP tool returns the same green summary.
- **Findings / still open:** disable-comments' *own* suite is red — its `setUp()` lacks the
  `: void` the modern PHPUnit Polyfills require (a real, pre-existing plugin-test bug, not a
  harness issue). templately validation (its own instance + elementor) and a dedicated pure
  Brain/Monkey (no-WP) example are deferred.
- **Do:** focus templately → `sandbox test integration` runs its `tests/integration/**`
  `WP_UnitTestCase` tests **green**, with **zero edits** to the plugin and **without**
  `wp-phpunit` required in its composer (sandbox provides the suite). Focus disable-comments →
  `sandbox test unit` runs its Brain/Monkey tests green.
- **Verify:** both suites pass; dev DBs intact; `wp_test` MCP tool returns the same summary.

---

## Phase 2 — wp-env-grade ergonomics + catalog removal

### [ ] T2.1 — `sandbox init`
- **Files:** `sb` (`cmd_init`).
- **Do:** in a plugin dir, scaffold `sandbox.config.json` (or convert an existing
  `.wp-env.json`), then `ensure_instance` + `ensure_test_harness`. One command: bare checkout
  → running, testable stack.
- **Verify:** `cd a-fresh-plugin && sandbox init && sandbox test` works end to end.

### [ ] T2.2 — Version knobs
- **Files:** config loader + `ensure_instance`.
- **Do:** `phpVersion`/`wpVersion`/`core` resolve to the image tag
  (`wordpress:<wp>-php<php>`) / `wp core download --version` / lsphp version. Absent → stays
  `wordpress:latest`. The cloned test suite (T1.1) matches the resolved WP version.
- **Verify:** a config pinning `phpVersion: 8.1` boots an 8.1 container (`wp_exec php -v`).

### [ ] T2.3 — Distribution: npm package + `sandbox` bin + brew
- **Files:** new `package.json` + JS `bin/sandbox` shim; brew formula; rename docs to `sandbox`.
- **Do:** npm bin execs the bundled Python `sb` via `python3` (fail fast if missing). Keep
  `./sb` as an alias. Keep `install.sh`.
- **Verify:** `npm pack` → install the tarball globally → `sandbox --help` runs.

### [ ] T2.4 — Remove the central catalog + migrate
- **Files:** `sb` (`cmd_projects`/`cmd_pick`/`cmd_use`/catalog half of `cmd_focus`),
  `sandbox.yml`, dashboards (`cmd_dashboard`/`cmd_web` focus dropdowns).
- **Do:** delete `projects[]` from `sandbox.yml`; retire/repurpose the catalog commands to
  operate on the current dir's project; point dashboards at the registry. Provide a one-time
  migration that writes a `sandbox.config.*` into each previously-cataloged plugin repo
  (embedpress, xspeed, …, templately, disable-comments).
- **Verify:** `sandbox.yml` has no `projects:`; `sandbox start` in each migrated repo still
  boots; no command references the deleted catalog.

---

## Phase 3 — Documentation (post-refactor)

### [ ] T3.1 — Rewrite docs to the shipped model
- **Files:** `README.md` (full rewrite), `CLAUDE.md`, `server.py` `instructions` baseline,
  a new `sandbox.config.*` reference.
- **Do:** teach per-project config, the one-time `claude mcp add … sandbox mcp` registration,
  `cd plugin` → tools, `sandbox test`. Remove central/CLI-first descriptions. Only after
  Phases 0–2 are merged, so it documents shipped behavior.
- **Verify:** a new dev, following only the README, gets a plugin tested end-to-end.

---

## Done-definition for the whole rewrite

- One `claude mcp add --scope user sandbox -- sandbox mcp`; `cd` into any plugin →
  `sandbox test` runs its real tests with zero plugin edits and zero composer pollution.
- No `projects[]` catalog; instances are per-worktree, registry-tracked, on-demand.
- `.wp-env.json` still boots (import); `sandbox.config.*` is canonical.
