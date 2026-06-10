# `sandbox.config.*` reference

A plugin repo becomes a **sandbox project** by carrying a `sandbox.config.json`
(or `.yml`) at its root. This is the per-project source of truth in the
MCP-first model — `cd` into the plugin and the tools (or `sandbox init` /
`sandbox ensure`) boot one instance for that directory, tracked in the registry.

There is **no central catalog**; each plugin self-describes here.

## Resolution order

For any project directory, the effective config is resolved as:

1. `sandbox.config.json` **or** `sandbox.config.yml` / `.yaml` (canonical, native)
   - `+ sandbox.config.override.{json,yml,yaml}` — gitignored, **deep-merged on top**
2. `.wp-env.json` — **import/fallback only** (mapped field-by-field; see below).
   `sandbox init` converts it to a native `sandbox.config.json`.
3. built-in defaults (below)

The project root is found by walking up from the directory to the nearest
`sandbox.config.*` / `.wp-env.json` / `.git`. Paths must live under `$HOME` (or a
`SANDBOX_PROJECT_ROOTS` entry) — `project_dir=/etc` is rejected.

## Schema

```jsonc
{
  // Plugins to set up in the instance.
  //   "."            → this repo (symlinked in + activated)
  //   "elementor"    → a wp.org slug (installed from wp.org + activated)
  //   "../addon"     → a path (symlinked in + activated)
  //   "https://…zip" → a zip URL (installed + activated)
  "plugins": ["."],

  // Themes to install; the FIRST entry is activated. Same entry forms as
  // plugins: a wp.org slug, a zip URL, or a local path (symlinked in).
  "themes": [],

  // Extra bind-mounts: wp-content path → absolute host path. Mounted (NOT
  // activated) — for private/Pro deps you don't want to install from wp.org.
  "mappings": { "wp-content/plugins/elementor-pro": "/abs/path/elementor-pro" },

  // Version pins. null → wordpress:latest (no implicit pin). Quote them so
  // YAML/JSON don't coerce "8.1" to a float.
  "phpVersion": null,   // e.g. "8.1" — resolves server-aware (see below)
  "wpVersion":  null,   // e.g. "6.4"

  // false | true | "subdirectory" | "subdomain". true = subdirectory (the
  // baseline that works on localhost:<port>). See "Multisite" below.
  "multisite": false,

  // Web stack. Only the compose web tier differs; DB/mailpit/wp-cli adapt.
  "server": "apache",   // apache | nginx | litespeed

  // wp-config.php constants, applied with their JSON types (bool/int/string/
  // null). See "wp-config constants" below.
  "config": { "WP_DEBUG": true },

  // Preferred WordPress port. null → auto-assigned from the free range.
  "port": null,

  // Test shape. "auto" detects WP_UnitTestCase (integration) vs Brain/Monkey.
  "tests": { "suite": "auto" }   // auto | unit | integration
}
```

All fields are optional; omitted fields take the defaults above.

### How version pins resolve (server-aware)

`phpVersion`/`wpVersion` map to the right image **per server**:

| server | image (php pinned) | image (wp+php pinned) |
|---|---|---|
| `apache` | `wordpress:php<php>` | `wordpress:<wp>-php<php>` |
| `nginx` | `wordpress:php<php>-fpm` | `wordpress:<wp>-php<php>-fpm` |
| `litespeed` | `litespeedtech/openlitespeed:1.8.2-lsphp<php_nodot>` | (WP via `wp core download`) |

The wp-cli container (where `sandbox test` runs composer + phpunit) follows the
PHP pin (`wordpress:cli-php<php>`), so tests execute on the project's PHP. The
cloned WP test suite also matches `wpVersion` (trunk when unpinned).

## wp-config constants (`config`)

Each `config` entry becomes a typed PHP `define()` rendered into the
instance's `WORDPRESS_CONFIG_EXTRA` compose env — on **both** the web tier and
the wp-cli service, so `wp eval`/tests see the same constants the site runs
with. Because the constants live in the generated compose file (not in
`wp-config.php`, which the official image's entrypoint regenerates from env on
every container start), they survive `sb down` / `sb up`. Sandbox defaults
(`WP_DEBUG_LOG`, `WP_DEBUG_DISPLAY`, `SCRIPT_DEBUG`, `WP_ENVIRONMENT_TYPE:
"local"`) apply first; project entries override them key-by-key.

Two special cases:

- `WP_DEBUG` maps to the `WORDPRESS_DEBUG` env var (the image defines the
  constant from it before the extra block runs). It defaults to **true** in
  the sandbox; set `"WP_DEBUG": false` to turn it off.
- On a **litespeed** instance the constants are additionally written as
  literals via `wp config set` (lsphp runs via suExec and can't read the
  container env; the OLS image doesn't regenerate `wp-config.php`, so the
  literals persist). This happens on install and on `./sb server <name>
  litespeed`.

Every define is `defined()`-guarded, so a literal constant already present in
`wp-config.php` never double-defines.

Config changes apply on the next instance **recreate** (like version pins) —
`recreate_instance` / `./sb instance delete` + `ensure`.

## Multisite

With `multisite: true` (or `"subdirectory"` / `"subdomain"`), provisioning
runs `wp core multisite-convert` after the single-site install, then:

- writes a marker file (`runtime/wp-<instance>/.sandbox-multisite`) that gates
  the network constants (`MULTISITE`, `SUBDOMAIN_INSTALL`,
  `DOMAIN_CURRENT_SITE` = the URL's host:port, …) inside
  `WORDPRESS_CONFIG_EXTRA`. The gate keeps the constants off until the network
  tables exist, and brings them back after every container restart. Deleting
  the marker drops the instance back to single-site mode.
- writes the WP network `.htaccess` (apache / litespeed; WordPress itself
  never writes it). nginx carries equivalent always-on rules in
  `config/nginx-sandbox.conf` (inert for single-site instances).

`true` means **subdirectory** — the baseline that works on
`localhost:<port>` with no wildcard DNS; sub-sites land at `/<slug>/`.
`"subdomain"` passes `--subdomains` and sets `SUBDOMAIN_INSTALL`, but
sub-site hosts (`<slug>.localhost` or a wildcard `.sb` domain) are NOT
resolved/proxied automatically yet — treat subdomain as a follow-up.

## `.wp-env.json` import mapping

When only a `.wp-env.json` exists, it's mapped onto the native schema:

| `.wp-env.json` | → `sandbox.config` |
|---|---|
| `core` (a `wordpress-X.Y.Z.zip` URL or bare version) | `wpVersion` (branches/other URLs → null = latest) |
| `phpVersion` | `phpVersion` |
| `plugins` | `plugins` |
| `themes` | `themes` |
| `mappings` | `mappings` |
| `config` | `config` |
| `multisite` | `multisite` |
| `port` | `port` |
| `testsPort`, `testsEnvironment`, `autoPort` | **ignored** (single-site model) |

`sandbox init` writes the converted result to `sandbox.config.json` so there's
one native source of truth (with `--force` it regenerates the same native file,
preserving an existing `.yml`).

## Where it's consumed

- `ensure_instance(project_dir)` / `sandbox init` / `sandbox ensure` — boot the
  instance using `plugins`/`mappings`/`server`/version pins/`config`.
- `run_tests(project_dir)` / `sandbox test` — provision the external WP harness
  at `wpVersion` and run phpunit in the wp-cli container at `phpVersion`.
- `focus_get(project_dir)` — returns the project's plugin + its `CLAUDE.md`.

Machine/global defaults (ports base, admin creds, image defaults) live in
`sandbox.yml`; per-machine overrides in the gitignored `sandbox.local.yml`.
