# Product Requirements Draft: Remote nginx front door

**Status**: Refining

**Created**: 2026-10-07

**Last Refined**: 2026-10-07

**Input**: "Remote hosting with a host-incumbent nginx as the public front door instead of Sandbox Caddy owning 80/443"

**Drafting Configuration**: Claude Opus 5.5 root drafting; one Opus read-only research agent for repository grounding

**Final Validation**: `PENDING` — independent readiness review

**Validated On**: N/A

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Sandbox remote hosting assumes it owns the server's public web ports. On every
registered remote, Sandbox installs Caddy and lets it take ports 80 and 443 for
production hosts (`sb host apply`), exposed instances (`sb deploy --expose`),
previews, and the remote MCP control endpoint (docs/remote-hosting.md §10:
"Caddy owns public 80/443 by hostname").

The operator is moving the `scaleway-sandbox` workload to a new server
(23.81.35.141, `alim-server-16-gb`) that is managed by the xCloud control panel.
On that server a host nginx already holds port 80, is managed by xCloud, and must
stay the front door. Docker is present and usable. Today Sandbox cannot host
anything there: installing Caddy would fight the incumbent nginx, and remote
provisioning has no check for a foreign listener, so the failure surfaces late as
a generic "could not configure HTTPS route" error.

Remote routing has no adapter seam. The existing ingress adapter system
(spec 037, `system-nginx` declared `implemented_unproven`) is scoped to the
local machine and explicitly excluded remote exposure. This feature adds the
remote counterpart so the move can happen without giving up any hosted feature.

## Users and Desired Outcomes

- **Operator with an nginx-managed server**: registers the server once as an
  nginx-fronted remote and then uses every remote hosting command exactly as on a
  Caddy remote, with the same public result.
- **Operator of existing Caddy remotes**: notices no change. Caddy stays the
  default and nothing about current remotes moves unless they opt in.
- **Owners of hosted sites (Lenzora, Amar Sonar Bangla, alimuzzaman.me)**: their
  sites keep HTTPS, access control, search-engine policy, aliases, uploads, and
  safe rollback after moving to the nginx server.
- **Agents operating Sandbox**: receive an actionable, specific refusal when a
  command would collide with a web server or hostname Sandbox does not own.

## Goals

- An operator can choose, per remote, that an existing host nginx is the public
  front door, and Sandbox never installs or starts Caddy on that remote.
- Every remote feature that works through Caddy today also works through nginx:
  production hosts, serve and redirect aliases, wildcard serve aliases (proxied
  only), exposed instances with aliases, previews, and the MCP control endpoint.
- One certificate rule for every Sandbox route on an nginx remote: a
  Cloudflare-proxied hostname uses a Cloudflare Origin CA certificate; a DNS-only
  hostname uses a publicly trusted certificate that Sandbox issues and renews.
- Access-control and edge behavior match the Caddy path: Basic Auth with
  `bypass_paths`, `bypass_routes` (method and path-template), and `bypass_ips`;
  the Cloudflare proxy-source check; removal of the `Authorization` header before
  it reaches the app on Basic-Auth-protected routes (the control endpoint's bearer
  authentication is preserved); robots-deny for previews, exposed instances, the control
  endpoint, and opted-in hosts; redirect aliases (308, path preserved, cycles
  rejected); HTTP-to-HTTPS redirect; IPv4 and IPv6 origin records; edge health
  checks; stale-route reporting and pruning; rollback on failed apply.
- Requests for hostnames Sandbox has not declared never reach a Sandbox app, and
  Sandbox routes never become the server's catch-all site.
- Upload size limits and streaming responses on Sandbox routes are not narrowed
  by the incumbent's server-wide settings, so WordPress uploads and the MCP
  streamable-HTTP endpoint behave as on Caddy.
- Access-control parity holds regardless of the incumbent's real-IP settings;
  where Sandbox cannot guarantee it, it refuses rather than serving with weaker
  access control.
- Sandbox secrets on the server (private keys, Basic Auth verifiers) are readable
  only by what needs them, not by sites the panel hosts.
- Sandbox-owned routing coexists with the panel's own sites; neither removes or
  rewrites the other's, and Sandbox never edits the incumbent's main
  configuration beyond the include points it already loads.
