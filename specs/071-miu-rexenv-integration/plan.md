# Implementation Plan: Miu Management and RexEnv Integration

**Branch**: `feat/miu-rexenv-integration` | **Date**: 2026-11-09 | **Spec**: [spec.md](spec.md)

**Input**: Clarified feature specification and authoritative PRD. The requested `readyprd.md` is absent; `prd.md` is present.

## Summary

Deliver one complete, qualified outcome: managed Miu service/dashboard and GitHub App QA workflow plus first-class, explicitly selected RexEnv project runtime. Docker remains the default. Miu management APIs are proposed upstream through the authorized fork and PR; Sandbox depends only on reviewed, authenticated management interfaces. RexEnv operations must use its qualified native integration. Both tracks converge in a complete CLI and applicable MCP lifecycle with durable recovery, full database-and-file baselines, identity-safe GitHub behavior, and live disposable-host evidence. No partial runtime release hidden behind a flag is acceptable.

## Technical Context

**Language/Version**: Sandbox Python CLI/MCP; Miu Node.js (Node >=22.13 for built-in SQLite); RexEnv's supported native CLI/MCP interface. Sandbox adapter boundaries are listed below; native execution and snapshot mechanisms must pass their prerequisite gates before they can advertise a capability.
**Primary Dependencies**: Existing Sandbox runtime, ingress, resolver, shared job/secrets/resource/recovery services; Miu authenticated HTTP API and local stdin-only credential provisioning; RexEnv qualified native execution bridge. No Miu private-state editing or ambient GitHub fallback.
**Storage**: Sandbox registry and owned state for project/site mapping, operation identity and leases; Miu built-in SQLite RequestStore for atomic request/capacity reservation and private credential storage. Snapshot baseline must capture supported upstream DB export/import plus Sandbox-owned files and retain recovery metadata.
**Testing**: Focused unit/contract checks, followed by supported CLI and applicable MCP workflows on approved disposable Ubuntu 24.04 hosts. Tests/type-checks through the required helper. No runtime qualification has been performed.
**Target Platform**: Initial managed Miu/RexEnv qualification: Ubuntu 24.04 headless, exact supported builds pinned and verified. Other platforms remain unqualified.
**Project Type**: Python CLI/MCP runtime adapters and host service management, plus upstream Node.js HTTP service/API extension in the authorized Miu fork.
**Performance Goals**: No unmeasured throughput or resource savings claims. All operations have finite request, output, time and capacity budgets.
**Constraints**: Explicit opt-in, ownership and consent before effects; exact repository/revision/site identity; accepted request IDs resolve before retry; unknown outcomes retain leases/capacity; no remote root/install endpoint; shared-host execution is not described as isolation. DB-only snapshots cannot satisfy full-baseline readiness.
**Scale/Scope**: One managed Miu owner/run slot per host as upstream currently operates; bounded retention (10,000 Miu requests) and finite resource capacity. Fail closed when retention or capacity limits are reached.

## Constitution Check

- I–II: PASS in design. Runtime/site selection remains per registered project with registry-authoritative exact site identity; ambiguous identity refuses mutation.
- III: PASS in design. Sandbox implementation remains in importable package modules registered through explicit manifests/contracts; `sb` remains the entry file.
- IV: RELEASE GATE. Live CLI/MCP evidence on qualified disposable stack is mandatory; not yet run and no readiness claim is made here.
- V: PASS in plan. Idempotent proposals/operations, durable unknown fences and matching docs are in full delivery scope.
- VI: PASS. Docker/Caddy remains default and unchanged; RexEnv is opt-in. No legacy path removal planned.
- Additional constraints: PASS in plan. No plugin/runtime/wp/vendor changes; no secrets or private Miu state access. Live host work requires approval and disposable targets.

## Project Structure

```text
sandbox/                         # runtime adapter, commands, shared services, explicit registrations
  core/                          # runtime/resource/operation contracts as existing architecture requires
  commands/                      # CLI lifecycle and service proposal commands
  ...                            # explicit MCP group registration and host adapters
tests/                           # focused contracts, integration and acceptance coverage
specs/071-miu-rexenv-integration/ # this plan and design artifacts
Miu upstream fork (separate repo): management API/auth/store modules and tests
```

