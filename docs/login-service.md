# Login service: one-time links for a hosted project

A hosted Compose project can give someone a short-lived, single-use link into a
private inbox, into its own sign-in flow, or into a private site such as a mockup
(see [Sharing a private site](#sharing-a-private-site-site_gate)). Sandbox renders one small service,
`sandbox-login`, into the target's Compose override. Nothing for it lives in the
project's repository except the declaration in its hosting manifest.

This bypasses a login. Declare it only on development targets.

## Declaring it

```yaml
autologin:
  login_service:
    image: python:3.13-alpine@sha256:<digest>   # must be pinned by digest
    enabled_until: 2026-12-01                   # required; within 30 days of apply
    session_ttl_seconds: 172800                 # required; 300 to 172800
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

A capture service with no login omits both `username` and `credential_secret`: setting
only one is refused. No `Authorization` header is sent, and no credential is rendered.
`inbox_gate` entries still need a login, because while a link is live the gate passes
visitors to the capture service's own login. A loopback-only port lets a private-network
proxy on the host reach a capture service like this.

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
| `request_origin`, `link_origin` and an absolute required `return` are on `allowed_host_suffixes` | Manifest parsing. Origins are lowercase https with no credentials or path |
| `enabled_until` holds through a request | Rechecked before a token is claimed and before the redirect; a websocket tunnel ends at that time or at its session expiry, whichever is first |
| The capture service is in this Compose project and reachable | Manifest parsing, apply preflight, and service startup |
| Any capture credential stays in this service's environment | Rendered override |
| `sandbox-login` publishes nothing. The capture service publishes only on `127.0.0.1` or `::1`, and never uses host networking | Apply preflight (`login_port_published`, `capture_port_published`) |
| No Sandbox route targets the capture service | Manifest parsing |
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
The cookie is `__Host-` prefixed, Secure, HttpOnly, SameSite=Strict, and host-only. It
has no `Max-Age` or `Expires`, so it ends when the browser closes, and the service also
refuses it once `session_ttl_seconds` has passed. Requests with a session reach the capture service with the
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

## Sharing a private site (`site_gate`)

A `site_gate` entry shares one background service of the project, such as a static
mockup, with someone who has no tailnet access and no account. The owner mints a
link and sends it. Opening the link starts a session on a public gate hostname,
and the gate forwards that browser's requests, websockets included, to the declared
upstream until the session ends. Without a session the gate hostname answers 404 to
everything.

Every hosted hostname, the primary included, gets a public DNS record. Keeping the
site private is the project edge's job: it answers the primary with 404 and sends
only the gate hostname to `sandbox-login:8080`. The owner views the site through
the upstream's loopback-only port, for example with a tailnet proxy on the server.

### Example: a static mockup

`sandbox.hosting.yml` (placeholder hosts):

```yaml
version: 1
project: mockups
environments:
  dev:
    compose:
      files: [compose.yml]
      service: edge                        # Sandbox routes every served hostname here
      container_port: 8080
      background_services: [web]
    healthcheck: {path: /, statuses: [404]}   # the edge's default answer
    deploy: {allowed_branches: [main], require_clean: true}
    host:
      primary: mockups.dev.example.test    # public DNS; the edge answers it with 404
      aliases:
        - {hostname: share.dev.example.test, mode: serve}
    cloudflare: {proxied: false, tls: acme}
    autologin:
      login_service:
        image: python:3.13-alpine@sha256:<digest>
        enabled_until: 2026-12-01
        session_ttl_seconds: 172800        # 48 hours per session
        allowed_host_suffixes: [dev.example.test]
      entries:
        mockup:
          mode: site_gate
          hostname: share.dev.example.test
          upstream: http://web:80
          ttl_seconds: 900                 # an unopened link dies after 15 minutes
```

`compose.yml`:

```yaml
services:
  edge:
    image: nginx:1.27-alpine@sha256:<digest>
    volumes: ["./edge.conf:/etc/nginx/conf.d/default.conf:ro"]
  web:
    image: nginx:1.27-alpine@sha256:<digest>
    volumes: ["./site:/usr/share/nginx/html:ro"]
    ports: ["127.0.0.1:8090:80"]           # the owner's private view; loopback only
```

`edge.conf`:

```nginx
map $http_upgrade $connection_upgrade { default upgrade; '' close; }
server {
    listen 8080 default_server;
    return 404;                            # the primary and anything unknown
}
server {
    listen 8080;
    server_name share.dev.example.test;
    resolver 127.0.0.11 valid=10s;         # starts even before sandbox-login exists
    set $gate sandbox-login:8080;
    location / {
        proxy_pass http://$gate;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }
}
```

To view the site yourself, run `tailscale serve --bg http://127.0.0.1:8090` (or an SSH
port forward) on the server. Sandbox does not manage that step.

### Declaration rules

- `hostname` is a served alias, never the primary. It fronts no other entry, because
  the gate answers every path on it.
- `upstream` is `http://<service>:<port>`, where the service is one of
  `compose.background_services`, and has no path, query or credentials. The gate
  connects to nowhere else.
- `path` is where links land. It defaults to `/__sandbox/share/<entry>`. `landing_path`
  is where a new session goes, and defaults to `/`. `ttl_seconds` (60 to 3600, default
  900) is the life of an unopened link. `requires_email` is false or omitted.
- `login_service.capture` is optional when every entry is a `site_gate`.
- `basic_auth` on the same environment is refused, because it would put a password
  prompt in front of the gate hostname.
- Apply preflight refuses an upstream that publishes a port beyond `127.0.0.1` or `::1`
  (`upstream_port_published`) or is missing from the project (`upstream_not_in_project`).

### Minting, listing and revoking

```bash
./sb host login-url --project-dir . --environment dev --remote myvps \
  --service mockup --confirm [--uses 3] [--ttl-seconds 600] [--json]
./sb host login-url ... --service mockup --list [--json]
./sb host login-url ... --service mockup --revoke l-1a2b3c4d5e6f --confirm   # or s-..., or all
```

- **The link is shown once, on your terminal.** The command writes it to the controlling
  terminal. Stdout and `--json` carry only the link id, expiry and uses. With no
  terminal (a background job, an agent's shell, a pipe) it refuses with
  `terminal_required` and mints nothing, so the link never lands in job output, logs
  or a transcript.
- `--uses N` (1 to 20, default 1) is how many browsers the link may start a session in.
- `--list` needs no `--confirm`. It shows ids, times, uses spent and sessions, never a
  link or a hash.
- Revoking a link also ends the sessions it started. Revoking ends an open websocket
  within 30 seconds.

Refusal codes: `terminal_required`, `uses_out_of_range`, `site_options_conflict`,
`site_gate_only` (site options on another entry), `site_link_limit` (32 live links),
`site_link_unknown`, and `site_entry_unknown` (the running service predates the entry:
run `host apply`).

### Gate contract

- **Link.** `https://<hostname><path>#sandbox_autologin=<token>`, a 256-bit token in the
  fragment. The page at `path` clears the fragment and posts the token by itself.
  Previews that do not run scripts never use the link.
- **Records.** Links, spent-use markers and sessions are files under
  `/run/sandbox-login/sites/<entry>/` on the service tmpfs, named by SHA-256 hash. The
  gate never stores a token or a session secret. They survive a process restart and
  end when the container is recreated.
- **Opening.** A live link and a claimed use set `__Host-sandbox_site_<entry>`. The
  cookie is host-only, `Path=/`, `Secure`, `HttpOnly` and `SameSite=Lax`, with
  `Max-Age` set to the session's remaining time. The response is a 303 to
  `landing_path` with `Referrer-Policy: no-referrer`.
- **Refusals.** Expired, spent, revoked, malformed and unknown links, and an `Origin`
  other than the gate's (or `null`, which browsers send from a `no-referrer` page), all
  get the same 404. More than 30 link attempts per entry in 60 seconds
  get 429.
- **Forwarding.** Only origin-form targets with exactly one `Host` header equal to the
  gate hostname are forwarded. The gate sends `Host: <hostname>`, drops hop-by-hop
  headers and its own cookies, and passes everything else.
- **Lifetime.** A session ends at `session_ttl_seconds`, at revoke, or at
  `enabled_until`, whichever comes first. After `enabled_until` the gate answers 404
  to everything.

## What it cannot do

It cannot identify who used a link: a forwarded share link works for whoever opens it
first, and `--list` shows only that a use was spent. Anyone with Sandbox access can mint one, and is
effectively root on that server. Whoever holds an unused link has that access. The clock
that sets a token's expiry is the issuing machine's, compared against the service's.
