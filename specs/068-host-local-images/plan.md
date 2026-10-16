# Implementation Plan: Local Images for Hosted Deployments

**Branch**: `feat/host-deploy-local-images-20261016` | **Date**: 2026-10-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/068-host-local-images/spec.md`

## Summary

Add a separate, opt-in local-image mechanism beside the existing signed immutable-image authority. A hosting module materializes one exact Git revision, renders a hermetic Compose build model, builds `linux/amd64` images locally, saves one archive per exact configuration identity, and issues a closed local receipt. A fixed registered-remote transport observes or streams each artifact, verifies exact configuration ID/platform/root-filesystem layers, and publishes an immutable remote receipt under its digest. `host apply --local-images <digest>` revalidates that remote receipt before Compose mutation, then executes the existing `compose.build: false` path with all current lifecycle checks.

## Technical Context

**Language/Version**: Python 3.11+ and POSIX shell at existing CLI/remote boundaries

**Primary Dependencies**: Python standard library, existing Sandbox manifest/secret/registered-remote services, Git, Docker Engine, Docker Compose v2, BuildKit/buildx

**Storage**: Owner-only local archives/receipts under `$SANDBOX_HOME/runtime/hosting/local-images`; owner-only remote receipts under the registered remote Sandbox home

**Testing**: Focused `PYTHONPATH=. python3 -m unittest` modules with synthetic subprocess environments, plus one real tiny-project `./sb host image build-local` Docker acceptance after implementation

**Target Platform**: Linux/macOS controller; local builder produces and inspects `linux/amd64`; destination is a registered Linux `amd64` Docker host

**Project Type**: Modular Python CLI with fixed registered-remote transport

**Performance Goals**: Stream archives without buffering image contents; skip every freshly proven artifact; bounded progress/receipts; finite build, per-artifact, and aggregate transfer deadlines

**Constraints**: No raw user SSH/Docker workflow; no ambient interpolation; no secrets in argv/output/receipts; no fallback build/pull during apply; exact config ID + platform + RootFS layers; preserve durable admission/recovery; focused tests below 5 seconds

**Scale/Scope**: Up to 64 services/artifacts, 256 KiB receipts, one target platform and registered remote per operation, multi-gigabyte streamed archives

## Constitution Check

*GATE before research: PASS. Rechecked after design: PASS.*

- Commands require an explicit hosted project/environment and existing registered remote; no implicit instance or direct state JSON access.
- Parser/dispatch stays in existing modules; mechanisms live in `sandbox/hosting/local_images.py` and `sandbox/transports/remote_local_images.py`.
- Implementation and docs complete first, followed by supported real CLI acceptance, then regressions/gates.
- Replay freshly observes and skips exact content; CLI/schema/help/docs/CHANGELOG land together.
- Existing build-enabled, hard no-build, and signed immutable-image paths remain available and unchanged unless the explicit receipt flag is present.
- Approved secret values remain private BuildKit inputs. Receipts contain symbolic bindings, never values or reversible hashes.
- Spec Kit artifacts remain development-only under existing release pruning.

No constitution violation requires an exception.

## Project Structure

### Documentation (this feature)

```text
specs/068-host-local-images/
├── prd.md
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/{cli.md,receipts.md}
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/
├── cli.py
├── commands/hosting.py
├── core/_hosting.py
├── hosting/local_images.py
├── hosting/recovery/policy.py
├── remote_runtime/{protocol.py,shapes.py}
└── transports/remote_local_images.py
tests/
├── test_hosting.py
├── test_hosting_local_images.py
├── test_remote_local_images.py
└── test_remote_runtime_protocol_shapes.py
README.md
skills/sandbox-cli/SKILL.md
docs/remote-hosting.md
CHANGELOG.md
```

**Structure Decision**: Receipt/build mechanisms live under hosting, registered transport mechanics under transports, and user/durable admission orchestration in the existing hosting command owner. The signed immutable-image authority is not reused because its trust and identity contracts differ.

## Design Decisions

1. Local receipts never satisfy `hostingImages`; any declared immutable-image intent refuses this workflow.
2. Compose config/build children receive a fixed environment plus explicit Docker compatibility keys and private inputs. Parent environment is never enumerated or copied.
3. Cross-controller render identity covers source/Compose topology, literal non-secret inputs, derived revision, and symbolic BuildKit secret bindings. Existing apply-side opaque secret metadata remains authoritative for current values.
4. Image equality requires the destination Docker `.Id` to equal the saved archive's exact portable configuration ID, plus exact `linux/amd64` and ordered RootFS layer digest. The builder engine's observed store ID is retained separately because Docker 29's containerd store can expose a different local identity; that value and all other digest aliases are never accepted as the destination ID.
5. Only digest-pinned non-built images are external requirements. Transfer observes them and refuses if absent; it never pulls or archives them.
6. Transfer publishes the complete bounded non-secret receipt plus target verification under a content digest on the remote, enabling Linux-build/Mac-apply.
7. A fixed transport reuses remote lookup and SSH argument construction, streams to a bounded owner-only temporary archive, validates before `docker load`, and publishes owner-only receipts atomically. It does not use generic buffered SSH or signed staging helpers.
8. Transfer and receipt-bound apply share one SSH-held remote-engine lock, independent of the not-yet-enabled hosting coordination store, and transfer retains storage-floor admission. Apply acquires the lock before delivery admission or source staging. Timeout/disconnect is unknown until a fresh observation; no blind second load occurs.
9. Apply retrieves by receipt digest and validates before `_run_compose` writes or starts anything. Receipt mode adds `--pull never`; existing hard no-build preflight remains additive.
10. `--local-images` is added to closed durable apply arguments/evidence so replay cannot change receipts. Receipt-free applies retain current behavior.
11. Public remote shapes require protocol/shape evidence and supported remote service migration before use.

## Implementation Phases

1. Add closed receipt models, clean source materialization, hermetic Compose projection, local engine checks, build/save/inspect, and safe persistence.
2. Add fixed remote observe/stage/load/publish transport with target/daemon/platform binding, storage admission, timeouts, and exact replay skips.
3. Add `host image build-local`, `host image push-local`, and `host apply --local-images`, plus durable argument binding and protocol shapes.
4. Integrate pre-mutation receipt verification with no-build apply while preserving source, initializer, health, topology, edge, and recovery behavior.
5. Update help, README, CLI skill, remote-hosting docs, CHANGELOG, and managed agent context.
6. Exercise the completed CLI against a tiny throwaway Compose project using the local Docker engine.
7. Write focused regressions from observed behavior and run focused then required broader gates.

## Complexity Tracking

No constitution violations. A new fixed transport is necessary because signed staging is a separate authority and generic SSH helpers either buffer input or provide no streamed stdin.
