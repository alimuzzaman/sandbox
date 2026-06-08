# Sandbox rewrite — reference notes & backlog

Supporting material for [`sandbox-improvement-plan.md`](sandbox-improvement-plan.md).
Not the plan; not the task list. Two parts: **wp-env reference** (the facts the test
harness must replicate) and **backlog** (deliberately out of scope).

---

## Part 1 — wp-env reference (version-accurate to 11.2.0)

Read first-hand from the installed `@wordpress/env@11.2.0`
(`~/.nvm/.../@wordpress/env/lib/**`), not trunk.

### Single-site is the modern model

- `testsEnvironment` defaults to `true` (dev + tests) but is **deprecated**:
  `start.js:104-109` warns *"wp-env starts both development and tests environments by
  default. This behavior is deprecated… add `testsEnvironment: false`. The `env`,
  `testsPort`, and `testsEnvironment` options are also deprecated."*
- Templately's `.wp-env.json` already sets `testsEnvironment: false` → **one site**.
  The Playground runtime is single-environment only (`playground/index.js:48`).
- **→ Sandbox is single-site; tests run against a separate `wp_tests` DB.**

### How `WP_UnitTestCase` is provided (the mechanism to copy)

Even single-site, wp-env still:

1. **Clones** `WordPress/wordpress-develop` `tests/phpunit` (sparse, `--depth 1`, at the
   matching WP version tag) to a host dir (`download-wp-phpunit.js`).
2. **Mounts** it at `/wordpress-phpunit`, sets `WP_TESTS_DIR=/wordpress-phpunit` on the
   wp + cli services (`build-docker-compose-config.js:227,244`).
3. Installs phpunit **container-global**: `composer global require --dev
   phpunit/phpunit:"^5.7.21 || … || ^10.0"`, `~/.composer/vendor/bin` on PATH
   (`docker-config.js:231-232`).
4. The plugin `bootstrap.php` does `getenv('WP_TESTS_DIR')` → `require includes/functions.php`
   → `tests_add_filter('muplugins_loaded', loader)` → `require includes/bootstrap.php`
   → `WP_UnitTestCase`.

**None of this is in the plugin's composer.** The only piece wp-env does *not* provide is
`yoast/phpunit-polyfills` (confirmed: zero references) — the plugin supplies it + sets
`WP_TESTS_PHPUNIT_POLYFILLS_PATH`. **Sandbox provides the polyfills too**, so the plugin's
`require-dev` can be empty.

**Boundary:** libraries the test code `use`s/autoloads (Brain\Monkey, Mockery) still need
an autoloader the plugin bootstrap includes — a PATH binary doesn't cover those.

### Two test shapes in our codebase

| Plugin | Shape | Needs |
|---|---|---|
| `templately` | `WP_UnitTestCase` integration | WP suite + tests DB + bootstrap |
| `disable-comments` | Brain/Monkey pure unit | just phpunit (no WP install) |

### Templately's existing wiring (proven, reusable)

- `.wp-env.json`: `testsEnvironment: false`, `phpVersion: 8.1`, `core: …6.9.4`, `multisite: true`.
- `scripts/wp-test.js`: `wp-env run cli --env-cwd=…/templately -- vendor/bin/phpunit`,
  with `WP_TESTS_DIR=…/vendor/wp-phpunit/wp-phpunit`, `WP_PHPUNIT__TESTS_CONFIG=…`.
- `scripts/herd-test.js`: host `vendor/bin/phpunit` with the same env, DB host `127.0.0.1`.
- `phpunit.xml.dist`: `unit` + `integration` suites; bootstrap `tests/unit/bootstrap.php`;
  `WP_TESTS_PHPUNIT_POLYFILLS_PATH=vendor/yoast/phpunit-polyfills`.
- Sandbox's DB topology is `db`/`wp`/`wp` — matches neither Templately config, so sandbox
  must ship its own `wp-tests-config.php`.

### Sandbox today (validated against `sb` + `server.py`)

- **No test harness** — zero `phpunit`/`WP_UnitTestCase`/`wp-phpunit` refs in `sb` or
  `server.py`.
- MCP = FastMCP, `mcp.run()` stdio entry (`server.py:1226`); per-instance servers bake
  `SANDBOX_INSTANCE` (`server.py:54`, `_build_mcp_entry` `sb:2252`); every tool has an
  `instance` param resolved by `_resolve_instance` (`server.py:135`).
- Skills already served via tools (`load_skill`/`load_context`/`load_workflow`,
  `server.py:1037-1126`) — no file install today.
- Ports: `_next_free_port` socket-bind probe + persisted heal (`sb:3407/3423/3464`).
- Per-instance web tiers apache/nginx/litespeed via `wordpress_image` + `cmd_server`.

---

## Part 2 — Backlog (out of scope for the rewrite)

Real ideas explored earlier, deliberately deferred so the MCP-first per-project
test-harness rewrite stays focused. Pull into a future plan when wanted.

### Herd as a 4th `server:` option (host driver)

Expose `server: herd` backed by an internal `docker`-vs-`host` `ExecDriver` seam (host
`wp --path`, host `mysql`, host log path), reusing Templately's `herd-env.js`/`herd-test.js`
sequences. **Trade-offs:** loses apache/litespeed switching, LiteSpeed-cache testing,
volume snapshots, and Linux/CI (Herd is macOS/Windows-only). Docker stays canonical.
Switching docker↔herd is a re-provision, not a hot web-tier swap.

### Xdebug mode selection

`cmd_xdebug` (`sb:3053`) is already trigger-based + live-toggle (better than wp-env's
baked-in/rebuild model) but hardcoded `mode=debug`. Add
`sandbox xdebug on --mode debug,coverage,profile,trace` (+ `--always`); wire `coverage`
to `sandbox test --coverage`. Path mappings are trivial (bind-mount at identical host
path).

### Multisite knob

Honor `multisite` from the config → `wp core multisite-install` instead of `wp core install`.

### `.wp-env-port` file for host-run Playwright

The agent never needs it (the MCP server returns the URL from `ensure_instance`). Only a
plugin's *existing host-side* Playwright suite that reads `.wp-env-port` would — add an
opt-in `sandbox url --write`. Not default.
