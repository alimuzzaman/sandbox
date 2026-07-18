# Data Model: Remote and Hermes Operations Hardening

## Remote Service Record

Non-secret facts appended to the existing remote record.

| Field | Validation | Purpose |
|---|---|---|
| `service_name` | exact Sandbox unit name | Selected ownership boundary |
| `transport` | `https` or `tailscale` | Listener policy |
| `bind` | loopback or configured private address only | Exposure guard |
| `port` | valid TCP port | Listener identity |
| `runtime_revision` | bounded non-secret digest | Staged runtime evidence |
| `ownership_marker` | bounded non-secret digest | Unit/record correlation |

The bearer credential is deliberately excluded. It remains in the existing local
secret store and remote owner-only credential file.

## Service Ownership Proof

| Field | Validation | Purpose |
|---|---|---|
| `remote_name` | registered selected remote | Prevent cross-remote action |
| `unit_name` | equals service record | Identify cgroup |
| `active_pid` | belongs to selected unit | Process-scoped control |
| `bind_port` | equals expected non-public listener | Detect drift |
| `runtime_path` | expected staged path | Detect unrelated service |
| `marker` | equals service record | Detect foreign/unit collision |

State: `proven`, `legacy_detected`, `missing`, or `ambiguous`. Only `proven` allows
lifecycle mutation.

## Component Health Fact

| Field | Meaning |
|---|---|
| `component` | Stable dependency name |
| `status` | `healthy`, `degraded`, `unknown`, or `not_applicable` |
| `reasons` | Stable reason-code list |
| `observed_at` | Time-bounded evidence timestamp |
| `evidence` | Bounded and redacted supporting facts |

Required components: remote MCP, remote reboot recovery, Hermes gateway, scheduler,
cron catalog, jobs, sessions, and managed worktrees. Any required degraded/unknown
fact makes aggregate health degraded.

## Cron Reconciliation Transaction

| Field | Meaning |
|---|---|
| `phase` | `planned`, `preflight`, `snapshot`, `replace`, `verify`, `rollback` |
| `prior_inventory` | Protected exact snapshot reference |
| `desired_fingerprint` | Current catalog identity |
| `removed_ids` / `created_ids` | Controlled mutation audit |
| `result` | `planned`, `converged`, `blocked`, `rolled_back`, `rollback_failed` |
| `evidence` | Bounded sanitized postcondition or recovery evidence |

Transitions: `planned → preflight → snapshot → replace → verify → converged`; a
post-replacement failure transitions to `rollback → rolled_back|rollback_failed`.

## Terminal Result Classification

| Field | Validation |
|---|---|
| `terminal_marker` | One documented marker only |
| `transition` | Valid scheduler terminal transition |
| `provider_evidence` | Explicit provider/client failure overrides marker |
| `classification` | `successful_terminal`, `protocol_error`, `provider_failure`, or `work_failure` |
