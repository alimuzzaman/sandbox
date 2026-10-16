# Feature Specification: Local Images for Hosted Deployments

**Feature Branch**: `feat/host-deploy-local-images-20261016`

**Created**: 2026-10-16

**Status**: Draft

**Input**: Ready PRD in `specs/068-host-local-images/prd.md`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Build Exact Hosted Images Locally (Priority: P1)

As a release operator, I can build the application images for one hosted environment from a clean materialization of an exact source revision on a faster local machine, while retaining the hosted deployment's effective build inputs and secret policy.

**Why this priority**: The feature has no value unless it can replace the expensive remote compilation with an exact, reproducible local build.

**Independent Test**: Use a clean tiny hosted project with multiple Compose service roles, build one exact revision locally, and verify that the returned bounded receipt identifies the revision, environment, render, target platform, services, image references, and immutable image identities without containing secret values.

**Acceptance Scenarios**:

1. **Given** a clean checkout, an exact revision, explicit images for every declared service, approved build inputs, and a capable local engine, **When** the operator requests a local build, **Then** every locally built application image targets `linux/amd64` and a bounded non-secret receipt is produced.
2. **Given** two runs for the same revision and declaration under different unrelated parent-process environments, **When** Sandbox renders the hosted build model, **Then** both runs identify the same Compose project, file set, profiles, declared interpolation inputs, build definitions, and value-safe configuration digest.
3. **Given** a required build secret supplied by the existing approved secret source, **When** the build succeeds or fails, **Then** the secret is available only to its declared build input and no secret value appears in arguments, progress, errors, receipts, or tracked files.
4. **Given** a dirty checkout, unresolved revision, missing secret, missing explicit service image, incompatible local engine, or unsupported target result, **When** build is requested, **Then** Sandbox refuses before creating application images and returns bounded evidence.

---

### User Story 2 - Transfer and Verify Receipt-Bound Images (Priority: P1)

As a release operator, I can explicitly transfer locally built images to a registered host through Sandbox and obtain immutable remote verification evidence that another controller can use.

**Why this priority**: A local build cannot support deployment until its exact artifacts are present and independently verified on the destination engine.

**Independent Test**: Transfer a valid receipt to a compatible registered host, verify exact image configuration identity, platform, and root-filesystem layers remotely, repeat the transfer, and observe that already-proven images are skipped while a remote receipt with a stable identity is returned.

**Acceptance Scenarios**:

1. **Given** a valid local receipt, intact artifacts, a compatible registered host, and explicit confirmation, **When** transfer is requested, **Then** Sandbox transfers only missing built artifacts through the registered transport and verifies every required remote image before publishing the remote receipt.
2. **Given** an image already resolves on the remote to the exact configuration identity, platform, and root-filesystem layers in the receipt, **When** the same transfer is repeated, **Then** the image is skipped and the resulting verification remains bounded and evidence-backed.
3. **Given** a digest-pinned external dependency, **When** transfer validates requirements, **Then** it verifies that exact digest reference on the remote without transferring, pulling, retagging, or substituting it.
4. **Given** a malformed, oversized, tampered, mismatched, or wrong-target receipt, **When** transfer is requested, **Then** Sandbox refuses before loading any image.
5. **Given** a timeout or transport interruption, **When** the transfer returns, **Then** Sandbox does not claim success; a later exact replay performs fresh observation before deciding whether to skip or transfer each artifact.

---

### User Story 3 - Apply Only Verified Local Images (Priority: P1)

As a release operator, I can select a transferred receipt by immutable identity during ordinary host apply so Sandbox proves the local-image handoff before any Compose mutation and then uses the existing no-build deployment path.

**Why this priority**: Receipt-bound admission is what turns transferred content into a supported deployment rather than an untrusted side channel.

**Independent Test**: From a clean controller that does not hold the local archives, apply the matching revision with the remote receipt identity and remote building disabled; then repeat with source, render, service, image, platform, target, and daemon mismatches and verify every mismatch refuses before Compose mutation.

**Acceptance Scenarios**:

1. **Given** a remote receipt for the selected registered host, exact source revision and environment, matching current hosted declaration, compatible engine, and exact remote images, **When** an explicitly receipt-bound apply is admitted, **Then** Sandbox runs the existing no-build apply and preserves all existing initializer, health, topology, edge, source, durable-job, and recovery checks.
2. **Given** Linux built and transferred the artifacts while macOS owns deployment admission, **When** macOS supplies the immutable remote receipt identity, **Then** it retrieves bounded non-secret evidence through the registered transport and can validate it without a local archive or secret-bearing handoff file.
3. **Given** remote building is enabled or the environment uses the existing immutable-image authority, **When** receipt-bound apply is requested, **Then** Sandbox refuses before Compose mutation.
4. **Given** source, environment, render, project identity, service set, image reference, image identity, platform, registered host, machine identity, or daemon identity differs, **When** receipt-bound apply is requested, **Then** Sandbox reports a bounded mismatch and never falls back to a remote build.

