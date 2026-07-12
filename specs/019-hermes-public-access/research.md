# Research: Hermes Public Dashboard Access

## Decision: Cloudflare Access plus a remotely managed Tunnel

Use a self-hosted Cloudflare Access application for `hermes.asb.bd`, then route only
that hostname through a remotely managed Tunnel to a local loopback proxy.

**Rationale**: Access evaluates browser identity before routing. Tunnel uses an
outbound-only connector and keeps the Hermes origin off the public network. Cloudflare
documents Access applications as deny-by-default and recommends creating Access before
publishing a tunnel route.

**Alternatives considered**:

- Public VPS Caddy route with Access: rejected because direct-origin bypass needs a
  separate hardening and Access JWT-validation design.
- Direct public Hermes bind with upstream OAuth: rejected because it breaks the
  loopback-only invariant and depends on upstream hosted-mode configuration.
- SSH forwarding only: retained as fallback, but does not meet browser access goal.

## Decision: Caddy only on loopback

Use a dedicated `127.0.0.1:9120` Caddy fragment that proxies to Hermes at
`127.0.0.1:9119`.

**Rationale**: Caddy can provide a stable target for the connector, configuration
validation/reload/rollback consistent with feature 015, and optional hash-only Basic
Auth. The route is never a public origin listener.

**Alternative considered**: Direct connector-to-Hermes is simpler but removes the
secondary credential and separately diagnosable local boundary requested for this
feature.

## Decision: Basic Auth is optional and Argon2id-hash-only

Use Caddy Basic Auth only after Access when an operator explicitly enables it. Store a
hash, not the plaintext password, in the generated remote configuration.

**Rationale**: Caddy requires pre-hashed passwords and supports `argon2id`. Basic Auth
is useful as a separately rotatable gate but is not MFA and must not replace Access.

**Alternative considered**: Make Basic Auth mandatory. Rejected because it adds a
shared-secret lifecycle and can obstruct automated browser/interactive-session recovery
without materially improving the primary identity policy.

## Decision: Keep Cloudflare tokens separated by scope

Use distinct secret references for DNS, Access API, Tunnel API, connector token, and
optional Basic password.

**Rationale**: Current Cloudflare Access application/policy APIs are account-scoped;
the existing feature-015 token is zone-oriented. Remotely managed tunnel connectors need
only their tunnel token at runtime, but anyone with that token can run the tunnel.

**Alternative considered**: Reuse one broad account token everywhere. Rejected because
it weakens least privilege and makes it impossible to revoke one operational capability.

## Decision: Treat interactive traffic as a first-class acceptance gate

The public route must prove normal pages, authenticated dashboard operations, streamed
responses, and WebSocket/PTY behavior.

**Rationale**: Cloudflare supports proxied WebSockets and Tunnel supports WebSockets,
but connections can be restarted by edge changes and an HTTP-only health probe is not
sufficient for the dashboard.

## Sources

- [Publish a self-hosted Cloudflare Access application](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/)
- [Cloudflare Access policies](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/)
- [Cloudflare Tunnel overview](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/)
- [Tunnel permissions and token handling](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/remote-tunnel-permissions/)
- [Cloudflare Access application API](https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/applications/methods/list/)
- [Cloudflare reusable Access policy API](https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/policies/methods/list/)
- [Caddy reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
- [Caddy basic_auth](https://caddyserver.com/docs/caddyfile/directives/basic_auth)
- [Cloudflare WebSockets](https://developers.cloudflare.com/network/websockets/)
- [Hermes web dashboard](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/web-dashboard.md)
