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

Sandbox routes every entry hostname to the gate itself (spec 081): `sandbox-login`
publishes one managed loopback port, the target's port plus 3000, and both front
doors send entry hostnames there. A project edge that still forwards the gate
hostname to `sandbox-login:8080` keeps working, but no longer sees that traffic.
Remove any other route to the capture service when the gate goes live.

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
| `sandbox-login` publishes only its managed `127.0.0.1:<port + 3000>` mapping. The capture service publishes only on `127.0.0.1` or `::1`, and never uses host networking | Apply preflight (`login_port_published`, `capture_port_published`) |
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

## Sharing a private site: share links

A share link lets someone with no tailnet access and no account open a private
development site, one folder or one page of it, or a stack page (see
[The stack page](#the-stack-page)). An agent can make the link and hand it over, if the
target allows it. Opening the link starts a session on a public gate hostname; the
gate forwards that browser's requests, websockets included, to the site until the
session ends. Without a session the gate hostname answers 404 to everything.

### Declaring it: the `share` block

```yaml
environments:
  dev:
    compose:
      files: [compose.yml]
      service: web                          # the site; Sandbox routes to it
      container_port: 80
    host:
      primary: mockups.dev.example.test     # answers 404 while private
      aliases:
        - {hostname: s7k2.dev.example.test, mode: serve}   # the gate hostname
    cloudflare: {proxied: false, tls: acme}
    share:
      hostname: s7k2.dev.example.test
      private: true                         # every other hostname answers 404
      agent_mint: true                      # agents may make links and receive them
      image: python:3.13-alpine@sha256:<digest>
      enabled_until: 2026-12-01             # required; within 30 days of apply
      allowed_host_suffixes: [dev.example.test]
      session_ttl_seconds: 43200            # optional; 300 to 172800, default 12 hours
```

That is the whole declaration: no edge config. Sandbox renders the gate as the
`sandbox-login` service, publishes it on its own loopback port (the target's port plus
3000), and both front doors send the gate hostname there. With `private: true`, the
front door answers every other hostname with 404, so nothing but a valid share session
reaches the site. Redirect aliases answer 404 too, instead of pointing at the primary.
A few answers stay, and none of them reaches the application: `robots.txt` (deny) on every
hostname, the HTTP-to-HTTPS redirect and ACME challenge files on port 80, and on nginx
the route marker that apply checks.

- Pick a gate hostname that says nothing, such as a few random characters. It is the
  only public way in.
- **Apply verification follows privacy.** Every gate hostname must answer
  `/__sandbox/gate-check` with the gate's own 404, marked `X-Sandbox-Gate: 1`; the gate
  reserves that path ahead of every link, session and upstream. With `private: true`,
  the primary, serve aliases and redirect aliases must answer 404: an answer of 2xx or
  3xx fails the apply. Each apply renders a fresh random `X-Sandbox-Route` tag into the
  front-door route of a target with a gate, and those 404s must carry it, so an older
  route or a cached page cannot pass. Every probe asks a new URL (a random
  `sandbox-verify` query). A gate behind a public target's Basic Auth gets its
  credentials. The application's own health is still checked on its loopback port.
  Image activation does not re-render the route, so it checks privacy without the tag.
- **No Cloudflare proxying.** `private: true` refuses `cloudflare.proxied: true`, and the
  apply refuses, before any effect, when Cloudflare still proxies any of the target's
  names (or the shared coverage wildcard). Make them DNS-only and purge the Cloudflare
  cache first, so no cached page outlives the switch and a DNS rollback never turns
  proxying back on.
- **A private apply closes the public name first.** Before it touches the runtime, an
  apply of a private manifest reads the front-door route (a failed read stops the
  apply), keeps it only if it is one Sandbox rendered private and nobody changed since
  (its last line is `# sandbox-private-route v1 <sha256 of the text above>`), removes it
  otherwise, and validates and reloads the front door either way, so the running
  configuration matches the disk. nginx also waits until the removed route's marker
  stops answering. If any of that cannot be confirmed, the apply stops before the
  runtime changes. The first private apply over a route without the mark (including
  one rendered private before the mark existed) therefore leaves the hostnames
  unrouted, the gate included, until the new route is live.
- **A failed apply fails closed.** The apply recreates the containers before it installs
  the new route, and a failure does not roll them back. For a private manifest a private
  route the apply already installed stays, a marked private previous route goes back,
  and anything else is removed. Run the apply again once the cause is fixed.
- With an `autologin.login_service` block, `share` carries only `hostname`, `private`,
  `agent_mint` and `stack_sign_in`, and uses that block's fields. `share` is then a
  reserved entry name.
- `basic_auth` is refused with `share.private` (nothing is left to protect) and with any
  `site_gate`.
- With `private: true`, apply refuses when the routed service publishes a port beyond
  `127.0.0.1` or `::1` (`upstream_port_published`), because that port would bypass the
  gate.

### Viewing a private site yourself

The routed service keeps its managed loopback port, shown by `sb host status` as
`loopback_port`. View it through the tailnet without a share link:

```bash
# on the server
tailscale serve --bg http://127.0.0.1:<loopback_port>
# or from your machine
ssh -L 8090:127.0.0.1:<loopback_port> <server>
```

Sandbox does not manage that step. `sb host tailnet-name` (spec 078) will give a
tailnet-only HTTPS name for the same port once its hardening lands; until then use one
of the above.

### Making, listing and revoking links

```bash
./sb host share create --remote R --project-dir . [--path /designs/home/] [--ttl 7d] [--uses 3] --json
./sb host share list   --remote R --project-dir . [--json]
./sb host share revoke l-<id>|s-<id>|all --remote R --project-dir . [--json]
```

Pick the target with `--project-dir` (plus `--environment`), `--target <project>/<env>`,
or `--name <stack slug>`. `--entry` picks a `site_gate` entry when there is more than
one; the `share` block's entry is the default. MCP tools `host_share_create`,
`host_share_list` and `host_share_revoke` take the same options.

- **Who gets the link.** With `share.agent_mint: true`, `create` returns the link once,
  in its `--json` result or MCP result, so an agent can give it to the person. Without
  it, the 075 rule holds: the link goes to the controlling terminal only, and with no
  terminal the command refuses with `terminal_required`. MCP always refuses then, with
  `agent_mint_not_enabled`. `create` refuses inside a durable job (`job_output_refused`),
  because job output is stored. The terminal rule keeps the link out of stdout, logs and
  job output; it does not prove a person is reading. A process that owns the terminal
  (an agent running the CLI under a PTY, or `script`) sees what is written to it.
- **Lifetime.** `--ttl` is `90m`, `12h`, `7d`, `2w` or seconds. The default is 7 days and
  the maximum 30. A link never outlives `enabled_until`; the result says
  `expires_clamped: true` when it was shortened.
- **Uses.** Without `--uses` any number of browsers may open the link until it expires
  (up to 512 sessions per gate). `--uses N` (1 to 20) stops after N browsers.
- **Scope.** `--path` shares one folder (ending in `/`) or one exact page. A session
  scoped to it gets 404 for every other path. Paths holding encoded slashes, backslashes
  or dots, dot segments, doubled slashes, `;`, `#` or `%25` are refused before matching.
  The scope stays on the server: the link carries no path.
- **Audit.** Every create, list and revoke appends a line to
  `$SANDBOX_HOME/runtime/share-links/audit.jsonl` on the controller and on the server:
  time, operation, controller, owner label, remote, target, entry, kind, link id, scope,
  expiry, uses, outcome. No token, hash or address.
- **Revoke.** Revoking a link ends its sessions at their next request and an open
  websocket within 30 seconds. `all` revokes every link and session of the entry.

`host login-url --service <site entry>` still mints a whole-site link (terminal only,
one use by default), now in the same form.

Refusal codes: `share_not_declared`, `agent_mint_not_enabled`, `terminal_required`,
`job_output_refused`, `share_ttl_invalid`, `share_scope_invalid`, `uses_out_of_range`,
`share_entry_required`, `share_entry_unknown`, `stack_unknown`, `stack_share_not_declared`,
`share_options_conflict`,
`site_link_limit` (32 live links), `site_link_unknown`, `site_entry_unknown` (the running
service predates the entry: run `host apply`), `login_service_expired`, `email_required`,
`share_failed`.

### The stack page

`sb host share create --remote R --stack <slug> --json` makes a link to a read-only page
for one recorded stack. The page lists the stack's hostnames, with role (the first DNS
label) and health, and sign-in links:

- **WordPress admin**, when the stack target declares `autologin` (`user`,
  `container_path`) and is not private. Minting writes a per-link mu-plugin,
  `sandbox-share-<expiry>-<link id>.php`, next to `container_path`. It holds a random
  key; the gate holds the same key. Each page view signs a fresh token that works once
  and for at most 10 minutes, on the main site of the main network only (on a multisite,
  every other blog refuses it). Revoking the link deletes the file and fails if the file survives; an expired one
  is inert and removed at the next mint.
- **Login-service entries** named in `share.stack_sign_in`, such as a Hub QA sign-in
  (`magic_link` or `inbox_gate`). Each view writes that entry's token record, as
  `host login-url` would, so the previous link of the entry stops working. A `magic_link`
  entry uses the target's saved address, or `--email`. The token is bound to the stack
  link: once that link is revoked or expires, an unused sign-in link from its page is
  refused, even one a view was still minting during the revoke, or one whose sign-in
  form was still being posted. A newer view replaces the entry's link, and the older one
  stops working even mid-post. A service session that a
  sign-in already started keeps its own lifetime (`session_ttl_seconds`).
- **Revoke retries.** If the gate revoke works but the WordPress key file cannot be
  deleted, the command says so; running the same revoke again deletes the file, even
  though the gate no longer knows the link. `host login-url --revoke` takes the same path.

Tokens ride in the URL fragment and are posted by a small page, so no request line or
access log holds one. The page is served by the stack target's own gate: the stack's
manifest needs a `share` block with `agent_mint: true`. Health comes from the gate: over
the project's network for the stack's own hostnames, and over public HTTPS for linked
WordPress sites on `allowed_host_suffixes`. A stack session reaches only the page.

The panel can call `sandbox.commands.host_share.create_share(remote, stack=<slug>,
caller="panel")`, which returns the same result as `create --json`.

## Sharing a private site with a `site_gate` entry (075)

A `site_gate` entry shares one *background* service of the project behind the gate,
while the routed service stays public. For a private site, prefer the `share` block
above. Links, sessions, scope and the gate contract are the same for both.

### Example

```yaml
autologin:
  login_service:
    image: python:3.13-alpine@sha256:<digest>
    enabled_until: 2026-12-01
    session_ttl_seconds: 172800
    allowed_host_suffixes: [dev.example.test]
  entries:
    mockup:
      mode: site_gate
      hostname: share.dev.example.test     # a served alias
      upstream: http://web:80              # a background service
```

Sandbox routes `share.dev.example.test` to the gate; the routed service and the other
hostnames are unchanged. A project edge written for 075 that forwards the gate
hostname to `sandbox-login:8080` is no longer needed.

### Declaration rules

- `hostname` is a served alias, never the primary. It fronts no other entry, because
  the gate answers every path on it.
- `upstream` is `http://<service>:<port>`, where the service is one of
  `compose.background_services`, and has no path, query or credentials. The gate
  connects to nowhere else.
- Links open at `/s/<id>` on the gate hostname. A declared `path` is accepted for
  compatibility and unused. `ttl_seconds` (60 to 3600, default 900) is the default
  lifetime of a `host login-url` link; `host share create --ttl` goes up to 30 days.
  `requires_email` is false or omitted.
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
./sb host login-url ... --service mockup --revoke l-<32 hex> --confirm   # or s-..., or all
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

- **Link.** `https://<hostname>/s/<id>#<token>`: a 128-bit id (`l-` and 32 hex) and a
  256-bit token in the fragment, so no request line or access log holds the token. The
  page at `/s/<id>` clears the fragment and posts the token by itself, so previews that
  do not run scripts never use the link. It answers only while that id is live, and 404
  otherwise: a prober learns nothing without an id.
- **Records.** Links (`sandbox.share-link.v2`: id, token hash, issue and expiry, uses or
  none, scope, kind), spent-use markers, sessions and stack pages are files under
  `/run/sandbox-login/sites/<entry>/` on the service tmpfs, mode 0600, named by SHA-256
  hash or link id. The gate never stores a token or a session secret. They survive a
  process restart and end when the container is recreated, as on every apply that
  changes the service.
- **Opening.** A live link with a use left sets `__Host-sandbox_share`. The cookie is
  host-only, `Path=/`, `Secure`, `HttpOnly` and `SameSite=Lax`, with `Max-Age` set to
  the latest remaining session. It holds up to 8 session secrets, so one browser can
  hold several shares on one gate. The response is a 303 to the link's scope (or the
  stack page) with `Referrer-Policy: no-referrer`.
