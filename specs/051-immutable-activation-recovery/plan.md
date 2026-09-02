# Implementation Plan: Immutable Activation and Recovery

**Branch**: `codex/feature-047-immutable-oci-clean` | **Date**: 2026-08-31 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/051-immutable-activation-recovery/spec.md`

## Summary

Add a target-fenced activation service that authenticates exact Feature 049/050 artifacts
through a machine activation binding and crash-safe Feature 050 proof-custody lease/pin,
inspects one-shot init before start, replaces selected Compose services from the proven
local digest with pull/build disabled, proves running identity/health/edge, and records
current plus one prior generation. Activation and rollback are operations in one state
machine. Feature 048 contributes observations only; adoption is zero-init only; rollback
requires a pre-forward machine compatibility grant.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Feature 049/050 closed models and canonical delivery projection;
Feature 050 authenticated prepared-lease/accepted-pin repository handoff; existing shared
`sandbox.hosting.recovery.repository.RecoveryRepository` as the sole outer `hosts.json`
parser/writer/locker; Compose/runtime adapter; durable request/edge replay authority; a new Feature
048 read-only activation-observer API (not failed-apply recovery authority)

**Storage**: Additive bounded fields in owner-only `$SANDBOX_HOME/runtime/hosts.json`;
existing per-target lock; no separate authority file and no secret values

**Testing**: `unittest`; fake runtime/edge/recovery adapters; crash/replay/race matrices;
synthetic secrets and forbidden-capability witnesses

**Target Platform**: Registered Linux Docker/Compose host matching Feature 050 proof

**Project Type**: Modular CLI/hosting orchestration service

**Performance Goals**: Finite per-phase deadlines; bounded output <= 1 MiB; selected
services <= 16, init steps <= 16, retained generations exactly 2

**Constraints**: No trust reinterpretation, broker/credential/helper/pull/build/tag/prune;
closed subprocess environments; no init replay after possible effect; edge uncertainty fences

**Scale/Scope**: One target, transaction, current generation, prior generation, plan/proof,
and shared owner per operation

## Constitution Check

| Principle or boundary | Assessment |
|---|---|
| I. Per-project | PASS. Explicit project/environment/registered target; no inference fallback. |
| II. Registry authority | PASS. Existing hosting repository owns additive state; no direct JSON consumer. |
| III. Modular package | PASS. New activation package owns policy/state machine; adapters own runtime effects. |
| IV. Live verification | PASS BY PLAN ONLY. Local fakes are not remote/edge/production proof. |
| V. Idempotency/docs | PASS. Shared owner, request digest, CAS, effect boundaries, receipts, tombstones, and docs. |
| VI. Parity before removal | PASS. Non-opt-in paths and old Feature 048 state remain compatible. |
| Secrets | PASS. Registry credentials are unreachable; app secret values remain in existing environment path and are not persisted. |
| Process | PASS. Inspect-before-start init, closed env, deadlines, bounded streams, termination proof. |
| Feature 049 | PASS. Closed plan equality only; no receipt/trust/signature policy import. |
| Feature 050 | PASS. Closed proof equality/local-presence validation plus proof-custody lease/pin port only; no broker/helper/pull import. |
| Feature 048 | PASS. Two read-only observations around a 051-owned non-authorizing provisional; protected effects and writes remain unreachable. |

No complexity exception.

## Project Structure

### Documentation (this feature)

```text
specs/051-immutable-activation-recovery/
├── prd.md
├── spec.md
├── checklists/requirements.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/activation.md
├── contracts/activation-state.md
├── contracts/recovery-integration.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/hosting/images/
└── activation/
    ├── __init__.py
    ├── models.py
    ├── policy.py
    ├── repository.py
    ├── service.py
    ├── init_runner.py
    └── runtime_observer.py

sandbox/transports/
└── remote_hosting_activation.py

sandbox/hosting/recovery/
├── models.py                    # additive read-only activation observation value
├── policy.py                    # exact pending-transition observation eligibility
├── service.py                   # read-only observer; no state/protected effect
└── repository.py                # sole outer hosts.json parser/writer/locker; narrow nested port

sandbox/core/_hosting.py         # capability registration only; no second state writer
sandbox/commands/hosting.py      # activate/adopt/rollback/image-recover dispatch

