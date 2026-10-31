# Product Requirements Draft: Local Images for Hosted Deployments

**Status**: Refined

**Created**: 2026-10-16

**Last Refined**: 2026-10-16

**Input**: "Build hosted service images on a faster local Docker engine, transfer them through Sandbox, and apply them remotely with exact source and image evidence."

**Drafting Configuration**: Codex root agent under the active repository policy; no delegated drafting

**Final Validation**: `PASS` — independent Sol readiness review

**Validated On**: 2026-10-16

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Hosted deployments currently build application images on the destination host when
the environment enables image building. On a shared, CPU-starved host, a large
application build can take more than an hour even though the actual image-layer work
is short. This extends releases, increases contention for unrelated workloads, and
makes every revision pay the cold-build cost.

Sandbox can already apply an environment without building when every declared
persistent and initialization service resolves to an explicit image present on the
remote engine. It does not provide a supported way to create those application images
on a faster operator machine, transfer them to the registered host, prove that the
remote image identities match the local build, and bind the later apply to that proof.
Operators must not bridge this gap with raw Docker, ad-hoc SSH, mutable tags without
evidence, or hand-edited host state.

## Users and Desired Outcomes

- **Release operator**: Build an exact application revision on a suitable local engine,
  transfer it once, and deploy it through the ordinary recoverable hosting workflow.
- **Application developer**: Retain the existing Compose build definition, build
  arguments, derived revision variables, and build-time secret behavior without
  maintaining a second deployment definition.
- **Host operator**: Know that remote apply will refuse before Compose mutation when
  an image, platform, source revision, service set, or configuration binding differs.
- **Other host users**: Avoid application compilation consuming the shared remote
  host's CPU while preserving all existing health, topology, source, and recovery
  checks.

## Goals

- Provide one supported workflow to build every declared hosted application image for
  an exact clean source revision on the operator's local engine.
- Produce a bounded, non-secret receipt that binds the source revision, environment,
  target platform, declared services, effective build definitions, image references,
  and immutable image identities.
- Transfer receipt-bound images through the registered Sandbox transport, skip images
  already proven present, and verify exact identities after transfer.
- Let an explicitly receipt-bound, no-build host apply verify the same source,
  environment, Compose model, platform, and remote images before Compose mutation.
- Preserve the current durable-job admission, hosting recovery, initializer, runtime
  health, topology, source-revision, and edge checks.
- Make repeated transfer and verification of the same valid receipt safe and
  evidence-preserving.

## Non-Goals

- Replacing registries as a general image-distribution system.
- Building on the destination host or silently falling back to a remote build.
- Supporting dirty working trees, arbitrary uncommitted overlays, or receipts for
  source that cannot be resolved to one exact commit.
- Replacing Compose, rewriting application Dockerfiles, or maintaining a second set of
  build arguments.
- Changing the hosted HTTP-readiness deadline or other deployment limits.
- Publishing Sandbox through the public install site.
- Managing registry credentials or copying them between controllers and hosts.
- Claiming release success from image transfer alone; ordinary host apply remains the
  deployment authority.

## Product Scenarios

### Scenario 1 — Build an exact revision locally

- **Starting state**: The application checkout is clean, the requested revision exists,
  the environment has explicit image references for every declared service, and the
  local engine can produce the remote target platform.
- **User action**: The operator requests a local image build for the environment and
  revision.
- **Expected outcome**: Sandbox builds from a clean materialization of that exact
  revision using the environment's effective Compose build inputs and approved secret
  sources, then returns a receipt location and a summary of each image without
  revealing secret values.

### Scenario 2 — Transfer images to a registered host

- **Starting state**: A valid local receipt and its image artifacts exist, and the
  selected registered host reports a compatible engine platform.
- **User action**: The operator reviews the transfer and explicitly confirms it.
- **Expected outcome**: Sandbox skips content already proven present, transfers only
  missing receipt artifacts through the registered transport, verifies every required
  remote identity, and records a remote verification receipt.

### Scenario 3 — Apply with transferred images

- **Starting state**: The environment has remote building disabled, a remote
  verification receipt exists under its immutable identity, and the source checkout on
  a potentially different controller still resolves cleanly to the receipt revision.
- **User action**: The operator starts an ordinary durable host apply and explicitly
  selects the remote receipt identity returned by transfer.
