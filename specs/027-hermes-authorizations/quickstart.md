# Quickstart: Hermes Authorization Controls

1. Run focused tests:

   ```sh
   python -m unittest tests.test_hermes
   ```

2. Read remote requests without mutation:

   ```sh
   ./sb hermes authorization list --remote scaleway-sandbox --json
   ```

3. Create a request only after you have the exact scope and deployed HTTPS origin:

   ```sh
   ./sb hermes authorization request --remote scaleway-sandbox --job lenzora-todo-task --scope preview-overlay --replay-origin https://example.test --reason 'Approved bounded preview-overlay replay work' --json
   ```

4. Review the returned ID, then explicitly approve it:

   ```sh
   ./sb hermes authorization show REQUEST_ID --remote scaleway-sandbox --json
   ./sb hermes authorization approve REQUEST_ID --remote scaleway-sandbox --confirm --json
   ```

5. Confirm the job is scheduled; trigger or wait only under the existing cron confirmation policy.

## Evidence (2026-07-15)

- `python3 -m unittest tests.test_hermes tests.test_cli tests.test_mcp` — 158 tests passed.
- `./sb hermes authorization list --remote scaleway-sandbox --json` — returned `ok: true`, `status: ok`, and an empty request collection without scheduler mutation.
