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
    "health_path": "/",
    "startupTimeoutSeconds": 300,
    "recreateOnEnsure": true
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

`startupTimeoutSeconds` is the bounded time Sandbox waits for the declared
health endpoint after `ensure` (30–3600 seconds; default 120). Set
`recreateOnEnsure` only when the service boot command must reconcile source- or
lockfile-dependent state, such as dependencies in a named `node_modules`
volume. It force-recreates the service container while preserving its declared
volumes. If the health deadline expires, the durable result includes a bounded
tail of the declared service's Compose logs.

Generic Compose projects can also use the normal registered-remote workflow.
Their declaration must include the public service, internal port, and health path
shown above; `./sb deploy --project-dir /path/to/project --remote NAME --ensure
--expose --domain app.example.com --json` transfers the working tree, ensures the
remote Compose service, probes its health, and routes the declared service over
HTTPS. It does not activate a plugin or run WordPress URL updates.

During provisioning, Sandbox installs every configured plugin before activating
any of them. Active plugins are then ordered by their WordPress `Requires Plugins`
headers. Unrelated active plugins are skipped during plugin activation and all
subsequent theme provisioning commands. This keeps dependency activation
deterministic and prevents onboarding redirects from interfering with
non-interactive startup.

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

`SANDBOX_HOME` is the explicit, highest-priority location selector when it is non-empty.
Without it, both the CLI and MCP read the last verified **absolute** path from the
non-secret, owner-only bootstrap hint `~/.config/sandbox/home` written by
`./sb home <dir>`; a relative, blank, missing, or unreadable hint falls back to
`~/sandbox`. This selector is only a path hint: it never triggers registry migration,
merging, or target discovery. The `./sb home <dir>` command relocates the base and
records the selected path only after
verification, so subsequent CLI and MCP launches continue to agree without exporting
the variable in every shell. A normal first command automatically migrates old
repo/config-only state only when that selected base is empty; if both sides hold state,
Sandbox stops without merging or deleting either source.

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
    "query-monitor": false,    // wp.org, installed inactive; qm_capture activates it
    "mcp-adapter": "https://github.com/WordPress/mcp-adapter/releases/download/v0.5.0/mcp-adapter.zip",
    "elementor": true,         // wp.org, active
    "elementor-pro": { "path": "~/dev/elementor-pro", "onDemand": true }
  },

  // Themes to install; the FIRST entry is activated. A wp.org slug, a zip URL,
  // or a local path (symlinked in). (Themes stay a separate list.)
  "themes": [],

  // Version pins. null → wordpress:latest (no implicit pin). Quote them so
  // YAML/JSON don't coerce "8.1" to a float. LEAVE wpVersion null unless you
  // need one exact WordPress build — a pin is EXACT, so "7.0" is the 7.0.0
  // release, not the newest 7.0.x. See "Version pins" below.
  "phpVersion": null,   // e.g. "8.1" — resolves server-aware (see below)
  "wpVersion":  null,   // e.g. "6.4.3" — omit to track the current release

  // false | true | "subdirectory" | "subdomain". true = subdirectory (the
  // baseline that works on localhost:<port>). See "Multisite" below.
  "multisite": false,

  // Extra hostnames this instance also answers on. Bare hostnames only — no
  // scheme, port, path, or wildcard. See "Aliases" below.
  "aliases": [],

  // Web stack. apache/nginx/litespeed are docker (only the compose web tier
  // differs); "herd" is HOST-native (Laravel Herd + host MySQL — see below).
  "server": "nginx",    // nginx (default) | apache | litespeed | herd

  // Scoped local hostname policy. Omission defaults new identity to .test;
  // persisted .tst identities remain unchanged. .local is rejected for new
  // names and public suffixes are verify-only (never locally shadowed).
  // `ingress`/`strategy` omitted => the DEFAULT provider: Sandbox's own
  // Docker/Caddy proxy + Sandbox-owned DNS. Naming an adapter opts in to host
  // adoption; switch anytime with `./sb domains use <provider>`.
  // See docs/clean-url-default.md.
  "domains": {
    "enabled": false,
    "tld": "test",
    "hostname": null,
    "ingress": null,
    "strategy": null,
    "wildcard": false
  },

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
slug even from a git worktree whose directory name differs. Canonical keys must
be lowercase WordPress slugs (`a-z`, `0-9`, `-`, `_`) and cannot contain a path
or traversal segment. Object entries accept only `path`, `zip`, `source`,
`active`, and `onDemand`; their values are validated rather than coerced.

