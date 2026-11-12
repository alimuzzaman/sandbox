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

## Panel "Share this stack" (2026-11-11 follow-up)

Automated: `python3 -m unittest tests.test_web_share` (route gate, body checks, refusal
mapping, no-store, link absent from `/api/hosting`, nothing written to stdout or
stderr). The node suite `src/web/tests/sharelabels.test.ts` runs from
`tests/test_web_poller.py` where node 24 is installed.

Manual, on a controller with a stack whose target sets `share.agent_mint: true`
(orchestrator, after the panel deploy):

1. Open `/hosting`, press "Share this stack" on the card and confirm. The dialog shows
   the link, expiry and use limit. Copy it and open it in a private window: the stack
   page renders.
2. Close the dialog and reload: the link is nowhere on the page, and DevTools shows no
   `localStorage` or `sessionStorage` entry holding it.
3. "Live links": the new link is listed with its expiry; revoke it, and the private
   window's reload answers 404.
4. On a stack without `agent_mint`, the button shows `agent_mint_not_enabled` with the
   fix; on one without a `share:` block, `stack_share_not_declared` with the fix.
5. `curl -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:<port>/api/hosting/share`
   answers 403 (no Origin, no panel header).

## Short links (2026-11-11 follow-up)

Automated (xspeed-dev, `--profile unit`): `tests/test_share_gate_runtime.py`,
`tests/test_host_share.py`, `tests/test_login_service_runtime.py`,
`tests/test_host_login_service.py`, `tests/test_web_share.py`. They check the new form
round-trips, old links still open, every bad token gets byte-identical 404s, the id is
the hash prefix and not the token, and the token is absent from logs, audit and output.

Manual (orchestrator, after the merge and a gate apply):

1. `sb host share create --remote R --project-dir . --json`: `link` reads
   `https://<gate>/s/#<22 characters>` and `link_id` reads `l-<12 hex>`.
2. Open it in a private window: the site opens, and the address bar shows no `#`.
3. An old-form link minted before the apply still opens until its expiry.
4. `curl -si -X POST -d token=AAAAAAAAAAAAAAAAAAAAAA https://<gate>/s/` answers 404.
5. `sb host share revoke l-<12 hex> --remote R --project-dir .`: a reload answers 404.
