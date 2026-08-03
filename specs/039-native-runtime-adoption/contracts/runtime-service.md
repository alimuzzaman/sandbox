# Contract: Native Runtime Service

## Configuration

`wordpressRuntime` is normalized by an explicit common config provider:

```json
{
  "wordpressRuntime": {
    "mode": "managed-native",
    "adapter": "ubuntu-nspawn",
    "php": "8.3",
    "database": "mariadb-10.11",
    "webServer": "nginx",
    "resources": {},
    "egress": []
  }
}
```

Non-Compose activation is valid only from the gitignored machine override. The committed
project may declare compatible requirements, but detection never opts in. Legacy
`server: herd` is imported into the adapter model during compatibility staging and remains
truthfully lower-isolation.

## Required operations

Every adoptable native adapter declares and implements:

- `preflight`, `ensure`, `status`, `health`, `open`
- `wordpress_cli`, bounded `exec`, filesystem and log access
- isolated production/test database handling and `test`
- `apply`/reconcile and conservative `destroy`

Optional operations are independently declared: stop, aggregate logs, snapshots, mail,
Xdebug, subdomain multisite, server switching, and remote deployment.

Capability checks occur before any package, runtime, route, database, or file side effect.

## Operation envelope

```json
{
  "ok": false,
  "operation": "ensure",
  "project_kind": "wordpress",
  "runtime": {
    "mode": "managed_native",
    "adapter": "ubuntu-nspawn",
    "isolation": "managed_container",
    "support_tier": "adoptable"
  },
  "state": "blocked",
  "capabilities": {"required": {}, "optional": {}},
  "backend": null,
  "health": {"ready": false},
  "reason": {"code": "isolation_prerequisite_missing", "message": "Managed-native cannot prove its network boundary; use Compose."},
  "mutated": false
}
```

Stable reason codes include `explicit_selection_required`, `pending_install_confirmation`,
`unsupported_matrix`, `version_unavailable`, `isolation_prerequisite_missing`,
`isolation_drift`, `foreign_collision`, `runtime_mode_change`, `php_version_mismatch`,
`unsupported_capability`, `cleanup_incomplete`, and `incumbent_lower_isolation`.

## C/A/B boundary

Successful C preflight/ensure returns only:

- backend veth address/port or incumbent document-root/backend requirements;
- PHP/database/execution capabilities and health;
- no hostname, DNS, ingress route, or TLS mutation.

The clean-URL orchestration then invokes A/B. Destroy asks A to clean its route separately;
C removes only unchanged C-owned backend/runtime state.

## Cleanup observation

Each resource is observed before it is removed, and every observation answers two
questions: which resource this is, and whether the host still has it. There are
exactly three outcomes, and conflating any two of them breaks cleanup:

| Outcome | Meaning | Cleanup |
|---|---|---|
| `present`, identity matches | the resource exists and is still ours | remove it |
| `present`, identity differs | it exists but changed | stop; retain `owned_state_drifted` |
| `absent` | a successful read found nothing to remove | count as removed, continue |
| observation failed | the question went unanswered | stop; retain `runtime_unavailable` |

`absent` is always a positive read — a registry that listed no such machine, a
profile file that does not exist while the profile list was readable without it.
It is never inferred from a failed read, and never from provisioning bookkeeping:
machine identities are reused across attempts, so a resource this attempt did not
create may still exist from an earlier one and must be observed, not assumed gone.

## Mode immutability

If a registry/runtime record with data exists and requested mode/adapter differs, every
ordinary operation returns `runtime_mode_change` with `mutated: false`. Only a separately
specified export/recreate/import operation may cross modes.