New `sandbox init` scaffolds include the current project (`"."`), Query Monitor
(installed but inactive until the first QM capture), and the official WordPress
MCP Adapter release zip by default. Replace the
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

On a remote host the same page is populated by mirroring the machine's Pro store
(`defaults.pro_plugins_home`, default `~/Sites/plugins-pro`) with
`./sb remote plugins <name>` — or automatically by `./sb deploy`. It copies the store
to `<remote $SANDBOX_HOME>/plugins-pro` and merges those slugs as bare paths into the
remote user-global catalog, so every instance on that host resolves them on demand.
See `docs/remote-hosting.md` → "Pro plugins on the remote host".

If an on-demand local path disappears after provisioning, it remains registered
so the install interception returns a clear local-source error. Sandbox never
falls back to downloading that configured slug from the registry. Re-provisioning
also reconciles declared plugins from active to inactive or on-demand by
deactivating that declared slug; unrelated user-installed plugins are untouched.

The generated Apache and Nginx/FPM stacks also reconcile ownership of
`wp-content/plugins` during bootstrap. This keeps the WordPress web user able to
create a new plugin directory for ordinary wp.org and wp-admin installs on the
bind-mounted development tree.

Generated local source binds are read-only inside every WordPress execution
service. This includes `defaults.plugins_home` and local plugin, theme, and
legacy mapping sources emitted as `extra_mounts`; the Nginx static sidecar uses
the same read-only source view. WordPress updater/editor writes to a local
source therefore fail by design, while edits made in the host checkout remain
visible immediately. WP.org and ZIP installs still write to the writable
runtime WordPress tree (`runtime/wp-<instance>`), including its `wp-content`
state, uploads, and cache directories; the shared download caches remain
writable as well.

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

### Version pins — pin PHP freely, pin WordPress deliberately

`wpVersion` is an **exact** version, never a version *line*: `"7.0"` installs the
7.0.0 release and stays there, it does **not** track 7.0.4. Leave it `null` (the
default) unless the work genuinely needs one exact build — reproducing a
version-specific report, or a regression bisect. Everything else (feature work,
plugin development, "match the user's stack") wants an unpinned, current
WordPress. `phpVersion` is different: PHP is a real compatibility variable and
pinning it costs nothing, so pin it whenever the target PHP matters.

A pin that is no longer wanted is not sticky: delete it and `./sb apply
--project-dir <DIR>` moves the live site to the current release (see below).

`phpVersion` maps to the right image **per server**; `wpVersion` never enters an
image tag at all:

| server | image (php pinned) | where `wpVersion` acts |
|---|---|---|
| `apache` | `wordpress:php<php>` | `wp core download --version=<wp>` at install |
| `nginx` | `wordpress:php<php>-fpm` | `wp core download --version=<wp>` at install |
| `litespeed` | `litespeedtech/openlitespeed:1.8.2-lsphp<php_nodot>` | `wp core download --version=<wp>` at install |
| `herd` | host PHP via `herd isolate php@<php>` (web) + `php<MM>` binary (CLI/phpunit) | host `wp core download --version=<wp>` at install |

The WP version is deliberately kept OUT of the image tag (the @wordpress/env
approach): a PHP-only base image plus an in-container core download avoids
`manifest unknown` errors for patch-level tags Docker Hub never published
(`wordpress:6.9.4-php8.1`), and keeps every server stack on ONE bootstrap path.

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

### WordPress PHP extension requirements

New WordPress scaffolds declare the immutable `wordpress@1` profile. Existing
projects that omit `phpExtensions` retain their previous images and behavior.

```json
{
  "phpVersion": "8.3",
  "phpExtensions": {
    "profile": "wordpress@1",
    "extensions": {
      "gd": true,
      "intl": "8.3.*"
    }
  }
}
```

