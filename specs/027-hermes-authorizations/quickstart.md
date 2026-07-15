# Quickstart: Hermes Authorization Controls

1. Run focused tests:

   ```sh
   python -m unittest tests.test_hermes
   ```

2. Read remote requests without mutation:

   ```sh
   ./sb hermes authorization list --remote scaleway-sandbox --json
   ```

3. An eligible cron creates a pending request from its fixed shipped template
when it needs that authorization. The dashboard cannot create requests or scan
cron output on this Hermes release.

4. Review the returned ID, then explicitly approve it. The matching job can
resume only that reviewed scope until `expires_at`; the local five-minute
`authorization-expiry` cron restores the catalog prompt at expiry.

   ```sh
   ./sb hermes authorization show REQUEST_ID --remote scaleway-sandbox --json
   ./sb hermes authorization approve REQUEST_ID --remote scaleway-sandbox --confirm --json
   ```

5. Confirm the job is scheduled; trigger or wait only under the existing cron confirmation policy.

## Evidence (2026-07-15)

- `./.cli-venv/bin/python -m unittest tests.test_hermes_dashboard_authorizations tests.test_hermes_catalog_integrity tests.test_hermes tests.test_mcp` — 161 tests passed.
- `./sb hermes cron reconcile --remote scaleway-sandbox --confirm --json` — converged the quota checker and `lenzora-todo-task` without production access.
- `python3 ~/.hermes/plugins/sandbox-authorizations/request.py --template lenzora-preview-overlay` — created pending request `da112b4384f5ff86`; no approval or cron prompt edit occurred.
- `./sb hermes cron verify 040044cc36a6 --remote scaleway-sandbox --timeout 120 --confirm --json` — the `authorization-expiry` cron completed successfully after refreshing the approved dev-only request with its expiry guard.
- `./sb hermes dashboard doctor --remote scaleway-sandbox --json` — dashboard v1.0.6 is healthy, loopback-only, and uses Hermes's upstream session authentication; the revoker superseded the prior approval so exactly one Lenzora approval remains active.
- `git fetch --dry-run --tags --prune origin` in the installed Hermes checkout — completed after restoring the canonical upstream remote; no checkout update was applied.
- `./sb hermes update plan --remote scaleway-sandbox --json` — resolved the pinned target
  `9de9c25f620ff7f1ce0fd5457d596052d5159596`, matched the installed checkout, and returned
  `up_to_date` without confirmation or mutation. This is immutable target/checkout evidence;
- `./sb hermes update provenance --remote scaleway-sandbox --json` — fetched the pinned tag into
  a disposable checkout, verified its SSH signature and exact commit, and confirmed the installed
  checkout remained `9de9c25f620ff7f1ce0fd5457d596052d5159596` with canonical origin
  `https://github.com/NousResearch/hermes-agent.git` before and after the check.

## Local verification refresh (2026-07-16)

- `./.cli-venv/bin/python -m unittest tests.test_hermes` — 149 tests passed.
- `./.cli-venv/bin/python -m unittest discover -s tests -p 'test_recovery_*.py' -q` — 81 tests passed.
- `./.cli-venv/bin/python -m unittest discover -s tests -q` — 720 tests passed, 1 skipped.
- This refresh performed no additional live remote acceptance, approval, deployment, deletion,
  schedule activation, or production recovery operation; the live evidence above remains historical.
- Authorization approval concurrency is fixture-proven: state CAS conflicts fail before prompt
  mutation, and prompt-delivery failures restore the prior state with a second CAS. A live race
  drill remains unperformed.