---

### User Story 4 - Preserve Existing Hosting Recovery Truth (Priority: P2)

As a host operator, I can distinguish image staging from deployment and continue to rely on existing durable job and recovery evidence when an apply later fails or is interrupted.

**Why this priority**: Image transfer must not rewrite, weaken, or overstate the established hosted-delivery lifecycle.

**Independent Test**: Stage valid images, induce a pre-mutation refusal and a post-admission runtime failure, and verify that transfer evidence never claims deployment success and existing apply/recovery records retain their original semantics.

**Acceptance Scenarios**:

1. **Given** successful transfer without apply, **When** delivery state is inspected, **Then** no deployment success or runtime activation is claimed.
2. **Given** receipt verification passes but ordinary apply later fails, **When** job and recovery evidence are inspected, **Then** the original apply remains failed or incomplete according to existing rules and the receipt remains only image-staging evidence.

### Edge Cases

- Multiple services may share one built image reference; the receipt records one artifact identity and all consuming service bindings without duplicate transfer.
- A declared service may have an explicit immutable image but no build definition; it is an external requirement and is never silently converted into a locally built artifact.
- A receipt may be replayed after the remote Docker daemon changes; machine or daemon binding mismatch requires fresh transfer verification.
- A mutable reference may resolve to a different image after transfer; apply compares current remote identity with the receipt and refuses.
- A valid receipt may outlive local archives; apply remains possible from remote evidence, while another transfer requiring absent local content refuses without claiming corruption of the remote receipt.
- Build or transfer output may be truncated, malformed, or empty; this is an unknown result, never success.
- Receipt and progress sizes are bounded even when a project declares many services or a remote command emits excessive output.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Sandbox MUST offer a local-build operation that accepts a project directory, hosted environment, optional exact revision, bounded timeout, and structured-output mode.
- **FR-002**: The local-build operation MUST require a clean source checkout and materialize the selected full commit without modifying or incorporating uncommitted state from the operator's checkout.
- **FR-003**: Sandbox MUST derive the local build's Compose project identity, Compose file set, active profiles, service roles, explicit image references, build contexts, build arguments, declared interpolation values, derived source revision values, and approved build-time secret names from the same hosted declaration used by apply.
- **FR-004**: Ambient parent-process variables that are not explicit approved inputs MUST NOT alter the rendered build model.
- **FR-005**: The initial feature MUST build and verify application images for exactly `linux/amd64`; it MUST refuse when the engine cannot produce or inspect that target or when any result reports another platform.
- **FR-006**: Every declared primary, background, and initialization service MUST resolve to an explicit image reference before build begins.
- **FR-007**: Sandbox MUST classify each required image as a locally built artifact or an immutable external requirement and MUST NOT silently pull, retag, transfer, or substitute external requirements.
- **FR-008**: Sandbox MUST reject the local-image workflow when the environment declares the existing signed or machine-owned immutable-image authority.
- **FR-009**: Sandbox MUST resolve build-time secrets only through the existing approved secret policy, expose them only to declared build secret inputs, and exclude their values from command arguments, logs, errors, receipts, digests that permit value recovery, and committed files.
- **FR-010**: A successful local build MUST create an owner-readable/writable, closed-schema, size-bounded receipt that includes its schema version, immutable receipt identity, source commit, environment, target platform, Compose project and render identity, service-to-image bindings, and per-image reference, exact configuration identity, platform, root-filesystem layer identity, artifact integrity metadata, and classification.
- **FR-011**: Receipt identity and validation MUST detect modification, truncation, unsupported schema, duplicate or ambiguous service/image bindings, unsafe paths, and content exceeding declared bounds before side effects.
- **FR-012**: Sandbox MUST offer a confirmation-gated transfer operation accepting a registered remote and local receipt, with finite per-artifact and overall time limits and secret-free structured progress.
- **FR-013**: Transfer MUST use the registered Sandbox transport and MUST verify the selected remote's authenticated machine identity, Docker daemon identity, and engine platform before loading content.
- **FR-014**: Transfer MUST skip a built artifact only after fresh observation proves the selected remote reference resolves to the receipt's exact configuration identity, exact platform, and exact root-filesystem layer identity.
- **FR-015**: Transfer MUST verify digest-pinned external requirements as already present under the exact declared digest and MUST refuse if any are absent or resolve differently.
- **FR-016**: Transfer interruption, timeout, empty output, or malformed output MUST yield an unknown or failed outcome rather than success; exact replay MUST reobserve remote identities before deciding what remains.
- **FR-017**: Successful transfer MUST publish a bounded, non-secret, immutable remote receipt addressed by digest and bound to the registered remote, authenticated machine identity, Docker daemon identity, source commit, environment, render, and verified image identities.
- **FR-018**: Sandbox MUST offer an explicit receipt selector on ordinary host apply; the selector MUST identify immutable remote receipt evidence and MUST NOT require the applying controller to hold local image archives.
- **FR-019**: Receipt-bound apply MUST be allowed only when remote image building is disabled and MUST verify the current clean source revision, hosted environment, Compose project and render identity, service set, image references, target platform, registered remote, machine identity, daemon identity, and current remote image identities before any Compose mutation.
- **FR-020**: Any missing, stale, malformed, oversized, mismatched, or unverifiable receipt or image observation MUST refuse apply before Compose mutation with bounded per-service or target evidence.
- **FR-021**: Receipt-bound apply MUST never enable remote building or fall back to a build, pull, tag substitution, or weaker identity comparison.
- **FR-022**: After receipt verification, apply MUST use the existing no-build lifecycle and retain all existing admission, initializer, health, topology, source publication, edge, durable job, failure, and recovery semantics.
- **FR-023**: Image build and transfer evidence MUST NOT claim that a revision was deployed, activated, healthy, or released.
- **FR-024**: Human-readable and structured command results MUST be semantically equivalent, bounded, and free of secret values; documented help MUST distinguish build, transfer, and apply authority.
- **FR-025**: Existing hosted apply invocations without a local-image receipt MUST retain their current behavior.
- **FR-026**: Public command, receipt, and remote-evidence schema changes MUST carry matching operator documentation, examples, changelog evidence, and compatibility validation.
- **FR-027**: Transfer and receipt-bound apply MUST share exclusive ownership of the remote Docker engine's mutable image-reference namespace; apply MUST acquire that ownership before delivery admission or source staging, and contention or ownership loss MUST refuse or remain unknown rather than continue uncoordinated.

