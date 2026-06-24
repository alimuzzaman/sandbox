# `wp db` ops need the mysql client — run them via the `wpcli` service, not exec-into-web

**Found + fixed:** 2026-06-24, live-verifying spec 002 dashboard restore (S2).

`_restore_snapshot` (sandbox/commands/data.py) reset the DB with
`wpcli(["db", "reset", "--yes"])`. The `wpcli()` helper (gotcha #18) **execs into
the web container** (built-in `wp-cli.phar` bind-mounted). On the **nginx** server
(`wordpress:*-fpm` web tier) that container has **no mysql client** — only the
`db` container and the dedicated `wpcli` service do. So `wp db reset` died with:

```
Error: Failed to get current SQL modes. Reason: env: 'mysql': No such file or directory
```

This broke `sb restore` (CLI), the spec-002 dashboard Restore (S2), and any
`wp db reset/import` path on an fpm instance. Snapshot/export were unaffected
(they already ran via `compose run --rm wpcli`, whose image ships
`/usr/bin/mysql` + `mysqldump` + `mariadb`). The failure was clean — `wp db reset`
aborts before dropping tables, so the DB stayed usable.

It was broader than restore: `./sb wp db query`, the `db_query` MCP tool, and the
`db create/drop/query` calls in provisioning/teardown all failed the same way on
nginx — every `wp db *` routed through the exec-into-web helper.

**Fix (shipped) — at the helper, not per-call:** both `wpcli()` (sandbox/core/
_docker.py) and the MCP server's `_wpcli` (mcp/wp-server/app.py) now detect a
`db` subcommand (`args[0] == "db"`) and route it to the dedicated **`wpcli`
service** (`compose run --rm wpcli`) instead of `exec`-ing into the web container.
One change fixes every call site: restore/reset, `sb wp db …`, the db_query MCP
tool, and provisioning's db create/drop. (`_restore_snapshot` also calls
`compose run --rm wpcli db reset` explicitly, pairing with its volume-mounted
import.) Verified: `sb wp db query` returns rows + exits 0; CLI `sb restore` and
the wp-admin dashboard Restore both roll back `blogname`.

**Lesson:** anything that shells to `mysql`/`mysqldump` (every `wp db` subcommand)
must run in the `wpcli` service, NOT via the exec-into-web `wpcli()` helper — the
fpm (nginx) web image has no DB client. apache (`wordpress:*` mod_php) bundles it,
which is why this only bit nginx. The MCP-server copy needs a Claude Code restart
(gotcha #4) to load. See [[elementor-save-needs-current-user]] for the "verify on
the actual server tier" lesson.
