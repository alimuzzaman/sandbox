# Multi-instance support — implementation spec

Author: drafted during xspeed zip-test session 2026-06-02. Status: ready to implement, not yet implemented.

## Why

Today `./sb` manages exactly one WordPress install. To test a release zip in isolation (no plugin-pro interference, no dev symlink), the only options are: (a) clone the sandbox repo into a sibling dir, (b) use an external tool like `wp-now`. Both are workarounds.

Goal: native support for multiple isolated WordPress instances inside one sandbox checkout. Each instance has its own containers, DB volume, WP install dir, and port. The same `sb` CLI manages all of them. A `--instance` flag (default `main`) routes every command to the right stack.

## sandbox.yml schema additions

New top-level `instances:` block:

```yaml
runtime:
  # Existing block stays. Becomes the default for the `main` instance
  # when no instances: block is present (backwards compat).
  wordpress_port: 8188
  db_port: 3318
  mailpit_port: 8125
  admin: { user: admin, password: admin, email: admin@example.com, site_title: Sandbox }
  wordpress_image: wordpress:latest
  mariadb_image: mariadb:latest
  wpcli_image: wordpress:cli

instances:
  main:
    # Inherits ports/admin from runtime: block. Override per-instance here.
    project: xspeed       # which projects entry to wire (optional)
  qa:
    wordpress_port: 8288
    db_port: 3328
    mailpit_port: 8225
    project: xspeed-clean # different project = different plugin set
    admin:                # per-instance admin overrides allowed
      site_title: Sandbox QA
```

**Resolution rules:**
- No `instances:` block in yaml → synthesize `{main: {}}` from `runtime:` block.
- Each instance inherits unset fields from `runtime:`.
- `project:` is optional; if unset, instance uses whatever `./sb use` was last run for that instance.

## Compose generation

Replace the checked-in `docker-compose.yml` with a Python template that emits `runtime/compose/<instance>.yml` per instance at `./sb apply` / `./sb setup` time.

**Per-instance derived values:**
- Compose project name: `sandbox-<instance>` (passed via `-p` to every `docker compose` call)
- Volumes: `db_data_<instance>`
- WP bind-mount target: `runtime/wp-<instance>/`
- All three port mappings come from instance's resolved config

**Template skeleton** (`config/compose-template.yml.j2` or just a Python f-string in `sb`):

```yaml
name: sandbox-{instance}

services:
  wp:
    image: {wp_image}
    depends_on:
      db: {{ condition: service_healthy }}
    ports:
      - "{wp_port}:80"
    command: >
      bash -c "sed -i 's|AllowOverride None|AllowOverride All|g' /etc/apache2/apache2.conf
      && docker-entrypoint.sh apache2-foreground"
    environment:
      WORDPRESS_DB_HOST: db:3306
      WORDPRESS_DB_USER: wp
      WORDPRESS_DB_PASSWORD: wp
      WORDPRESS_DB_NAME: wp
      WORDPRESS_DEBUG: "1"
      WORDPRESS_CONFIG_EXTRA: |
        define('WP_DEBUG_LOG', true);
        define('WP_DEBUG_DISPLAY', false);
        define('SCRIPT_DEBUG', true);
        define('WP_ENVIRONMENT_TYPE', 'local');
    volumes:
      - ./runtime/wp-{instance}:/var/www/html
      - ./runtime/seeds:/seeds
      - {plugins_host}:{plugins_host}
      - ./config/php-sandbox.ini:/usr/local/etc/php/conf.d/zz-sandbox.ini:ro

  db:
    image: {db_image}
    environment:
      MARIADB_DATABASE: wp
      MARIADB_USER: wp
      MARIADB_PASSWORD: wp
      MARIADB_ROOT_PASSWORD: root
    ports:
      - "{db_port}:3306"
    volumes:
      - db_data_{instance}:/var/lib/mysql
    healthcheck:
      test: ["CMD-SHELL", "mariadb-admin --user=root --password=$$MARIADB_ROOT_PASSWORD ping --silent"]
      interval: 5s
      retries: 20

  mailpit:
    image: axllent/mailpit
    ports:
      - "{mailpit_port}:8025"
      - "1025"

  wpcli:
    image: {wpcli_image}
    depends_on: [wp]
    user: "33:33"
    working_dir: /var/www/html
    environment:
      WORDPRESS_DB_HOST: db:3306
      WORDPRESS_DB_USER: wp
      WORDPRESS_DB_PASSWORD: wp
      WORDPRESS_DB_NAME: wp
    volumes:
      - ./runtime/wp-{instance}:/var/www/html
      - ./runtime/seeds:/seeds
      - {plugins_host}:{plugins_host}
      - ./config/php-sandbox.ini:/usr/local/etc/php/conf.d/zz-sandbox.ini:ro
    entrypoint: ["wp", "--allow-root"]
    command: ["--info"]

volumes:
  db_data_{instance}:
```

