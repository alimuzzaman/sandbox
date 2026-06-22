# Quickstart: Headless Debugging Tools — live verification

Prerequisites: a running instance (`./sb ensure`/`ensure_instance`) with
`WP_DEBUG`/`WP_ENVIRONMENT_TYPE=local`. All checks are live (constitution IV).

## 1. dump/dd → tailable file

- Add `dump(get_option('siteurl'));` to a loaded plugin/theme path; load a page.
- `tail_log(file="dump", project_dir=…)` (or `./sb dump`) shows the rendering with a
  `=== dump HH:MM:SS file:line ===` header.
- `dd($x)` writes then halts with a pointer to the file.
- On a non-local env, confirm the mu-plugin no-ops (no `dump`/`dd` defined).

## 2. Query Monitor capture

- `qm_capture(url="<a slow page>", project_dir=…)` → fires the request and returns
  parsed JSON: `db_queries`, `php_errors`, `timing`, … (hooks trimmed by default).
- First call auto-activates QM; confirm normal requests before that carried no QM.
- Capture an **anonymous** URL → still returns data (no login).
- `wp_rest(path + "?_envelope")` returns the `qm` envelope for REST-scoped data.

## 3. Xdebug

- `xdebug(action="on|status")` / `./sb xdebug …` on a Docker instance → toggles + reports state; a request with `XDEBUG_TRIGGER` breaks (trigger requirement).
- On a **herd** instance → status is reported; toggle returns a clear actionable message that per-instance toggling is unsupported (no opaque abort).

## 4. Hygiene

- `./sb dump --clear` / `./sb qm --clear` truncate the logs; both are gitignored.
