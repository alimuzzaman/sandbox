# Contract: cleanup routine control actions and CLI

## CLI

```text
sb resources routine --remote NAME --status [--json]
sb resources routine --remote NAME --enable --confirm [--cadence EXPR] [--timeout SPAN] [--exclude GLOB ...] [--json]
sb resources routine --remote NAME --disable --confirm [--json]
sb resources routine --routine-run --json        # host only; invoked by the timer unit
```

- Exactly one of `--status|--enable|--disable|--routine-run`.
- `--enable`/`--disable` without `--confirm` → `protected_operation`, nothing sent.
- `--remote` is required except for `--routine-run`; an unregistered or unprovisioned remote → `remote_unavailable` before any request.

## Control request (`POST /resources`, `resource_schema: 1`)

```json
{"action": "cleanup_routine_enable",
 "expected_runtime_revision": "<rev>",
 "cadence": "daily", "timeout": "30min", "randomized_delay": "5min",
 "exclusions": ["lenzora*"]}
{"action": "cleanup_routine_disable"}
{"action": "cleanup_routine_status", "history": 30}
```

Unknown keys → `invalid_request`. All strings are length-capped; exclusions ≤ 32.

## Control response

```json
{"resource_schema": 1, "transport": "control",
 "service": {"runtime_revision": "<rev>"},
 "result": {
   "ok": true,
   "routine": {"enabled": true, "cadence": "daily", "timeout": "30min",
               "randomized_delay": "5min", "exclusions": ["lenzora*"],
               "effective_exclusions": ["lenzora*", "keep-*"],
               "enabled_revision": "<rev>", "next_run": "2026-10-09T00:03:00Z",
               "enabled_at": "2026-10-08T10:00:00Z", "disabled_at": null},
   "last_run_revision": "<rev>",
   "runs": [ {RunRecord}, ... ]
 }}
```

Failure: `{"ok": false, "error": {"code": C, "message": M}}`, where `C` is one of
`invalid_request`, `invalid_cadence`, `invalid_exclusion`, `invalid_timeout`,
`runtime_revision_mismatch`, `systemd_unavailable`, `linger_disabled`,
`routine_install_failed` (prior units restored), or `routine_remove_failed`.

Status never includes SSH targets, tokens, or file contents other than the
fields above (FR-026).

## Probe `reclaim` change

`reclaim_action` may now return `{"ok": false, "reason": "host_reclaim_busy", "detail": "guard"|"apply_transaction"}`
before writing any manifest line. `ReclaimService.cleanup` reports it as
`status: "skipped"`, `code: "host_reclaim_busy"`.
