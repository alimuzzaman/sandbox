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
  returning `{ok, passed, summary, output}`; the mode extension adds a resolved `mode` field.
- **Follow-ups:** `sandbox test` can still mutate the plugin's `composer.lock` when the lock is
  incompatible (acceptable, but note it) and leaves `.phpunit.result.cache`; both live in the
  plugin repo. The explicit `auto|unit|integration` selector and no-WP pure-unit runner are
  now implemented; a fresh external-plugin acceptance run remains separately protected.
- **Files:** `sb` (new `cmd_test` + subparser) + `server.py` (`run_tests` tool).
- **Do:** `sandbox test [auto|unit|integration] [-- <phpunit args>]`. Auto-detection is
  read-only and conservative: WordPress, mixed, unknown, or unsafe evidence selects
  integration; only Brain/Monkey-only evidence selects unit. Unit mode uses project
  Composer dependencies and PHPUnit without WP suite/DB/environment setup. `run_tests`
  accepts the same optional `mode` and adds the resolved mode to its existing result.
- **Verify:** see T1.4.

### [x] T1.4 — Validate against both shapes (acceptance)
- **Done (WP_UnitTestCase path proven green):** a smoke `WP_UnitTestCase` test on the
  disable-comments instance passed — `OK (3 tests, 4 assertions)` — exercising WP load, the
  plugin-under-test load, and the WP post factory against the isolated `wp_tests` DB, with
  **zero WP-testing deps in the plugin's composer** (sandbox supplied suite + phpunit +
  polyfills). The `run_tests` MCP tool returns the same green summary.
- **Findings / still open:** disable-comments' *own* suite is red — its `setUp()` lacks the
  `: void` the modern PHPUnit Polyfills require (a real, pre-existing plugin-test bug, not a
  harness issue). templately validation (its own instance + elementor) remains deferred;
  the pure Brain/Monkey (no-WP) runner is now covered by the feature's fixture gate.
- **Do:** focus templately → `sandbox test integration` runs its `tests/integration/**`
  `WP_UnitTestCase` tests **green**, with **zero edits** to the plugin and **without**
  `wp-phpunit` required in its composer (sandbox provides the suite). Focus disable-comments →
  `sandbox test unit` runs its Brain/Monkey tests green.
- **Verify:** both suites pass; dev DBs intact; `wp_test` MCP tool returns the same summary.

---

## Phase 2 — wp-env-grade ergonomics + catalog removal

### [x] T2.1 — `sandbox init`
- **Done:** `./sb init [--project-dir DIR] [--force] [--no-test-harness]` (`cmd_init`).
  Writes a native config — scaffolding the canonical schema (`sandbox.config.json`) for a
  bare dir, or **converting** an existing `.wp-env.json` (the resolved `pconf` already carries
  the mapped schema via `load_project_config`, so the DEFAULTS-key projection drops the private
  import-bookkeeping keys) — then `ensure_instance` + provisions the phpunit harness (extracted
  shared helper `_provision_test_harness`, reused by `cmd_test`). `--force` regenerates the
  **same** native file (preserves an existing `.yml`/`.yaml` rather than writing a shadowing
  `.json`). Idempotent: an existing native config is kept unless `--force`. Verified live on a
  bare fixture: scaffold → boot (`sbinit` @ :8192)
  → `./sb test` green (`OK (2 tests, 3 assertions)`, PHPUnit 9.6.34, zero composer deps in
  the plugin). The `.wp-env.json` convert mapping verified deterministically
  (`core`→`wpVersion`, privates dropped, `testsPort` ignored).
- **Note:** `init` boots, so it relies on `find_project_root` stopping at the plugin (its
  `.git` / config / `.wp-env.json`). A bare dir nested inside a parent git repo resolves to
  the parent — real plugin checkouts are their own repos, so this is the expected behavior.
