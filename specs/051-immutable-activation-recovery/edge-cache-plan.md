# Feature 051 extension plan: declared edge-cache purge

This amendment covers Lenzora's two hosted environments without changing the
existing deployment split: production continues to use `host image activate`
(Feature 051 v2), while development continues to use ordinary `host apply`.

## Contract

“Purge all edge caches” means Cloudflare's zone-wide `purge_everything` for each
explicitly approved zone covering the selected manifest routes. It does not
clear browser storage, Next.js/server caches, Redis, R2, or an independently
managed CDN. The effective plan exposes both normalized routes and the exact
zone allowlist. A policy is opt-in; an enabled malformed or unavailable policy
refuses before deployment effects.

The manifest policy is:

```yaml
cloudflare:
  proxied: true
  tls: origin-ca
  ssl_mode: strict
  cache_purge:
    on_deploy: true
    scope: zone_all
    zones: [example.test]
```

Zone names are allowlisted, normalized, bounded, and must cover every declared
route. The provider must return the exact requested zone name and ID before any
purge is sent. No parent-zone search is allowed after an arbitrary provider
error.

## Durable effect protocol

The existing target-wide owner and outer `RecoveryRepository` remain the sole
host-state parser/writer/locker. A purge operation binds the registered remote,
target, project/environment, deployment request and revision, generation subject
when present, route digest, full purge-policy digest, ordered zone IDs/names, and
`zone_all` scope. Each zone has a bounded state (`prepared`, `effect_entered`,
`acknowledged`, `refused`, or `acceptance_unknown`). The operation is persisted
before its first provider write; `effect_entered` is persisted immediately before
each POST and the acknowledgement immediately afterward. Exact terminal replay
returns the stored receipt without a second POST. A timeout or connection loss
after effect entry is `acceptance_unknown`; the provider ID is not treated as a
global propagation proof and observation-only recovery never resubmits it.

## Integration order

For v2 activation and rollback: exact runtime proof → durable purge preparation →
per-zone purge acknowledgements → fresh route verification → fresh runtime proof →
generation commit. `observe_generation_v2` only reads retained purge evidence.
Adoption cannot purge. Existing development `host apply` uses the same order
after runtime readiness and route reconciliation; incomplete or uncertain purge
keeps edge state non-success and does not repeat source sync, init, or Compose.

## Files and gates

- Policy/value/provider boundary: `sandbox/hosting/edge_cache.py`,
  `sandbox/core/_cloudflare.py`, and `sandbox/core/_hosting.py`.
- Durable v2/host state binding: `sandbox/hosting/recovery/repository.py`,
  `sandbox/hosting/images/activation/v2_models.py`,
  `sandbox/hosting/images/activation/v2_repository.py`.
- Runtime/CLI composition: `sandbox/commands/hosting.py`, `sandbox/cli.py`.
- Lenzora manifests and protected deployment result classifiers are updated in
  the separate Lenzora checkout without touching its existing dirty files.
- Focused tests cover normalization, zone preflight, provider errors, crash and
  replay matrices, v2/apply integration, recovery/adoption zero-write paths,
  CLI output, and secret non-disclosure. Full local gates and live validation
  remain separate evidence; production deployment and zone-wide purge require
  explicit authorization.
