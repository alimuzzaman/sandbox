# `sandbox.config.*` reference

## Recovery environment

`RECOVERY_RCLONE_DESTINATION` selects the configured rclone destination for read-only recovery
listing/verification. `RECOVERY_PASSPHRASE` is inherited only for protected capture operations;
never write it to config or pass it as a CLI/MCP argument.

A plugin repo becomes a **sandbox project** by carrying a `sandbox.config.json`
(or `.yml`) at its root. This is the per-project source of truth in the
MCP-first model — `cd` into the plugin and the tools (or `sandbox init` /
`sandbox ensure`) boot one instance for that directory, tracked in the registry.

There is **no central catalog**; each plugin self-describes here.

## Generic Compose projects

For non-WordPress projects, set `kind` to `compose` (the aliases `php`, `js`,
`javascript`, `node`, `docker`, `laravel`, `laravel-sail`, and `astro` are
normalized to the same framework-neutral adapter). The project owns its
Compose file and declares the public service and container port:

```json
{
  "kind": "compose",
  "framework": "laravel",
  "compose": {
    "file": "docker-compose.yml",
    "service": "laravel.test",
    "internal_port": 80,
    "health_path": "/"
  }
}
```

The adapter supports `ensure`, `status`, `start`, `stop`, `logs`, bounded argv
`exec`, `apply`, and non-destructive `destroy`. Sandbox writes only its
loopback port overlay under `$SANDBOX_HOME/runtime/projects/<instance>/`; it
validates the Compose service name, internal/host port range, health path, label
override, and descriptor roots before writing that overlay or invoking Docker.
does not rewrite the project's Compose file, infer or execute package scripts,
or remove project-owned volumes on destroy. WordPress-only tools (WP-CLI,
database, Mailpit, WordPress filesystem, abilities, snapshots) fail before
their side effects with a capability error.

Generic Compose projects can also use the normal registered-remote workflow.
Their declaration must include the public service, internal port, and health path
shown above; `./sb deploy --project-dir /path/to/project --remote NAME --ensure
--expose --domain app.example.com --json` transfers the working tree, ensures the
remote Compose service, probes its health, and routes the declared service over
HTTPS. It does not activate a plugin or run WordPress URL updates.

`sb init --type astro` is a convenience preset: it reads `package.json` and
the lockfile/configuration without executing project code, then writes an
explicit `sandbox.config.json` and `sandbox.compose.yml` for review. PHP,
Node/JavaScript, Docker-native, Laravel/Sail, Astro, and similar projects use
the same Compose adapter rather than separate framework runtimes.

## Resolution order

The loader discovers the common project descriptor and selects `kind` **before**
applying runtime defaults. Missing `kind` is compatibility shorthand for
`"wordpress"`; explicit WordPress and omitted-kind documents normalize the same.
WordPress-only fields are owned by the WordPress schema rather than the common
descriptor. New schemas register through `sandbox.config.registry` and must not
add kind branches to CLI, MCP, registry persistence, or `sandbox_core.py`.

`sandbox_core.load_project_config` remains a compatibility facade for existing
callers, not a new extension API. New application code consumes the descriptor and
runtime services from `sandbox.application.context`. Registry files likewise go
through the project-registry repository so locking, version checks, unknown-field
preservation, identity validation/backfill, and atomic replacement remain consistent.
Schema and adapter registrations also require non-empty, control-free identities,
unique owned kinds, and integer ordering values.

For any project directory, the effective config is resolved as (highest
priority last):

1. built-in defaults (below)
2. **user-global** — `~/.config/sandbox/config.json` (machine-wide; see below)
3. `sandbox.config.json` **or** `sandbox.config.yml` / `.yaml` (canonical, native)
   - `+ sandbox.config.override.{json,yml,yaml}` — gitignored, **deep-merged on top**
4. `.wp-env.json` — **import/fallback only** (mapped field-by-field; see below).
   `sandbox init` converts it to a native `sandbox.config.json`.