Values may be `true`, `false`, an exact/`X.Y.*`/`php` version constraint, or
`{"state":"enabled|disabled","version":"..."}`. Unknown extensions and
unsupported provisioning/disable requests fail before the runtime is changed.
`wordpress@1` asserts curl, DOM, Exif, Fileinfo, Hash, JSON, Mbstring, MySQLi,
OpenSSL, PCRE, and XML, and selects the allowlisted GD child-image recipe when
neither GD nor Imagick is named. Sandbox pins official WordPress web and WP-CLI
parents by registry digest, builds content-addressed child images below
`$SANDBOX_HOME/runtime/build/php-extensions/`, and verifies the requirement in
web, WP-CLI, bounded-exec, and PHPUnit planes.

During `sb migrate --apply`, automatic first-run migration, or `sb home <dir>`,
the persisted extension requirement and digest metadata move with the other
pure state. The generated `runtime/build/php-extensions/` contexts are excluded
from that copy, removed from the old base only after the pure-data verification
passes, and recreated at the destination with the side-effect-free planner and
materializer for Apache/nginx instances. Migration never pulls parent images,
builds child images, or probes a runtime. Database volumes remain outside the
base, and existing uploads, snapshots, and project files are preserved
byte-for-byte.

Compose auto-provisioning is deliberately narrow in v1. GD, Intl, Zip, and the
other checked-in core recipes are supported for official Apache/nginx parents.
Imagick and Xdebug are observation-only for Compose: if requested but absent,
Sandbox returns `unsupported_provisioning` rather than invoking PECL or accepting
a package/URL. Managed-native may resolve Imagick only through its separately
approved signed-APT package plan. Generic Compose, LiteSpeed, Herd, Valet, custom
images, arbitrary packages, URLs, Dockerfiles, shell fragments, and unknown/global
INI mutation are never auto-modified by this field.

`sb status` / `sb status --json` and `sb doctor` / `sb doctor --json` use the
same canonical extension report and process result. JSON mode writes exactly one
document to stdout and exits nonzero after emitting it when an extension check
fails; a valid nonzero remote status document is forwarded with the same result.
The report includes the profile, catalog revision/digest, canonical requirements,
resolution digest, every web/WP-CLI/bounded-exec/PHPUnit observation, readiness,
drift, and staleness. A build digest appears only when its read-only cache receipt
is complete. Provenance is limited to recipe-catalog and parent digests plus
allowlisted recipe IDs; raw probe stdout/stderr, context paths, image URLs,
commands, shell fragments, and unrelated or arbitrary project values are never
reported.

Extension failures use the stable codes `missing`, `version_mismatch`,
`version_unobservable`, `unsupported_provisioning`, `unsupported_disable`, and
`plane_drift`. Projects that omit `phpExtensions` retain the legacy status shape.
`sb apply` rebuilds only
the WordPress web tier (`wp` plus nginx when selected); DB, Mailpit, uploads,
snapshots, and project files are preserved.
Status JSON omits credential-like fields and redacts `sandbox_autologin` values.

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
`ensure`. Both version pins apply: a `phpVersion` change lands because the web
tier is force-recreated against the new image, and a `wpVersion` change (or a
REMOVED pin) lands because apply reconciles WordPress core itself. See
**In-place reconcile (`sb apply`)** below.

## Aliases (extra hostnames)

`aliases` lists additional hostnames one instance answers on, alongside its
primary domain. The CDN case is the motivating one: point a CDN pull zone at
the instance, give it its own hostname, and let it fetch assets without the
origin redirecting it away.

Declaring an alias reaches four places, and it needs all four to actually work:

1. **Route.** The generated Caddyfile gets a site block per alias, reverse-
   proxying the same instance port as the primary domain. Aliases are routed
   even when the instance has no `.tst` domain — the proxy matches on `Host`.
2. **Certificate.** `./sb secure` mints ONE cert per instance, keyed by its
   primary domain, with every alias as an extra SAN. The alias site block reads
   the primary's cert files, so https covers every name at once.
3. **wp-config.** `WP_HOME` and `WP_SITEURL` are defined from the request host
   **when that host is a declared alias**, so WordPress serves the alias as
   itself rather than redirecting to the primary domain.
4. **Instance block.** The declaration is persisted into
   `sandbox.local.yml`, so it survives `sb apply` and travels to a remote with
   the project.

**Resolution is yours to arrange.** Only `.tst` names are wildcarded by the
sandbox resolver; any other alias needs an `/etc/hosts` entry locally, or a
real DNS record for a remote.

### What the host-aware URL does and does not do

