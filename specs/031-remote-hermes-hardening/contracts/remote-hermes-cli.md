# CLI Contract: Remote and Hermes Operations Hardening

All commands support `--json`. Result envelopes never contain bearer credentials,
raw SSH targets, prompt bodies, or unbounded job output.

## Remote service

| Command | Mutation | Contract |
|---|---:|---|
| `sb remote service status NAME --json` | no | Reports service record, ownership state, listener facts, enablement/activity, and redacted reason codes. |
| `sb remote service migrate NAME --plan --json` | no | Returns exact installation/migration steps, prerequisites, legacy evidence, and no credentials. |
| `sb remote service migrate NAME --confirm --json` | yes | Writes only after ownership/prerequisite proof; returns applied or rollback result. |
| `sb remote service stop NAME --confirm --json` | yes | Stops only a proven selected service unit; ambiguity returns `remote_service_ownership_unknown`. |

Existing `sb remote up`/`down` retain their compatibility entrypoints but route through
the scoped service contract when a service record exists. An unproven legacy process
must never be terminated by generic argv matching.

## Hermes health

`sb hermes health --remote NAME --json` returns `status` plus an ordered `reasons`
array and component facts. Required codes include:

- `remote_mcp_not_installed`, `remote_mcp_not_enabled`, `remote_mcp_inactive`
- `user_linger_disabled`, `gateway_ownership`, `scheduler_unavailable`
- `cron_drift`, `cron_failure`, `cron_result_protocol_error`
- `stale_session`, `dirty_managed_worktree`

`unknown` component evidence always degrades aggregate health.

## Hermes cron

| Command | Mutation | Contract |
|---|---:|---|
| `sb hermes cron reconcile --remote NAME --force-replace --json` | no | Returns exact plan or `blocked` legacy explanation. |
| `sb hermes cron reconcile --remote NAME --force-replace --confirm --json` | yes | Runs preflight/snapshot/replacement/verification and returns `converged`, `rolled_back`, or `rollback_failed`. |
| `sb hermes cron verify --remote NAME JOB --confirm --json` | yes | Separates trigger, transition, provider, and terminal-result evidence; does not return prompt/output bodies. |

## Error behavior

- Invalid bind or unsafe ownership: no mutation, stable actionable error code.
- Missing/unsafe credential file: no service start, redacted error.
- Provider/client rejection always overrides a nominal terminal marker.
- Scheduler postcondition failure attempts restore exactly once and reports the
  verification result.
