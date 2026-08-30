# CLI Contract: Remote Host Swap and Memory Monitor

All operations are additive actions on the existing global `resources` command. They require
`--remote NAME`; omission never falls back to the controller/local host. Every action accepts
`--json`. Existing resource/storage actions and flags retain their meanings.

## Read-only status

```text
sb resources swap-status --remote NAME [--budget SECONDS] [--json]
```

Returns aggregate RAM, every opaque swap-area observation, active/persistent state,
swappiness, Sandbox ownership, remote service/revision evidence, monitor health/freshness,
retention, next sample, operation block, reboot-verification state, and separate aggregate
container swap eligibility. It never mutates and never requires confirmation.

## Read-only plan

```text
sb resources swap-plan --remote NAME --operation enable
  [--size-gib 1..8] [--budget SECONDS] [--json]

sb resources swap-plan --remote NAME --operation disable
  [--budget SECONDS] [--json]
```

- Enable defaults to 4 GiB, swappiness 15, five-minute sampling, current plus eight weekly
  history files, and 32 MiB total history.
- `--size-gib` is parsed by the controller as an integer from 1 through 8 inclusive, retained
  in requested/effective policy, checked against every RAM/filesystem/free-reserve bound, and
  bound into the immutable plan identity. The remote has no planning action.
- Every observed value, threshold, comparator, reserve, result, intended fixed artifact,
  rollback element, expiry, and plan ID is returned.
- `--size-gib` with disable is `invalid_mode`.
- Planning is read-only. An ineligible request returns `refused`, not a partial plan that can
  be applied.

## Confirmed apply/reconcile

```text
sb resources swap-apply --remote NAME --plan-id ID --confirm
  [--budget SECONDS] [--json]
```

- `--confirm` is mandatory and checked before service/provider construction.
- Apply accepts no operation, size, swap path, artifact, command, or policy override.
- The canonical plan carries the already reviewed effective `size_gib`; the controller and
  remote revalidate it and all capacity inputs. A top-level apply size remains an unknown key.
- Target, plan identity, first-acceptance expiry, service marker/revision, host identity,
  observation digest, ownership, capacity, RAM headroom, monitor state, and operation lock
  are revalidated. An already journaled same operation may reconcile after expiry; an
  expired plan cannot begin a new operation.
- Replaying the same plan reconciles the same operation identity or returns its proven
  terminal result. A different intent is refused while an operation or incomplete rollback
  blocks mutation.
- Empty, malformed, duplicated, late, or unavailable transport output returns `partial` or
  a typed failure; it never authorizes a second identity or reports success.
- An active conflicting operation renders as `refused` with `operation_in_progress`.
  Unknown delivery or invalid response evidence renders as `partial` with
  `response_invalid`. No additional top-level result statuses are permitted.

For disable, the confirmed plan removes only verified active configuration and stops future
sampling. Previously retained bounded aggregate history remains readable under a minimal
disabled-state ownership receipt; first-version disable does not delete it.

## Bounded read-only history

```text
sb resources swap-history --remote NAME
  [--since UTC] [--until UTC] [--limit 1..1000]
  [--budget SECONDS] [--json]
```

`--limit` defaults to 288. The complete response is capped at 1 MiB. Output reports the
requested and observed ranges, counts, freshness, completeness, malformed/missing evidence,
and truncation. Returned records contain only the `AggregateMemorySample` allowlist.

## Common envelope

```json
{
  "schema_version": 1,
  "ok": true,
  "action": "swap-status",
  "status": "complete",
  "target": {
    "kind": "remote",
    "name": "registered-name",
    "identity": "opaque-host-id"
  },
  "data": {},
  "error": null
}
```

Actions are `swap-status`, `swap-plan`, `swap-apply`, and `swap-history`.

Status values:

- status/history: `complete`, `partial`, `failed`;
- plan: `planned`, `refused`, `failed`;
- apply: `applied`, `already_current`, `refused`, `partial`, `failed`,
  `rollback_complete`, `rollback_incomplete`.

Error objects contain `code`, safe `message`, `retryable`, and optional bounded
`observed`/`recovery_command`. They never contain credentials, endpoints with credentials,
raw host output, paths, commands, arguments, environments, process/container identities, or
artifact contents.

## Required stable refusal/error codes

- `remote_required`
- `unknown_remote`
- `remote_unreachable`
- `remote_service_ownership_unknown`
- `remote_runtime_revision_mismatch`
- `remote_swap_protocol_mismatch`
- `unsupported_platform`
- `required_facility_unavailable`
- `confirmation_required`
- `invalid_mode`
- `invalid_size`
- `invalid_range`
- `invalid_limit`
- `plan_not_found`
- `plan_expired`
- `plan_target_mismatch`
- `plan_drifted`
- `plan_replay_incompatible`
- `unmanaged_swap`
- `unsafe_swap_artifact`
- `ownership_unknown`
- `insufficient_capacity`
- `insufficient_disable_headroom`
- `operation_in_progress`
- `rollback_incomplete`
- `history_unavailable`
- `response_invalid`

New codes may be added compatibly. Existing meanings cannot be silently changed.

## Privacy and compatibility

- Human and JSON renderers consume the same strict service result.
- Unknown evidence remains explicit and cannot render as healthy/current/owned.
- Host swap and aggregate container eligibility are distinct fields.
- Persistence is not reboot verification.
- No new MCP tool is part of the first-version contract. JSON CLI is the automation surface;
  future MCP adapters must call the shared service and preserve this contract.
