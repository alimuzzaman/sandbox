# Contract: Recovery Service

Public operations: `plan`, `create`, `list`, `verify`, `restore_plan`, `restore_apply`,
`retention_plan`, `retention_apply`, `schedule_plan`, `schedule_apply`, and `schedule_remove`.

Read operations perform no external writes. Mutating operations receive explicit confirmation
where required, use injected process/path/clock/lock/remote services, return a stable redacted
result envelope, and never accept arbitrary shell text or destination paths from callers.

Create publishes manifest last. Restore apply requires a prior compatible plan and checkpoints.
Retention apply accepts only candidate IDs from a freshly recomputed plan.
