# Contract: controller update state file

Path: `$SANDBOX_HOME/runtime/controller-update/state.json`, mode `0600`,
directory `0700`. Shared by every checkout on the machine. Written by
`sandbox.controller_update.state` only, under an exclusive `flock` on
`state.json.lock` (5 s budget), via temp file + `os.replace`.

```json
{
  "schema_version": 1,
  "hints": {
    "<remote>": {"revision": "<40-hex>", "source": "install|probe", "recorded_at": 1762780000.0}
  },
  "refusals": [
    {"revision": "<40-hex>", "upstream": "origin/latest", "reason": "not_on_upstream", "until": 1762801600.0}
  ]
}
```

Read rules: a missing, unreadable or malformed file is an empty state; a
malformed entry is dropped; expired refusals are dropped on the next write.
Writers never raise to their caller (`record_hint` and `record_refusal` are
best effort and return `bool`).

API:

- `record_hint(remote: str, revision: str, source: str, *, now=None) -> bool`
- `load() -> State` (hints and live refusals)
- `record_refusal(revision, upstream, reason, ttl, *, now=None) -> bool`
- `refused(state, revision, upstream, *, now=None) -> bool`
