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

  // Web stack. apache/nginx/litespeed are docker (only the compose web tier
  // differs); "herd" is HOST-native (Laravel Herd + host MySQL — see below).
  "server": "apache",   // apache | nginx | litespeed | herd

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
| `herd` | host PHP via `herd isolate php@<php>` (web) + `php<MM>` binary (CLI/phpunit) | WP via host `wp core download` |

The wp-cli container (where `sandbox test` runs composer + phpunit) follows the
PHP pin (`wordpress:cli-php<php>`), so tests execute on the project's PHP. The
cloned WP test suite also matches `wpVersion` (trunk when unpinned). On `herd`,
`phpVersion` is **authoritative for both tiers**: the web tier is pinned via
`herd isolate php@<v> --site <instance>` (run after `herd secure`, then verified
against `herd isolated` and retried once), and CLI + phpunit run the
version-specific Herd binary (`8.1` → `<Herd bin>/php81`). The generic host
`php` and `herd which-php` report Herd's *default* version, not the isolated
one, so resolving the `php<MM>` binary from the pin is what makes `sb wp …`,
`sb test`, and the MCP `wp_cli`/`wp_exec` honor `phpVersion`. Unpinned (or a PHP
Herd doesn't ship) falls back to the default host `php` rather than aborting.

## Host driver (`server: "herd"`)

`server: "herd"` provisions the instance on **host PHP via Laravel Herd + host
MySQL (DBngin)** instead of Docker — same `sandbox.config.json`, same `sb`/MCP
toolchain. It's a per-machine choice, so it belongs in the gitignored
`sandbox.config.override.json` rather than the shared config. Docker stays
canonical; macOS/Windows only (wherever Herd runs).

What provisioning does: the WP install lives at the usual
`runtime/wp-<instance>/`, served by `herd link` at `https://<instance>.test`
(`herd secure` runs automatically; `herd isolate php@<v> --site <instance>`
applies the `phpVersion` pin to the web tier — run AFTER secure so the site is
registered, then verified+retried). The database is `sandbox_<instance>` on host
MySQL (`127.0.0.1:3306`, `root`, no password — override via
`SANDBOX_HERD_DB_HOST/PORT/USER/PASSWORD`; Herd CLI path via
`SANDBOX_HERD_CLI`). Because the WP dir is the canonical `runtime/wp-<i>`,
`tail_log` / `fs_*` / plugin+mapping symlinks work unchanged.

Same end-state as docker: `config` constants (pinned literal via
`wp config set` — the host wp-config is stable, nothing regenerates it),
`multisite` (`multisite-convert`, constants written literally), `themes`,
`plugins`, `mappings`, app password, autologin. `sb test` runs the SAME cached
WP suite + phpunit.phar on the **pinned** host PHP against a per-instance tests
DB (`sandbox_<instance>_tests`). The MCP tools (`wp_cli`, `db_query`, `wp_exec`,
`tail_log`, …) route to the host transparently and also honor the pin —
`wp_cli` runs `<php<MM>> <wp.phar>`, and `wp_exec` prepends a per-instance shim
dir (`runtime/herd-shims/<instance>/`) to PATH so bare `php`/`wp`/composer
resolve to the pinned version. `sb apply --project-dir` reconciles in place
(re-pins constants instead of recreating a web tier).

Not supported on herd (v1): snapshots/restore, Xdebug toggling, Mailpit
capture (`mail_list` stays empty — no mailpit host), `./sb server` hot
switching (docker↔herd is a re-provision: change `server` + `./sb instance
delete` + `./sb ensure`), `.sb` domains/`sb secure` (Herd owns `.test` TLS),
and subdomain-multisite sub-hosts. `./sb instance delete` tears down fully:
drops both host DBs, `herd unisolate` + `unsecure` + `unlink`, removes the WP
dir and the `runtime/herd-shims/<instance>/` shims.

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

Config changes apply **in place** with `./sb apply --project-dir <DIR>` (MCP:
`apply_config`) — it re-renders compose and recreates only the web tier, so the
new constants take effect **without dropping the DB or uploads**. This is the
non-destructive alternative to `recreate_instance` / `./sb instance delete` +
`ensure`. A changed `wpVersion` is reported but not applied by `apply`
(swapping core under a live DB needs an explicit recreate); a `phpVersion`
change *does* apply because the web tier is force-recreated against the new
image. See **In-place reconcile (`sb apply`)** below.

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

`"subdomain"` passes `--subdomains` and sets `SUBDOMAIN_INSTALL`. Sub-site
hosts are now **proxied** when the instance has a `.sb` domain: the generated
Caddyfile emits a wildcard site block `*.<name>.sb` alongside the apex
`<name>.sb` (both reverse-proxy the same instance port), so `sub1.<name>.sb`
serves the right sub-site. dnsmasq already wildcards `.sb`, so resolution needs
no extra step. When you `./sb secure` a subdomain-multisite instance, the cert
is minted with a `*.<name>.sb` SAN in addition to `<name>.sb`, so every
sub-site host is HTTPS-trusted by one cert. (Wildcards directly under `.sb` are
browser-rejected, but `*.<name>.sb` — one level deeper — is a valid SAN.)
Plain `localhost:<port>` subdomain multisite still has no per-sub-site hostname;
assign a `.sb` domain to host sub-sites.

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

## In-place reconcile (`sb apply` / `apply_config`)

`./sb apply --project-dir <DIR>` (MCP tool `apply_config`) reconciles a
**running** instance with its current `sandbox.config.*` **without dropping the
database or uploads**. Use it after editing config — toggling a constant
(`TEMPLATELY_DEV_API`, `WP_DEBUG`), adding a plugin/theme, or enabling
multisite. It:

1. Rewrites the `instances.<name>` block in `sandbox.local.yml` from the
   current project config (constants, multisite flag, version pins, extra
   bind-mounts).
2. Regenerates the compose file and `compose up -d --force-recreate`s only the
   web tier. Constants survive via `WORDPRESS_CONFIG_EXTRA`; the DB volume is
   untouched, so **no data loss**. A `phpVersion` change takes effect (the web
   image is recreated); a `wpVersion` change is **reported but not applied**
   (core swaps under a live DB are left to an explicit `recreate_instance`).
3. Re-syncs plugin/theme symlinks + installs (idempotent).
4. Runs `wp core multisite-convert` if multisite was **newly** enabled
   (idempotent — skips an already-converted network). Switching an existing
   multisite between subdirectory↔subdomain is **not** applied in place; that
   needs a recreate.

Contrast with `recreate_instance` (destroy + re-boot — wipes DB + uploads) and
bare `./sb apply` with no `--project-dir` (the legacy alias for `./sb setup`,
which re-applies the sandbox's own `sandbox.yml`).

## Captured mail (Mailpit)

Every instance runs a Mailpit container (SMTP `1025` internally, web UI on the
instance's `mailpit_port`). A generated mu-plugin
(`runtime/wp-<instance>/wp-content/mu-plugins/00-sandbox-mail.php`) routes **all**
PHP mail there: on `phpmailer_init` it switches PHPMailer to SMTP at
`mailpit:1025`, and via `wp_mail_from` it replaces WordPress's default
`wordpress@localhost` sender (which PHPMailer rejects as an invalid address — no
TLD) with `wordpress@sandbox.test`. The mu-plugin lives in the shared
`runtime/wp-<instance>` bind-mount, so it captures mail from **both** the web
tier and the wp-cli tier (CLI / cron / tests). It is (re)written on every
`sb up` and on `sb install`, so it survives `sb down` / `sb up`. Read captured
mail with the MCP `mail_list` / `mail_get` tools, or the Mailpit web UI.

Without it the official `wordpress` image's `sendmail` is absent, so
`wp_mail()` silently returns `false` and nothing reaches Mailpit.

## Snapshot / restore

`./sb snapshot <name>` exports the DB (`wp db export --add-drop-table`) and
tars uploads. `./sb restore <name>` runs **`wp db reset --yes` before the
import**, so restore is a true point-in-time replacement: tables created
*after* the snapshot (e.g. multisite sub-site `wp_2_*` tables from an FSI run)
are dropped, not merged. `--add-drop-table` alone only drops tables present in
the dump, so without the pre-reset those newer tables would survive.

## Where it's consumed

- `ensure_instance(project_dir)` / `sandbox init` / `sandbox ensure` — boot the
  instance using `plugins`/`mappings`/`server`/version pins/`config`.
- `apply_config(project_dir)` / `sandbox apply --project-dir` — reconcile a
  running instance with its config in place (no data loss).
- `run_tests(project_dir)` / `sandbox test` — provision the external WP harness
  at `wpVersion` and run phpunit in the wp-cli container at `phpVersion`.
- `focus_get(project_dir)` — returns the project's plugin + its `CLAUDE.md`.

Machine/global defaults (ports base, admin creds, image defaults) live in
`sandbox.yml`; per-machine overrides in the gitignored `sandbox.local.yml`.
