# Quickstart: validating hosted share links

Placeholder hosts throughout. Tests run on xspeed-dev through `sb job-start --remote
xspeed-dev` only; never against a live xspeed-dev target.

## Automated

```bash
./sb job-start --remote xspeed-dev --timeout 1800 -- python -m pytest -q \
  tests/test_host_share.py tests/test_share_gate_runtime.py tests/test_hosted_noindex.py \
  tests/test_login_service_runtime.py tests/test_host_login_service.py tests/test_hosting.py
```

Expected: all pass, and the no-index test fails if the header is removed from any
renderer branch (checked once by hand during development).

## Manual, on a scratch target (orchestrator)

1. Add the `share` short form from `contracts/manifest.md` with `private: true` and
   `agent_mint: true`, then `sb host plan` and `sb host apply --confirm`.
2. `curl -sI https://<primary>/` shows 404 and `X-Robots-Tag: noindex, nofollow, noarchive`.
3. `sb host share create --remote R --project-dir . --path /designs/home/ --json` returns
   `link`.
4. Open the link in a private window: the page renders at `/designs/home/`, and the address
   bar has no `#`. `/designs/other/` answers 404.
5. `sb host share revoke <link_id> --remote R --project-dir .`: a reload answers 404.
6. For a stack: `sb host share create --remote R --stack <slug> --json`, open it, reload, and
   check that the sign-in links change and that a used WordPress link is refused the second
   time.