Generation triggers: `./sb apply`, `./sb setup`, and lazily before any compose call if the file is missing or older than `sandbox.yml`.

## CLI refactor (`sb`)

### Module-level constants

Replace these:
```python
COMPOSE = ROOT / "docker-compose.yml"
ACTIVE  = ROOT / ".active-project"
FOCUS   = ROOT / ".focus"
WP_DIR  = ROOT / "runtime" / "wp"
PLUGINS_DIR = WP_DIR / "wp-content" / "plugins"
```

With helpers that take an instance:
```python
def compose_file(instance: str) -> Path:
    return ROOT / "runtime" / "compose" / f"{instance}.yml"

def wp_dir(instance: str) -> Path:
    return ROOT / "runtime" / f"wp-{instance}"

def plugins_dir(instance: str) -> Path:
    return wp_dir(instance) / "wp-content" / "plugins"

def active_project_file(instance: str) -> Path:
    return ROOT / f".active-project.{instance}"

def focus_file(instance: str) -> Path:
    return ROOT / f".focus.{instance}"
```

### Compose chokepoint

```python
def compose(*args: str, instance: str = "main", check=True, capture=False):
    return run(
        ["docker", "compose",
         "-p", f"sandbox-{instance}",
         "-f", str(compose_file(instance)),
         *args],
        check=check, capture=capture,
    )

def wpcli(args: list[str], *, instance: str = "main", check=True, capture=False):
    return compose("run", "--rm", "wpcli", *args,
                   instance=instance, check=check, capture=capture)
```

### Global flag

Add to top-level argparse:
```python
p.add_argument("--instance", default=None,
               help="Which sandbox instance to target (default: main, or "
                    "$SANDBOX_INSTANCE if set)")
```

Resolution: `args.instance or os.environ.get("SANDBOX_INSTANCE") or "main"`. Validate against `cfg["instances"]` keys; unknown → `die()`.

Stash on `args.resolved_instance` early in `main()` so every command function reads it from one place.

### Subcommands that need instance threading

Every command function gets the instance from `args.resolved_instance` and passes it to `compose()` / `wpcli()` / state-file helpers:

| Function | Notes |
|----------|-------|
| `cmd_up` | `compose("up", "-d", ..., instance=...)` |
| `cmd_down` | `compose("down", instance=...)` |
| `cmd_status` | Print active project + focus for the chosen instance. Add `--all` to print every instance side-by-side. |
| `cmd_logs` | `compose("logs", "-f", "wp", "db", instance=...)` |
| `cmd_shell` | `compose("exec", "wp", "bash", instance=...)` |
| `cmd_install` | Use resolved port for `--url`; write app password under `instances.<inst>.mcp.wp.application_password` |
| `cmd_focus` | Per-instance focus file |
| `cmd_use` | Per-instance active project |
| `cmd_wp` | Threads through |
| `cmd_seed` | Threads through |
| `cmd_visit` | Pass resolved port via env so visit.py hits the right WP |
| `cmd_snapshot` / `cmd_restore` / `cmd_snapshots` | Snapshots are per-instance; store under `runtime/snapshots/<instance>/<name>/` |
| `cmd_update` | Per-instance active project |
| `cmd_open` | Per-instance ports |
| `cmd_xdebug` | Per-instance container |
| `cmd_introspect` | Per-instance |
| `cmd_clean` | `--instance qa` cleans only qa. Bare `clean` requires `--all` (safety). |
| `cmd_setup` / `cmd_apply` | Iterate every instance defined; up + install each |
| `cmd_doctor` | Per-instance health, or `--all` |
| `cmd_pick` / `cmd_add` | Operate on resolved instance's plugin dir |

### New subcommand: `./sb instances`

```python
def cmd_instances(cfg, args) -> None:
    for name, inst in resolved_instances(cfg).items():
        running = is_running(name)  # docker compose -p sandbox-<name> ps -q
        active = read_text(active_project_file(name)) or "(none)"
        focus  = read_text(focus_file(name)) or "(none)"
        port   = inst["wordpress_port"]
        status = "● running" if running else "○ stopped"
        print(f"  {status}  {name:<10}  http://localhost:{port}  "
              f"project={active}  focus={focus}")
```

## State file conventions

- `.active-project.<instance>` (one per instance)
- `.focus.<instance>` (one per instance)
- Existing `.active-project` / `.focus` (no suffix) → on first run, move to `.active-project.main` / `.focus.main`. Leave symlinks at the old paths for one release.

## Auto-migration of `runtime/wp/`

In `cmd_apply` / `cmd_setup`, before generating compose files:

```python
old = ROOT / "runtime" / "wp"
new = ROOT / "runtime" / "wp-main"
if old.exists() and not old.is_symlink() and not new.exists():
    info(f"Migrating runtime/wp/ → runtime/wp-main/ (one-time)")
    old.rename(new)
    old.symlink_to(new.name)  # leave compat symlink for one release
```

Same for `runtime/snapshots/<name>/` → `runtime/snapshots/main/<name>/`.

## MCP server changes (`mcp/wp-server/server.py`)

Tools that gain an `instance: str = "main"` parameter:

- `wp_cli`, `wp_exec`, `wp_rest`, `db_query`, `tail_log`
- `fs_read`, `fs_write`, `fs_list`
- `mail_list`, `mail_get`
- `focus_set`, `focus_get`
- `activate_plugin`, `deactivate_plugin`
- `import_content`, `visit`
- `load_context`, `load_skill`, `load_workflow` (instance-agnostic — no change needed)

Routing helper:

```python
def docker_compose_args(instance: str) -> list[str]:
    return ["docker", "compose",
            "-p", f"sandbox-{instance}",
            "-f", f"runtime/compose/{instance}.yml"]
```

`wp_cli`, `wp_exec`, etc. all use this prefix instead of bare `docker compose exec wp ...`.

`focus_get` reads `.focus.<instance>` and the per-instance active project.

Update tool docstrings to mention the `instance` param and that it defaults to `"main"`.

Env vars (`WP_URL`, `MAILPIT_URL`, etc.) become a per-instance lookup: read `sandbox.yml` on each call and resolve from `instances.<instance>.wordpress_port` etc. Cache the parsed config in-process; invalidate when mtime changes.

## Migration / backwards compatibility

- Old yaml (no `instances:` block) → synthesize `{main: <runtime block>}`. Existing users see zero change.
- `./sb` with no `--instance` flag → resolves to `main`.
- `SANDBOX_INSTANCE=qa ./sb wp plugin list` → routes to `qa`.
- Old state files migrated lazily on first read.
- Old `runtime/wp/` directory migrated by `cmd_apply` (one-time, with compat symlink).
- The checked-in `docker-compose.yml` becomes redundant. Options: (a) delete it and only ship the generator, (b) keep it as a reference but tag it with a comment that `sb` ignores it. Pick (a) — it'll just rot.

