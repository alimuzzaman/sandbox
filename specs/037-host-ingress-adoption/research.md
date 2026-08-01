# Research: Host Ingress Adoption

## Decision 1: Kernel listener scope is authoritative; product identity is best effort

**Decision**: Normalize TCP listeners into address family, address, port, wildcard scope,
dual-stack behavior, inode/process/service evidence, and ownership confidence. Determine
conflict by endpoint overlap, not numeric port alone. On Linux prefer `/proc/net/tcp*` plus
bounded process/service evidence and `ss`; on macOS use `lsof` plus socket/service data.

**Rationale**: `127.0.0.1:80` and `127.0.0.77:80` can coexist, while `0.0.0.0:80`
conflicts with both. IPv6 `::` may also accept IPv4 depending on effective v6-only state.
Process identity may be hidden from an unprivileged observer, but the listener conflict
must still be reported correctly.

**Alternatives considered**: The current `bind(127.0.0.1)` probe produces false answers
for the proxy endpoint; parsing process names alone misses wildcard and namespace detail;
temporarily binding candidate sockets during status creates a race and is not purely
observational.

## Decision 2: Select once for all promised protocols

**Decision**: Build an `IngressOffer` only when one adapter owns or can bind all required
HTTP/HTTPS endpoints and supports requested TLS/wildcard behavior. B receives that
adapter's accepted listener addresses. Different products are never combined for one
hostname.

**Rationale**: Split HTTP/HTTPS ownership makes redirects, certificates, attribution,
health, and rollback ambiguous. An unrequested listener is preserved/reported but does not
expand the route mutation.

**Alternatives considered**: Per-protocol adapters appear flexible but produce broken
redirect/certificate semantics; always demanding both ports would reject valid HTTP-only
requests.

## Decision 3: Use owned fragments and the incumbent's full validation path

**Decision**: nginx, Apache, and file-configured Caddy adapters write one deterministically
named fragment only after positively identifying an already-enabled include surface. They
validate the complete current config, stage candidate state, validate the complete
candidate config under the incumbent service identity/privilege, atomically activate,
gracefully reload, and run post-apply route plus baseline health checks. Failure restores
the exact prior fragment and revalidates/reloads.

**Rationale**: Editing a shared monolithic file is hard to attribute and rollback.
Validating only a fragment misses interactions with foreign routes. The current host Caddy
already imports `/etc/caddy/conf.d/*.caddy`, providing a safe owned-fragment seam; its
service identity must perform validation because certificate files are not readable by the
developer account.

**Alternatives considered**: Appending to primary config risks clobbering user edits;
blind reload can take down all routes; treating command exit alone as health misses a
route that reloads but does not serve.

## Decision 4: Prefer persistent config over an ephemeral Caddy API unless resume is used

**Decision**: For a normal Caddyfile service, use an existing import directory and the
documented validate/reload flow. Use the Caddy admin API only when detection proves the
service is intentionally API-managed/persistent (for example resume mode), the control
surface is protected as required, and optimistic concurrency evidence is available.

**Rationale**: The official Caddy service warns that API changes are overwritten on the
next restart when `--resume` is absent. A successful ephemeral API call is not durable
route adoption.

**Alternatives considered**: Always using the admin API creates restart drift; rewriting
the primary Caddyfile violates ownership; an unavailable/unprotected surface remains
detect-only.

## Decision 5: Traefik adoption requires an existing file provider

**Decision**: Detect Traefik static configuration and adopt only an enabled, writable,
watched file-provider directory. Write one atomic dynamic file containing a unique router
and service. Do not modify static configuration. Verify through observed dynamic state and
an HTTP request; rollback the file if health does not converge.

**Rationale**: Traefik's file provider is its documented external dynamic-configuration
surface. Adding providers or labels to foreign containers would mutate installation policy
and couple Sandbox to an unrelated network.

**Alternatives considered**: Docker-label injection requires control of the foreign
container; dashboard/API mutation is not a persistent configuration contract.

## Decision 6: Herd/Valet routing belongs to A

**Decision**: Use documented CLI link/proxy/unlink/unproxy and secure/unsecure operations
through one adapter. C supplies document-root/backend requirements; B supplies `.test`
identity/resolution; A alone mutates the site/TLS route.

**Rationale**: Current Herd runtime provisioning mixes runtime and route ownership. A
single owner prevents double cleanup and makes Docker-backend-through-Herd equivalent to a
native Herd document root.

**Alternatives considered**: Keeping route actions in C would violate the three-feature
contract; private Herd/Valet config writes are unnecessary.

## Decision 7: Proof-gate every support tier and detect-only signature

**Decision**: The manifest declares detection evidence, control prerequisite,
capabilities, platform, credential/consent needs, and live-proof identifier. A mutating
adapter without matching evidence reports implemented-unproven/detect-only. Nginx Proxy
Manager, DDEV, Local, XAMPP, Windows-side Laragon/WAMP, and unidentified listeners never
mutate initially.

**Rationale**: Detection breadth is useful, but claiming adoption without a documented
and live-proven route lifecycle is unsafe.

**Alternatives considered**: Reverse-engineering private databases/UI APIs is brittle;
calling every `nginx` process “system nginx” is insufficient evidence.

## Decision 8: Preserve last-applied state and baseline health evidence

**Decision**: Before mutation, capture the exact owned fragment (if any), canonical route
observation, incumbent identity, validation result, and bounded health samples of existing
routes. Cleanup/update requires the observed route digest to match last-applied state.

**Rationale**: A Sandbox marker does not prove the operator did not later change the
route. Baseline samples make “incumbent stayed healthy” observable without claiming to
enumerate every possible route.

**Alternatives considered**: Hostname-only ownership can delete foreign configuration;
unbounded crawling is impractical; no baseline cannot prove rollback preserved service.

## Primary references

- nginx command-line control and config testing: <https://nginx.org/en/docs/switches.html>
- Apache HTTP Server control: <https://httpd.apache.org/docs/2.4/programs/httpd.html>
- Caddy API/config/reload: <https://caddyserver.com/docs/api>
- Traefik file provider: <https://doc.traefik.io/traefik/providers/file/>
- Laravel Valet proxy lifecycle: <https://laravel.com/docs/valet>
- Current host evidence: active Caddy owns `*:80`/`*:443`, imports
  `/etc/caddy/conf.d/*.caddy`, and runs from `/etc/caddy/Caddyfile`.

