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
  "fallback_url": "http://localhost:8123"
}
```

Validation requires a registered instance, at least one loopback address for a local name,
and a bounded fallback URL. An accepted address is data, never a shell fragment.

### Operations

`observe(request) -> DomainResult`

- Read-only; reports hostname intent, pin/source, current owner/tier, actual answer,
  matching owned state, drift, and fallback.
- Does not start the authority, prompt, acquire privilege, flush caches, or write state.

`plan(request) -> DomainPlan | DomainResult`

- Pure after observation; selects exact/zone/external/incumbent strategy.
- Carries observation fingerprint, mutations, rollback operations, verification steps,
  consent/privilege requirements, and expected result.
- Returns a structured fallback/pending result when no safe plan exists.

`apply(plan, interactive) -> NamingResult`

- Rejects expired/mismatched fingerprints before mutation.
- Requires recorded consent or a current interactive acceptance.
- Starts/updates the authority before adding a routed-resolver rule; removes candidate
  authority state if route activation fails.
- Verifies fresh resolution to one accepted address. It does not call A to add a route.

`cleanup(owner, interactive) -> CleanupResult`

- Removes only unchanged owned binding state.
- Removes a shared zone only after its final owner is gone.
- Reverts routed-resolver state before stopping the final authority binding.
- Returns incomplete recovery for drift/unavailability; safe to repeat.

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
it to `false`. Output contains no secret or unrestricted host configuration content.

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