- **Files:** `sb` (`cmd_init`, reusing `load_project_config`'s `.wp-env.json` mapping).
- **Do:** in a plugin dir, scaffold `sandbox.config.json` (or convert an existing
  `.wp-env.json`), then `ensure_instance` + `ensure_test_harness`. One command: bare checkout
  → running, testable stack.
- **Verify:** `cd a-fresh-plugin && sandbox init && sandbox test` works end to end.

### [x] T2.2 — Version knobs
- **Done:** version pins now resolve **server-aware** at compose time. `ensure_instance`
  stores `php_version`/`wp_version` on the instance block (not a pre-baked apache image);
  `resolve_instances` surfaces them; the new `_web_image(server, php, wp, explicit)` picks the
  right tag per stack — apache `wordpress:<wp>-php<php>`, nginx the `-fpm` flavor, litespeed
  `litespeedtech/openlitespeed:1.8.2-lsphp<php>` — and an explicit non-default `wordpress_image`
  still wins. The wpcli image follows the PHP pin too (`wordpress:cli-php<php>`) so phpunit runs
  on the project's PHP; the bare `wordpress:cli`/`wordpress:latest` defaults act as derive
  sentinels (no churn for unpinned instances — verified identical regen). `cmd_install` passes
  `--version=<wp>` to `wp core download` (the litespeed path, where core isn't baked in). The
  cloned test suite already matches `wpVersion` (T1.1).
- **Verified live:** `phpVersion: "8.1"` booted instance `sbphp81` with web image
  `wordpress:php8.1` → `php -v` = **PHP 8.1.34**, cli image `wordpress:cli-php8.1`, HTTP 200;
  unpinned `disable-comments` regenerated to identical `wordpress:latest`/`wordpress:cli`.
- **Bonus fix landed here:** `instance delete` now also drops the project→instance registry
  entry (`registry_remove`) — previously a stale "ready" record survived a delete.
- **Still open:** booting nginx/litespeed pinned instances was verified only via deterministic
  image-string generation (the apache acceptance path booted for real); a no-WP Brain/Monkey
  fast path for `tests` is still TODO (carried from T1.3/T1.4).
- **Files:** `sb` (`resolve_instances`, `_web_image`/`_cli_image`, the `_web_*` builders,
  `ensure_instance`, `cmd_install`, `cmd_instance` delete).
- **Do:** `phpVersion`/`wpVersion`/`core` resolve to the image tag
  (`wordpress:<wp>-php<php>`) / `wp core download --version` / lsphp version. Absent → stays
  `wordpress:latest`. The cloned test suite (T1.1) matches the resolved WP version.
- **Verify:** a config pinning `phpVersion: 8.1` boots an 8.1 container (`wp_exec php -v`).

### [x] T2.3 — Distribution: npm package + `sandbox` bin + brew
- **Done:** `package.json` (`@templately/sandbox`, bins `sandbox`+`sb`) + `bin/sandbox.js`
  (Node shim: finds python3, fails fast with an install hint, else execs the bundled `sb` —
  which is a polyglot shell+python file, so `python3 sb …` runs cross-platform incl. Windows).
  `files` is a secret-safe **allowlist** (so `*.local.yml`/`.env*`/`runtime/`/`.venv`s never
  ship even though `.npmignore` can't prune inside `files`-listed dirs); a `prepack` strips
  `__pycache__` and `!skills/sandbox-release/**` drops the maintainer skill. Homebrew formula
  at `packaging/homebrew/sandbox.rb` (depends `python@3.12`, `--HEAD` installable; tagged
  release fills `url`/`sha256` from `make-release.sh`). `packaging/README.md` documents all
  three channels. `./sb` and `install.sh` kept.
- **Verified:** `npm pack` → 80 files, zero secret/`.pyc`/`sandbox-release` leaks; installed to
  a throwaway prefix (`npm i -g <tgz> --prefix …`) → both `sandbox` + `sb` bins symlinked,
  `sandbox --help` and `sandbox init --help` run from outside the repo.
- **Still open:** brew `sha256` is a placeholder until a versioned tarball is published; the
  Phase 3 doc rewrite (T3.1) is where READMEs switch to the `sandbox` name everywhere.
- **Files:** `package.json`, `bin/sandbox.js`, `.npmignore`, `packaging/homebrew/sandbox.rb`,
  `packaging/README.md`.
- **Do:** npm bin execs the bundled Python `sb` via `python3` (fail fast if missing). Keep
  `./sb` as an alias. Keep `install.sh`.
- **Verify:** `npm pack` → install the tarball globally → `sandbox --help` runs.

### [x] T2.4 — Remove the central catalog + collapse to one MCP server
- **Scope (per the user):** NO migration into other repos. Remove the projects catalog AND
  the multi-instance *management* surface that doesn't fit the per-project model; keep the
  registry/`resolve_instances`/`instance=` routing (the new-model engine) and the dashboards
  (re-pointed at the registry). Net: `cd` into any WP plugin, point the MCP at it → it works;
  no catalog, nothing to pre-register.
- **Old code reference (user request — for revisiting):** the full pre-removal catalog +
  multi-instance code lives at commit **`25fc4094280f66ad78600548c003e7b7aea46dea`**
  (`git show 25fc409:sb`). Noted inline in `sandbox.yml` where `projects:` used to be.
- **Done:**
  - **Catalog removed:** deleted `cmd_projects`/`cmd_pick`/`cmd_use`/`cmd_add` + their helpers
    (`_install_picked_project`/`_resolve_plugin_github`/`_parse_pick_input`/`_project_for_plugin`/
    `parse_repo`/`persist_local_plugin`/`_drop_sandbox_claude_md`/`_web_projects`/
    `_web_available_plugins`, ~506 lines via an AST-boundary script) + their subparsers/handlers;
    removed `projects:` from `sandbox.yml`. Stripped every `cfg["projects"]` read.
  - **Re-pointed at the registry:** `cmd_doctor`/`cmd_update`/`cmd_status`/`collect_instance_rows`
    now read `registry_find_instance` (the project root) instead of the catalog/`.active-project`;
    `cmd_focus` dropped its catalog auto-link; `_onboard_instance`/`cmd_setup` install wp.org slugs.
  - **One MCP server:** `mcp_server_name()`→constant `sandbox`; `_build_mcp_entry()` is env-free
    (`<sb> mcp`, routes by `project_dir`); `register_claude_user_scope`/`write_claude_mcp_config`
    register/write ONE `sandbox` entry + clean stale `sandbox-<name>`/`wp-sandbox` (new
    `_stale_mcp_servers`); `cmd_uninstall` deregisters once + stale.
  - **`instance create` removed:** `cmd_instance` is delete-only (CLI rejects `create`, points to
    `./sb init`); subparser delete-only; the curses `n` key + web create action print a
    per-project pointer; `instance delete` no longer deregisters a per-instance MCP server.
- **Verified live:** `./sb --help` lists no `projects`/`pick`/`use`/`add` (has `init`); zero
  dangling refs; `./sb instances`/`doctor`/`status` show registry-backed project + a single
  `sandbox` MCP server; **fresh `./sb init` + `./sb test` → `OK` green** (core flow intact);
  `./sb instance delete` cleans containers/volume/dir/block/registry (no MCP deregister);
  `./sb instance create` is rejected.
- **Adversarial review (6 real findings, fixed):** `cmd_update` hard-died on `main` (never in
  the registry) → now graceful info+return like `status`/`doctor`; `claude_usage` per-instance
  attribution had collapsed onto `main` (MCP namespace is constant now) → re-pointed to attribute
  by each sandbox tool call's `project_dir` via the registry; stale `server.py` docstrings (old
  per-instance env-binding model) → rewritten to the single-server `project_dir` model.
- **Web dashboard follow-up (done):** the "New instance" form (which read the removed `projects`
  payload + POSTed to the rejected create action) was replaced with a per-project pointer page;
  dropped the dead `Project` type/field + form helpers across `create.ts`/`types.ts`/`state.ts`/
  `main.ts`; rebuilt the vendored bundle (`config/sandbox-web.js`, typecheck clean). `build-web-js.sh`
  runs `npm ci` itself, so no manual `node_modules` step was needed.
- **Files:** `sb`, `sandbox.yml`, `mcp/wp-server/server.py`, `src/web/*`, `config/sandbox-web.js`.

---

## Phase 3 — Documentation (post-refactor)

### [x] T3.1 — Rewrite docs to the shipped model
- **Done:**
  - **README.md** — full rewrite to the per-project model: install (curl/npm/brew) + the
    one-time single-server `claude mcp add --scope user sandbox -- ./sb mcp`; the
    `sandbox.config.json` + `cd plugin` → `init`/`ensure`/`test` flow; the test-harness pitch;
    the ~21-tool table (project_dir routing, `ensure_instance`/`run_tests`); instance management
    via the dashboards/`server`/`instance delete`. Kept the strong "Plain Claude vs sandbox"
    comparison. Removed all catalog/`./sb use|pick|add`/per-instance-MCP/`focus <plugin>` content.
  - **CLAUDE.md** — replaced the MCP-surface table (15→~21 tools, no `focus_set`/`focus_resolve`),
    the focus-singleton handshake (→ the `project_dir` handshake), the whole multi-instance section
    (→ per-project instances + dashboards/server-switch, no per-instance servers / `instance create`),
    the `${var}` `projects:` example, the catalog-based Common loops, and the folder layout.
  - **docs/sandbox-config-reference.md** — new: full schema (plugins/themes/mappings/php+wpVersion/
    multisite/server/config/port/tests), resolution order + override, server-aware version table,
    `.wp-env.json` import mapping, and where each field is consumed.
  - **server.py `instructions`** — verified current (2232 chars; `project_dir`/`ensure_instance`;
    no stale `focus_set`/`sandbox-<name>`/`instance create`/`projects`). No change needed.
- **Verify:** README's commands all map to shipped behavior (`init`/`ensure`/`test` exist; single
  `sandbox` registration; the config-reference link resolves); zero stale `./sb use|pick|add`,
  per-instance-MCP, or `focus <plugin>` refs remain in README/CLAUDE.md.
- **Files:** `README.md`, `CLAUDE.md`, `docs/sandbox-config-reference.md`.

---

## Done-definition for the whole rewrite

- One `claude mcp add --scope user sandbox -- sandbox mcp`; `cd` into any plugin →
  `sandbox test` runs its real tests with zero plugin edits and zero composer pollution.
- No `projects[]` catalog; instances are per-worktree, registry-tracked, on-demand.
- `.wp-env.json` still boots (import); `sandbox.config.*` is canonical.
