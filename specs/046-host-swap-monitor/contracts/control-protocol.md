# Authenticated Control Contract: Host Memory and Swap

Feature 046 extends the existing authenticated `POST /resources` endpoint. The endpoint
accepts JSON objects no larger than the existing request bound and returns JSON within the
existing bounded control response. It never accepts executable source, shell, argv,
environment, arbitrary path, unit text, or file content from the controller.

## Service envelope

Every response has this outer shape:

```json
{
  "resource_schema": 1,
  "host_memory_schema": 1,
  "transport": "control",
  "service": {
    "ownership_marker": "24-lowercase-hex",
    "runtime_revision": "24-lowercase-hex"
  },
  "result": {}
}
```

The controller rejects the result before interpretation unless the schema is supported and
the marker/revision exactly match the selected registered service record and local runtime.
An old endpoint without `host_memory_schema` is `remote_swap_protocol_mismatch`. There is no
SSH/direct-host fallback and no automatic service migration.

## Actions

The only host-memory wire actions are the three underscore-named actions below. Planning is
controller-owned: `swap-plan` obtains `host_memory_status`, computes and stores the immutable
plan locally, and never submits a remote plan action.

### `host_memory_status`

```json
{
  "action": "host_memory_status",
  "remote_name": "registered-name",
  "budget_seconds": 15
}
```

Read-only. Returns strict `RemoteSwapState`, service evidence, and no raw probe output.

### `host_memory_history`

```json
{
  "action": "host_memory_history",
  "remote_name": "registered-name",
  "since": "2026-08-30T00:00:00Z",
  "until": "2026-08-31T00:00:00Z",
  "limit": 288,
  "budget_seconds": 15
}
```

Read-only. `since` and `until` may be null. `limit` is 1-1,000. Returns a strict
`HistoryWindow`; total serialized result is at most 1 MiB. Unknown input keys are refused.

### `host_memory_apply`

```json
{
  "action": "host_memory_apply",
  "remote_name": "registered-name",
  "operation_id": "64-lowercase-hex",
  "plan": {
    "plan_id": "64-lowercase-hex",
    "operation": "enable",
    "target_identity": "opaque-host-id",
    "service_ownership_marker": "24-lowercase-hex",
    "runtime_revision": "24-lowercase-hex",
    "expires_at": "2026-08-30T12:15:00Z",
    "observation_digest": "64-lowercase-hex",
    "effective_policy": {},
    "intended_artifact_digests": {},
    "rollback_scope": []
  },
  "confirmed": true,
  "budget_seconds": 300
}
```

Protected. The remote service accepts only the canonical plan fields and recomputes the
plan/operation identities before obtaining the host mutation lock. `confirmed` must be the
literal boolean `true`. Missing, extra, mismatched, expired, or malformed values are refused
before provider mutation. Apply never accepts a path, command, unit body, file body, or
credential. For enable, canonical `effective_policy.size_gib` is an integer from 1 through 8
that was selected by controller planning and bound into the plan ID; the remote revalidates
that size and every capacity calculation. A top-level size or policy override is refused.

## Host-side phase and replay rules

1. Validate request schema, service evidence, identities, and canonical digests.
2. Acquire the fixed root-owned non-overlapping lifecycle lock and read the journal.
3. A new operation is accepted only while the plan is current. A matching operation that
   was journaled while current may resume after plan expiry or return its verified terminal
   result; a different operation is refused while work or incomplete rollback exists.
4. Observe and compare every plan-bound field before each consequential phase.
5. Record and fsync the next phase before mutation; run fixed bounded argv through the
   narrow privilege provider; observe and record the result afterward.
6. Report `applied` only after active/persistent swap, effective swappiness, monitor/timer,
   fresh sample, retention, next sample, and receipt all verify.
7. Report `rollback_complete` only after every recorded prior-state element verifies
   restored. Otherwise retain `rollback_incomplete` and the unrelated-mutation block.

Disable stops and removes only proven active configuration. It stops future samples but
preserves prior bounded aggregate history under an atomically minimized disabled-state
receipt. History deletion is not an apply phase in the first version.

Transport loss does not erase the journal. The client performs status/read-only
reconciliation and may resubmit only the same operation identity. A conflicting active
operation is `refused` with `operation_in_progress`; unknown delivery or invalid response
evidence is `partial` with `response_invalid`. No additional top-level outcome vocabulary is
introduced by the transport.

## Fixed host authority

The provider may manage only this exact fixed surface:

- `/var/lib/sandbox/host-memory/sandbox.swap`, one root-owned regular swap file;
- the systemd swap unit deterministically derived from that path with
  `systemd-escape --path --suffix=swap`;
- `/etc/sysctl.d/90-sandbox-host-memory.conf`, the owned global-swappiness drop-in;
- `/etc/systemd/system/sandbox-host-memory-monitor.service` and
  `/etc/systemd/system/sandbox-host-memory-monitor.timer`;
- `/usr/local/libexec/sandbox-host-memory-monitor`, the root-owned aggregate helper;
- `/etc/logrotate.d/sandbox-host-memory-monitor`, the owned rotation policy;
- `/var/log/sandbox/host-memory.jsonl` plus at most eight owned weekly rotations;
- `/var/lib/sandbox/host-memory/receipt.json` and `operation.json`;
- `/run/lock/sandbox-host-memory.lock`, the transient lifecycle lock.

Every ancestor and artifact is checked for expected owner, type, safe mode, link count, and
digest. Symlinks, hard-link ambiguity, foreign content, duplicate persistence, unowned swap,
or conflicting sysctl policy are refusals. The provider never edits arbitrary `fstab`, cron,
user shell, container limit, or remote-service files.

## Strict observation/privacy schema

The endpoint builds returned objects from allowlisted typed fields. It does not redact a raw
environment or command dump after collection; those values are never collected. Sample and
status output excludes:

- process or container names/IDs;
- PIDs, command lines, arguments, environments, users, and working directories;
- raw swap/file/unit/log/receipt paths or contents;
- network endpoints, bearer values, and secret-like fields;
- unbounded stderr/stdout or exception representations.

Malformed or unknown host data is represented by bounded reason codes. Unknown JSON keys,
duplicate keys, invalid UTF-8, oversized files/responses, invalid numbers/timestamps, and
clock regressions fail closed or produce explicit partial evidence.

## Feature composition

The public read-only `HostMemoryStatusProjection` may be injected into Feature 047 host
governance. The control endpoint itself exposes no governance mutation and Feature 047 may
not submit `host_memory_apply`. Spec 043 storage monitoring never calls these actions and
this provider never reads Spec 043 policy, records, locks, or schedules.