- Provisioning detects a foreign web server on ports 80/443 and refuses to
  install Caddy, naming the nginx option instead of failing late.

## Non-Goals

- Changing the default: Caddy remains the default remote front door, and the
  local clean-URL contract (docs/clean-url-default.md) is untouched.
- Removing, disabling, or degrading the Caddy path (constitution principle VI).
- Integrating with the xCloud API or making Sandbox-hosted sites appear in the
  xCloud dashboard. Sandbox manages its own routing files only.
- Supporting front doors other than nginx (Apache, OpenLiteSpeed, Traefik).
- Switching an existing remote between Caddy and nginx while it hosts anything;
  this release covers choosing the mode before a remote hosts.
- Migrating workloads from `scaleway-sandbox` to the new server, or DNS cutover.
  Those are operational follow-ups that use this feature.
- Local (non-remote) ingress changes; spec 037 already owns those.

## Product Scenarios

### Scenario 1 — Register an nginx-fronted remote

- **Starting state**: A fresh server where a host nginx owns port 80, Docker is
  installed, and the SSH user has passwordless sudo.
- **User action**: The operator registers and provisions the remote, choosing
  nginx as its front door.
- **Expected outcome**: Provisioning completes without installing or starting
  Caddy, the incumbent nginx keeps serving its existing sites, and the remote
  reports nginx as its front door.

### Scenario 2 — Publish a production host behind nginx

- **Starting state**: An nginx-fronted remote and a hosting manifest with a
  Cloudflare-proxied domain, serve and redirect aliases, Basic Auth with bypass
  rules, and `robots: deny`.
- **User action**: The operator runs host plan, then a confirmed host apply.
- **Expected outcome**: The public URL serves the app over HTTPS trusted through
  the Cloudflare edge, aliases serve or redirect as declared, auth and bypass
  outcomes match the Caddy path, robots policy is served, and the edge health
  check passes.

### Scenario 3 — DNS-only host gets a public certificate

- **Starting state**: A hosting manifest with a DNS-only domain on an
  nginx-fronted remote.
- **User action**: Confirmed host apply.
- **Expected outcome**: A publicly trusted certificate is issued and is renewed
  before expiry without operator action; the origin serves publicly trusted HTTPS.

### Scenario 4 — Exposed instance, preview, and control endpoint

- **Starting state**: An nginx-fronted remote.
- **User action**: The operator runs `sb deploy --expose` (with an alias), creates
  a preview, and enables the HTTPS MCP control endpoint.
- **Expected outcome**: Each is reachable at its hostname with the robots-deny
  default; certificates follow the proxied/DNS-only rule; stale routes are
  reported and removed only when pruning is requested; an authenticated
  streamable-HTTP MCP session completes a tool call.

### Scenario 5 — Remove routes and Sandbox's footprint

- **Starting state**: Sandbox routes and hosts are live on an nginx remote.
- **User action**: The operator destroys a preview, prunes a stale instance
  route, removes a host, and finally removes the remote's Sandbox routing.
- **Expected outcome**: Each step removes only Sandbox's routing and certificates
  for that hostname; the panel's sites keep serving; after full removal no
  Sandbox routing remains loaded.

### Scenario 6 — Failed apply rolls back cleanly (negative)

- **Starting state**: A working production host behind nginx.
- **User action**: The operator applies a change whose routing fails validation,
  or whose edge health check fails.
- **Expected outcome**: The previous routing is restored, every other site on the
  server keeps serving, and the command reports which phase failed.

### Scenario 7 — Foreign web server blocks Caddy (negative)

- **Starting state**: A server where a web server not managed by Sandbox owns
  port 80 or 443, registered without choosing nginx.
- **User action**: The operator provisions, exposes an instance, creates a
  preview, or applies a host.
- **Expected outcome**: Sandbox refuses before any change, names the process that
  owns the port, and points to the nginx front-door option. Nothing is installed.
  Sandbox's own Caddy on an existing Caddy remote is never treated as foreign.
  (This extends the confirmed "block Caddy install" decision to every command
  that would install or configure Caddy.)

### Scenario 8 — Hostname already served by the panel (negative)

- **Starting state**: The panel already serves `example.com` on the nginx remote.
- **User action**: The operator applies a host (including one with a wildcard
  serve alias), exposes an instance, or creates a preview whose hostnames match
  or cover `example.com`.