- **Expected outcome**: Sandbox verifies the receipt, service set, effective Compose
  model, target platform, source revision, and remote image identities before Compose
  mutation, then follows the existing no-build apply path and all existing post-start
  checks.

### Scenario 3A — Linux build and Mac apply

- **Starting state**: Linux built and transferred the images, while the clean checkout
  that owns durable deployment admission is on macOS.
- **User action**: The macOS operator supplies the immutable remote receipt identity to
  the durable apply; no local archive or secret-bearing file is copied between
  controllers.
- **Expected outcome**: The macOS controller reads and revalidates the bounded non-secret
  receipt through the registered remote, binds it to its own clean source and current
  manifest, and either applies normally or refuses before Compose mutation.

### Scenario 4 — Reuse already transferred content

- **Starting state**: Some or all receipt images are already present on the selected
  remote with exact accepted identities.
- **User action**: The operator confirms transfer of the same receipt again.
- **Expected outcome**: Sandbox observes and skips exact content, transfers only
  missing artifacts, and returns a new bounded verification result without rebuilding
  or reloading proven images.

### Scenario 5 — Digest-pinned registry dependency

- **Starting state**: A declared database, queue, or other dependency uses an immutable
  registry digest rather than an application-built artifact.
- **User action**: The operator builds and transfers the environment receipt.
- **Expected outcome**: The dependency is retained in the receipt as an immutable
  external requirement. Transfer never substitutes a tag for the digest; it verifies
  that the exact digest reference is available on the remote and refuses with an
  actionable result when it is absent.

### Negative Scenario — Source changed or dirty

- **Starting state**: The requested revision differs from the apply source, or the
  checkout contains changes that cannot be attributed to that revision.
- **User action**: The operator attempts to build or apply with the receipt.
- **Expected outcome**: Sandbox refuses before build or remote Compose mutation and
  identifies the source-binding mismatch without exposing file contents.

### Negative Scenario — Platform mismatch

- **Starting state**: The local result is not for the selected host's platform, or any
  required image reports another platform.
- **User action**: The operator attempts transfer or apply.
- **Expected outcome**: Sandbox refuses before transfer or Compose mutation and reports
  the expected and observed platform identifiers.

### Negative Scenario — Image or Compose drift

- **Starting state**: A remote reference resolves to a different image, a service image
  reference or build definition changed, an artifact is missing, or the remote
  verification receipt belongs to another source/environment.
- **User action**: The operator attempts apply.
- **Expected outcome**: Sandbox refuses before Compose mutation with bounded per-service
  mismatch evidence and never falls back to building remotely.

### Negative Scenario — Interrupted transfer

- **Starting state**: The transport disconnects or reaches its finite deadline while an
  image is loading.
- **User action**: The operator inspects or safely repeats the same receipt transfer.
- **Expected outcome**: Sandbox treats the transfer result as unknown until a fresh
  read-only observation proves image identities; it neither claims success nor starts
  a second blind load.

### Negative Scenario — Existing immutable-image authority

- **Starting state**: The environment declares the existing signed or machine-owned
  immutable-image staging and activation policy.
- **User action**: The operator attempts local image build, transfer, or receipt-bound
  apply.
- **Expected outcome**: Sandbox refuses before build, transfer, or Compose mutation;
  local-image receipts cannot bypass or satisfy the separate image authority.

### Negative Scenario — Invalid receipt or target

- **Starting state**: A receipt is malformed, oversized, tampered, belongs to another
  remote, or was verified against a different remote machine or Docker daemon identity.
- **User action**: The operator attempts transfer or apply.
- **Expected outcome**: Sandbox refuses before loading an image or mutating Compose and
  reports a bounded receipt, target, or engine binding error.

### Negative Scenario — Build prerequisites unavailable

- **Starting state**: An approved build secret is missing, or the local Docker,
  BuildKit/buildx, Compose, or target-platform capability is unavailable or incompatible.
- **User action**: The operator attempts local build.
- **Expected outcome**: Sandbox refuses before image build and emits a bounded,
  secret-free prerequisite result.

### Negative Scenario — Build input or interpolation drift

- **Starting state**: A declared derived input, Compose file, project identity, profile,
  build argument, or approved interpolation input is missing or changed, or an ambient
  parent-process variable attempts to override the declared render.
- **User action**: The operator attempts build, transfer, or apply, as applicable.
- **Expected outcome**: Sandbox either renders the same declared model independently of
  ambient variables or refuses before build, image transfer, or Compose mutation; a
  receipt for a different render is never accepted.