### Key Entities

- **Local Build Receipt**: Immutable, non-secret evidence for one exact source/environment/render and its locally produced or externally required images; it owns artifact integrity metadata but does not assert deployment.
- **Image Requirement**: One explicit image reference and its classification, consuming services, platform, exact configuration identity, and root-filesystem identity.
- **Remote Verification Receipt**: Digest-addressed evidence that a registered remote machine and Docker daemon freshly proved every image requirement; usable across controllers without local archives.
- **Render Identity**: A value-safe identity for the exact hosted Compose project, file set, profiles, declarations, approved interpolation inputs, derived revision values, build definitions, and secret names.
- **Receipt-Bound Apply**: An ordinary durable hosted apply whose additional pre-mutation admission proof is a remote verification receipt.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can build all application images for a valid tiny hosted project at one exact revision using one supported local-build command and receives a complete receipt with no manual Docker or SSH steps.
- **SC-002**: Two equivalent builds run with different unrelated ambient variables produce the same render identity; changing any declared Compose file, project identity, profile, build argument, approved interpolation input, service binding, environment, or source revision changes that identity or causes refusal.
- **SC-003**: For every accepted built image, local and remote observations agree on exact configuration identity, `linux/amd64` platform, and root-filesystem layer identity before a remote receipt is published.
- **SC-004**: Repeating transfer of an already verified receipt sends zero image archives for images whose fresh remote observations still match exactly.
- **SC-005**: Each defined negative source, platform, target, daemon, receipt, render, service, image, external-dependency, immutable-authority, prerequisite, and interruption case refuses before its prohibited side effect in automated acceptance evidence.
- **SC-006**: A clean macOS controller can admit the matching receipt-bound apply using only the remote receipt identity, current source/declaration, approved secret source, and registered remote; no local archive handoff is required.
- **SC-007**: Secret canaries used in acceptance appear zero times in captured command arguments, structured output, human output, errors, receipts, remote evidence, and committed changes.
- **SC-008**: Existing no-build and build-enabled hosted apply regression suites remain green, and receipt-free invocations preserve their prior behavior.
- **SC-009**: Every new or changed focused test completes within 5 seconds, and no required repository test exceeds the repository's 5-second per-test limit.
- **SC-010**: Operators can identify from CLI help and hosted-deployment documentation which command builds, which transfers, which verifies, and which operation alone can claim deployment success.

## Assumptions

- The first supported target is a Linux `amd64` destination, including local engines that use engine-managed emulation but produce and report the exact target platform.
- The operator has a functioning local Docker-compatible engine with Compose and BuildKit/build-secret support and a registered remote reachable through Sandbox.
- Source and deployment controllers can independently access the same repository commit and approved secret source; they do not exchange secret values through receipts.
- The remote Docker engine preserves image configuration and root-filesystem identities across the supported archive load path.
- Digest-pinned external dependencies are provisioned independently on the remote; this workflow verifies but does not distribute them.
- Existing hosted manifest, remote registration, durable-job, recovery, and secret-provider contracts remain authoritative.
- Readiness-timeout changes, general registry publication, multi-platform output, and public install-site publication remain outside this feature.
