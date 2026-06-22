# Data Model: Headless Debugging Tools

Files + a per-instance xdebug state. No database.

## Dump log entry (`wp-content/debug-dump.log`)

Append-only, plain text. Each `dump()`/`dd()` call writes:

```
=== dump HH:MM:SS <file>:<line> ===
<CliDumper rendering of each arg>
```

`dd()` writes then `wp_die()`s. Gated on `WP_DEBUG`/`WP_ENVIRONMENT_TYPE=local`;
`dump`/`dd` defined only then, `function_exists`-guarded.

## QM record (`wp-content/qm.jsonl`)

One JSON object per captured request, appended:

```
{ ts, url, is_ajax, data: { db_queries, php_errors, timing, http, assets_scripts,
  assets_styles, conditionals, request, block_editor, … } }
```

Collector ids whitelisted; `hooks` dropped by default (huge). Written on `shutdown`
(`PHP_INT_MAX`) by reading `QM_Collectors` directly (no auth gate). `qm_capture`
returns the **last** line, filtered to requested collectors.

## Xdebug toggle (per instance)

| State | Meaning |
|-------|---------|
| on | xdebug extension enabled (Docker: container ini; herd: `php<MM>` ini), trigger mode |
| off | disabled |
| status | reports current state + how to trigger |

Trigger mode requires `XDEBUG_TRIGGER` (cookie/GET/env) on the request (gotcha #7).

## Lifecycle

- dump/QM logs: created on first write; truncatable via `./sb dump --clear` / `./sb qm --clear`; gitignored.
- QM plugin: installed-inactive at provision → active after first `qm_capture` → off via `./sb qm off`.
