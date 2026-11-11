# Implementation Plan: Shared server secret use

**Branch**: `feat/shared-server-secret-use` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

**Input**: User-expanded full-source scope and accepted installation binding port. Prepared with installed speckit-plan, setup-plan and active plan template. Historical packet is in history/disabled-groundwork-20261111/. Independent expansion review is pending.

## Summary

Implement a clean-client to authenticated-server to broker to actual trusted-consumer path. Keep existing personal-local behavior. Replace only the new server-scope unconditional guard with negotiated installed dispatch. The client sends nonsecret reviewed references and original replay identity. The actual ControlGate supplies actor provenance; the server resolves canonical references, reserves intent, records preflight refusal or committed admission, and creates an in-process operation whose grants are checked at use and before each effect.

## Technical Context

**Language/Version**: Existing Python 3.12, Starlette control middleware, registered Sandbox command/services. No new dependency or generic job system.
**Primary Dependencies**: Accepted `sandbox.installation.binding` port, existing registered secret broker/source adapters, jobs registry, hosting recovery and target/source authority ports. Existing delivery admission validates job context only; it grants no secret authority.
**Storage**: Add credential-operation tables through the jobs repository service and its SQLite transaction discipline. No parallel JSON ledger or direct registry reads. Only nonsecret canonical intent, scoped replay key, outcome, admission, effect checkpoint and tombstones persist.
**Testing**: Deferred until all source/docs complete, then short meaningful CLI commands and focused regressions, each with explicit timeout <=60 seconds and actual walltime/log report. No full suites/builds/soak. Prior 27-failure round is not passed verification; source correction of its version trap is not a rerun.
**Target Platform**: Existing private Linux owner installation and future managed installation; no relocation or migration.
**Project Type**: CLI and authenticated control endpoint plus broker/consumer integration.
**Performance Goals**: Bounded request bodies, fields, status and execution deadline; reuse existing finite job limits rather than promise throughput. Proposed schema limits live in contracts/authenticated-consumption-v1.md and require review.
**Constraints**: No values in client, ordinary transport envelopes, argv, logs, polls, retained storage, artifacts, temp files or inherited environment. The intended server consumer may receive an explicit minimal environment or approved protected runtime-secret facility under the reviewed custody policy. No owner-bearer distribution, selfSSH, self-remote, --here, fake grants or weakening generic secrets-run guards.
**Scale/Scope**: One server-selected installation, exact reviewed source/artifact and target, per operation. Multiple simultaneous operators retain separate grants and equal owner policy. Installed availability is distinct from accepted source design.

## Constitution Check

| Principle | Pre-research / post-design result |
| --- | --- |
| I per-project | Use registered project identity and target service; no implicit instance |
| II registry authority | Jobs/source/target services own reads and writes; no consumer reads registry JSON |
| III modular entry | New modules under sandbox/hosting/shared_credentials; existing manifests own registration; sb unchanged |
| IV live proof | Full acceptance PENDING and unauthorized here. Source completion and short checks cannot prove installed operation |
| V idempotency/docs | Atomic replay and effect checkpoints; source and docs together |
| VI parity | Preserve existing local paths, facades and URL proxy; only newly selected server scope changes |

No constitution waiver. Source implementation is authorized; release acceptance is not asserted.

## Project Structure

### Documentation (this feature)

spec.md, plan.md, research.md, data-model.md, quickstart.md, tasks.md, contracts/authenticated-consumption-v1.md and history/disabled-groundwork-20261111/. PRD remains historical under the refine downstream boundary. Existing source-groundwork and future-owner-broker contract paths become historical pointers.

### Source Code (repository root)

