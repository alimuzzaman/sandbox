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