**Structure Decision**: Extend existing Sandbox runtime/ingress/resolver and shared job, secrets, resource and recovery services through their explicit manifests/contracts. Miu API/store/auth changes belong only in the authorized Miu fork. Generic execution uses a qualified native execution bridge, not an invented upstream `generic exec` command. Use the explicit ownership map below; do not create consumers of prohibited facade/registry internals.

## Delivery Phase Manifest

### Phase 1 — Complete managed Miu and RexEnv integration (single release outcome)

- **Outcome**: Users can manage the Miu service/dashboard and authorized QA, and operate explicitly selected RexEnv projects through the complete qualified lifecycle with truthful recovery.
- **Scope**: Miu upstream management API and CLI provisioning; Sandbox service lifecycle proposals/configuration/health/dashboard/run/evidence operations; App-only auth and consent; RexEnv opt-in runtime and owned site mapping; native execution; supported database export/import plus owned file capture/restore; operation ledger, unknown fences, leases/capacity, safe removal; docs; full CLI and applicable MCP acceptance on Ubuntu 24.04.
- **Assigned PRs**: Miu prerequisite: https://github.com/akashmdiu/miu/pull/1 (draft, head `77d67bcc90e1e88d86161fe4dc9ef48906229708`). Sandbox integration PR remains planned and unopened. These are dependent PRs.
- **Target release cycle**: Unassigned; blocked pending explicit release approval.
- **Dependencies and order**: (1) Review/land authorized Miu API and policy prerequisite upstream. (2) Implement Sandbox adapters and proposals against the reviewed contract while retaining fail-closed behavior. (3) Qualify exact RexEnv and Miu builds on an approved disposable Ubuntu 24.04 host. (4) Verify full DB+file baseline/recovery and Miu lifecycle/QA evidence end-to-end. (5) Assemble CLI/MCP outcome and documentation; release readiness only after all gates. Miu API review can proceed independently, but the assembled feature stays blocked until every gate passes.
- **Acceptance criteria**: Spec FR-001–025 and SC-001–008; two RexEnv projects remain isolated; full DB+file restore verified; no duplicate accepted operations after interruption; App-only scoped identity and no unauthorized writes; unsafe PRs refused before execution; Miu proposals disclose affected/preserved resources; qualified Ubuntu 24.04 headless Miu service/dashboard/QA/evidence/upgrade-recovery/removal; no neighbor/unowned resource changes.
- **Readiness**: Miu API prerequisite implemented and contributed as a draft; 92 management tests pass on Node 24.18.0 and Node 26.5.0 on macOS. Three source-review findings have fixes and focused regressions; the final cleanup commit awaits independent head review. Sandbox application implementation and runtime qualification are incomplete.
- **Blockers**: Miu draft has no upstream approval/merge or final-head review; verified App adoption and complete install/update management remain unbuilt; exact supported Miu/RexEnv builds and provenance not qualified live; no supported full-site snapshot mechanism yet demonstrated end-to-end; no host qualification, CLI/MCP acceptance, or recovery interruption evidence. Do not claim readiness or split a partial runtime release.

## Complexity Tracking

No constitution deviations are approved or planned. The cross-repository Miu API dependency is required by the spec's authenticated supported-interface requirement; Sandbox will fail closed until its contract is reviewed and available.


## Integration ownership and dependency boundaries

