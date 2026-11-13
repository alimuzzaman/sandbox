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
| Each `deploy.derived_environment` key with `pushed_commit_sha` reaches `sandbox-login` from the apply's environment file, so apply and `sb host status` prove its revision like every other running service. A key starting `SANDBOX_LOGIN_` is refused | Rendered override and manifest parsing |
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

The installed owner panel uses this same saved target default for magic-link actions
on a stack page. It reads only the admitted target's native records from the owner's
managed state. If aliases have conflicting defaults, or the address is missing,
invalid or denied for an entry, creation stops before minting.
Address selection and final page validation hold all target and alias writer locks,
so a concurrent saved-default update cannot leave the page using an older identity.
A controller laptop's saved default is not an owner-server default. No email is
inferred from Git, the Tailnet peer or an application account, and no email enters
the share journal or audit.

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

Magic-link capture serializes mailbox snapshot, application request, and capture polling
with one process-local exchange lock shared by all magic-link entries. Waiting is bounded
by the upstream timeout; a busy lock returns HTTP 503 `capture_busy`. After acquiring
the lock, the gate checks that it is enabled and the source token is still current,
checks both again after the mailbox snapshot and immediately before sending the
application request, then releases the lock on every exit. This lock is separate from
the pending-token pool lock, so issuing, claiming, and evicting tokens does not wait for
mailbox I/O. This proves capture selection is serialized in source; it does not verify
that the real Hub permits multiple one-time tokens to coexist or defines compatible
token-replacement policy. Real Hub token policy remains UNVERIFIED.

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
        - {hostname: s.dev.example.test, mode: serve}      # the gate hostname
    cloudflare: {proxied: false, tls: acme}
    share:
      hostname: s.dev.example.test
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

- Pick a short gate hostname, such as `s.<zone>`: a link is the hostname plus 34
  characters. The hostname is not a secret; only the token is. It is still the only
  public way in, so give it no other route.
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
- **A private apply closes the public name first.** Before any source reaches the
  remote (a push to the deployed branch updates its working tree), an apply of a private manifest reads the front-door route (a failed read
  stops the apply) and installs a deny route: every name, the gate's included, answers
  404. The deny route is installed with a forced validate and reload and, on nginx,
  proven loaded by its own marker. It owns the hostnames, so no default or catch-all
  server answers for them. On nginx it keeps an HTTPS listener with whatever
  certificate the route already has; if the current route serves HTTPS and no
  certificate is left, the apply refuses. If any of that cannot be confirmed, the apply
  stops before the source changes. Routes Sandbox renders for a private manifest end with
  `# sandbox-private-route v1 <sha256 of the text above>`. The gate is therefore down
  from the closure until the new route is live, and an apply that is refused or fails
  before its route is installed leaves the deny route, or the previous route when it
  carries that mark.
- **A failed apply fails closed.** The apply recreates the containers before it installs
  the new route, and a failure does not roll them back. For a private manifest a private
  route the apply already installed stays, a marked previous route goes back, and
  otherwise the deny route stays. Run the apply again once the cause is fixed.
- Limits: on nginx, the deny route covers HTTPS only once the target's public
  certificate exists; on the very first apply HTTPS for those names is whatever the
  server answered before. Image activation checks privacy without the route tag.
  Applies and destroys of one target hold a remote lease (see `host_apply_lease_held` in
  [remote hosting](remote-hosting.md)); controllers older than this fix and the on-host
  stack reaper do not take it.
- A destroy removes the route before it stops the containers. If the teardown then fails,
  the names are unrouted while the runtime still runs: only a server-wide catch-all that
  proxies to the target's own loopback port could reach it.
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

A link reads `https://s.dev.example.test/s/#<token>`: the gate hostname, `/s/#`, and a
22-character token. `list` and `revoke` name it by its id, `l-` and 12 hex characters
taken from the token's hash; the id cannot be turned back into the link. Links minted
before 2026-11-11 read `/s/l-<32 hex>#<43 characters>` and keep working, revoked by that
id, until they expire (30 days at most).

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
`site_link_limit` (32 live links), `site_record_limit` (128 links kept, spent ones included,
until they expire or are revoked), `site_link_unknown`, `site_entry_unknown` (the running
service predates the entry: run `host apply`), `login_service_expired`, `email_required`,
`share_failed`.

### The stack page

`sb host share create --remote R --stack <slug> --json` requests one recorded stack
page and its direct `stack.uses` companions. Spec 088 source is integrated with
short links and owner-panel sharing in cbda71e. Final regression reconciliation,
independent authentication review and authorized runtime acceptance remain open. Installed v2
support is UNKNOWN. Read the [exact contract](../specs/088-stack-page-access/contracts/access.md)
and [future acceptance plan](../specs/088-stack-page-access/quickstart.md) before rollout.