The project root is found by walking up from the directory to the nearest
`sandbox.config.*` / `.wp-env.json` / `.git`. Paths must live under `$HOME` (or a
`SANDBOX_PROJECT_ROOTS` entry) — `project_dir=/etc` is rejected.

### User-global config (`$SANDBOX_HOME/config.json`)

A machine-wide layer that applies to **every** project on this machine — declare
a shared dependency once instead of copying it into each repo's
`sandbox.config.override.json`. Same schema as a project config. Lives under the
per-user base `$SANDBOX_HOME` (default `~/sandbox`, spec 009), consolidated with
all other machine-state. `config.yml` / `.yaml` also work. Backward-compat: until
`./sb migrate --apply` runs, the legacy `~/.config/sandbox/config.json`
(honoring `$XDG_CONFIG_HOME`) is still read as a fallback.

It sits **under** the project in priority:

- **Scalars** (`phpVersion`, `server`, `port`, …): the project wins; the
  user-global value applies only where the project is silent.
- **Lists** (`plugins`, `themes`) and **dicts** (`mappings`,
  `mappings_inactive`, `config`): **UNIONED** — the project's entries are kept
  and the user-global entries are added (project wins on a dict-key clash).

The canonical use is a Pro plugin you always want available but **not** force-
activated — declare it once as `mappings_inactive` so any workspace's FSI /
imports can activate it on demand:

```jsonc
// ~/.config/sandbox/config.json
{
  "mappings_inactive": {
    "wp-content/plugins/elementor-pro": "~/dev/elementor-pro"
  }
}
```

> **Host paths must be absolute or `~`-anchored.** Relative paths in a
> project config resolve against that project's root; in the user-global file
> there is no single project root, so a relative path would resolve
> per-project and is almost never what you want.

Both the `sb` CLI and the MCP server read the merged config (via
`sandbox_core.load_project_config`), so the layer is picked up everywhere with
no per-tool wiring. Test override: point `SANDBOX_USER_CONFIG` at an explicit
file path.

## Schema

```jsonc
{
  // This project's own WordPress plugin slug. Optional, but recommended for
  // git worktrees because legacy plugins:["."] otherwise falls back to the
  // directory name. Canonical plugin-map keys remain authoritative.
  "slug": "my-addon",

  // Plugins — a slug-keyed MAP (the canonical form, spec 010). The KEY is the
  // authoritative install slug (worktree-proof). The value sets SOURCE and/or
  // STATE; see "Plugins (the slug-keyed map)" below for the full rules.
  //   "<slug>": true            → org, install + activate
  //   "<slug>": false           → org, install but inactive
  //   "<slug>": "."|"~/x"|"/abs"→ local source, active
  //   "<slug>": "https://…zip"  → zip source, active
  //   "<slug>": { "path"|"zip"|"source", "active", "onDemand" }  → full control
  "plugins": {
    "my-addon":  ".",          // this repo, active (slug = the key, not the dir)
    "query-monitor": true,     // wp.org, active by default in new scaffolds
    "mcp-adapter": "https://github.com/WordPress/mcp-adapter/releases/download/v0.5.0/mcp-adapter.zip",
    "elementor": true,         // wp.org, active
    "elementor-pro": { "path": "~/dev/elementor-pro", "onDemand": true }
  },

  // Themes to install; the FIRST entry is activated. A wp.org slug, a zip URL,
  // or a local path (symlinked in). (Themes stay a separate list.)
  "themes": [],

  // Version pins. null → wordpress:latest (no implicit pin). Quote them so
  // YAML/JSON don't coerce "8.1" to a float.
  "phpVersion": null,   // e.g. "8.1" — resolves server-aware (see below)
  "wpVersion":  null,   // e.g. "6.4"

  // false | true | "subdirectory" | "subdomain". true = subdirectory (the
  // baseline that works on localhost:<port>). See "Multisite" below.
  "multisite": false,

  // Web stack. apache/nginx/litespeed are docker (only the compose web tier
  // differs); "herd" is HOST-native (Laravel Herd + host MySQL — see below).
  "server": "nginx",    // nginx (default) | apache | litespeed | herd

  // Local domain TLD for the `./sb domains` proxy: instances serve at
  // <name>.<tld> (e.g. https://myplugin.tst). Default "tst". Avoid "sb"
  // (a real ccTLD) and "test" (owned by Herd/Valet).
  "tld": "tst",

  // wp-config.php constants, applied with their JSON types (bool/int/string/
  // null). See "wp-config constants" below.
  "config": { "WP_DEBUG": true },

  // Preferred WordPress port. null → auto-assigned from the free range.
  "port": null,

  // Test environment. auto selects unit only for unambiguous Brain/Monkey-only
  // evidence; unknown, mixed, and WordPress-marked projects use integration.
  "tests": { "suite": "auto" }   // auto | unit | integration
}
```