The generated PHP matches `$_SERVER['HTTP_HOST']` against the declared alias
list and nothing else. Anything unrecognized — a spoofed `Host` header, the
instance's own primary domain, or wp-cli, which sets no `HTTP_HOST` at all —
leaves both constants undefined and WordPress falls back to the `home` and
`siteurl` options exactly as before. That is deliberate:

- It is **additive**. The primary hostname's behavior is unchanged, so adding
  an alias cannot break a working site.
- `HTTP_HOST` is attacker-controlled. Allowlisting it is what stops a forged
  `Host` from rewriting the URL WordPress prints into a password-reset mail.
- A request that arrives with a port in the `Host` (`cdn.example.com:8188`)
  does not match a bare alias, and falls back the same way.

The scheme comes from `X-Forwarded-Proto` when the direct `HTTPS` flag is
absent, because Caddy — and, on a remote, Cloudflare in front of it —
terminates TLS and forwards plain HTTP.

Because the constants outrank the DB options whenever they ARE defined,
`wp option update home` only ever affects the primary hostname. That is the
same relationship any `WP_HOME` constant has with the option.

### Not available for multisite or herd

Aliases resolve to an empty list on a multisite instance: a network already
maps hostnames to sites through `wp_site.domain`, and a second name for site 1
would fight that mapping rather than extend it. Use subdomain multisite's
wildcard route instead. Herd serves its own sites at `<name>.test` and never
routes through the sandbox proxy, so aliases do not apply there either.

### On a remote

`./sb deploy --expose` routes the primary domain first, then each alias, so a
bad alias never leaves the instance unreachable on its own hostname.
`--alias HOSTNAME` (repeatable) overrides the project declaration for a one-off.

Remote routes are per-hostname files, so changing `--domain` leaves the old
route serving. Deploy reports those as `stale_routes`; `--prune-routes` deletes
the ones that point at this instance's port and are neither the current domain
nor a declared alias. Pruning is opt-in because the inventory is read from the
whole host, and a route may belong to a checkout that this project's config
cannot see.

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

For a ready Docker instance, `sb ensure` first attests that every required web
plane has exactly the read-only self-bind source set generated from
`defaults.plugins_home` plus that instance's `extra_mounts`. Drift returns
`instance_mount_drift`; unavailable or malformed Docker inspection returns
`instance_mount_state_unavailable`. Both refuse before writing registry,
Compose, environment, snapshots, or project wiring. Run the explicit
`sb apply --project-dir <DIR>` / `apply_config` reconciliation after the state
is available; Herd has no Docker source-bind attestation.

After mount attestation and canonical HTTP reachability, `sb ensure` also runs
a bounded, read-only `wp core is-installed` gate. A successful result keeps the
ready fast path; only an empty `rc=1` result followed by a successful
`wp db query SELECT 1 --skip-column-names` is treated as uninstalled and falls
through to the existing install path using the current project configuration
and per-call overrides. Any output, malformed result, timeout, transport or
database failure returns `instance_install_state_unavailable` with
`mutated:false`, before port, Compose, registry, or project-wiring writes. A
changed `wpVersion` on an installed site remains apply-only; `ensure` only
warns about drift.

1. Rewrites the `instances.<name>` block in `sandbox.local.yml` from the
   current project config (constants, multisite flag, version pins, extra
   bind-mounts).
2. Regenerates the compose file and `compose up -d --force-recreate`s only the
   web tier. Constants survive via `WORDPRESS_CONFIG_EXTRA`; the DB volume is
   untouched, so **no data loss**. A `phpVersion` change takes effect here (the
   web image is recreated).
3. Re-syncs plugin/theme symlinks + installs (idempotent).
4. Runs `wp core multisite-convert` if multisite was **newly** enabled
   (idempotent — skips an already-converted network). Switching an existing
   multisite between subdirectory↔subdomain is **not** applied in place; that
   needs a recreate.
