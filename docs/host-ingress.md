# Host ingress adoption

Adoption is OPT-IN. The default ingress is Sandbox's own Caddy proxy on every platform
and for every runtime; switch with `./sb domains use <provider>` and see
[the clean-URL default](clean-url-default.md). Proof tiers below gate adoption only —
with zero adoptable adapters the default provider still serves clean URLs.

Sandbox treats listener ownership as a host-safety boundary. Detection reads kernel listener
evidence and best-effort public process identity; it never binds a candidate socket, reloads
an incumbent, reads a product's private state, or changes DNS.

## Listener semantics

Ports are evaluated per advertised protocol. An exact `127.0.0.1:80` listener does not
conflict with a dedicated Sandbox loopback address, while `0.0.0.0:80` conflicts with every
IPv4 address on port 80. An IPv6 wildcard is considered an IPv4 conflict only when the host
reports effective dual-stack behavior. HTTP and HTTPS may not be served by different
incumbents for the same hostname.

The per-port instance URL remains valid whenever clean-URL adoption is unavailable. A
confirmed listener conflict is reported as a listener conflict, never as a Docker failure.

## Support tiers and proof

Only an adapter with a documented control surface and an accepted live lifecycle proof can
be advertised as adoptable. The checked-in production qualification currently names only
Linux system Caddy, exact HTTP, and evidence `037-t044-ubuntu-2404`; runtime strings,
configuration, environment variables, CLI/MCP values, and harness objects cannot add to or
widen it. Nginx Proxy Manager remains credential-pending; DDEV, Local, XAMPP, Laragon, and
WAMP are detection-only or outside-platform. Their classification comes only from public
listener/process evidence and cannot grant route mutation authority.

The current code enables only the Linux system-Caddy exact-HTTP adapter. Selection still
requires a live observed Caddy process with proven listener ownership and the installed
helper's read-only preflight to validate the complete active Caddy config. Changed or
unidentified owners, foreign listeners, Darwin, HTTPS, wildcard hostnames, and missing
helper/import control fail closed before route mutation. Local tests do not complete 037:
normal live Linux CLI adoption remains to be captured. Sandbox Caddy, Herd/Valet, nginx,
Apache, and Traefik remain unadvertised for incumbent adoption.

## Explicit scoped helper installation

The repository helper is not passwordless by default. After reviewing the network root and
the generated fixed verbs, an operator installs one root-owned copy and one UID/root-scoped
sudoers rule interactively:

```bash
./sb domains ingress status --json
sudo tools/ingress-helper.sh install /absolute/sandbox-home/runtime/network "$(id -un)"
```

The grant permits only preflight, redacted baseline discovery, validation, prepare,
activation, observation, rollback, and cleanup through the installed helper. It does not
permit helper installation. Every operational call must match the exact recorded network
root and caller UID. Prepare copies the candidate into a root-owned `/run` transaction,
then verifies its digest, canonical project owner, hostname, backend, and route ID before
the incumbent configuration is touched. Missing policy, changed ownership, symlinks,
candidate replacement, or an empty foreign-route baseline fails closed.

Route activation is separate from name resolution: runtime C supplies a backend, ingress A
offers acceptable addresses and capabilities, domain service B verifies the hostname, then A
may validate, apply, and verify one attributable route. A alone owns hostname, TLS, and
route cleanup.

## Recovery

Before an incumbent route changes, Sandbox records the exact successful route observation.
Cleanup compares the current observation with that record. Drift, unavailable incumbents, or
failed validation retain a minimal non-secret recovery record and leave the route untouched.
Shared packages and unrelated ingress routes are never removed.

Do not run live adoption, cleanup, or reload proof until the applicable domain-service and
ingress dependencies are accepted on `latest`. The evidence requirements and manual sequence
are in [the 037 quickstart](../specs/037-host-ingress-adoption/quickstart.md).
