# Validation Quickstart: Remote and Hermes Operations Hardening

## Preconditions

- Use a clean local checkout and a dedicated temporary Sandbox home for fixture tests.
- Do not run migration, remote start/stop, force reconcile, or cron verify against a
  registered remote without a separate current approval.

## Local automated checks

Run focused tests first:

```sh
./.cli-venv/bin/python -m unittest \
  tests.test_remote tests.test_hermes tests.test_hermes_gateway tests.test_mcp_server
```

Run the applicable full suite afterward:

```sh
./.cli-venv/bin/python -m unittest discover -s tests
```

Expected: all service, ownership, redaction, health, reconciliation, rollback, and
terminal-result fixtures pass; no test requires a registered remote mutation.

## Read-only CLI contracts

```sh
./sb remote service status missing-remote --json
./sb hermes health --remote missing-remote --json
./sb hermes cron reconcile --remote missing-remote --force-replace --json
```

Expected: stable, redacted validation errors; no SSH command or remote mutation.

## Approved disposable remote acceptance

Only in a separately approved change window:

1. Capture a read-only before-state using the service status and Hermes health
   contracts.
2. Request the migration plan and review listener, credential, and rollback steps.
3. Confirm migration on one disposable remote; verify unit activity, enablement,
   loopback/private listener, authenticated endpoint, and no credential in metadata or
   process output.
4. Verify that stopping the selected unit leaves an unrelated HTTP fixture alive.
5. Reboot, then re-run read-only status/health and capture after-state.
6. Consider cron migration or a job verification only under an additional explicit
   approval; keep the two changes separate.