5. **Reconciles WordPress core** against the live `wp core version`:

   | config | live core | apply runs |
   |---|---|---|
   | `wpVersion: "6.8.2"` | `6.8.2` | nothing |
   | `wpVersion: "6.8.2"` | anything else | `wp core update --version=6.8.2 --force` (upgrade **or** downgrade) |
   | no pin | current release | nothing |
   | no pin | older | `wp core update` → current release |

   Then `wp core update-db` (`--network` on multisite), so the schema follows
   the files. Container recreates never re-version WordPress — core lives in the
   bind mount — so this step is the ONLY thing that moves a running instance off
   the core it was installed with. It is non-fatal: a wp.org failure warns and
   leaves the site on its current core. The result comes back as
   `wp_core: {from, to, changed}`.

   The web health gate probes the instance's canonical URL directly (without
   following redirects); 2xx–4xx responses count as reachable, while 5xx and
   transport failures retry within the bounded timeout.

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
tars uploads. Add `--db-only` to omit uploads; when forcing a DB-only overwrite,
any old uploads archive is removed. `./sb restore <name>` runs **`wp db reset --yes` before the
import**, so restore is a true point-in-time replacement: tables created
*after* the snapshot (e.g. multisite sub-site `wp_2_*` tables from an FSI run)
are dropped, not merged. `--add-drop-table` alone only drops tables present in
the dump, so without the pre-reset those newer tables would survive.

Each newly provisioned Docker instance captures a protected DB-only `@install`
baseline and a full `install-baseline` snapshot after final setup or plugin/theme
wiring. A successful onboarding seed refreshes those restore points to include its fixture.
`./sb snapshots` reports `@install` separately (it is a reset target, not a
normal named snapshot); use `./sb reset --yes` or MCP `wp_reset(confirm=true)`
to restore it. MCP `snapshot(name, db_only=true, project_dir=...)` captures a
named DB-only snapshot.

## Host ingress and clean URLs

Omitting `domains.ingress` selects the default provider, `sandbox-caddy` — Sandbox's own
Docker/Caddy proxy plus Sandbox-owned DNS — on every platform and for every runtime.
Naming any other adapter opts in to host adoption. `./sb domains use <provider>` writes the
machine-local selection and `./sb domains use` reports the active one; switching never
reprovisions and never changes the persisted hostname. `SANDBOX_CLEAN_URL_MODE` overrides
both for one shell. See [the clean-URL default](clean-url-default.md).

Host ingress is separate from the project resolver policy. The committed project pin, when
supported by the project schema, is an intent only; a gitignored machine-local override is
the effective choice when both are present. Pins name an ingress adapter (or `disabled`),
not a process, port, or private product database. An unavailable pin is reported as
`pin_unavailable`; Sandbox does not silently choose another incumbent.

```jsonc
// sandbox.config.json — portable intent
{ "domains": { "ingress": "system-caddy" } }

// sandbox.config.override.json — machine-local preference
{ "domains": { "ingress": "herd-valet" } }
```

Use `./sb domains ingress support --json` to inspect tiers and `detect`, `status`, or `plan`
to inspect listener ownership without mutation. `cleanup` and `reconcile` operate only on
an attributable, unchanged route record for the selected project/label. They retain a
minimal non-secret recovery record on drift or incumbent unavailability.

First route mutation requires interactive consent for the observed incumbent identity. MCP
and other non-TTY calls never prompt: they return `pending_consent` or
`pending_credentials` with a machine-local credential reference only. Do not place password
or token values in either project config, ingress-state JSON, output, or a recovery record.
The detailed listener and adapter rules are in [host-ingress.md](host-ingress.md).

The first enabled mutation candidate is `system-caddy` on Linux for exact HTTP hostnames
only. It requires an existing imported `/etc/caddy/conf.d/*.caddy` surface and the explicit
UID/network-root-scoped helper installation documented there. An HTTPS or wildcard request
returns the per-port fallback; it is never silently downgraded to HTTP.

## WordPress runtime selection

`wordpressRuntime` is separate from the durable-job `runtime` policy below. Omission keeps
the existing Compose backend. A committed project may declare version requirements, but a
native mode becomes active only when the same project has an explicit gitignored machine
override (normally `sandbox.config.override.json`).

```jsonc
// sandbox.config.override.json — machine-local and gitignored
{
  "wordpressRuntime": {
    "mode": "managed-native",          // compose | managed-native | incumbent-native
    "adapter": "ubuntu-nspawn",
    "php": "8.3",
    "database": "mariadb-10.11",
    "webServer": "nginx",              // nginx | apache
    "resources": {
      "cpu_percent": 200,
      "memory_bytes": 2147483648,
      "pids": 512,
      "runtime_seconds": 3600,
      "disk_bytes": 8589934592,
      "inodes": 500000,
      "fds": 4096,
      "connections": 512,
      "io_weight": 100
    },
    "egress": []
  }
}
```

