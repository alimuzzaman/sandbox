# Contract: Stray container reclaim

## Plan output (`sb resources plan --tier T --json`)

A workspace candidate:

```json
{
  "kind": "worktree",
  "locator": "<deploy-src>/<repo>-workspace-<hash>",
  "stop_containers": ["<id>"],
  "remove_containers": [
    {"id": "<64-hex>", "name": "<docker-name>", "fingerprint": "<sha256>",
     "evidence": {"image": "<image>", "mounts": [{"type": "bind",
       "source": "<checkout>/<sub>", "destination": "<path>"}],
       "networks": ["bridge"], "network_mode": "bridge", "running": true,
       "created": "<rfc3339>", "started_at": "<rfc3339>", "restart_count": 0}}
  ]
}
```

A kept workspace (in `skipped`):

```json
{"kind": "worktree", "locator": "<checkout>", "reason": "bound_container_protected",
 "protected_containers": [{"id": "<id>", "name": "<name>", "reason": "bind_outside_checkout"}]}
```

## Cleanup request (controller to host)

Each candidate item adds `remove_containers` exactly as planned. Omitted for
non-workspace candidates.

## Cleanup outcome

- Removed: `status: removed`, `teardown.strays_removed` lists the ids.
- Refused: `status: failed`, `reason: stack_teardown_incomplete`,
  `teardown.failures` holds the cause (`container_evidence_changed:<id>`, ...);
  the checkout is kept and no container was touched when the cause is evidence.

## Plan-id forms

| Plan made with | Apply with |
|---|---|
| `resources plan --scope cache` or `--scope stale` | `resources cleanup --plan-id ID --confirm` |
| `resources plan --tier T` or `workspace reap` dry run | `resources cleanup --tier T --plan-id ID --confirm` |

A tier plan applied with a different `--tier` is refused with `plan_kind_mismatch`
before the plan is begun.
