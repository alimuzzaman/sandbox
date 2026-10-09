# Contract: Runtime Compatibility, Pins and Refusals

## CLI
- Global `--strict-runtime` (also `SANDBOX_STRICT_RUNTIME=1`) on every remote-touching command.
- `sb remote service status NAME [--json]` adds `control_protocol` (local/installed), `compatibility` (verdict object) and `pins` (list, bounded 64).
- `sb remote service migrate NAME [--dry-run|--confirm] [--break-pin HOLDER ...]`: plan adds `would_break_pins` and `stops_serving_below_protocol`; confirmed apply refuses `remote_runtime_pins_unacknowledged` (zero writes) unless every unexpired unbroken pin is named.
- `sb remote pin list NAME [--json]`; `sb remote pin release NAME [--holder HOLDER] [--break-pin HOLDER]`.
- `sb remote ...` registration busy: `remote_registration_busy` with `holder` {pid, command, remote, started_at}.

## Refusal JSON (shared)
```json
{"ok": false, "error": {"code": "remote_runtime_revision_mismatch",
  "message": "...", "verdict": "protocol_newer",
  "local": {"revision": "<24 hex>", "protocol": {"spoken": 2, "oldest_served": 1}},
  "installed": {"revision": "<24 hex>", "protocol": null},
  "remedies": ["sb remote service migrate xcloud-london --confirm"]}}
```

## Remote pin program
- Inputs (argv, JSON): action `list|register|release|break`, pin fields. Output: one JSON line. Exit 0 on success; non-zero → caller reports `strict_pin_unverifiable` (strict) or omits pins with `pins_unavailable` (plan/status).