### Negative Scenario — Secret safety

- **Starting state**: A build requires an approved build-time secret.
- **User action**: The build succeeds or fails and the operator inspects progress,
  errors, receipts, and transferred metadata.
- **Expected outcome**: No secret value appears in command arguments, receipts,
  progress, error text, stored transfer evidence, or committed files.

## Proposed Product Behavior

- Local build explicitly targets `linux/amd64` without requiring a remote selector.
  Transfer independently proves that the selected host engine is compatible.
- The local build source is a clean materialization of one full commit. The operator's
  working directory is not modified and uncommitted overlays are excluded.
- Local build derives the Compose project identity, file set, profiles, build arguments,
  and non-secret interpolation inputs from the same bounded manifest, source, and
  approved secret-resolution inputs as hosted apply. Undeclared parent-process
  environment cannot influence the rendered build model. The receipt binds the project
  identity and a value-safe digest of that exact render.
- Every primary, background, and initialization service must resolve to an explicit
  image reference. Services sharing one built image share one artifact identity.
- Approved build-time secrets may be consumed only through the existing Sandbox secret
  resolution policy and must remain ephemeral and value-free in receipts and output.
- The receipt is closed, self-identifying, size-bounded, stored with owner-only
  permissions, and validated before any transfer or apply decision.
- Successful transfer publishes a bounded non-secret remote receipt under its immutable
  digest and binds it to the registered remote machine and Docker daemon identity. This
  digest is the only cross-controller handoff needed by apply.
- Remote transfer is confirmation-gated, uses only the registered Sandbox transport,
  has finite per-artifact and overall bounds, and produces progress that contains no
  secret-bearing command or environment detail.
- A transfer timeout or disconnect is not success. Fresh observation decides whether
  the content arrived, and an exact repeat may skip content already proven present.
- Digest-pinned external images are verified as exact requirements. Their absence is a
  refusal rather than permission to substitute, mutate the receipt, or weaken the
  no-build contract.
- Image equivalence requires the selected reference to resolve to the exact Docker
  configuration/image ID recorded by the build, on the exact platform, with the exact
  root-filesystem layer identity. Archive and receipt digests protect transport and
  metadata integrity but are not alternative image identities.
- Apply accepts local images only when the environment has remote building disabled.
  Receipt verification is additive to, not a replacement for, ordinary durable apply
  admission and runtime evidence.
- A valid receipt does not authorize deployment by itself. Transfer and host apply
  remain separate explicit actions with separate outcomes.
- Environments using the existing immutable-image staging/activation authority are
  ineligible for this path; the two trust models are deliberately not interchangeable.

## Constraints and Dependencies

- The operator machine and selected host must expose compatible Docker/Compose
  capabilities, and the locally produced image platform must equal the host platform.
- The initial required target is `linux/amd64`; unsupported or ambiguous platforms
  fail closed.
- Application source and hosting manifest remain the authorities for the Compose files,
  service roles, derived environment, build inputs, and no-build policy.
- Image artifacts may be large. Transfer must be streaming and bounded rather than
  buffered wholly in memory or copied into Git.
- Local archives remain controller-local and must never be committed. Transfer publishes
  only a bounded, non-secret, digest-addressed verification receipt remotely so another
  authorized controller can apply without copying local artifacts.
- Existing secret-provider, redaction, remote registration, transport, durable-job,
  hosting recovery, storage-floor, and per-target hosting ownership policies remain
  in force. Because image references are Docker-daemon global, local-image transfer
  and receipt-bound apply additionally require engine-wide mutual exclusion.
- A remote runtime revision mismatch prevents reliance on the new protocol until the
  remote service is migrated through the supported lifecycle.
- Updating the one shared remote runtime can temporarily make older controller
  checkouts incompatible; the operator must review that impact before migration.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Source policy | Exact clean commit only | Makes the build and later apply reproducible and excludes ambiguous overlays | User request and existing hosting policy |
