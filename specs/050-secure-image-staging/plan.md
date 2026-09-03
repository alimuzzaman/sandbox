# Implementation Plan: Secure Private Image Staging

**Branch**: `codex/feature-047-immutable-oci-clean` | **Date**: 2026-08-31 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/050-secure-image-staging/spec.md`

## Summary

Add a target-scoped staging service that validates Feature 049 plans, accepts one
replay-safe request, binds a machine-owned fixed broker/helper policy, launches one
bounded remote helper, pulls the exact digest with temporary credentials, observes one
coherent local image identity, and commits a canonical `StagedImageProof`. It has no
dependency on Compose, init, edge, activation, adoption, or rollback.

## Technical Context

**Language/Version**: Python 3.11+; fixed remote helper uses the installed Sandbox Python

**Primary Dependencies**: Feature 049 models/projection; existing secret broker;
registered remote transport; durable repository/locking; systemd user transient services on
cgroup v2; Docker CLI only behind the measured helper

**Storage**: Owner-only stage ledger under `$SANDBOX_HOME/runtime/`; volatile helper-owned
credential workspace on the target

**Testing**: `unittest`, synthetic credentials, fake broker/remote/helper/daemon/process tree,
crash/replay and forbidden-surface scans

**Target Platform**: Registered Linux Docker host; exact platform from Feature 049 plan

**Project Type**: Modular CLI/remote staging service

**Performance Goals**: Finite per-phase deadlines; bounded output <= 1 MiB; plan/service
fan-out inherited from Feature 049

**Constraints**: No raw credential persistence; no inherited parent environment; no shell-
constructed secret; no Compose/runtime/edge reachability; exact installed helper revision;
fail before credential resolution without systemd/cgroup-v2 ownership capability

**Scale/Scope**: One plan image, target, helper process tree, request, and proof per operation;
per target, 64 total full proofs including pinned proofs, 4096 tombstones, at most 64 live activation proof leases/pins,
and at most 16 MiB serialized authority

## Constitution Check

| Principle or boundary | Assessment |
|---|---|
| I. Per-project | PASS. Request binds explicit project/environment/remote; no fallback target. |
| II. Registry authority | PASS. Registered remote API and dedicated stage repository own state. |
| III. Modular package | PASS. Staging modules extend `sandbox/hosting/images/`; transport is an adapter. |
| IV. Live verification | PASS BY PLAN ONLY. Local tests are not GHCR/host proof; disposable acceptance remains gated. |
| V. Idempotency/docs | PASS. Ledger, request digest, generation, tombstone, and docs are first-class. |
| VI. Parity before removal | PASS. Existing build/no-build hosting paths remain. |
| Secrets | PASS BY DESIGN. Existing broker, fixed recipient/helper, temporary volatile handling, no raw values in state/results. |
| Process | PASS. Closed synthetic environment, process-tree ownership, finite deadlines, cleanup proof. |
| Feature 049 | PASS. Plan is validated, never reinterpreted. |
| Feature 048 | PASS. No recovery state or command changes in this feature. |

No complexity exception.

## Project Structure

### Documentation (this feature)

```text
specs/050-secure-image-staging/
├── prd.md
├── spec.md
├── checklists/requirements.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/staging-policy.md
├── contracts/stage-protocol.md
├── contracts/staged-image-proof.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/hosting/images/
├── staging_models.py
├── staging_policy.py
├── staging_repository.py
├── staging_service.py
├── staging_worker.py
└── staging_helper.py

sandbox/transports/
└── remote_hosting_images.py

sandbox/secrets/
└── service.py                    # narrow fixed-recipient staging adapter

sandbox/commands/
└── hosting.py                    # public stage dispatch only

scripts/provision_image_stage_helper.py  # shared measured helper provisioning
scripts/install-remote.sh                # remote bootstrap caller

tests/
├── test_image_stage_helper_provisioning.py
├── test_hosting_image_staging_policy.py
├── test_hosting_image_staging_repository.py
├── test_hosting_image_staging_service.py
├── test_hosting_image_staging_secrets.py
├── test_hosting_image_staging_process.py
└── test_remote_hosting_images.py
```

**Structure Decision**: Policy/models/repository/service stay inside the hosting-image
package. The transport adapts registered remotes. The remote worker/helper owns Docker and
temporary credential material. The command receives only safe envelopes. No staging module
imports Compose or activation services.

## Architecture and Effect Order

```text
VerifiedImagePlan -> staging policy -> durable accept -> broker lease
  -> measured helper -> exact pull -> mandatory credential cleanup
  -> coherent local observation -> StagedImageProof -> terminal ledger commit
```

Every arrow is generation/request bound. The helper receives a closed non-secret plan frame
and a separate bounded credential frame. Credentials are absent before broker lease and after
cleanup. Proof observation begins only after cleanup. Repository commits never serialize
frames, stdout/stderr, argv, environment, or private paths.

Feature 051 proof custody is distinct from the broker credential lease and target effect
lease. It uses this cross-store handoff:

```text
target mutation lock -> host-state transaction lock -> stage-ledger target lock
  -> durable prepared proof lease/pin -> proof verification -> durable host acceptance
  -> accepted pin promotion -> reverse unlock
```

The prepared lease pins the proof before verification and survives a crash. Its holder is
the durable activation-owner/request identity, never a process or unrelated recovery
identity. Its finite admission deadline never auto-unpins: expiry forbids a new host
acceptance; the same holder promotes an already committed exact acceptance even after the
deadline, or cancels only after proving acceptance absent. Terminal release requires the
same accepted activation owner and durable terminal receipt. The compactor can evict only
unleased/unpinned proofs and never edits proof-custody ownership.

## Phase 0: Research Conclusions

See [research.md](research.md). All unknowns are resolved: policy pins exact plan digest,
broker recipient/helper are fixed before resolution, temporary material must be verified
volatile, process-tree ownership precedes replay, one coherent daemon epoch proves local
identity, and canonical proof/ledger identities are separate.

## Phase 1: Design

- [data-model.md](data-model.md)
- [contracts/staging-policy.md](contracts/staging-policy.md)
- [contracts/stage-protocol.md](contracts/stage-protocol.md)
- [contracts/staged-image-proof.md](contracts/staged-image-proof.md)
- [quickstart.md](quickstart.md)

## Implementation Sequence

1. Author every contract, leak, cgroup ownership, proof-expiry, finite saturation,
   proof-custody lease/pin handoff, crash replay, identity-projection, visibility, replay,
   and no-activation test RED.
2. Implement closed policy/request/proof/process value models.
3. Implement the durable repository and exact replay/conflict/uncertainty rules.
4. Add fixed broker adapter and measured helper protocol.
5. Add helper process-tree cleanup, exact pull, volatile credential lifecycle, and coherent proof.
6. Compose the service/transport/command with no activation imports.
7. Run local suites, architecture boundaries, human credential/process review, then leave live gates open.

## Complexity Tracking

No violations.
