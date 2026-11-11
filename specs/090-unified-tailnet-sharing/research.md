# Phase 0 research: Unified tailnet sharing

Date: 2026-11-11. Local source and public official documentation reads only.
No authenticated API, credentials, receiver probe, test, compile/import or runtime
behavior was exercised. Design conclusions are not installed capability evidence.

## Resolved authority decision

The explicit user/coordinator clarification supersedes the earlier draft's stronger
actor-target ACL preservation condition. A genuine currently admitted server-owner
operator may delegate the receiver's verified network reachability in a bounded
bearer grant, including destinations the operator's own node cannot directly reach.
This consequence must appear in mandatory human authentication/release review.
No global Tailscale ACL edits, public/LAN/metadata targets or application-auth bypass
are approved. The revised PRD independently passed personal Sol high readiness review.

## Source facts

- Canonical share source currently provides site/stack kinds, derived short IDs,
  scope/lifetime/use enforcement and common audit. Generic URL support is not yet
  implemented or enabled by these design artifacts.
- Tailnet-name models/service own configured addresses, DNS/certificate/ingress
  records, not dynamic peer identity or actor admission. They are not used as proof.
- In the integration source, ControlGate.admit_request creates the genuine receipt;
  _receipt_current checks its issuer/epoch plus service principal or current member/
  server-observed peer identity. Middleware attaches request.state.control_receipt.
- The genuine shared authentication module's require_authenticated validates issued
  provenance and current policy in a synchronous worker. Receipts are nonserializable.
  That dependency is owned elsewhere and absent from this isolated090 topic; it must
  be integrated by its owner, never copied/faked or replaced by a login dictionary.
- Gate epoch is process admission identity, not the explicit owner-delegation policy
  revision. Both are bound; root-owned factory/route composition remains required.

## Official receiver evidence

Tailscale documents machine-readable peer/user metadata from status --json, while
warning that its format can change. A bounded adapter must parse a supported subset
and refuse incompatible/missing identity instead of assuming stable raw JSON.
[Tailscale CLI](https://tailscale.com/docs/reference/tailscale-cli).

The official ipnstate and LocalAPI source define daemon observation structures and
receiver-local status service behavior. The source is reference material, not proof
that this server runs a particular supported version or exposes that capability.
[ipnstate source](https://github.com/tailscale/tailscale/blob/main/ipn/ipnstate/ipnstate.go),
[LocalAPI source](https://github.com/tailscale/tailscale/blob/main/ipn/localapi/localapi.go).

Application capability inspection exists, but no new app grant/global ACL edit is
needed for the clarified owner delegation policy. The actual ControlGate receipt is
the actor source. Do not infer a person's target ACL from whois or receiver reachability.
[Application capabilities](https://tailscale.com/docs/features/access-control/grants/grants-app-capabilities).

## Selected design

1. Actual current ControlGate actor receipt plus explicit policy-revision binding,
   no caller actor fields/receipt serialization. Recheck around slow work and effects.
2. Current trusted receiver node/netmap observation and DNS answers all bound to one
   stable node. Only that node's actual TailscaleIPs, not subnet routes or name config.
3. Fixed address/Host/TLS target pinning and bounded TCP/TLS connectivity establishment
   under receiver identity. No HTTP probe or supplied secret-link redemption.
4. Canonical common grant/session storage and per-grant isolated public origin under
   an existing verified origin profile. Mint must not mutate DNS/certs/ingress.
5. Explicit compatible browser profile; no authority expansion to repair apps. Track
   closeable streams so revocation cannot report completion while forwarding survives.
6. New URL capability stays unavailable until all real actor/observer/connector/origin/
   management/storage/revoke paths are wired, reviewed and tested by their owners.

## Remaining implementation and acceptance dependencies

Real owner middleware-to-handler transport, authenticated dependency integration,
trusted receiver observer, pinned connector and registered gate origin/factory are
implementation work, not unresolved user policy. Their absence must be represented
as unavailable capability, not a reason to narrow the requested final feature.
No tasks or source definition can manufacture READY. Root owns all executed checks;
real receiver/browser proof and human authentication release approval remain external
required gates. Existing088 source review gaps and parked login are not waived.