| Initial target | `linux/amd64`, with mismatch refusal | Matches the Lenzora destination and prevents accidental native-platform images | User request |
| Remote build fallback | Never | A fallback would recreate the host contention this feature exists to avoid | User request |
| Apply mode | Explicit receipt plus remote building disabled | Preserves the existing hard no-build contract and makes intent reviewable | User request and existing contract |
| Transfer authority | Separate confirmation-gated action | Loading images changes the remote engine and must not be implicit in apply | Repository safety policy |
| External digest references | Verify exact remote availability; never substitute or silently pull | Keeps registry authentication and mutable-tag behavior outside this feature | Existing no-build and secret boundaries |
| Cross-controller handoff | Transfer publishes a digest-addressed remote receipt; apply selects that digest | Supports Linux build/transfer and Mac deployment without copying archives or credentials | User's stated machine topology |
| Existing image authority | Refuse the local-image workflow when `hostingImages` authority is declared | Prevents an unsigned local path from bypassing the signed or machine-owned trust model | Existing hosting image contract |
| Image equivalence | Exact configuration/image ID, platform, and root-filesystem layer identity | Matches requested image-ID verification and fails closed across engines | User request and Docker identity evidence |
| Recovery | Fresh observation after uncertain transfer; no blind retry | Avoids duplicate effects and false success after transport loss | Existing delivery policy |
| Existing checks | Preserve all source, initializer, health, topology, edge, and recovery checks | Local image evidence is additive deployment input, not a weaker deployment mode | User request |
| Public release | Excluded | The user requested branch/latest/remote-service rollout, not install-site publication | User request |

## Open Questions

- None.

## Acceptance Outcomes

- On a clean throwaway project, an operator can build a `linux/amd64` image locally,
  receive a valid per-service receipt, and verify that the receipt contains the exact
  commit, environment, image references, immutable identities, platform, and build
  configuration identity.
- For every primary, background, and initialization service, a confirmed transfer
  either proves the exact image already present or transfers and subsequently proves
  it; no unproven service is reported as ready.
- Repeating transfer of an unchanged receipt sends zero image archive or layer payload
  bytes for every image already proven present; bounded observation traffic is allowed.
- Apply refuses before Compose mutation for a changed source revision, dirty source,
  wrong environment, changed service set, changed build definition, missing remote
  receipt, wrong remote/machine/daemon binding, missing image, wrong image ID, changed
  layer identity, platform mismatch, or conflicting immutable-image authority.
- A valid receipt-bound apply uses the existing no-build path and reaches the same
  source, initializer, health, topology, and edge evidence requirements as an ordinary
  hosted apply.
- A transfer interruption never produces a success claim without a subsequent exact
  observation; replay of the same receipt skips exact completed artifacts and resumes
  only artifacts still missing or mismatched.
- A Linux controller can build and transfer a receipt, and a macOS controller can apply
  that remote receipt by digest without copying an archive or local receipt between
  machines.
- Two local builds of the same declared inputs under different unrelated parent-process
  environments produce the same build configuration identity; changing any declared
  Compose input or project identity invalidates the old receipt before Compose mutation.
- Secret-bearing acceptance fixtures can build successfully while automated scans of
  stdout, stderr, receipts, stored remote evidence, and committed changes find zero
  secret values.
- The local build and receipt workflow completes successfully on both supported
  controller operating systems when each has compatible engine capabilities.
- Focused regression tests complete in under five seconds each, and the repository's
  required gates pass without introducing a test over five seconds.
- After supported remote-service migration, status reports the installed runtime
  revision as matching the released controller revision before Lenzora uses the new
  workflow.

## Risks and Assumptions

- **Risk**: A Docker implementation that does not preserve the exact configuration ID
  and layer identity through save/load will be refused rather than treated as
  equivalent.
- **Risk**: Large image streams may be interrupted after the remote engine has already
  imported content, making transport exit status alone insufficient.
- **Risk**: Incomplete isolation from an operator's parent environment could change
  effective Compose interpolation; acceptance requires deterministic identity across
  unrelated parent environments.
- **Risk**: Build output or subprocess errors can accidentally reproduce secret values
  unless all progress and error paths use the existing redaction boundary.
- **Risk**: A new remote protocol deployed to a shared host can strand older pinned
  controller checkouts until they are updated.
- **Assumption**: Lenzora's application-built services have explicit stable image
  references and retain their Compose build definitions when remote building is
  disabled.
- **Assumption**: Digest-pinned external dependencies used by Lenzora are already
  available on the remote; this feature does not pull or transfer them when absent.
- **Assumption**: The selected build engine can produce and inspect the required
  `linux/amd64` image identity, whether natively or through engine-managed emulation.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent readiness review verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
