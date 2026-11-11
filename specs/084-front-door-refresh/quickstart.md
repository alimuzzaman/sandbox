# Quickstart: validate the front-door refresh

## Unit and integration tests (xspeed-dev durable job, Haiku helper)

```
<controller>/sb job-start --remote xspeed-dev --project-dir <worktree> --cancel-on-stall --timeout 1800 -- \
  python3 -m unittest tests.test_front_door_refresh tests.test_delivery_stack_selector \
  tests.test_hosting tests.test_share_private_rollout tests.test_hosted_noindex \
  tests.test_front_door_nginx tests.test_host_recovery_cli tests.test_skill_mirrors \
  tests.test_repo_instruction_consistency tests.test_mcp_composition tests.test_host_share
```

Expected: all new tests pass; failures only where the baseline already fails.

## Live (orchestrator, after merge and migrate)

1. Plan: `sb host refresh-front-door --remote xspeed-dev --name test-stacks --json`.
   Expect `ok: true`, `action: plan`, `legacy_binding: true` on a pre-084 target.
2. Refresh: same command with `--confirm`. Expect `action: refreshed`.
3. `curl -sI https://<primary>/` shows `X-Robots-Tag: noindex, nofollow, noarchive`.
4. `sb host status --remote xspeed-dev --name test-stacks --json` shows the same revision
   and loopback port as before; the containers' start times are unchanged.