- **Expected outcome**: Sandbox refuses before any change when a declared
  hostname or wildcard alias matches or covers a hostname the panel serves, and
  names the conflicting site; the panel's site is untouched.

### Scenario 9 — Panel regenerates its own config (negative)

- **Starting state**: Sandbox sites are live behind nginx; the panel adds, edits,
  or regenerates one of its own sites.
- **User action**: None from Sandbox.
- **Expected outcome**: Sandbox sites keep serving; a later Sandbox status or
  doctor check reports whether each Sandbox route is still loaded and whether any
  Sandbox hostname is now also claimed by a panel site. A re-apply restores
  dropped Sandbox routes without touching the panel's sites, and refuses (as in
  Scenario 8) rather than take a hostname back from the panel.

### Scenario 10 — Sandbox routing would break the panel's checks (negative)

- **Starting state**: A Sandbox route depends on something that later becomes
  invalid (for example a removed certificate file).
- **User action**: The panel tests or reloads nginx for its own change.
- **Expected outcome**: Status or doctor reports the invalid Sandbox route before
  it blocks the panel, and the fix is a Sandbox action that needs no panel action.

### Scenario 11 — Certificate renewal fails (negative)

- **Starting state**: A DNS-only Sandbox host whose renewal cannot complete.
- **User action**: None.
- **Expected outcome**: Status and doctor report the failure, with the expiry
  date, at least 14 days before the certificate expires.

### Scenario 12 — Mode switch on a hosting remote (negative)

- **Starting state**: A remote that already hosts routes.
- **User action**: The operator tries to change its front door.
- **Expected outcome**: Sandbox refuses with guidance to choose the mode on a
  remote that hosts nothing; nothing changes. Choosing nginx on a remote where
  Sandbox's own Caddy is installed or listening is refused with guidance to
  remove Sandbox's Caddy first; Sandbox never removes it implicitly.

### Scenario 13 — Existing Caddy remote is unaffected (negative)

- **Starting state**: `scaleway-sandbox`, a Caddy remote, serving production.
- **User action**: Upgrade Sandbox to the release containing this feature and run
  host apply, deploy, and preview as before.
- **Expected outcome**: Routing state and command outcomes are unchanged.

## Proposed Product Behavior

- Front-door choice is explicit and recorded per remote at registration or
  provisioning. Caddy is the default when nothing is chosen.
- On an nginx-fronted remote, Sandbox manages only routing it owns, clearly named
  as Sandbox's, loaded through include points the incumbent already uses.
- Every Sandbox routing change on an nginx remote is checked before it takes
  effect, is reversible, and is serialized against every other Sandbox routing
  change on that server. Serialization is new for instance and preview routes;
  today only host applies are serialized.
- Certificates follow one rule for every Sandbox route: Origin CA for proxied
  hostnames, public automatic issuance and renewal for DNS-only hostnames.
- Status, doctor, and domain inventory report the front-door mode, whether each
  Sandbox route is loaded and reachable, invalid routes, and certificate expiry.
- Refusals name the conflicting owner (process or site) and the supported next
  step; they never stop or reconfigure a web server Sandbox does not own.

## Constraints and Dependencies

- Constitution principle VI: the Caddy path is not stubbed, gated, or removed;
  the nginx mode is additive.
- CLAUDE.md module boundaries: the new mode registers through an explicit
  manifest/contract, with capability checks before side effects.
- The SSH user needs passwordless sudo (already required by remote provisioning).
- The incumbent must be a standard nginx that loads included site files and can
  check a configuration before it takes effect. The first target is xCloud's
  nginx.org build, whose workers run as user `xcloud`, the same user as the
  panel's hosted sites.
- Cloudflare remains the DNS provider; Origin CA issuance uses the existing
  Cloudflare credentials.
- Lenzora's deploy pipeline is pinned to the installed Sandbox runtime on its
  remote; a release containing this feature must follow the existing
  remote-install-and-repin procedure.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Certificate ownership | Sandbox issues and renews certificates served by nginx | Works on any nginx server; no panel dependency | User, 2026-10-07 |