Unknown keys and implicit native adapters are errors. `resources` are hard ceilings, not
hints. Source is read-only by default; no writable host path is inferred. Egress remains
deny-by-default. A scoped grant must name an instance owner, exact public CIDR and TCP ports
or an HTTPS hostname, expiry, and revocation state; unsafe or unproven grants stay closed.

Incumbent adapters use `mode: "incumbent-native"` and require an explicit user database
reference in the operation/local secret configuration. Passwords and tokens must not be
placed in `sandbox.config.json`, runtime ownership state, or committed overrides. Herd,
Valet, and POSIX status truthfully reports shared-host/lower isolation.

Mode/adapter changes are refused once an instance contains data. Export/recreate/import is a
separate future workflow; ordinary `ensure` and `apply` never migrate between runtimes.
Use `./sb native support --json` and `./sb native preflight --project-dir . --json` before
selection. See [native-runtime-isolation.md](native-runtime-isolation.md).

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

### Machine-local remote metadata

Each `remotes:` entry in `sandbox.local.yml` may include an optional lowercase
`provider` slug:

```yaml
remotes:
  myvps:
    provider: hetzner
```

This field is descriptive only. `sb remote list` displays the configured value, or
`unknown` when it is absent; it does not infer provider behavior, billing, or transport.
Because it is machine-local metadata, edit `provider` directly in `sandbox.local.yml`
when needed.

## Durable runtime policy

`runtime` is an optional project-level policy for explicit argv development and
test jobs. Without it Sandbox preserves local behavior. With a configured
provisioned remote, it can make remote execution the project default:

```jsonc
{
  "runtime": {
    "default": "remote",                 // local | remote
    "remote": "scaleway-sandbox",        // required for default remote
    "workspace": "default",              // reusable label
    "executionProfile": "unit",
    "outputProfile": "smart",
    "maxParallel": 4,
    "retentionDays": 7,
    "executionProfiles": {
      "e2e-long": {"timeoutSeconds": 21600, "stallSeconds": 900,
                    "cancelGraceSeconds": 60, "cleanup": "retain"}
    },
    "outputProfiles": {
      "sample-20": {"mode": "sampled", "everyLines": 20,
                    "heartbeatSeconds": 30}
    }
  }
}
```

For example, `./sb job-start --profile unit -- <argv>` selects the built-in
`unit` execution deadline profile for an explicit job.

Execution policy resolves in this order: explicit job fields, selected workspace
policy, project runtime policy, then the operation fallback (`exec`). `None` is
the only absence sentinel, so `--no-cancel-on-stall` is an explicit false
override rather than an instruction to inherit a profile's true value. A
workspace may choose an execution/output profile:

```jsonc
"workspaces": {
  "qa": {"executionProfile": "e2e-long", "outputProfile": "sample-20"}
}
```

Every profile has a finite timeout (at most seven days). Built-ins are `exec`,
`unit`, `integration`, `e2e`, `ci`, `overall`, and `overnight`; output built-ins
are `full`, `smart`, `errors`, `sampled`, and `quiet`. Explicit `--timeout`
overrides a profile. `--local` overrides a configured remote; `--remote NAME`
selects a named provisioned remote.

Accepted jobs retain the resolved profile, deadline source/reminder, stall
threshold, cancellation grace, cancel-on-stall value, cleanup policy, and field
provenance. Remote submission sends that frozen policy and requires
`job.execution-policy.v1` before staging; an older or unadvertised controller is
refused with reprovision/update guidance instead of applying its own config.
This source release adds the client check and advertised capability only; it is
not proof that any existing remote controller has been updated or reprovisioned.

Use `sb exec … --detach -- <argv>` for long work, then read `sb job-status` and
`sb job-output`. Output is durably retained on the execution host, not held in
an SSH/MCP child-process pipe. Persistent named workspaces are for development;
use isolated deterministic labels for parallel matrix cells, and reset/destroy
them explicitly when no active lease remains.

Remote CI uses the selected runtime remote when `ci run` omits `--local`. Its
outer deadline belongs to Sandbox, while the remote co-located `act` invocation
executes one workflow cell per durable child. A blocked preflight is a no-side-
effect result; pass each exact compatibility ID with `--accept-difference` only
when the semantic divergence is intentional and reviewable.

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
