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
