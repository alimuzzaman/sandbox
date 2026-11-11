# Implementation Plan: Unified tailnet sharing

**Branch**: `feat/stack-page-access` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

**Input**: Reviewed PRD and specification for 090.

**Status**: Phase 0 research incomplete. No implementation-ready claim, Phase 1 contract or tasks yet.

## Summary

Extend the existing canonical share target model, authenticated management path and
session gate to verified tailnet web targets. Preserve stack/recorded-entry behavior
and 088 companion recovery. Root owns the thin wrapper and every executed check.
The specification is manually quality-checked; none of its acceptance scenarios
has been executed for generic URL sharing.

## Technical Context

**Language/Version**: Existing Python hosting/controller/runtime, TypeScript panel,
and existing PHP only for declared WordPress sign-ins. No new auth server.

**Primary Dependencies**: Existing canonical share package and gate, registered
remote management transport, tailnet identity/authority verification. The last
mechanism is NOT established by existing tailnet-name DNS configuration.

**Storage**: Existing durable original-grant identities and private gate records
must be extended through owned schema/contracts; no raw registry JSON consumers.
No new storage schema is finalized in this phase.

**Testing**: Root personally runs short synthetic and shared-boundary shards.
Real receiver/browser/stream and authorization proof require separate authority.
This planning run executes no tests, probes, imports, builds or helpers.

**Target Platform**: Existing controller and authenticated tailnet receiving server;
HTTP(S) browser services only, with explicit supported compatibility profiles.

**Project Type**: Existing CLI/controller, gate and web panel feature.

**Performance Goals**: Bounded admission/verification and streaming termination.
Exact deadlines, request/body/header/resource bounds and freshness budgets must
be finalized against the authority provider before Phase 1, not guessed into a
published option.

**Constraints**: No deployment, credentials, source SDK release, live share/gate,
remote job or workflow authority. No generic URL flag until required authority,
receiver, scope, application-auth and READY capabilities are implemented and reviewed.

**Scale/Scope**: Stack, recorded entry/path, and verified tailnet URL target union;
existing finite gate/storage bounds remain enforced. Browser compatibility and
additional URL-record limits remain Phase 0 design inputs.

## Constitution Check

1. Explicit target ownership: PASS as requirement; actor-to-target authority proof
   remains an unresolved design gate, never inferred from cwd or reachability.
2. Owned registries: PASS as constraint; extend registered APIs/contracts, no direct
   instance/hosting-state JSON consumers.
3. Single entry/modular package: PASS as direction; extend canonical share engine,
   not a standalone wrapper issuer or auth service.
4. Live verification: NOT SATISFIED for completion; root-run source shards and owner-
   authorized installed receiver/browser proof remain required.
5. Idempotency/docs: retain original IDs and unknown-effect fences; persist safe
   descriptors before effects. No implementation/schema has been enabled.
6. Parity: no existing path removed or disabled; no fallback bypass added.

**Phase 0 gate**: DO NOT proceed to Phase 1 contracts/implementation until the
receiver-side actor/node/ACL authority provider and its freshness/revocation
semantics are established. [research.md](research.md) names the missing evidence.
No constitution exception or broader authority has been approved.

## Project Structure

### Documentation (this feature)

```text
specs/090-unified-tailnet-sharing/
  prd.md                      # independent readiness PASS; unchanged by specify/plan
  spec.md                     # product requirements and acceptance matrix
  checklists/requirements.md  # document quality only
  plan.md                     # Phase 0 state; this file
  research.md                 # grounded findings and unresolved design evidence
```

Phase 1 data-model, contracts and quickstart, then Phase 2 tasks, remain uncreated.
This is deliberate sequencing, not completion by omission.

### Source Code (repository root)

```text
sandbox/commands/host_share.py         # existing canonical management entry
sandbox/hosting/share/                 # existing target, scope, attempt and page mechanisms
sandbox/hosting/login_service/        # existing gate/session and management receiver
sandbox/hosting/tailnet_names/        # existing DNS/ingress ownership; NOT actor ACL proof
sandbox/commands/ui_dash.py           # existing authenticated panel bridge
src/web/                             # existing share UI
```

**Structure Decision**: Reuse these mechanisms and add only an owned target/
verification seam after Phase 0 resolves the proof source. This is a proposed
integration boundary, not an approved wire/schema or implementation plan.

## Complexity Tracking

No new exception to the constitution is requested. A second authentication system,
manually interpreted ACL policy, public mailbox fallback or reachability-only
verification is not an accepted simplification.