| Concern | Owning files | Contract |
| --- | --- | --- |
| Public feature models and validation | `sandbox/integrations/models.py`, `sandbox/integrations/manifest.py` | Immutable public service/site/build/qualification/proposal references; bounded external values parsed before service dispatch. Manifest registers provider id, schema owner and capability claims. |
| Supported Miu client | `sandbox/integrations/miu/client.py`, `sandbox/integrations/miu/service.py` | Only `/management/v1` and an explicitly selected origin. Inject a secret-use transport rather than reading Miu state, dashboard cookies or credential files. Pin request identity and revisions; resolve the original identity after transport ambiguity. |
| Miu lifecycle | `sandbox/integrations/miu/lifecycle.py` | Reviewed install/configure/upgrade/recover/remove proposals bind exact source/build, resource inventory, ownership, active-work observation and recovery coverage. Apply uses a fixed host-helper contract; no remote installer HTTP endpoint or unpinned download. Human App consent stays in Miu. |
| Feature-owned durable metadata | `sandbox/integrations/repository.py` | Private transactional records for service proposals, service ownership, site bindings, operation intent and unresolved effects. References registered projects and shared jobs; never replaces the registry or reads its JSON. Unknown effects retain reservations without timeout takeover. |
| RexEnv policy adapter | `sandbox/runtimes/incumbent/rexenv.py` | `invoke(OperationRequest)` returns the existing `OperationResult`; exact observed site id/domain/project/root must match the stored binding. Platform/build/ownership/trust/qualification checks precede effects. No global stack stop or implicit adoption. |
| RexEnv supported transport | `sandbox/integrations/rexenv/client.py` | Fixed finite CLI/MCP calls on the pinned build. Site ids are authoritative; domain-based CLI calls revalidate domain-to-id immediately before mutation. No invented generic exec command. |
| Trusted native execution | `sandbox/integrations/rexenv/execution.py` | Explicit trusted-shared-host bridge over the existing bounded process/execution gateway. Supported WP argv can use `site_wp_run`; arbitrary argv/tests require separately reviewed executable/root/env policy and positive child cessation. Absent bridge proof means that capability is unavailable, not a fallback. |
| Full baseline bridge | `sandbox/integrations/rexenv/snapshots.py` | Named baseline binds site/project/build, DB dump and complete owned file tree; finite quotas, no symlink traversal, stable/quiesced capture, retained rollback artifact, crash state and post-restore verification. Database adapter uses supported RexEnv export/import. Use existing recovery service for recovery-set storage; DB import timeout is an unknown effect. |
| Composition and selection | `sandbox/application/context.py`, `sandbox/config/wordpress_runtime.py`, `sandbox/config/descriptors.py`, `sandbox/runtimes/manifest.py` | Register the explicit backend through `RuntimeBackendRegistry`; extend canonical descriptor validation only. Keep Compose default, persisted ownership resolution and ingress responsibilities intact. Read clean-URL contract before network changes. |
| CLI and MCP | `sandbox/commands/miu.py`, `sandbox/commands/rexenv.py`, `sandbox/commands/manifest.py`, `mcp/wp-server/tools/miu.py`, `mcp/wp-server/tools/rexenv.py`, `mcp/wp-server/tools/manifest.py` | Thin adapters share the same feature service. Feature-owned parser registration; MCP explicit dependency injection, no `app.py` helper namespace. Existing runtime commands still dispatch through `RuntimeService`. |
| Shared mechanisms | `sandbox/application/job_service.py`, `sandbox/jobs/manifest.py`, `sandbox/secrets/service.py`, `sandbox/resources/service.py`, `sandbox/recovery/service.py` | Reuse public job submission/status/cancel, secret-use scopes, resource plans and recovery APIs. Extend explicit manifests only if a missing generic mechanism is demonstrated; feature adapters own RexEnv/Miu policy. |

### Mechanism gates before execution and snapshots

Source evidence establishes `rex --json site info <domain>`, DB export/import, and scoped WP argv; it does not establish generic execution, a full-site snapshot or atomic database rollback. Read the exact v0.9.7 source before selecting argument shapes. Never promote master-only evidence to the pinned build.

For the full baseline, export produces an artifact path owned by RexEnv; validate returned path, regular-file identity and ownership before opening through the bridge. Reject symlinks, hard links, unexpected roots, oversized SQL and inconsistent inventories. Copy only a positively owned file tree under a reviewed root, exclude no site content silently, and retain both restore point and a pre-restore recovery point. Hold the site lease through stop/quiesce, DB/files effects, restart and verification. The bridge must positively observe absence of writers; `site stop` alone is not assumed to stop every database client. The candidate export omits stored routines/events; complete coverage requires proving their absence or a supported complete dump. Per-site stop only changes serving and leaves services running. A missing quiescence mechanism is a precise release blocker, not permission to describe a best-effort copy as complete.

The Miu draft exposes App metadata as configured/unverified and build provenance as unknown where no supported attestation exists. Sandbox preserves those states. Full App create/adopt/grant inspection and truthful installed revision need upstream supported interfaces before service lifecycle qualification.

### Verification order

Finish all agreed Sandbox implementation and documentation first. Then run supported CLI/MCP acceptance on approved disposable targets; only afterward write regressions from observed behavior and run gates through the test helper. Static refusal/contract checks do not substitute for Ubuntu 24.04 full-lifecycle proof. Miu is a separate repository and its completed test-first evidence remains valid.