All fields are optional; omitted fields take the defaults above.

### Test modes

`sandbox test` accepts an optional `auto`, `unit`, or `integration` mode. An explicit
mode overrides `tests.suite`; otherwise `auto` performs bounded, read-only inspection
of project-local PHPUnit/Composer/test files. WordPress markers, mixed markers,
unknown projects, and unsafe paths fail closed to integration. Unit mode skips the
WordPress test suite, polyfills, isolated test database, and `WP_TESTS_*` environment;
integration mode retains the existing externally provisioned harness. `--provision-only`
is integration-only.

### Project slug

`slug` names the plugin represented by this project checkout. It is used only
for legacy self references such as `plugins: ["."]`; with `"slug": "my-addon"`,
that legacy entry installs the current checkout at
`wp-content/plugins/my-addon` even if the worktree directory is named
`my-addon-fix-123`.

The canonical `plugins` map does not need top-level `slug` because its key is
already the install slug:

```jsonc
{
  "plugins": {
    "my-addon": "."
  }
}
```

If `slug` is omitted, `plugins: ["."]` keeps its old behavior and uses the
project directory name.

## Plugins (the slug-keyed map)

`plugins` is a **map keyed by plugin slug** (spec 010). Each entry decouples two
orthogonal axes:

- **source** — where the code comes from: a wp.org/registry slug (default), a zip
  URL, or a **local path** (overrides org with your checkout).
- **state** — `active` · `inactive` · `on-demand` (lazy; not installed until
  something requests the slug).

The **key is the authoritative slug**, so a local source installs under the right
slug even from a git worktree whose directory name differs.

New `sandbox init` scaffolds include the current project (`"."`), Query Monitor,
and the official WordPress MCP Adapter release zip by default. Replace the
`plugins` field in a project config with a smaller map/list if that project
should not install those development helpers.

### Value shorthands

| Value | Means |
|-------|-------|
| `true` | state: **active** (source unset → resolved) |
| `false` | state: **inactive**, installed (source unset → resolved) |
| `"."` / `"~/x"` / `"../x"` / `"/abs"` | source: **local path** (state unset) |
| `"https://….zip"` | source: **zip** (state unset) |
| `{ "path"\|"zip"\|"source", "active"?, "onDemand"? }` | full control (exactly one source) |

A boolean sets **only state**; a string sets **only source**. "From org" is never
stamped by a shorthand — it's the **final fallback** when no layer set a source.

### Merge across layers (no clobbering)

The map is resolved by **normalize-then-field-merge**, never whole-value replace.
Each layer (user-global → project → `sandbox.config.override.json`) is normalized
to `{source?, active?, onDemand?}` with unset fields left UNSET, then merged
**per field** (a higher layer wins only on the fields it sets). So a machine
override that changes one plugin's source keeps every other plugin and field
intact — and `project: true` + `override/catalog: "<path>"` resolves to
**active, from that path** (both kept; org fallback not applied).

### User-global = source catalog

The user-global `$SANDBOX_HOME/config.json` is a machine-wide **source catalog**:
list every local checkout once.

```jsonc
// $SANDBOX_HOME/config.json
"plugins": {
  "templately":    "~/Sites/git/templately",      // source only → on-demand; NOT auto-enabled
  "elementor-pro": "~/dev/elementor-pro",
  "query-monitor": { "active": true }             // explicit active → force-on in EVERY instance
}
```

