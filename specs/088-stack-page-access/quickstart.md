# Validation guide

Implementation is not yet verified. Use placeholder stacks on an authorized disposable
remote. Do not mint/revoke live links or apply stacks without owner approval. Full-stack
owns fs-try/host mutation until it releases that ownership; leave dev/setup-plan alone.

1. Read current local/installed revision through supported Sandbox commands, then
   check active durable jobs and edge-nginx/hosting-caddy/docker-pool locks.
2. With an already recorded fixture Hub, plan a WordPress stack with uses naming it.
   Confirm all invalid forms fail before writes. Confirm only with separate approval.
3. Open the clean stack URL and reload. Compare every route/revision to stack list;
   inspect unavailable/missing inbox and missing-shared-stack states.
4. With permission to mint on a disposable fixture, click WordPress sign-in twice.
   Consume in reverse order; both work once, replay fails, expiry fails. Check that no
   token appears in request lines, polling payloads, audit or browser storage.
5. After short-links merge/rebase and authorized protocol upgrade, open one stack link
   in a fresh non-tailnet browser. Exercise own/shared routes, inboxes and sign-ins.
   Test eight-link bound, bad Origin/body/header and every preflight gap.
6. Interrupt companion mint and induce partial revoke. Inspect attempt listing; retry
   cleanup/revoke after gate restart and prove no unused sign-ins remain valid.
7. Luna runs focused Python suites (host_stack, web_hosting_api, web_share, host_share,
   hosting, login_service_runtime, share_gate_runtime), UI typecheck/build, then required
   repository gates on the remote using finite-timeout durable jobs. Use a replay-safe
   request id and retain job id; ambiguous submission is not a passing result.
8. Review the fixed helper report: command, pass/fail/skip, first failure ≤40 lines,
   complete log path. Fresh Sol independently reviews the final diff against latest.
   Flag authentication changes for owner sign-off before release.

All captured subprocesses in new/changed tests use tests.subprocess_support helpers
with synthetic environments. Save logs and browser evidence in the shared scratch root.
