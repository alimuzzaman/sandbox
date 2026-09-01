# Implementation Plan: OCI Trust and Verification

**Branch**: `codex/feature-047-immutable-oci-clean` | **Date**: 2026-08-31 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/049-oci-trust-verification/spec.md`

## Summary

Add a closed, deterministic OCI trust package that normalizes machine policy,
untrusted project intent, and a canonical release receipt into an immutable
`VerifiedImagePlan`. The package is a pure value/policy layer: it has no credential,
Docker, process, remote, clock, randomness, or repository authority. Feature 050 and
051 consume the plan and canonical `DeliveryIdentityProjection` through its closed
contract and cannot reinterpret trust. Registry visibility is explicitly unobserved
here and deferred to Feature 050.

## Technical Context

**Language/Version**: Python 3.11+ using the repository's supported interpreter

**Primary Dependencies**: Python standard library (`dataclasses`, `hashlib`, `json`,
`re`, immutable mappings); explicit config-provider manifest

**Storage**: None

**Testing**: `unittest`; pure contract/property fixtures; effect-denial witnesses

**Target Platform**: Controller-side, platform-independent pure Python

**Project Type**: Modular CLI support library

**Performance Goals**: Deterministic validation of a maximum-size document in under
100 ms on the supported development host with bounded allocation

**Constraints**: Closed schemas; canonical JSON; no external I/O or time/random reads;
no secret-derived values; input document <= 128 KiB; <= 64 services; safe diagnostics

**Scale/Scope**: One policy, receipt, project intent, and plan per verification

## Constitution Check

| Principle or boundary | Pre-design | Post-design |
|---|---|---|
| I. Per-project instance | PASS. Verification requires explicit project intent and boots no instance. | PASS. No instance or registry access exists. |
| II. Registry authority | PASS. No runtime registry/state is read. | PASS. Machine policy is normalized through an explicit provider, not raw state. |
| III. Modular package | PASS. New logic lives under `sandbox/hosting/images/` and config registers explicitly. | PASS. No legacy facade consumer is added. |
| IV. Live verification | PASS BY PLAN ONLY. Pure checks are not host proof; later features own live acceptance. | PASS. Quickstart states the evidence boundary. |
| V. Idempotency/docs | PASS. Pure canonicalization is deterministic and docs/tests land together. | PASS. Canonical digest contract is explicit. |
| VI. Parity before removal | PASS. Existing hosting paths are untouched. | PASS. Non-opt-in behavior has regression acceptance. |
| Secrets/security | PASS. No credential authority is composable. | PASS. Contracts forbid secret and effect-bearing dependencies. |
| Feature 048 | PASS. Legacy/sibling state is neither read nor mutated. | PASS. Its contracts remain unchanged. |

No constitution violation requires a complexity exception.

## Project Structure

### Documentation (this feature)

```text
specs/049-oci-trust-verification/
├── prd.md
├── spec.md
├── checklists/requirements.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/verification.md
├── contracts/verified-image-plan.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/
├── config/
│   ├── hosting_images.py          # closed machine/project normalization
│   └── manifest.py                # explicit provider registration
└── hosting/
    └── images/
        ├── __init__.py            # narrow public value/policy exports
        ├── models.py              # immutable bounded value types
        └── trust.py               # pure verifier and safe result projection

tests/
├── hosting_image_fixtures.py
├── test_hosting_image_trust.py
├── test_hosting_image_contracts.py
└── test_hosting_image_boundaries.py

docs/
├── remote-hosting.md
└── remote-hosting-implementation.md
```

**Structure Decision**: Extend hosting through one explicit `images` package. Config
normalization owns input spelling; value models own closed schemas; `trust.py` owns
the sole decision. No command, transport, Docker, process, or state module depends
back into this layer.

## Architecture and Dependency Direction

```text
machine config provider ─┐
project intent normalizer├─> pure trust verifier ─> VerifiedImagePlan
receipt value parser ────┘                          (closed, immutable)

Feature 050 staging ───── validates plan envelope only
Feature 051 activation ── validates plan envelope only
```

The verifier constructor accepts only immutable value objects. An effect-capable
dependency is not part of its interface. All parsers reject unknown fields before
constructing values. Canonical digest helpers are package-owned and domain-separated;
the Feature 048 recovery digest helper is not reused as cross-domain authority.

## Phase 0: Research Conclusions

Decisions are recorded in [research.md](research.md). No `NEEDS CLARIFICATION`
remains. The key choices are target-platform manifest identity, separately digested
receipt payloads, exact machine/policy authority binding, canonical plan identity,
and dependency-level effect denial.

## Phase 1: Design

- [data-model.md](data-model.md) defines immutable values and validation rules.
- [contracts/verification.md](contracts/verification.md) defines the pure decision.
- [contracts/verified-image-plan.md](contracts/verified-image-plan.md) defines the
  only downstream trust handoff.
- [quickstart.md](quickstart.md) defines RED-first local validation and the evidence
  boundary.

## Implementation Sequence

1. Write contract and boundary tests first, including effect witnesses.
2. Add closed immutable value models and canonical serializers.
3. Add machine/project/receipt normalizers through explicit config ownership.
4. Add the pure verifier and safe public result.
5. Add consumer-validation tests that mutate every plan field.
6. Run non-opt-in hosting compatibility and documentation checks.

## Complexity Tracking

No violations.
