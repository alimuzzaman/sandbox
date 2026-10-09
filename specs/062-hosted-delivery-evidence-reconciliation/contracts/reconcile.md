# Contract: Reconciliation

## CLI / MCP

```
./sb host reconcile --project-dir P --environment E --remote R [--request-id ID] [--json]
./sb host apply ... [--no-reconcile]
./sb delivery inspect ...          # unchanged, local-only; uncertain attempts shown "reconcilable"
./sb job-start --wait ...          # bounded reconcile on interrupted+uncertain
```

MCP `host_reconcile(project_dir, environment, remote, request_id?)` returns the same `data`.

- Exit 0 for `adopted_by_observation`, `no_effect_proven`; 1 otherwise (typed `code` = result).
- `diverged` output names spec 051 settlement (`host image settle` / `host recover`).
- Refusals name only commands runnable from the caller's checkout.

## Ports

```
ObservationPort.observe(target) -> Observation | Unavailable      # one read, 15 s
ImageRecordPort.terminal(target, request_id) -> record | None
reconcile.run(target, request_id, *, observation_port, image_port, lease) -> ReconciliationResult
fence_release.evaluate(attempt) -> {release: bool, rule, missing}
```

No consumer reads delivery or job state files directly.
