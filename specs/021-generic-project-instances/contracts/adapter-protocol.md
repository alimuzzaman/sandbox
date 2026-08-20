# Contract: Runtime Adapter Protocol

An adapter is selected once from the effective project kind and receives explicit dependencies for registry access, runtime paths, process execution, port allocation, URL/proxy operations, and logging.

## Required operations

| Operation | Input | Result contract |
|---|---|---|
| `validate` | project descriptor | Normalized adapter descriptor or structured validation errors; no mutation |
| `ensure` | descriptor, label, existing record | Idempotently ready instance record or structured failure |
| `status` | descriptor, record | Current lifecycle/health information without mutation |
| `start` / `stop` | descriptor, record | Updated state; repeated calls succeed |
| `logs` | descriptor, record, bounded options | Bounded text plus source metadata; no unbounded streaming in MCP |
| `exec` | descriptor, record, command, timeout | Exit code/stdout/stderr with existing output limits |
| `apply` | current descriptor, record | Reconciled Sandbox-owned state without deleting project-owned data |
| `destroy` | descriptor, record | Removes adapter/Sandbox state; preserves source and generic project volumes |

## Capability gate

Every operation declares one capability. Dispatch checks the selected adapter before invoking the handler. Failure shape:

```json
{
  "ok": false,
  "error": "unsupported_capability",
  "project_kind": "compose",
  "required": "wordpress.cli",
  "available": ["instance.ensure", "instance.status", "instance.logs"],
  "suggestion": "Use instance_exec for commands in the declared service."
}
```

Adapters do not import from `sandbox.core` using wildcard imports and do not depend on namespace back-fill. The WordPress adapter may delegate to the existing implementation during compatibility migration but exposes the same protocol.

## Convergence amendment — 2026-08-13: canonical identity and fresh observation

Every adapter receives a kind-neutral `ProjectIdentity` resolved by the shared
identity service:

```json
{
  "root": "canonical-project-root",
  "identity": "opaque-stable-id",
  "display_name": "safe-name",
  "label": "default",
  "kind": "compose|wordpress|other",
  "adapter": "adapter-id",
  "capabilities": ["instance.status"]
}
```

An adapter MUST NOT derive identity from a plugin slug, container name, current
working directory, or a second hash. CLI and MCP dispatch pass the same record.

`status` and plugin/runtime state observations return `observed_at` and an
opaque `observation_generation`. State-changing operations increment or
invalidate that generation. A cached value from another session is either
refreshed before returning or explicitly marked `stale:true`; it cannot satisfy
an active/inactive assertion. Capability rejection occurs before adapter
subprocess, REST, database, or filesystem work.