| Certificate rule for all routes | Proxied hostname → Cloudflare Origin CA; DNS-only → public automatic certificate (hosts, previews, exposed instances, control endpoint alike) | One consistent rule; disposable previews avoid public CA rate limits | User, 2026-10-07 |
| Release scope | All remote features: host apply, aliases, deploy --expose, previews, MCP control endpoint | Full parity lets the new server replace scaleway-sandbox | User, 2026-10-07 |
| Mode selection | Explicit per remote; a foreign listener on :80/:443 blocks every Caddy-installing or Caddy-configuring command with guidance | No silent mode switches on production servers | User, 2026-10-07 |
| Panel integration | Sandbox-owned nginx files only; no xCloud API; sites not shown in xCloud dashboard | No vendor dependency | User, 2026-10-07 |
| Default front door | Caddy remains default | Existing remote behavior; principle VI forbids degrading it | Existing policy |
| Supported incumbents | nginx only in this release | Matches the target server; bounds scope | User (approach choice), 2026-10-07 |

## Open Questions

- None.

## Acceptance Outcomes

- On the xCloud-managed server, Amar Sonar Bangla, Lenzora, and alimuzzaman.me
  (with its aliases) can each be applied behind nginx at a test hostname and serve
  HTTPS trusted through the Cloudflare edge (proxied) or publicly trusted at the
  origin (DNS-only), with edge health checks passing, and Caddy is never installed
  on that server.
- For a fixed request matrix (auth, each bypass kind, robots, redirect aliases,
  HTTP-to-HTTPS, Authorization header seen by the app), the same host on a Caddy
  remote and on the nginx remote returns the same status codes and auth outcomes.
- A request to the nginx server for an undeclared hostname never reaches a
  Sandbox app.
- Applying a wildcard alias that covers a panel-served hostname is refused
  before any change.
- A 50 MB WordPress media upload succeeds on a Sandbox host behind nginx.
- With the panel's real-IP rewriting enabled, Cloudflare-verified IP bypass still
  grants access only to the declared address, or Sandbox refuses to apply.
- An authenticated streamable-HTTP MCP session through the nginx control endpoint
  completes a tool call; an unauthenticated request gets 401.
- A DNS-only test host's certificate is renewed by a forced early renewal with no
  operator action, and a forced renewal failure appears in status and doctor.
- A deliberately failing apply restores the previous routing, and every other
  site on the server (Sandbox's and the panel's) keeps answering throughout.
- On a server with a foreign web server on :80, provisioning without the nginx
  option refuses before any change and names the listener.
- After the panel adds or edits one of its own sites, all Sandbox routes still
  serve, or status reports exactly which are missing and a re-apply restores them.
- Removing Sandbox's routing leaves no Sandbox route loaded and every panel site
  serving.
- Sandbox private keys and Basic Auth verifiers on the nginx server are not
  readable by the panel's site user.
- On `scaleway-sandbox`, Caddy routing files are byte-identical and command exit
  codes and status fields are unchanged before and after the release.

## Risks and Assumptions

- **Risk**: The panel may rewrite or remove files it did not create, or reload
  nginx with a config that drops Sandbox includes. Observed on the target server
  (2026-10-07): xCloud runs no resident agent; it connects as root over SSH for
  panel-initiated actions, and its scheduled jobs (`xcloud-cleanup`, hourly
  monitoring) do not touch nginx configuration. The remaining exposure is
  panel-initiated nginx regeneration (Scenario 9).
- **Risk**: nginx workers run as the same user as the panel's hosted sites, so
  secrets could be exposed to those sites unless Sandbox keeps them out of reach.
- **Risk**: Server-wide settings set by the panel (body size, timeouts,
  buffering, real-IP) apply to Sandbox routes unless overridden per route.
- **Risk**: Public certificate issuance needs the HTTP challenge to reach Sandbox
  through the incumbent nginx; a panel default site may intercept it.
- **Risk**: A shared nginx means a Sandbox mistake could affect panel sites;
  checking before every change and Scenario 10 detection are required.
- **Risk**: Parity spans many edge rules; subtle differences could weaken access
  control, so the request-matrix comparison is mandatory.
- **Assumption**: The panel tolerates additional nginx site files and reloads
  initiated outside the panel.
- **Assumption**: Passwordless sudo for the SSH user stays in place on the
  target server (granted 2026-10-07).

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [ ] The latest independent readiness review verdict is `PASS`.

**Readiness**: `NOT READY`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
