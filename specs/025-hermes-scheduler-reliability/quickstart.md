# Quickstart: Validate Hermes Scheduler Reliability

## Prerequisites

- Use the configured `scaleway-sandbox` remote.
- Ensure local tests pass before remote mutation.
- Keep the current explicit confirmation for cron replacement and gateway convergence.

## 1. Local contracts

```bash
python3 -m unittest tests.test_hermes tests.test_cli tests.test_mcp
./sb selftest
```

Expected: scheduler/catalog tests pass, CLI and MCP expose matching controls, and the Sandbox self-test remains green.

## 2. Read-only remote evidence

```bash
./sb hermes health --remote scaleway-sandbox --json
./sb hermes worktree list --remote scaleway-sandbox --json
./sb hermes cron catalog --remote scaleway-sandbox --json
./sb hermes cron reconcile --remote scaleway-sandbox --force-replace --json
./sb hermes gateway converge --remote scaleway-sandbox --json
```

Before migration, health should identify the provider rejection false-success and gateway owner conflict. Preview must enumerate every current cron removal and every desired creation without changing state.

Repeat three harmless list/status calls within 60 seconds and inspect the generated SSH arguments or client diagnostics in a safe test environment. Expected: the first call may establish a control connection; later calls reuse the same endpoint-hashed control path, retain separate results, and a deliberately stale control socket recovers through a fresh connection.

## 3. Preserve agent work

Review every dirty worktree from step 2 under its repository instructions. Run repository-specific checks. Commit and push only validated changes; retain invalid/unrelated changes with their worktree paths documented.

## 4. Converge the gateway

```bash
./sb hermes gateway converge --remote scaleway-sandbox --confirm --json
```

Expected: one active `hermes-gateway-sandbox.service`, no active legacy/manual owner, `hermes cron status` available, and no process-count or restart-count drift across the full 120-second observation.

## 5. Replace and verify cron jobs

```bash
./sb hermes cron reconcile --remote scaleway-sandbox --force-replace --confirm --json
./sb hermes cron validate --remote scaleway-sandbox --json
./sb hermes cron reconcile --remote scaleway-sandbox --json
```

Expected: exact desired catalog, zero invalid routes, and a second preview with `changes=false`.

## 6. Prove execution

Select the catalog's harmless health/acceptance entry and run:

```bash
./sb hermes cron verify JOB_ID --remote scaleway-sandbox --timeout 600 --confirm --json
./sb hermes health --remote scaleway-sandbox --json
```

Expected: verified terminal evidence. Provider rejection, script failure, empty agent result, or contradictory request evidence returns failure rather than `ok`.

## 7. Fresh-server reproducibility

Run the documented remote install/setup path on an acceptance account, preview gateway/catalog convergence, apply, and repeat steps 5–6. No ad hoc remote script or raw cron edit should be required.
