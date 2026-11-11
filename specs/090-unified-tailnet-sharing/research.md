# Phase 0 research: Unified tailnet sharing

Date: 2026-11-11. Source inspection only. No external API, credentials, network
probe, test, compile/import or runtime behavior was exercised.

## Established from reviewed intent and local source

- The independent 090 PRD readiness review passed. Generic destinations must use
  the existing canonical engine/gate and preserve actor rights, not just server reachability.
- `sandbox/hosting/share/__init__.py` currently defines site/stack kinds, derived
  short IDs, bounded path scope, finite lifetime, optional existing-kind use counts
  and common audit helpers. Generic URL support is not present in that target model.
- `sandbox/commands/host_share.py` resolves recorded targets/declarations and manages
  canonical grants. Its current records and operator opt-in are not by themselves
  proof of arbitrary tailnet node/service rights.
- `sandbox/hosting/tailnet_names/models.py` derives a configured remote tailnet IP
  and name/ingress target. `service.py` coordinates DNS/certificate/ingress ownership.
  Those scoped sources do not establish initiating-actor ACL authority for an
  arbitrary service on another node. Owning a DNS record is not that proof.
- 088 source checkpoints and root's supplied shadow counts are distinct from
  independent authentication release review and installed acceptance. Parked or
  missing application-auth/READY capability remains explicit unavailability.

## Unresolved design evidence

1. **Authoritative actor and policy source**: Identify a supported authenticated
   receiver-side mechanism that binds the actual initiating operator to current
   node/service/port authorization. It must account for server rights exceeding
   actor rights and policy changes. No policy evaluator or trusted actor mapping
   may be invented from a hostname, controller label or successful connection.
2. **Identity and freshness**: Establish authoritative node ID plus DNS/IP binding,
   source authenticity, maximum evidence age, handling of multi-answer names,
   address/node replacement, proof loss and live authority revocation. Existing
   name provisioning records alone are insufficient.
3. **Receiver/application readiness**: Define actual receiver support for HTTPS
   identity, immutable destination pinning, scoped forwarding, application auth,
   cookies/assets/redirects, WebSocket/stream termination and request limits.
   Source strings and a generic URL parser are not READY evidence.
4. **Secret-bearing URL policy**: Establish safe supported handling and rejection
   of URL content without fetching/redeeming it for probes or leaking through
   forwarding/logging/browser behavior. No secret link was supplied or accessed.

## Rejected shortcuts

- DNS suffix or tailnet address range alone; server reachability as actor ACL proof.
- A second auth server/token issuer, raw mailbox listener, or legacy autologin fallback.
- Blindly following redirects or dynamic DNS to repair compatibility.
- Calling missing proof an empty ACL/allowed result, issuing a grant first, or
  advertising an unsupported generic URL option.

## Resolution needed before Phase 1

Root/security owner must identify or approve the supported authority source and
actor-binding model; then source/documentation research can establish its exact
claims, freshness and failure semantics without credentials or runtime probes.
If no such source is available, generic targets remain unavailable; existing stack
and recorded-service paths continue unchanged. This dependency is not feature
completion and does not authorize removing required checks.

No decision to enable generic forwarding has been made. No contracts, code or
runtime state are created by this research note. Root retains integration and
all execution; independent source/auth review and owner release sign-off remain required.