| Owner | Files / responsibility |
| --- | --- |
| 086 root integration | sandbox/hosting/shared_credentials/{models,admission,service,broker,consumers,application,installation,manifest}.py; sandbox/hosting/credential_selection.py |
| 086 control integration | mcp/wp-server/server.py retains ControlGate proof and registers credential routes; mcp/wp-server/tailnet_access.py supplies immutable receipt factory |
| 086 ledger through jobs owner API | sandbox/jobs/registry.py adds credential operation transaction methods and versioned tables; sandbox/jobs/models.py exposes nonsecret projections |
| 086 client/driver | sandbox/commands/hosting.py, sandbox/commands/host_stack.py, sandbox/cli.py and sandbox/commands/manifest.py; sandbox/transports/hosting_credentials.py fixed authenticated client |
| 086 broker registration | sandbox/secrets/service.py callback consumption API and sandbox/hosting/shared_credentials/manifest.py fixed profiles/consumers; retain existing escalation guards |
| 086 application channel | sandbox/hosting/shared_credentials/application.py protected server runtime-secret facility and fixed launcher; no Hub repository edits or mandatory socket protocol |
| 086 matching docs/checks | README.md, skills/secret-inspection/SKILL.md, docs/remote-hosting.md and focused tests named in tasks.md |
| EXCLUSIVE installation owner, no 086 writer | sandbox/installation/binding.py, GET /hosting/installation, commands/remote.py and lifecycle join/adopt/refresh. Do not initialize identities in 086 |
| Sibling 085/088 | No edits; consume accepted target/source/lifecycle service ports; OwnerJobOperation is not credential admission |

**Structure Decision**: Keep one credential service with a narrow broker interface and fixed consumer manifest. Extend real job persistence for retained authority rather than reuse context-only evidence as admission or build a second generic scheduler.

## Phase 0: Research outcome

See research.md. Actual ControlGate result is discarded today. SecretService.use_profile launches an argv/env consumer and cannot grant arbitrary owner execution. Existing hosting provider env-file rendering is unsuitable for server provider credentials. Source design adds fixed trusted consumer callbacks and reviewed minimal-environment/protected runtime-secret delivery, without bypassing generic run guards. Legacy staging is not automatically approved.

## Phase 1: Design and implementation boundaries

Follow contracts/authenticated-consumption-v1.md for wire fields, accepted installation port, proof constructors, ledger transitions and consumer handoff. Complete all capable paths in source, including actual callback invocation. No injectable capability=true production bypass. Runtime capability registration requires handlers, ledger, broker and measured application adapter wired together; installed discovery alone is insufficient.

Read-only installation resolution precedes reservation. Broker preflight may internally read registered source data but exposes names/status only. Persist missing-key refusal before effects. Commit credential admission separately from lifecycle admission; then obtain required source-writer and target fence before staging. Check full installation binding, policy, source/profile revision, deadline/cancellation and target fence again at consumption and each later effect. Serialize effect starts and cancellation through the operation transaction/checkpoint, holding the appropriate existing target fence through the effect. A crash during an irreversible effect becomes effects_unknown, requiring inspection, never automatic redispatch.

Application delivery implements the reviewed protected runtime-secret facility in the corrected contract. Do not reuse arbitrary legacy render_env_file staging. No Hub app rewrite is authorized. Provider API calls use fixed in-process clients with explicit broker callbacks and no ambient credential fallback. Generated credentials keep their separate authorized lifecycle and are never provider substitutes.

## Validation coverage and release boundary

All 29 FRs require source review and focused caller-visible scenarios. Short source checks are pending; installed revision, native broker authority, real app artifact and runtime consumption evidence are unavailable. No actual GS ref, mapping, provider API compatibility or deployed handler is verified. Root acf08da6 owns integration and arranges independent expanded-packet review. Human review must precede release of authentication, custody and production-path changes.

## Complexity Tracking

| Limitation | Reason | Treatment |
| --- | --- | --- |
| Live proof unavailable | No runtime/deploy/credential authority in this task | Report pending, implement full source now, keep installed enablement separate |
| Agent context hook skipped | Formal086 docs-only ownership | Existing plan link already points here; root owns any context changes |
| Actual GS artifact uninspected | Supplied report says exact ref unavailable | Implement explicit adapter protocol and artifact validation; do not assert legacy image compatibility or retry GS |

Correction D2/D3: request-key lookup precedes mutable reference resolution for retained inspection; new reservations resolve refs. Server-local target authority uses RecoveryRepository.target_mutation_port with shared_credentials capability, target-wide transaction held across each effect, short jobs CAS only, real source-writer authority separate. See authenticated-consumption-v1.md for exact lookup schema and lock order.