The page shows bounded own routes, delivered revisions/health, inbox and direct Shared
routes. GET/HEAD/refresh issue no sign-ins. A click sends same-origin authenticated
POST `/__sandbox/stack/sign-in`, exact `{action,csrf}`, JSON and
`X-Sandbox-Stack-Action: 1`, using a live host-only stack session. The server chooses
only declared entry actions, `$wordpress`, or companion index actions. Navigation
success is `{ok:true,url}` once; status success is `{ok:true,available:true}`. Neither
DOM, persistent browser storage, query nor audit receives tokens. Browser proof pending.

Each click issues independently, with at most eight pending hashes per logical entry
across visitors/source links. Ninth evicts oldest unclaimed issuance. Unused links
fail after source revoke/expiry; source is rechecked through delayed exchanges.
Application sessions already established keep their normal lifetime. Plain CLI
singleton sign-ins remain separate. Shared WordPress requires a per-link claim plugin
and a fixed HTTPS online HMAC claim with signed receipt before auth. Callback carries
only token hash/nonce/time/MAC and kid, never raw token/key; audit excludes hashes too.
See the contract for exact framing, bounds and fail-closed receipt validation.

Companion descriptors are flat private gate metadata, not nested pages. An authenticated
non-consuming POST `/__sandbox/stack/companion-status` probes child token/id/expiry and
paired title/hosts/notes over fixed TLS origin, no redirects, five-second timeout.
Navigation returns the existing child fragment URL only after this check. Child sets
its own host-only cookie. Parent session confers no child, panel or proxy authority.
Child restart/lost page means unavailable; no automatic remint or fallback.

Before any key/grant effect, installed gates must advertise ALL of `stack-page-v2`,
`stack-signin-pool-v1`, `wordpress-claim-v1`, `companion-status-v1`. A capability string
in source is not installed capability or revision proof. Retained v1 pages are readable
by the compatible reader only while their tmpfs records remain present; v1 WP actions
are explicitly unavailable because the stateless plugin cannot enforce eviction.
Applying a new runtime resets gate tmpfs links, sessions, stack pages, and pools,
including legacy records. Reissue v2 explicitly after an authorized apply; there is no
silent migration or link survival promise. `sandbox.share-link.v2` is unchanged.
Review this compatibility limitation before release.

Creation must cover every own/direct-used route and inbox before any effect. Mapping
an ordinary route to no action means author-declared `open`, subject to known private/
Basic Auth restrictions. Missing linked-site access metadata is a gap, never evidence
of anonymous reachability. `share.route_sign_in` maps at most 16 exact served hostnames
to listed entry names or `$wordpress` for the configured WP primary. Magic-link mapping
uses link_origin hostname. `inbox_sign_in` is parser-optional but coverage-required
whenever recorded `ports.mailpit` exists, and must name a listed inbox_gate.
`stack_sign_in` parses unique names; v2 page limits actions to 16. The pending pool
limit of eight is a separate rule. Overflow/gaps refuse, never silently truncate.

Durable attempts write schema 3 with original-settlement evidence and original ids and bounded cleanup descriptors,
never bearer tokens, keys, emails or whole manifests. Unknown acceptance requires
list/revoke of original ids, not mint replay. Revoke parent then every companion;
retain all survivors on partial failure. Gate absence is separate from WP key removal,
and repeated cleanup must still remove the original per-link plugin. Missing parent
metadata does not erase attempts. Runtime copy-id/fingerprint must be independently
observed under ownership locks; descriptor equality alone is no proof, and same-slug
replacement must fail closed. Controller recovery fields are source-defined: attempt
summaries contain `attempt_id`, `phase`, parent `link_id`, `expires_at`, `target`,
`stack`, and per-grant `target`, `entry`, `id`, `expires_at`, `status`, `gate_revoked`,
and `wordpress_revoked`. Pending cleanup returns `ok:false`, `phase:cleanup_pending`,
`code:share_cleanup_pending`, and `still_live` rows with `target`, `entry`, `id`,
`expires_at`, `status`, and `reason_code`.

Stack list includes current `links` and `sessions`, plus durable `attempts`. A missing
current parent with recorded attempts returns empty current arrays and
`partial_reasons:["current_parent_unavailable"]`; an incomplete durable phase returns
`partial_reasons:["durable_cleanup_pending"]`. Stack-wide revoke combines durable
original-id cleanup and individually selected legacy grants from the current parent;
partial results use `phase:cleanup_pending`, `code:share_cleanup_pending`, `still_live`,
and sorted `partial_reasons` (`durable_cleanup_unconfirmed`,
`legacy_cleanup_unconfirmed`, or `current_parent_unavailable`). It does not send an
unbounded gate-wide revoke.

