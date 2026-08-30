# Contract: Domain Resolution Service

## Boundary

Spec A supplies an `IngressNamingOffer`; B returns a `NamingResult`. B never activates an
HTTP/TLS route and C never participates in hostname selection.

### IngressNamingOffer

```json
{
  "instance": {"project_root": "/canonical/project", "label": "default"},
  "accepted_addresses": ["127.0.0.77"],
  "protocols": ["http"],
  "capabilities": {"wildcard": false, "tls": false},
  "fallback_url": "http://localhost:8123",
  "probe": {"address": "127.0.0.77", "port": 80, "protocol": "http"}
}
```

Validation requires a registered instance, at least one loopback address for a local name,
and a bounded fallback URL. An accepted address is data, never a shell fragment. The
`fallback_url` is recovery/display information only; it is never the status health target.
When selection can attribute a listener to the selected ingress, the optional `probe`
identifies its concrete loopback address, port, and protocol. The probe address must be
one of the accepted addresses.

### Operations

`observe(request) -> DomainResult`

- Read-only; reports hostname intent, pin/source, current owner/tier, actual answer,
  matching owned state, drift, and fallback.
- Status performs no resolver, DNS, ACME, ingress, or application mutation. For an owned
  binding, it validates a fresh DNS answer before attempting any selected-ingress HTTP
  probe.
- Does not start the authority, prompt, acquire privilege, flush caches, or write state.

`plan(request) -> DomainPlan | DomainResult`

- Pure after observation; selects exact/zone/external/incumbent strategy.
- Carries observation fingerprint, mutations, rollback operations, verification steps,
  consent/privilege requirements, and expected result.
- Returns a structured fallback/pending result when no safe plan exists.

`apply(plan, interactive) -> NamingResult`

- Rejects expired/mismatched fingerprints before mutation.
- Requires recorded consent or a current interactive acceptance.
- Before resolver qualification, installs or upgrades the fixed helper to the exact
  source-owned version. Its final read-only service identity (PID, start ticks, UID, and
  control group) is bound into authorization and revalidated inside the helper immediately
  before the resolver write.
- Starts/updates the authority before adding a routed-resolver rule; removes candidate
  authority state if route activation fails.
- Verifies fresh resolution to one accepted address. It does not call A to add a route.

`cleanup(owner, interactive) -> CleanupResult`

- Removes only unchanged owned binding state.
- For systemd-resolved, requires the current helper PID/start/UID/control identity to
  equal the binding's applied identity at the service boundary and again inside the helper
  before every receipt, fragment, or reload mutation.
- Removes a shared zone only after its final owner is gone.
- Reverts routed-resolver state before stopping the final authority binding.
- Returns incomplete recovery for drift/unavailability; safe to repeat.

### Read-only selected-ingress diagnostic

After fresh DNS validation returns exactly one answer accepted by the selected ingress,
status may probe the offer's attributable concrete loopback `probe` endpoint. The route
probe sends the requested hostname as an explicit HTTP `Host` value and, when an adapter
provides HTTPS probing, as TLS SNI. It does not resolve the hostname again, discover a
proxy, follow redirects, or disclose response bodies or headers. If an HTTPS SNI policy is
not available, the probe reports `ingress_probe_unavailable` rather than downgrading.

The public diagnostic fields are a closed envelope: `ingress` and `application` each
contain only a `state`, and `reason` contains only a stable `code`. No endpoint,
exception, header, body, or other adapter detail is exposed. The stable reason codes are
exactly `fresh_dns_unavailable`, `answer_mismatch`, `ingress_listener_unreachable`,
`ingress_connect_timeout`, `application_response_timeout`,
`application_http_unhealthy`, `ingress_probe_unavailable`, and `ready`.

### DomainResult envelope

```json
{
  "ok": false,
  "state": "pending_consent",
  "hostname": "demo.test",
  "hostname_source": "default",
  "strategy": "systemd-resolved",
  "strategy_source": "detected",
  "resolver": {"owner": "systemd-resolved", "tier": "adoptable"},
  "actual_answers": [],
  "expected_addresses": ["127.0.0.77"],
  "ownership": "none",
  "health": "fallback",
  "fallback_url": "http://localhost:8123",
  "reason": {"code": "consent_required", "message": "Run interactively to review resolver adoption."},
  "mutated": false
}
```

Stable `state` values: `ready`, `fallback`, `pending_consent`, `pending_privilege`,
`unsupported`, `incompatible_identity`, `foreign_collision`, `drifted`,
`cleanup_incomplete`, `invalid`.

Every failure/pending envelope includes `mutated`; non-interactive pending results must set
it to `false`. For selected-ingress status diagnostics, `fallback_url` remains recovery/
display information and is never the health target; the additive public fields are limited
to the closed `ingress`/`application` state objects and stable `reason.code` described
above. Output contains no secret or unrestricted host configuration content.

## Adapter contract

Every adapter declares identity, platforms, detection evidence, supported record breadth,
mutation/rollback verbs, privilege shape, and proof tier in `sandbox.network.manifest`.
Adapters receive typed models plus injected command/filesystem mechanisms. They may not
read the registry repository or another adapter's state.

An adapter may be selected only if:

1. observation positively matches its owner/mode;
2. requested exact/wildcard capability is supported;
3. manifest proof tier permits adoption;
4. pin, consent, credentials, and privilege are satisfied;
5. no foreign binding/endpoint collision exists.

The systemd-resolved implementation remains `implemented_unproven` and non-adoptable in
ordinary support until T067's normal Linux CLI proof is captured. The example envelope
above describes the eventual promoted state, not current advertised support.
