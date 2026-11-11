# Implementation Plan: Unified tailnet sharing

**Branch**: `feat/stack-page-access` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

**Status**: Source design for implementation/review. No enabled capability or runtime acceptance.

## Summary

One canonical target union/management engine and existing auth gate support stacks,
recorded entries/paths and verified tailnet services. The clarified policy delegates
receiver-owner network reachability, not the initiating person's direct target ACL.
Use genuine ControlGate request receipts, explicit policy revision and receiver-side
node/address verification before pinned forwarding. Missing integration is unavailable.

## Technical Context

**Language/Version**: Existing Python hosting/controller/gate and TypeScript panel;
existing PHP only for explicitly declared WordPress sign-ins. No new auth server.
**Primary Dependencies**: Canonical share engine; real ControlGate receipt/current
member revalidation from the owning shared-auth source; trusted Tailscale daemon
observer; existing registered management/ingress transport and origin profile.
**Storage**: Versioned owned canonical grant records, safe recovery descriptors and
private verified-target config; no direct unrelated registry JSON consumers.
**Testing**: Root personally runs bounded focused/shared-boundary fixtures. No tests,
imports, builds, target probes or helpers are executed by this planning workflow.
**Target Platform**: Existing controller and owner receiving server; HTTP(S) services.
**Project Type**: Existing CLI/control transport/public gate feature.
**Performance Goals**: Contracted 10s total admission, 2s per observer/connection step,
5s tracked revocation-close budget; these are design bounds, not measured performance.
**Constraints**: No release/SDK publish/host jobs/credentials/live gates or forwarding;
no URL option until genuine registered capability. Human authentication release review
must explicitly approve intentional receiver-owner authority delegation.
**Scale/Scope**: Existing gate bounds remain; bounded 4096peer/2MiB observation frame,
4 pinned node addresses, URL<=4096bytes; finite new-URL uses1..20, existing kinds unchanged.

## Constitution Check

1. Explicit target/owner: PASS in design; genuine current owner receipt, explicit
   receiver policy revision and immutable target replace invented actor-target ACL proof.
2. Owned mechanisms: extend registered share/auth/observer APIs. No JSON namespace bypass.
3. One entry/module boundary: sb remains entry; common share engine/gate owns mechanisms.
   Transport and receiver observer adapters do not mint independent tokens.
4. Completion: NOT satisfied by source design. Root-run source plus separately approved
   real receiver/browser acceptance is mandatory; no source checkpoint closes the feature.
5. Idempotency/docs: original IDs before unknown-before-effects, no remint unknown;
   schema/contracts/docs/tests coevolve; existing 088 recovery fences retained.
6. Parity: no old path removed or silently disabled. Unsupported new URL support
   refuses without changing existing target-kind behavior.

Phase0 policy choice is RESOLVED by explicit user clarification and revised independent
PRD PASS. Actual installed actor/observer/connector/origin/stream capabilities are NOT
established. Absence must block URL READY, not source implementation of owned components.

## Project Structure

```text
specs/090-unified-tailnet-sharing/
  prd.md, spec.md, checklists/requirements.md
  plan.md, research.md, data-model.md, contracts/sharing.md, quickstart.md
sandbox/hosting/share/
  __init__.py             # canonical lifecycle/types, existing source
  tailnet_target.py       # planned closed URL/receiver target boundary
  tailnet_actor.py        # planned genuine receipt/policy adapter, no receipt issuer
  tailnet_observer.py     # planned bounded trusted receiver observation/connector
  tailnet_transport.py    # planned handler for existing registered control transport
sandbox/hosting/login_service/runtime.py  # common gate storage/session/proxy extension
sandbox/commands/host_share.py            # canonical target union/transport selection
```

MCP/server route wiring and the shared authentication source are integration-owner
files. Do not copy their implementations or edit their checkout. Own adapters must
consume the real public receipt API; missing dependencies refuse. Trusted owner
factory installs the real actor/observer/forwarding ports and isolated origin profile
into the canonical gate. Parser presence never announces runtime capability.

## Design Decisions

See data-model and sharing contract. Receiver Tailscale state supplies stable peer
identity and node-owned addresses; DNS must match the same peer. Connection uses
those pinned addresses with original Host/TLS identity and bounded non-HTTP probe.
No public/LAN/metadata/subnet-address shortcut. Actor input is genuine in-process
request state, never a serializable caller dictionary. Policy revision is an explicit
owner-delegation revision, distinct from ControlGate's process policy epoch.

Use per-grant isolated host-only origins under an existing provisioned origin profile
so simultaneous grants cannot select another upstream via cookie/path ambiguity.
Mint performs no DNS/TLS/ingress provisioning. Incompatible apps are unavailable;
never strip TLS/CSP/login or widen path automatically. Track closeable connections
under original grant/policy identity and retain incomplete cleanup if cessation fails.

## Phase Output and Acceptance Ownership

Phase1 artifacts are source contracts, not verified implementation. Phase2 tasks
must name root-run checks and integration-owner route/factory work. Independent
source/auth review and human release approval remain mandatory. Root receives no
progress message until this owner's requested work is ready for final handoff.

## Complexity Tracking

No constitution exception requested. A second auth system, fabricated actor mapping,
reachability-only target identity, copied dependency, or app-auth fallback is rejected.