CLI JSON refusal envelopes include `ok:false`, `code`, and `message`; coverage refusal
also includes `gaps` rows with `stack`, `target`, `piece`, `code`, `fix`, and optional
`hostname`. Create, list and revoke print partial results and exit nonzero. Text list
output includes retained attempt and grant identities, statuses and partial reasons;
JSON retains `ok:false` and `partial_reasons`. Focused CLI fixtures exercise this
contract; they do not establish installed behavior. The panel API projects only bounded safe attempt and live-grant identifiers,
phase/status, expiry, and allowlisted reasons; list is HTTP 200 only when complete and
409 for partial/invalid projection. Private cleanup descriptors are not projected.
`expires_clamped` is a boolean; there is no detailed clamp-reason field.

An original operation with unknown completion stays fenced after a process crash.
Repeated revoke reports `mint_completion_unknown`; neither an absent gate record nor
a new request proves that the old writer has stopped. Confirmed live grants can be
revoked using their saved runtime identity; unknown unused capabilities must reach
their recorded expiry unless original completion can be established. This can delay
recovery and prevents an unsafe replacement mint. Owner-local execution additionally
retains its original process handle and native writer leases, as described in
[owner-panel-sharing.md](owner-panel-sharing.md).

The version-3 `original_settled` fact records completion separately from gate revoke.
It permits retrying WordPress removal after partial compensation. Version-2 records
remain readable with this fact false: legacy `gate_revoked` never implies settlement.
Legacy revoked/expired records with cleanup descriptors reopen as unknown cleanup,
including mixed attempts. A subsequent v3 save cannot close that uncertainty.
Descriptor-free terminal rows have zero-effect provenance: v2 required a descriptor
before the first key/mint effect. New v3 revocation requires original settlement
or a proven planned-only zero-effect closure.
No remote state migration runs automatically.

Controller stack issuance and panel revocation prove exact running source/config/access/target parity,
including current applied agent_mint, under the real physical apply leases. Gate/WP
effects use immutable container ids. A local unapplied enable or stale enable after
another controller opted out refuses before key/grant effects. Unknown transport
retains physical ownership for coordinated recovery; authentication expiry alone
cannot prove that the old writer stopped. Per-click entries honor their shorter
declared TTL as well as the ten-minute/source/window bounds. Mapped route rows open
their declared action; only anonymous routes render ordinary open links.

#### Worked stack template

This complete hosting declaration illustrates the declared fields for a repository
with `compose.yml` services `wordpress` and background `capture`. The latter must
actually be Mailpit with login configured using the same secret, API at `/mail`, and
any published capture port bound to loopback. The named mailpit port must be wired by
the repository Compose configuration through MAILPIT_PORT. No app or Compose code is
created here. `fixture-hub` must already be an isolated recorded same-remote stack
with its own complete gate/access declaration; `uses` does not provision it.

All domains are reserved placeholders, not deployment destinations. Replace the
synthetic image digest with an owner-reviewed real pin, choose an enabled_until
within 30 days at apply, supply CAPTURE_PASSWORD through the approved secret workflow,
and select an authorized remote/ref before planning. The digest below is intentionally
syntactic, not an image availability claim. No hostname, IP, email or credential from
a real service is needed in committed examples.

```yaml
version: 1
project: fixture-{slug}
stack:
  slug: '^[a-z0-9][a-z0-9-]{0,30}$'
  ports: [mailpit]
  uses: [fixture-hub]
environments:
  sandbox:
    compose:
      files: [compose.yml]
      service: wordpress
      container_port: 80
      background_services: [capture]
    healthcheck: {path: /, statuses: [200, 302]}
    host:
      primary: wp-{slug}.example.test
      aliases:
        - {hostname: 'share-{slug}.example.test', mode: serve}
        - {hostname: 'inbox-{slug}.example.test', mode: serve}
    deploy:
      allowed_branches: ['{ref_branch}']
      require_clean: true
    cloudflare: {proxied: true, tls: origin-ca, ssl_mode: strict}
    secrets:
      required: {CAPTURE_PASSWORD: FIXTURE_CAPTURE_PASSWORD}
      values:
        MAILPIT_PORT: '{port:mailpit}'
    autologin:
      user: fixture-admin
      service: wordpress
      request_path: /wp-login.php
      container_path: /var/www/html/wp-content/mu-plugins/99-sandbox-host-autologin.php
      ttl_seconds: 600
      login_service:
        image: python:3.13-alpine@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
        enabled_until: 2026-12-01
        session_ttl_seconds: 3600
        allowed_host_suffixes: [example.test]
        capture:
          service: capture
          adapter: mailpit_v1
          api_url: http://capture:8025/mail
          username: fixture-capture
          credential_secret: CAPTURE_PASSWORD
      entries:
        mail:
          mode: inbox_gate
          requires_email: false
          hostname: inbox-{slug}.example.test
          path: /__sandbox/login/mail
          ttl_seconds: 600
          landing_path: /mail/
    share:
      hostname: share-{slug}.example.test
      private: false
      agent_mint: true
      stack_sign_in: [mail]
      route_sign_in:
        wp-{slug}.example.test: '$wordpress'
      inbox_sign_in: mail
```

