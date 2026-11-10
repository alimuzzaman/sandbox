# Quickstart: validating stray container reclaim

## Unit tests (on the remote, never locally in a non-Haiku session)

```sh
./sb job-start --remote xspeed-dev --project-dir . --timeout 900 -- \
  python3 -m unittest tests.test_reclaim_stray_containers tests.test_reclaim_project_teardown -v
./sb job-status <job-id> --remote xspeed-dev --json
./sb job-output <job-id> --remote xspeed-dev --stream combined --tail-bytes 16384
```

Expected: every case in `tests/test_reclaim_stray_containers.py` passes (stray inside
reclaimed, bind outside protected, named volume protected, Compose-labelled untouched,
changed-since-plan refused, crash rerun idempotent, tier mismatch refused).

## Dry evidence on xspeed-dev (read-only)

```sh
./sb resources plan --remote xspeed-dev --tier safe --json
```

Before the runtime is migrated this still shows the stray as part of
`stop_containers` only. After the orchestrator migrates the runtime
(`sb remote service migrate xspeed-dev --confirm`, `runtime_revision_state: match`),
the same dry plan shows `remove_containers` with evidence for the owned stray, or the
workspace under `skipped` with `bound_container_protected`.

## Real cleanup

Run by the orchestrator after merge and migrate only:
`./sb resources cleanup --remote xspeed-dev --tier safe --plan-id <id> --confirm`.