- **Refusals.** Expired, spent, revoked, malformed and unknown links, and an `Origin`
  other than the gate's (or `null`, which browsers send from a `no-referrer` page), all
  get the same 404. More than 30 link attempts per entry in 60 seconds
  get 429.
- **Forwarding.** Only origin-form targets with exactly one `Host` header equal to the
  gate hostname are forwarded. The gate sends `Host: <hostname>`, drops hop-by-hop
  headers and its own cookies, and passes everything else.
- **Lifetime.** A session ends at `session_ttl_seconds`, at its link's expiry, at revoke,
  or at `enabled_until`, whichever comes first. Every request checks that the session's
  link still exists, so a revoke ends access at the next request. A browser whose
  session ended can open an unspent link again. After `enabled_until` the gate answers
  404 to everything.
- **No indexing.** Every gate response carries `X-Robots-Tag: noindex, nofollow,
  noarchive`, replacing any value from the upstream, and every page the gate serves has
  a robots `noindex, nofollow` meta tag. `/robots.txt` answers `Disallow: /`.

## What it cannot do

It cannot identify who used a link: a forwarded share link works for whoever opens it,
and `list` shows only that a use was spent. The rate limit is per entry, not per
client, so a prober can make legitimate opens wait out the minute. A stack page grants
WordPress admin and the listed sign-ins to whoever holds its link. Anyone with Sandbox access can mint one, and is
effectively root on that server. Whoever holds an unused link has that access. The clock
that sets a token's expiry is the issuing machine's, compared against the service's.
