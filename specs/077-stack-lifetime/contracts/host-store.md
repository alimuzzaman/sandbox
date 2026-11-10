# Host Store Contract

The controller runs `python3 -c <hoststore source + main call>` over SSH with one
JSON request on stdin and reads one JSON line from stdout. The program never reads
the request from argv and never echoes secrets (records hold none).

## Request

```json
{"op": "list|check|claim|update|release|record_merge|classify|policy",
 "now": "2026-11-10T10:00:00Z", "caller": {"label": "...", "controller": "<32 hex>"},
 "...op fields": "..."}
```

| op | Fields | Effect |
|---|---|---|
| `list` | — | all records (valid ones parsed, invalid file names listed), unclassified compose projects found under `runtime/hosts/<p>/<e>`, policy, free bytes |
| `check` | claim fields | the claim decision without writing |
| `claim` | `record` (draft), `copy_id`, `explicit_copy`, `legacy`, `new_ttl` | atomic: refuse or write; returns the stored record |
| `update` | `compose_project`, `ttl_seconds?`, `lifetime?`, `force_owner` | owner-checked TTL/lifetime change |
| `release` | `compose_project`, `copy_id?`, `force_owner` | remove the record after a controller teardown |
| `record_merge` | `compose_project`, `commit`, `evidence` | write merge evidence if `stack.commit == commit` |
| `classify` | `project`, `environment`, `lifetime?`, `ttl_seconds?`, `clear`, `force_owner` | write or remove a `kind: target` record |
| `policy` | `set?` | read or replace limits |

## Response

`{"ok": true, "result": {...}}` or `{"ok": false, "code": "<code>", "error": "<msg>", "holder": {...}?}`.

## Invariants

- Every write happens under `runtime/target-lifetimes/.lock` (flock, exclusive) and
  is a temp-file write plus `os.replace`; files are 0600, the directory 0700.
- Every write appends one line to `audit.jsonl` (op, compose_project, caller, at).
- A record that fails validation is never rewritten by `claim`/`update`; only
  `release --force-owner` or `classify` by an explicit operator replaces it.
- `claim` refuses before writing; a refusal changes nothing.