tests/
├── test_hosting_image_activation_models.py
├── test_hosting_image_activation_policy.py
├── test_hosting_image_activation_repository.py
├── test_hosting_image_activation_init.py
├── test_hosting_image_activation_runtime.py
├── test_hosting_image_activation_service.py
├── test_hosting_image_activation_recovery.py
├── test_hosting_image_activation_races.py
├── test_hosting_image_activation_cli.py
└── test_architecture_boundaries.py       # narrow activation package import/export gate
```

**Structure Decision**: A feature-owned activation package contains closed models,
policy, state machine, nested value codec/candidate transitions, and repository coordination.
It never parses, locks, replaces, or fsyncs outer `hosts.json`. The existing shared recovery
repository remains the sole outer parser/writer/locker and exposes one narrow activation
transaction port that preserves legacy/unknown fields. Runtime/Compose/edge calls remain
adapter effects. Feature 048 receives only a narrow observation projection and returns a
value without writing. Activation imports Feature 049/050 value validators plus one
authenticated stage-repository proof-custody port. Feature 050 remains the sole writer of
custody records; activation only coordinates the port with host-state acceptance and never
imports trust/staging policy or credential services.
`sb host image recover` is a new 051 dispatch; existing failed-apply recovery is untouched.

## Architecture and Effect Order

```text
plan + proof -> machine binding -> target lock -> host-state lock -> stage-ledger lock
  -> prepared proof lease/pin -> equality/policy/local preflight -> shared durable accept
  -> accepted proof pin -> reverse release of stage/state locks
  -> [init create -> inspect -> effect_entered -> start -> exit receipt]*
  -> exact local no-pull/no-build replacement -> coherent running/health proof
  -> immutable edge sub-request -> fresh proof -> atomic generation commit
```

The prepared proof lease is durable before validation, remains pinned across host-state
acceptance, and uses the durable activation-owner/request identity as holder. Its admission
deadline never auto-unpins: expiry forbids new acceptance, while same-holder crash replay
promotes an already committed exact acceptance or cancels only proven absence. Process and
unrelated recovery identities have no holder rights. Only the exact accepted activation
owner releases after terminal authority is durable. Effects remain blocked until accepted-
pin promotion is durable.

Adoption stops after exact no-effect proof and requires zero init. Rollback selects only
the retained prior generation after validating its pre-forward compatibility grant, then
uses the same replacement/proof/edge/commit path. Feature 048 returns exact-new/prior/
neither/ambiguous observation. Recovery takes a first Feature 048 observation, stores a
051-owned `authorizing: false` provisional with its exact evidence identity, immediately
re-observes, and only then enters the closed activation/rollback recovery matrix when
pre/post identity and epoch/generation/transaction match. `neither`/`ambiguous` never
promote; `exact_prior` only closes proven pre-effect work without generation advance;
`exact_new` promotes only when the current phase already has all required authoritative
receipts. The matrix-selected result/promotion is one atomic outer-state commit.

## Phase 0: Research Conclusions

See [research.md](research.md). All consequential choices are closed: one shared target
owner, inspect-before-start init, same-transaction edge replay, zero-init adoption,
pre-forward machine rollback grant, and additive observation-only Feature 048 integration.

## Phase 1: Design

- [data-model.md](data-model.md)
- [contracts/activation.md](contracts/activation.md)
- [contracts/activation-state.md](contracts/activation-state.md)
- [contracts/recovery-integration.md](contracts/recovery-integration.md)
- [quickstart.md](quickstart.md)

## Implementation Sequence

1. Author all acceptance/architecture/compatibility tests RED before source changes.
2. Add closed authority/projection/request/policy/generation/forward-subject/grant/
   receipt/observation/recovery-provisional/result and proof-pin binding value models.
3. Add additive state repository, exact target/state/stage lock order, proof lease/pin
   handoff, shared target ownership, CAS, replay, and tombstones.
4. Add inspect-before-start init runner and coherent runtime observer adapters.
5. Implement activation/adoption/rollback in one service and state machine.
6. Add a new Feature 048 read-only activation observer and distinct 051 recovery dispatch
   with 051-owned provisional/two-observation promotion; leave existing failed-apply
   recovery authority unchanged.
7. Add CLI/remote dispatch and existing edge replay adapter composition.
8. Run local suites, boundary scans, race/crash matrices, human security review, and
   leave live remote/edge/production gates explicitly open.

## Complexity Tracking

No violations.