A bare path in the catalog **never enables** a plugin on its own — it only says
*where* the code is. A plugin is enabled for an instance only when the **project**
declares its slug (then its source resolves from the catalog), it's pulled
**on-demand**, or the catalog entry sets `active: true` explicitly.

### On-demand (lazy local plugins)

`{ "path": "…", "onDemand": true }` (or a catalog-only path) is **not installed**
at provision. When Templately Full Site Import, `wp plugin install <slug>`, or the
wp-admin "Add Plugin" flow requests that slug, the sandbox serves your **local
copy** (no download) via a mu-plugin (`plugins_api` + `upgrader_pre_download`,
zipping the local dir to a throwaway temp copy). A wp-admin screen — **Plugins →
Sandbox On-Demand** — lists on-demand plugins with a one-click "Install from
local" button.

The generated Apache and Nginx/FPM stacks also reconcile ownership of
`wp-content/plugins` during bootstrap. This keeps the WordPress web user able to
create a new plugin directory for ordinary wp.org and wp-admin installs on the
bind-mounted development tree.

The local runtime also sets WordPress `FS_METHOD` to `direct` and repairs the
parent `wp-content` directory during bootstrap. This prevents wp-admin and
Templately dependency installs from falling back to unavailable FTP/SSH
credentials when the bind mount starts with host-owned directories.

### Legacy keys (deprecated sugar)

The pre-010 keys still work, translated into the map at load time, preserving
their exact behavior — but emit a one-line deprecation hint:

| Legacy | Equivalent map entry |
|--------|----------------------|
| `plugins: [".", "slug", "/path", "…zip"]` | array form → install + activate |
| `mappings: { "wp-content/plugins/<slug>": "/p" }` | `"<slug>": { "path": "/p", "active": true }` |
| `mappings_inactive: { "wp-content/plugins/<slug>": "/p" }` | `"<slug>": { "path": "/p", "active": false }` |

Non-plugin `mappings` (other wp-paths, e.g. `wp-content/mu-plugins/…`) are
unchanged. If a slug appears in both a legacy key and the map, the **map wins**
(with a warning). Prefer the map for new configs; the legacy keys will be removed
in a later release once the map is proven.

> Per-plugin example configs live in each plugin repo
> (`sandbox.config.override.example.json`); migrate those to the map form, e.g.
> `{ "plugins": { "templately": "/Users/you/Sites/git/templately" } }`.

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
delete` + `./sb ensure`), `.tst` domains/`sb secure` (Herd owns `.test` TLS),
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
hosts are now **proxied** when the instance has a `.tst` domain: the generated
Caddyfile emits a wildcard site block `*.<name>.tst` alongside the apex
`<name>.tst` (both reverse-proxy the same instance port), so `sub1.<name>.tst`
serves the right sub-site. dnsmasq already wildcards `.tst`, so resolution needs
no extra step. When you `./sb secure` a subdomain-multisite instance, the cert
is minted with a `*.<name>.tst` SAN in addition to `<name>.tst`, so every
sub-site host is HTTPS-trusted by one cert. (Wildcards directly under `.tst` are
browser-rejected, but `*.<name>.tst` — one level deeper — is a valid SAN.)
Plain `localhost:<port>` subdomain multisite still has no per-sub-site hostname;
assign a `.tst` domain to host sub-sites.

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

## Hermes remote defaults

`sb hermes` stores no provider credentials in project configuration. It uses an
explicit configured `remote` from the existing `sandbox.local.yml` `remotes:`
mapping, and creates remote runtime metadata at
`$SANDBOX_HOME/runtime/hermes.json`. The remote account owns Hermes under
`$HOME/.hermes` and managed Git checkouts under
`$SANDBOX_HOME/hermes-repos`.

The generated profile runs the absolute remote `sb mcp` command with the same
`SANDBOX_HOME`, keeps MCP calls sequential, enables the complete Sandbox MCP
catalog, and uses manual terminal approval with dangerous cron commands denied.
See [hermes-agent.md](hermes-agent.md) for the operator workflow and trust
boundary.
