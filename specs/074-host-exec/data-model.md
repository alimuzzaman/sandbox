# Data Model: Host Exec

## ResolvedTarget (frozen)

| Field | Type | Rule |
|---|---|---|
| remote | str | registered remote name |
| project | str | `TARGET_PART_RE` |
| environment | str | `TARGET_PART_RE` |
| kind | `"stack"` or `"plain"` | stack when a stack record matched |
| project_dir | str | absolute; for a stack exactly `runtime/stacks/<slug>` |
| slug | str or None | set only for stacks |
| state_key | str | `remote/project/environment`; must exist in `hosts.json` |

## ExecRequest (frozen)

| Field | Type | Rule |
|---|---|---|
| target | ResolvedTarget | |
| service | str | listed by the deployed `config --services` |
| argv | tuple[str, ...] | non-empty; passed verbatim |
| timeout_seconds | int | 1..3600, default 300 |
| capture | bool | true for `--json` |

## ExecOutcome (frozen)

| Field | Type | Rule |
|---|---|---|
| exit_code | int | remote status unchanged; 255 transport or watchdog |
| code | str | `ok`, `host_exec_failed`, `host_exec_timeout`, `host_exec_transport_failed`, `host_exec_watchdog_expired`, `host_exec_interrupted` |
| duration_ms | int | wall time of the run |
| stdout, stderr | bytes | captured only when `capture`; at most 1 MiB each |
| stdout_truncated, stderr_truncated | bool | |

`ok` is derived: `exit_code == 0 and code == "ok"`.

## Refusal codes (`HostingRefusal.code`)

`host_target_invalid`, `host_target_unknown`, `host_target_busy`,
`host_exec_unknown_service`, `host_exec_usage`, `remote_not_provisioned`.

## Audit record (one JSON line)

`at` (RFC 3339 UTC), `remote`, `target` (`P/E`), `service`, `argv` (each
element through `redact_text`), `exit_code`, `code`, `duration_ms`. No stdin,
environment, output, hostnames or paths.
