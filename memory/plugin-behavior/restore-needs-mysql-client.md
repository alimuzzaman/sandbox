# `sb restore` fails on fpm/nginx instances — no `mysql` client in the wp tier

**Discovered:** 2026-06-24, live-verifying spec 002 dashboard restore (S2).

`cmd_restore` (sandbox/commands/data.py) imports the snapshot via wp-cli
`wp db reset` + `wp db import`, which run in the **wp container** and shell out to
the `mysql` client binary. On the **nginx** server (`wordpress:*-fpm` web tier)
that container has **no mysql/mariadb client** — only the `db` container does
(`/usr/bin/mariadb`). Result:

```
Error: Failed to get current SQL modes. Reason: env: 'mysql': No such file or directory
```

- **Affects:** `sb restore` (CLI), the spec-002 dashboard Restore (S2 — the bridge
  job correctly spawns it and reports `failed`), and likely `sb reset` (spec 008)
  and any `wp db import/reset` path on an fpm instance.
- **NOT affected:** snapshot/export (worked), take/list/delete from the dashboard.
- **Clean failure:** `wp db reset` aborts before dropping tables, so the DB stays
  usable (siteurl still resolves) — the instance is left recoverable, not wedged.
  (This incidentally verified spec-002 T016/U1 failure-path + T008/A1 guard.)

**Likely fix direction:** run the import/reset against the `db` container's
`mariadb` client (e.g. `docker compose exec -T db mariadb`), or ensure a mysql
client is available to wp-cli in the fpm wp tier. apache (`wordpress:*` mod_php)
images historically bundle the client, which is probably why this wasn't caught
on apache instances. See [[elementor-save-needs-current-user]] for the broader
"verify on the actual server tier" lesson.
