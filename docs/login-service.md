# Login service: one-time links for a hosted project

A hosted Compose project can give someone a short-lived, single-use link into a
private inbox or into its own sign-in flow. Sandbox renders one small service,
`sandbox-login`, into the target's Compose override. Nothing for it lives in the
project's repository except the declaration in its hosting manifest.

This bypasses a login. Declare it only on development targets.

## Declaring it

```yaml
autologin:
  login_service:
    image: python:3.13-alpine@sha256:<digest>   # must be pinned by digest
    enabled_until: 2026-12-01                   # required; within 30 days of apply
    session_ttl_seconds: 3600                   # required; 300 to 86400
    allowed_host_suffixes: [dev.example.test]   # required
    capture:
      service: capture                          # a background service of this project
      adapter: mailpit_v1
      api_url: http://capture:8025/mail
      username: capture
      credential_secret: CAPTURE_PASSWORD       # a required or generated secret
  entries:
    inbox:
      mode: inbox_gate
      requires_email: false
      hostname: gate.dev.example.test
      path: /__sandbox/login/inbox
      ttl_seconds: 600
      landing_path: /mail/
    app:
      mode: magic_link
      requires_email: true
      hostname: gate.dev.example.test
      path: /__sandbox/login/app
      magic_link:
        request_url: http://app:8080/login/request
        request_origin: https://app.dev.example.test
        link_origin: https://app.dev.example.test
        link_path: /login/verify
        link_pattern: 'https://app\.dev\.example\.test/login/verify\?[^\s"<>]+'
        required_params: {return: "https://app.dev.example.test"}
        poll_timeout_seconds: 10
        denied_emails: [admin@dev.example.test]
```

`hostname` must be a served alias of the target, never its primary hostname. A
cookie path is not a security boundary on a shared origin.

Every served hostname reaches the target's one published service, so the project's
own edge must forward the gate hostname to `sandbox-login:8080`, websocket included.
Use a variable upstream with Docker's resolver, so the edge still starts when the
service is not declared. Sandbox cannot see inside that edge: remove any other route
to the capture service when the gate goes live.

## What keeps it from being left on

| Guard | Enforced by |
|---|---|
| Undeclared means no container | Manifest parsing and the rendered override |
| `enabled_until` is required, unexpired, and within 30 days | Apply refuses; the running service answers 404 after it |
| No standing access: without a live token or session, every public route is 404 | Running service; health answers only on an internal loopback port |
| Every hostname of the target matches `allowed_host_suffixes` | Manifest parsing. List only development domains, so a production domain can never qualify |
| The capture service is in this Compose project and reachable | Manifest parsing, apply preflight, and service startup |
| The capture credential stays in this service's environment; no ports are published | Rendered override, apply preflight on `capture` and `sandbox-login`, and a refusal when the capture service is the routed service |
| A left-on service is visible | `sb host status` lists each entry and the expiry |

## Issuing a link

```bash
./sb host login-url --project-dir . --environment dev --remote myvps \
  --service app --confirm
```

For an entry with `requires_email: true`:

- With no saved address, a terminal is prompted. With no terminal, the command stops
  with `email_required` so an agent can ask the person. Sandbox never guesses and
  never reads git config or the signed-in account.
- The first address becomes this target's default, stored in Sandbox's host state.
- `--email` changes the address for one link and leaves a saved default alone.
  `--email` with `--save-email` replaces the default.

The address is personal data at rest in that state file. The output names the address
each link is for.

`denied_emails` refuses listed addresses at issue, from every source, with
`email_denied`. Use it for accounts that hold more access than a normal user, and for
any application that grants that access by person rather than by origin. It covers only
the addresses you list, so keep it in step with the application's own list.

Other refusal codes: `invalid_email`, `email_not_applicable`,
`login_entry_required`, `login_entry_unknown`, `login_service_not_declared`,
`login_service_expired`, `login_service_expiry_too_far`, `login_link_failed`.

## Contract

**Link.** `https://<hostname><path>#sandbox_autologin=<token>`. The token sits in the
fragment, so it never appears in a request line or a proxy's access log. A token is 256
random bits, base64url encoded.

**Token record.** `/run/sandbox-login/<entry>.json`, on a tmpfs, mode 0600, replaced
atomically:

```json
{"schema":"sandbox.login-token.v1","entry":"app","token_sha256":"<hex>",
 "issued_at":1700000000,"expires_at":1700000900,"email":"person@example.test"}
```

`token_sha256` is the SHA-256 of the token's ASCII bytes. `expires_at` is whole Unix
seconds, and the token is dead at that second. `email` is present only for an entry
that requires one. Issuing a new link replaces the record, so the previous unused link
stops working. A missing or malformed record, or one outside the 60 to 3600 second
range, counts as no record.

**Using a link.**

- GET and HEAD show a confirmation page and change nothing, so a chat preview cannot
  use up the link.
- POST with the token consumes it. A wrong token consumes nothing. Consumption is one
  `O_CREAT|O_EXCL` marker per token hash, so exactly one of any number of concurrent
  POSTs wins, and a repeat gets 410.
- Responses send `Referrer-Policy: no-referrer` and redirect to an address with no token.

**`inbox_gate`.** A consumed link starts a session and redirects to `landing_path`.
The cookie is `__Host-` prefixed, Secure, HttpOnly, SameSite=Strict, host-only, and
lasts `session_ttl_seconds`. Requests with a session reach the capture service with the
service's credential added, websocket included. While a link is outstanding, a visitor
with no session reaches the capture service's own login; otherwise they get 404. Sessions
are held by a key in process memory, so restarting the service ends all of them. There
is no per-session revoke.

**`magic_link`.** The address comes only from the token record, never from the browser.
On a consumed link the service:

1. Notes the messages already in the capture service.
2. POSTs `{"email": ...}` to `request_url` with `Host` and `X-Forwarded-Proto` set from
   `request_origin`. Nothing in the browser's request can change that origin.
3. Polls for a new message, within `poll_timeout_seconds`, addressed to exactly that
   recipient and arriving after the request started.
4. Finds exactly one `link_pattern` match. Its origin, path, and every `required_params`
   value must match the declaration. It redirects the browser there and follows no
   other link.

| Outcome | Status | Body |
|---|---|---|
| The application rate-limited the request | 429 | `rate_limited` |
| The application refused the request | 502 | `request_failed` |
| No matching mail in time | 504 | `mail_timeout` |
| Mail arrived but its link does not match | 502 | `template_mismatch` |
| The capture service cannot be read | 502 | `capture_unavailable` |

A token is consumed before the exchange starts, so it cannot trigger a second mail. It
stays consumed if a later step fails: issue a new link.

## What it cannot do

It cannot identify who used a link. Anyone with Sandbox access can mint one, and is
effectively root on that server. Whoever holds an unused link has that access. The clock
that sets a token's expiry is the issuing machine's, compared against the service's.
