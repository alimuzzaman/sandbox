# Implementation Plan: Observation-Only Hosting Recovery

**Branch**: `codex/host-observation-recovery` | **Date**: 2026-08-31 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/048-host-observation-recovery/spec.md`

## Summary

Add `sb host recover` as a durable, fail-closed recovery surface. Future durable
`host apply` children receive authoritative job/request context from the supervisor and
create a pre-effect operation receipt. Apply and recovery share a per-target file lock and
generation CAS in the existing atomically replaced hosting state. Recovery validates the
terminal job and immutable apply receipt, captures one bounded read-only host epoch including
exact config/image/topology/service/phase evidence, and either commits receipt-only
reconciliation or refuses. A separate confirmed request may call only the existing edge
continuation after immediate revalidation.

## Technical Context

**Language/Version**: Python 3.9+ standard library; POSIX shell only inside existing bounded remote observers

**Primary Dependencies**: existing `sandbox.commands.hosting`, `sandbox.core._hosting`, durable `JobService`/repository/supervisor, registered remote transport, Cloudflare/Caddy edge helpers, Feature 046 read-only host identity projection

**Storage**: additive version-1 fields in owner-only `$SANDBOX_HOME/runtime/hosts.json`; per-target owner-only lock files; bounded immutable attempts and compact non-reusable tombstones; machine-local keyed secret-binding material under `$SANDBOX_HOME/runtime/hosting/`

**Testing**: Python `unittest` focused hosting, job-supervisor, repository/policy, CLI contract, security/privacy, race, crash, and mutation-witness cases

**Target Platform**: macOS/Linux controller with an explicit registered provisioned Linux remote

**Project Type**: modular Python CLI plus existing SSH/read-only host observer and durable local job runtime

**Performance Goals**: one recovery observation within a 60-second default/300-second maximum deadline; at most 16 persistent services, 16 one-shot services, 32 images, 64 phases, 128 KiB receipt; lock acquisition bounded to 30 seconds

**Constraints**: no target inference; no legacy adoption; recovery source always clean; no secret values or raw config; no new remote executable protocol; no source/runtime/image mutation from observation; edge effects only after a second confirmed request; no Feature 047 priority claim

**Scale/Scope**: one explicit target and attempt per request; bounded history of 64 full attempts plus permanent compact tombstones; one host observer command per attempt

## Constitution Check

*GATE: PASS before Phase 0. Re-checked after Phase 1: PASS.*

| Principle or boundary | Assessment |
|---|---|
| I. Per-project model | PASS. Recovery requires explicit project, environment, and remote; it boots no instance. |
| II. Registry authority | PASS. Remote selection uses the registered API; no state JSON is read by a new consumer. |
| III. Modular package | PASS. Host CLI remains in its owning command module; new recovery models/repository/policy/service live in `sandbox/hosting/recovery/`. |
| IV. Live verification | PASS BY PLAN ONLY. Focused local checks do not prove remote behavior; disposable live acceptance remains an activation gate. |
| V. Idempotency/docs | PASS. Request identity, lock, CAS, immutable attempts, tombstones, and docs land together. |
| VI. Parity before removal | PASS. Existing apply/status/diagnose/default Docker-Caddy paths remain; nothing is disabled. |
| Module/protocol boundaries | PASS. Durable context is fixed supervisor metadata; hosting owns its receipts. No new consumer of legacy facades or raw registries is added. |
| Secrets/security | PASS BY DESIGN. Secret comparison uses a keyed opaque digest; values never persist or appear in output. Human security review is required before release. |
| Feature 047 boundary | PASS. Recovery consumes only authorizing read-only governance projection when available and owns no admission or priority. |

Post-design check remains PASS. The contracts use fixed typed data, target-scoped locking,
single atomic state writes, bounded observations, and fail-closed compatibility. No
constitution exception is required.

## Project Structure

### Documentation (this feature)

```text
specs/048-host-observation-recovery/
├── prd.md
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli.md
│   └── state.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/
├── commands/hosting.py              # thin CLI integration and existing edge adapter
├── core/_hosting.py                 # state compatibility and atomic persistence
├── application/job_service.py       # immutable descriptor request/source context
├── jobs/supervisor.py               # authoritative child context injection
└── hosting/recovery/
    ├── __init__.py
    ├── models.py                    # strict bounded identities/results
    ├── policy.py                    # pure eligibility/classification
    ├── repository.py                # target lock, CAS, attempts, tombstones
    └── service.py                   # job binding, observation, reconcile, edge dispatch

docs/remote-hosting.md
docs/remote-hosting-implementation.md
skills/sandbox-cli/SKILL.md

tests/
├── test_host_recovery_models.py
├── test_host_recovery_policy.py
├── test_host_recovery_repository.py
├── test_host_recovery_service.py
├── test_host_recovery_cli.py
├── test_hosting.py
├── test_job_service.py
└── test_job_supervisor.py
```

**Structure Decision**: A small feature-owned package keeps recovery policy and state away
from the already-large hosting command. Existing hosting helpers remain the only remote
observation and edge-effect adapters. The durable job runtime supplies authenticated
identity context generically; it does not import hosting.

## Research

See [research.md](research.md). All implementation choices are resolved.

## Design Artifacts

- [data-model.md](data-model.md)
- [contracts/cli.md](contracts/cli.md)
- [contracts/state.md](contracts/state.md)
- [quickstart.md](quickstart.md)

## Complexity Tracking

No constitution violations require justification.
