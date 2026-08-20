# Contract: Ingress Service and Clean-URL Sequence

## Sequence

```text
C backend requirements
  → A observe/select (no hostname route)
  → A IngressNamingOffer
  → B choose/preserve hostname + install/verify resolution
  → B NamingResult
  → A plan/apply route + end-to-end verify
```

A is the exclusive owner of hostname routes and TLS actions, including Herd/Valet. B may
not activate ingress; C may not create a hostname route.

## IngressNamingOffer

```json
{
  "selection_id": "sha256:...",
  "adapter": "system-caddy",
  "support_tier": "adoptable",
  "accepted_addresses": ["127.0.0.1"],
  "required_protocols": ["http"],
  "capabilities": {"tls": true, "wildcard": true},
  "expires_at": "2026-08-01T12:00:30Z",
  "fallback_url": "http://localhost:8123",
  "probe": {"address": "127.0.0.1", "port": 80, "protocol": "http"}
}
```

The offer is short-lived and bound to the listener/product fingerprint. It contains no
hostname route and no credential. `fallback_url` is recovery/display information only; it
is never the status health target. When present, `probe` is the attributable selected
ingress endpoint: its concrete loopback address belongs to `accepted_addresses`, and its
port and protocol are selected by the adapter.

## Operations

`observe(request) -> IngressStatus`

- Read-only; inspects every relevant endpoint separately.
- Reports bind scope even when the process/product owner is unavailable.

`select(request) -> IngressNamingOffer | IngressResult`

- Honors machine override over project pin, and pins over detection.
- Chooses exactly one adapter that can serve every required protocol/capability.
- Chooses Sandbox Caddy only when exact endpoints are free or Sandbox-owned.

`plan_route(offer, naming_result, backend) -> RoutePlan`

- Rejects expired offers, hostname answers outside `accepted_addresses`, foreign route
  collisions, unsupported backend shape, missing consent/credentials, or changed owner.
- Remains pure and returns validation/reload/health/rollback actions.

`apply_route(plan, interactive) -> IngressResult`

- Re-observes and validates preconditions before mutation.
- Applies the transaction contract from [adapter.md](adapter.md).
- Returns the clean URL only after incumbent route and end-to-end health succeed.

`cleanup_route(owner, interactive) -> IngressResult`

- Removes only unchanged last-applied state and verifies reload/baseline health.
- Drift/unavailability creates non-secret incomplete cleanup; repeat is safe.

### Selected-ingress status diagnostic

The domain-status consumer validates fresh DNS before attempting HTTP against this offer's
`probe` endpoint. The health request sends the requested hostname explicitly as HTTP
`Host` and, when an adapter provides HTTPS probing, as TLS SNI. It connects directly to
the concrete loopback endpoint: no DNS lookup, proxy discovery, or redirect following is
allowed, and response bodies/headers are not disclosed. If the selected adapter cannot
provide the required HTTPS SNI policy, status returns `ingress_probe_unavailable` rather
than downgrading. Status is read-only and performs no DNS, ACME, ingress, or application
mutation.

The public diagnostic envelope is closed to `ingress.state`, `application.state`, and one
stable `reason.code`; endpoint, exception, body, and header details stay private. The
stable reason codes are exactly `fresh_dns_unavailable`, `answer_mismatch`,
`ingress_listener_unreachable`, `ingress_connect_timeout`,
`application_response_timeout`, `application_http_unhealthy`,
`ingress_probe_unavailable`, and `ready`.

## IngressResult envelope

```json
{
  "ok": false,
  "state": "foreign_collision",
  "ingress": "system-caddy",
  "pin": null,
  "pin_source": null,
  "support_tier": "adoptable",
  "endpoints": [{"address": "0.0.0.0", "port": 80, "scope": "wildcard", "owner": "caddy"}],
  "protocols": ["http"],
  "capabilities": {"tls": true, "wildcard": true},
  "route": {"hostname": "demo.test", "ownership": "foreign", "health": "fallback"},
  "fallback_url": "http://localhost:8123",
  "reason": {"code": "hostname_claimed", "message": "The selected ingress already has a foreign route."},
  "cleanup": {"complete": true, "residual": []},
  "mutated": false
}
```

Stable states include `ready`, `fallback`, `pending_consent`, `pending_credentials`,
`pin_unavailable`, `capability_gap`, `port_conflict`, `foreign_collision`, `drifted`,
`rollback_complete`, `rollback_incomplete`, and `cleanup_incomplete`.

The `fallback_url` in this envelope is only a recovery/display path. It is never used as
the domain-status health target; status uses the selected attributable loopback `probe`
instead.

No confirmed listener conflict may use a Docker-unavailable reason code or message.