`$wordpress` is reserved, never an entry name or member of stack_sign_in. Route mapping
hostname KEYS may use approved placeholders; entry names and inbox_sign_in stay literal.
WordPress's `user`, `service`, `request_path`, `container_path` and `ttl_seconds`
may accompany `login_service` and `entries` in the same `autologin` block. Both
integrations retain their existing validation; named entries alone do not enable
WordPress login. The normalized WordPress settings also enter applied sharing facts.

This is a declaration example, not an applied fixture. Parser success would still
not prove complete route coverage or installed support. Real WP/MySQL,
multisite, persistent cache, uniform 404/timing and browser runtime acceptance remain
unverified.

### From the panel

On a controller, each stack card on the `sb web` Hosting page has **Share this stack**
and **Live links**. Share asks once, then mints a stack link with the defaults (7 days,
no use limit) through `create_share(remote, stack=<slug>, caller="panel")`, so it needs
`share.agent_mint: true` like any agent. The link is shown once, with a copy button,
its expiry and use limit; it reads `https://<gate>/s/#<token>`. It is never saved: not in the page's data, browser storage,
the URL or a log. Close the dialog and it is gone; revoke it from Live links, which
lists ids, expiry and uses for links and sessions, never a link itself.

Refusals show their code and message. `agent_mint_not_enabled` and
`stack_share_not_declared` come with the fix. A server's own panel has no controller
record for its stacks and shows the `sb host share create` command to run on the
controller instead.

The panel has no login, so the share route (`POST /api/hosting/share`) refuses any
request that does not carry an Origin equal to its Host, a JSON body and the panel's
own `X-Sandbox-Panel` header, and no page may frame the panel. Over plain `http://<tailnet IP>` the browser blocks the
clipboard API; the dialog selects the link for a manual copy instead. Contract:
`specs/081-hosted-share-links/contracts/panel-http.md`.

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
- Links open at `/s/#<token>` on the gate hostname. A declared `path` is accepted for
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
./sb host login-url ... --service mockup --revoke l-<12 hex> --confirm   # or s-..., or all
```

- **The link is shown once, on your terminal.** The command writes it to the controlling
  terminal. Stdout and `--json` carry only the link id, expiry and uses. With no
  terminal (a background job, an agent's shell, a pipe) it refuses with
  `terminal_required` and mints nothing, so the link never lands in job output, logs
  or a transcript.
- `--uses N` (1 to 20, default 1) is how many browsers the link may start a session in.
- `--list` needs no `--confirm`. It shows ids, times, uses spent (spent links too, until
  they expire, so they can be revoked) and sessions, never a
  link or a hash.
- Revoking a link also ends the sessions it started. Revoking ends an open websocket
  within 30 seconds.

Refusal codes: `terminal_required`, `uses_out_of_range`, `site_options_conflict`,
`site_gate_only` (site options on another entry), `site_link_limit` (32 live links),
`site_record_limit` (128 links kept, spent ones included),
`site_link_unknown`, and `site_entry_unknown` (the running service predates the entry:
run `host apply`).

### Gate contract

- **Link.** `https://<hostname>/s/#<token>`: one 128-bit token (22 URL-safe characters)
  in the fragment, so no request line, access log or `Referer` holds it. The page at
  `/s/` clears the fragment and posts the token by itself, so previews that do not run
  scripts never use the link. The page is the same whatever links exist, so it tells a
  prober nothing. The gate stores only the token's SHA-256 and finds the link by
  comparing that hash, in constant time, with every stored record. A link's public id
  is `l-` and the first 12 hex characters of the hash.
- **Old-form links.** A link minted before 2026-11-11, `/s/l-<32 hex>#<43 characters>`,
  opens on its own path until it expires: that page answers only while its id is live.
  Nothing mints this form any more, and the path goes once the last such link has
  expired.
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
  stack page). Stack action and WP fragment openers use `Referrer-Policy: same-origin`
  to preserve same-origin POST Origin; token-bearing cross-origin referrals remain excluded.
- **Refusals.** Expired, spent, revoked, malformed and unknown links, and an `Origin`
  other than the gate's (or `null`, which browsers send from a `no-referrer` page), all
  get the same 404, with the same body and headers, after the same lookup. More than 30 link attempts per entry in 60 seconds
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

The capability reply includes public boolean panel_agent_mint. Panel recovery
without a manifest checks it on the exact original bound running gate before
revoke/key effects. Disabling agent_mint refuses panel mutations; ordinary CLI
operator cleanup remains available. Both durable and current legacy stack cleanup
hold physical leases and address immutable service ids.