## Tests / acceptance criteria

A PR ships when:

1. `./sb apply` on a yaml with no `instances:` block produces `runtime/compose/main.yml` and migrates `runtime/wp/` → `runtime/wp-main/`.
2. `./sb apply` on a yaml with both `main` and `qa` produces two compose files; `./sb up --instance qa` starts only qa containers; `./sb up --instance main` starts only main.
3. Both instances can run simultaneously without port clashes (verify with `docker ps`).
4. `./sb wp --instance qa plugin list` and `./sb wp --instance main plugin list` show independent plugin lists.
5. `./sb visit --instance qa http://localhost:8288/` and `--instance main http://localhost:8188/` both work.
6. `./sb instances` lists both with status + ports.
7. `./sb snapshot --instance qa baseline` creates `runtime/snapshots/qa/baseline/`.
8. MCP `wp_cli(command="plugin list", instance="qa")` routes correctly; without `instance`, routes to `main`.
9. `./sb clean` without `--all` errors when more than one instance exists; `./sb clean --instance qa` wipes only qa volume.
10. Smoke: install xspeed.zip in `qa` instance; verify `assets/admin.js` loads (no broken plugin-URL bug seen in the symlink-based main instance).

## Out of scope

- Per-instance MCP server registrations — keep one server with `instance` arg.
- Cross-instance operations (copy snapshot from main to qa, etc.) — punt to a follow-up.
- Sharing the same DB between instances — no. Isolation is the whole point.

## Implementation order (suggested commits)

1. **Compose generation + schema parsing.** New `runtime/compose/` dir; `apply` generates `main.yml` from default config. No behavior change yet (still uses checked-in compose).
2. **Cut over `compose()` / `wpcli()` to use generated `main.yml`.** Verify no regression on existing flows.
3. **Add `--instance` flag + thread through up/down/status/wp/install/seed/visit.** Default `main`.
4. **State files: per-instance active-project + focus, with one-time migration of old files.**
5. **Auto-rename `runtime/wp/` → `runtime/wp-main/`.**
6. **`./sb instances` subcommand.**
7. **Snapshot/restore/clean instance-aware.**
8. **MCP server: add `instance` arg to all tools.**
9. **Docs: CLAUDE.md "Common loops" + README + this spec → "implemented in commit X".**
10. **Smoke: spin up `qa` instance, install xspeed.zip, screenshot.**

Each commit should pass `./sb doctor` for the default `main` instance with no flags.

## Risks / things to watch

- **Container name collisions**: docker-compose project names must differ. Use `-p sandbox-<instance>` everywhere; never let it fall back to the default (which is the dir name).
- **`SANDBOX_PLUGINS_HOST` is shared across instances**: that's fine — both instances bind-mount the same plugin source dirs. But it means activating the same plugin in both instances will both follow the same symlink to the same git working tree. Document this.
- **`wp-content/plugins/` symlinks are per-WP-dir**: when `./sb add foo` runs against `main`, it links into `runtime/wp-main/wp-content/plugins/foo`. If you want the same plugin in `qa`, `./sb add foo --instance qa` (or `./sb use <project> --instance qa`).
- **App password is per-instance**: each instance gets its own WP install → its own user → its own application password. Store under `instances.<name>.mcp.wp.application_password` in `sandbox.local.yml`, not the flat `mcp.wp.application_password`.
- **MCP config rewrites**: `register_claude_user_scope` writes one MCP entry with env vars baked in. Since the single MCP server now talks to multiple instances, those baked env vars should point to `main`. The MCP server itself reads per-instance config from `sandbox.yml` for non-default instances.
- **`./sb setup` time multiplies by instance count**: each instance gets its own `wp core install`. Acceptable for 2; reconsider for 5+.
