# Quickstart: validate the site gate

## Unit and runtime suites (remote test helper)

Run these in a Haiku helper on the remote, through `sb job-start` (see plan.md):

- `tests.test_host_login_service` covers parsing, rendering, preflight and the CLI
  mint/list/revoke paths, plus the link canaries.
- `tests.test_login_service_runtime` covers a real gate on 127.0.0.1 with a fake
  upstream. It checks 404 without a session, link use, replay, revoke, websocket, Host and
  absolute-form tricks, and the rate limit.

Expected result: both suites `OK`, with no regressions in the existing tests.

## Live check (the parent session, on a development target it owns)

1. Declare the entry (see `docs/login-service.md`, "Sharing a private site"), then run
   `./sb host apply … --confirm`.
2. `curl -sS -o /dev/null -w '%{http_code}' https://share.example.test/` returns `404`.
3. Run `./sb host login-url --service mockup --confirm --json` in a real terminal. The
   link shows on the terminal, and the JSON has `link_id` and no `url`.
4. Open the link in a private browser window. It lands on `/` with no fragment, and the
   site renders.
5. Opening the same link again in another private window returns 404.
6. `./sb host login-url --service mockup --list --json` shows one link with `used: 1` and
   one session.
7. `./sb host login-url --service mockup --revoke <s-…> --confirm`. The next reload in
   the first window returns 404.
