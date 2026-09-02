# Quickstart: Observation-Only Hosting Recovery Validation

This guide is source/local validation only. Do not use a live remote, secret file, DNS,
Caddy, or production target during implementation.

## Focused checks

```sh
python3 -m unittest \
  tests.test_host_recovery_models \
  tests.test_host_recovery_policy \
  tests.test_host_recovery_repository \
  tests.test_host_recovery_service \
  tests.test_host_recovery_cli \
  tests.test_job_service \
  tests.test_job_supervisor
```

Use fakes for the job ledger, remote observer, host identity projection, edge adapter, clock,
and protected-effect witnesses. Tests must use `tests.subprocess_support.synthetic_environment`
for changed captured subprocesses.

## Required local scenarios

1. Current exact failed apply reconciles receipt only and increments once.
2. Exact replay returns the same attempt and generation.
3. Legacy Lenzora-shaped generic job plus unbound host receipt refuses.
4. Dirty, changed target/config/secret reference/image/topology/service/phase cases refuse.
5. Partial, truncated, timed-out, duplicate, or torn epoch refuses.
6. Apply/recovery race gives one owner and CAS result.
7. Edge requires a second identity, reference, unchanged generation, and confirmation.
8. Edge uncertainty never repeats and survives full-attempt compaction.
9. Public/persisted evidence contains no raw argv, paths, source, environment, or secret.

## Release activation gates

After integration, but only with separate authorization:

1. Update the installed remote Sandbox runtime through the supported lifecycle and verify its
   exact revision.
2. Run disposable non-production acceptance with synthetic secrets and an inert test domain.
3. Review mutation witnesses and rollback/uncertainty results.
4. Update Lenzora's deploy wrapper to create eligible current-contract applies and invoke
   recovery with explicit identities/generation.
5. Recover development first. Production deploy remains a separate reviewed operation with
   terminal job, declared-service health, edge readiness, and direct public proof.
