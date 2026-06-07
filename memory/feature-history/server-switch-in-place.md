# Feature: switch an instance's web server in place (`./sb server`)

Shipped via build-feature. Adds `./sb server <name> <apache|nginx|litespeed>` —
mutate an instance's `server` field, regen compose, recreate the web tier on the
same URL/port/DB/content. No new instance needed to test another stack.

## The non-obvious finding (worth remembering)

**OpenLiteSpeed's lsphp does NOT inherit the container environment.** The
official `wordpress` image (apache/nginx) exports `WORDPRESS_DB_*` to php-fpm,
and the default `wp-config.php` reads creds via
`getenv_docker('WORDPRESS_DB_*', <default>)`. Under OLS, lsphp runs via suExec
and `getenv()` returns empty — so the moment you switch an apache-built instance
to litespeed, WP 500s with "Error establishing a database connection."

Adding `WORDPRESS_DB_*` to the litespeed `wp` compose service does **NOT** fix it
(verified: the container has the env, but a script fetched through OLS sees
`getenv("WORDPRESS_DB_HOST") == ""`).

**Fix:** pin the creds as LITERAL constants in `wp-config.php` via `wp config set`
(`_pin_db_creds_in_config`). Literal values are server-agnostic — correct under
apache, nginx, AND OLS. Done on switch-to-litespeed in `cmd_server`. This is also
why a *fresh* litespeed instance works: `cmd_install` does `wp config create`
with literal `--dbhost/--dbname/...`, never relying on env.

## Other gotchas handled

- **Orphan nginx sidecar:** switching away from nginx leaves the `nginx` service
  running. `compose up -d --force-recreate --remove-orphans` (services computed
  from the NEW server) drops it. `--force-recreate` also swaps the `wp` image
  (wordpress:latest ↔ OpenLiteSpeed) even though the service name is unchanged.
- **Same files, different mount:** WP files always live at host
  `runtime/wp-<inst>`; only the in-container docroot + uid differ per server
  (`_server_runtime`). Switching is NOT a data move. `_relax_perms_for_uid_switch`
  loosens perms when litespeed is on either side (33 ↔ 1000), though in practice
  OLS suExec adopts the file owner (uid 33), so perms were not the blocker — the
  DB env was.
- **litespeed .htaccess:** WP won't write one under OLS; `_ensure_litespeed_htaccess`
  (extracted from `cmd_install`) writes it + reloads OLS so permalinks/REST work.

## Files

- `sb`: `cmd_server`, `_pin_db_creds_in_config`, `_relax_perms_for_uid_switch`,
  `_ensure_litespeed_htaccess` (extracted), `server` subparser + dispatch.
- CLAUDE.md + README.md: per-server section documents the in-place switch.

## Verified (live, instance `main`)

apache→nginx (REST 200, sidecar up) → nginx→apache (orphan gone) →
apache→litespeed (OLS image, 500 → pinned creds → 200) → litespeed→apache
(literal creds still work under apache, image back to wordpress:latest).
Guards: same-server no-op (exit 0), unknown instance (exit 1), invalid type (exit 2).
