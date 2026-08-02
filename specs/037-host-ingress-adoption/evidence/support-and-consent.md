# Support tiers, pins, and consent (macOS)

**Scope**: T063 — live capture of advertised support tiers, pin precedence, consent
behaviour, credential-pending and detect-only reporting. Captured on darwin through `./sb`.

**Host**: macOS 15 (Darwin 25.6.0). 2026-08-02.

## Advertised tiers

```text
  sandbox-caddy          implemented_unproven   adoptable=False caps=http,https,wildcard
  herd-valet             implemented_unproven   adoptable=False caps=http,https,wildcard
  system-nginx           implemented_unproven   adoptable=False caps=http,wildcard
  system-apache          implemented_unproven   adoptable=False caps=http,wildcard
  system-caddy           implemented_unproven   adoptable=False caps=http
  traefik                implemented_unproven   adoptable=False caps=http,wildcard
  nginx-proxy-manager    credential_pending     adoptable=False caps=http,https,wildcard
  ddev                   detect_only            adoptable=False caps=http,https
  local                  detect_only            adoptable=False caps=http
  xampp                  detect_only            adoptable=False caps=http
  laragon                detect_only            adoptable=False caps=http
  wamp                   detect_only            adoptable=False caps=http
  unidentified           unidentified           adoptable=False caps=-
```

Every mutation-capable adapter is `implemented_unproven` and therefore `adoptable=False`
(FR-010, FR-011). Nginx Proxy Manager reports `credential_pending`; DDEV, Local, XAMPP,
Laragon and WAMP report `detect_only`; anything unrecognised is `unidentified`. No tier is
promoted by code presence alone.

Note the product consequence, which is why the default provider matters: with zero
adoptable adapters, adoption cannot serve a single clean URL on this host today. The
default Docker/Caddy provider does (`evidence/default-provider.md`).

## Pin precedence

```text
project only  provider=system-nginx   source=project          adoption=True
machine wins  provider=herd-valet     source=machine_override adoption=True
env wins      provider=traefik        source=environment      adoption=True
disabled      provider=disabled       source=machine_override adoption=False disabled=True
nothing set   provider=sandbox-caddy  source=default          adoption=False
```

Machine-local beats the committed project pin, and the environment beats both (FR-023).
`./sb domains use --project-dir <dir>` reports the effective provider including the project
layer, and reports `default: sandbox-caddy` alongside it. An unknown id is refused before
any write:

```text
./sb domains use nope  ->  state=invalid reason=unknown_provider (lists known ids)
```

## Consent and non-interactive callers

```text
domains ingress apply --json      -> {"ok": false, "state": "requires_domain_handoff",
                                      "reason": {"code": "verified_naming_required"},
                                      "mutated": false}
domains ingress reconsider --json -> {"ok": false, "state": "invalid",
                                      "reason": {"code": "consent_identity_required"},
                                      "mutated": false}
```

Both returned immediately, without a prompt and without mutation (FR-019, FR-020). Route
activation refuses to proceed from the low-level transport at all: it requires B's verified
naming result first (FR-008).

## Consent lifecycle (Ubuntu 24.04, live)

Captured against the proven system Caddy adapter on the Ubuntu host:

```text
non-interactive, no consent   ok=False  pending_consent   consent_required   mutated=False
interactive decline           ok=False  fallback          consent_declined
  recorded: {"decision": "declined", "policy_version": 1}
authorize again               ok=False  fallback          consent_declined   prompted 0 times
reconsider <identity>         ready
authorize after reconsider    ok=True   ready             prompted 1 time
  recorded: {"decision": "accepted", "policy_version": 1}
```

A remembered decline suppresses the offer entirely — the decider was never called again —
until an explicit `reconsider` clears it (FR-019, FR-021, FR-022). The non-interactive
caller returned immediately with no prompt and no mutation (FR-020).

## Not covered

- Credential storage for Nginx Proxy Manager (`credential_pending` was observed, but no
  credential was supplied).
