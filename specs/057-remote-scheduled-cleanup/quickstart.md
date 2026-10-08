# Quickstart: validating scheduled safe cleanup

## Local (no remote)

```bash
python3 -m unittest tests.test_cleanup_routine_contract tests.test_cleanup_routine_units \
  tests.test_cleanup_routine_store tests.test_cleanup_routine_run \
  tests.test_cleanup_routine_cli tests.test_host_reclaim_guard
python3 -m unittest tests.test_resource_reclaim_service tests.test_resource_remote \
  tests.test_storage_monitor_schedule tests.test_server_transport
python3 -m unittest tests.test_architecture_boundaries
```

Expected: all pass. The run tests seed eligible, excluded, STOPPED, PROTECTED
and LIVE resources in a temp `SANDBOX_HOME`, and assert SC-002 (manifest before
removal; zero forbidden items), SC-003 (`timed_out` from a forced bound), SC-006
(a second reclaim while the guard is held → `skipped_busy`) and SC-007 (31 runs →
30 records).

## Live (disposable remote; coordinate first)

Prerequisites: no active deploy on the target remote; install with
`./sb remote service migrate <remote> --confirm` and confirm
`runtime_revision_state: match`; repin any pinned consumer checkout.

```bash
./sb resources routine --remote R --enable --confirm --cadence '*:0/10' --json   # short test cadence
./sb resources routine --remote R --status --json      # enabled, next_run set
# wait one period
./sb resources routine --remote R --status --json      # one run record; manifest present iff removals
./sb resources routine --remote R --disable --confirm --json
# wait two periods
./sb resources routine --remote R --status --json      # disabled; no new runs
```

Expected: SC-001, SC-004 and SC-005 hold; the host has no
`sandbox-cleanup-routine.timer` after disable.
