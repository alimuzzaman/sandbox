# Contract: CLI and payloads

## sb remote add ... --storage-floor-gb N / sb remote set-storage-floor NAME --gb N
- N: integer 0-100000; 0 clears the floor. Stored as `storage_floor_gb` on the registration. `sb remote list` print it.

## Admission refusal (CLI --json and MCP)
```json
{"ok": false, "status": "blocked", "code": "remote_storage_floor",
 "error": "remote job submission blocked by the remote storage floor",
 "target": {"kind": "remote", "remote": "xcloud-london"},
 "storage": {"free_bytes": 0, "reserved_bytes": 0, "estimate_bytes": 0,
             "floor_bytes": 0, "estimate_source": "history|default",
             "reason": "below_floor|measurement_unavailable"},
 "recovery": {"automatic_cleanup": false, "automatic_retry": false,
              "guidance": "Reclaim space with ./sb workspace reap --remote R --dry-run, then --confirm."},
 "retryable": false,
 "side_effects": {"staging_started": false}}
```
Human form: `error: remote job submission blocked by the remote storage floor (remote_storage_floor): free 32.0 GB, reserved 0.0 GB, estimate 3.0 GB, floor 30.0 GB.`

## Reclaim outcome reasons (new)
- candidate reason `finished_job_idle` (tier `safe`)
- outcome reason `stack_teardown_incomplete` (source kept)
- outcome detail `teardown`: `{projects, containers_removed, volumes_removed, volumes_kept_shared, networks_removed, networks_kept}`

## sb host heal --remote R [--project P] [--environment E] [--confirm] [--json]
```json
{"ok": true, "action": "heal", "dry_run": true,
 "storage": {"free_bytes": 0, "floor_bytes": 0, "above_floor": true},
 "candidates": [{"container": "name", "project": "sandbox-host-p-e",
                 "restart_count": 5, "max_retries": 5, "exit_code": 1}],
 "restarted": [{"container": "name", "state": "running"}]}
```
Refusals: `code: storage_floor_not_clear` (at/below/unknown, `--confirm` only), `code: confirmation_required` never (dry run is the default).
