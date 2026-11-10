# Quickstart: validate the Stacks and hosted sites view

## 1. Unit tests (on the remote through job-start)

```sh
python -m unittest tests.test_panel_records tests.test_panel_view tests.test_panel_health \
  tests.test_web_hosting_api tests.test_deploy_stack_link
```

Expected: all pass. The secret-planting tests confirm that no planted value appears in any written record or API payload.

## 2. Panel against fixture state (local)

1. Make a scratch `SANDBOX_HOME` holding:
   - `runtime/hosts/xspeed-hub-demo/staging/summary.json`, a stack `demo` with routes `hub-demo.example.test` (primary), `admin-demo.example.test` and `renderer-demo.example.test`, ports `{mailpit: 18142}`;
   - `runtime/stack-sites/demo/wp-demo.example.test.json`;
   - `runtime/hosts/marketing/production/summary.json`, a plain target;
   - `runtime/hosts/legacy/production/`, with no summary.
2. Run `SANDBOX_HOME=<scratch> ./sb web --port 8799`, with the this-server source forced for the fixture (see `tests/test_web_hosting_api.py` for the switch used in tests).
3. Open `http://127.0.0.1:8799/hosting`. Expected:
   - stack `demo` lists four server rows, the WordPress site labelled "linked";
   - details show branch, commit, template, the Mailpit port 18142 and the created time;
   - health reads "checking", then "unreachable" for the `example.test` names;
   - "Hosted sites" lists `marketing` and `legacy`, the latter noting that its details are recorded from its next apply.

## 3. Read-only check

`curl -s -X POST http://127.0.0.1:8799/api/hosting` returns 404, and `/api/action` has no hosting action.

## 4. Follow-up 2026-11-10: lifetimes, landing page, apply outcome

1. Tests: `python -m unittest tests.test_panel_lifetime tests.test_panel_records tests.test_panel_view tests.test_host_inventory_hosted tests.test_web_hosting_api tests.test_web_poller`. `test_web_poller` also runs `node --test` over `src/web/tests/`.
2. Fixture: add `runtime/target-lifetimes/` to the scratch `SANDBOX_HOME`. Include a temporary copy expiring in 1 hour, an expired temporary copy, two copies of one branch with different owners, a hosted canonical stack, one malformed file and one truncated file.
3. Open `/hosting`. Expected:
   - the "Lifetimes" list starts with the expired copy ("reapable · expired"), then the 1-hour copy;
   - the two copies of one branch are separate rows, each with its owner;
   - the malformed and truncated files read "unclassified, protected", never reapable.
4. Open `/host/local` with the this-server source. Expected: "Hosted apps (sb host apply / stacks)" leads with its count. With no dev instances, there is no "Start all", "Stop all" or "New instance" prompt. The sidebar shows both counts.
5. Write a summary with `last_apply` `{"status": "unverified", "reason": "required_evidence_missing"}`. Expected: "applied · delivery unverified (required evidence missing)", not "failed".
