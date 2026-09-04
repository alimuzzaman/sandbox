# Feature Specification: OCI Trust and Verification

**Feature Branch**: `codex/feature-047-immutable-oci-clean`

**Created**: 2026-08-31

**Status**: Draft

**Input**: Ready PRD at `specs/049-oci-trust-verification/prd.md`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Produce One Verified Image Plan (Priority: P1)

A release operator presents one machine policy, project declaration, and release
receipt. Sandbox verifies their exact authority, provenance, image, platform, and
application topology and returns one canonical plan that later features can consume.

**Why this priority**: No credential or host effect is safe until one component owns
and freezes the complete trust decision.

**Independent Test**: Supply canonical matching inputs in different key orders and
prove they return byte-equivalent plans and the same plan digest while every effect
witness remains untouched.

**Acceptance Scenarios**:

1. **Given** an identified machine policy approving an exact receipt payload digest,
   repository-qualified target-platform manifest digest, configuration digest,
   platform, provenance, and service topology, **When** matching bounded project and
   receipt input is verified, **Then** one complete `VerifiedImagePlan` is returned.
2. **Given** semantically identical inputs with different presentation order, **When**
   each is verified, **Then** both return the same canonical plan and digest.
3. **Given** valid input, **When** verification succeeds, **Then** no credential,
   network, Docker, process, remote, time, randomness, or persistence authority is
   reached.

---

### User Story 2 - Refuse Ambiguous or Untrusted Evidence (Priority: P1)

A security reviewer can show that mutable, partial, malformed, substituted, or
conflicting evidence never produces a plan or performs an effect.

**Why this priority**: A pure verifier is useful only when every failure is closed and
bounded before downstream work exists.

**Independent Test**: Run the complete malformed/unknown/substitution matrix against
effect witnesses and confirm every case returns one stable refusal, no partial plan,
and zero effects.

**Acceptance Scenarios**:

1. **Given** a tag, OCI index, alternate registry, foreign platform, derived child,
   mixed release, or malformed digest, **When** verification runs, **Then** it refuses
   with no plan.
2. **Given** project or receipt input attempts to supply or replace machine authority,
   **When** verification runs, **Then** it refuses before interpreting that value as
   trust.
3. **Given** unknown fields, duplicate services, unsupported versions, oversize input,
   or contradictory receipt/provenance/topology facts, **When** verification runs,
   **Then** it returns a bounded non-authorizing result.

---

### User Story 3 - Hand Off Trust Without Reinterpretation (Priority: P2)

A staging or activation caller can validate the closed plan envelope and use its
identities without gaining authority to reinterpret trust, signatures, provenance,
platform, or topology.

**Why this priority**: A verified plan must be a stable dependency boundary, not a
suggestion that each later phase can weaken.

**Independent Test**: Validate exact and modified plan envelopes through the public
consumer contract. Exact plans pass structural validation; altered, partial, legacy,
or unknown plans fail without invoking the trust decision again.

**Acceptance Scenarios**:

1. **Given** a complete plan, **When** a consumer validates its closed schema and
   digest, **Then** the consumer receives only its bounded immutable values.
2. **Given** any changed plan field, unknown field, missing field, or digest mismatch,
   **When** a consumer validates it, **Then** it refuses.
3. **Given** old Feature 047 image state or a Feature 048 sibling receipt, **When** it
   is presented as a plan, **Then** it remains untouched and non-authorizing.

### Edge Cases

- Machine/policy authority identity matches but canonical policy digest differs.
- Policy digest matches but comes through the untrusted project/receipt channel.
- Receipt payload is valid but its separately supplied identity is self-referential,
  mismatched, or computed from a non-canonical representation.
- Receipt names the same manifest digest with a different repository or platform.
- An OCI index happens to equal an approved-looking digest.
- Platform values differ only by case, aliases, missing variant, or unsupported form.
- Persistent and one-shot partitions overlap, contain duplicates, omit the primary
  service, include a dependency, or exceed their bound.
- Signature-like fields are supplied while v1 policy is `not_required`.
- Input reaches a byte, string, collection, nesting, or diagnostic bound.
- A dependency attempts to read time, randomness, environment, filesystem state, or
  another external authority during verification.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Verification MUST accept three separate channels: trusted machine-policy
  input, untrusted project intent, and untrusted release receipt/provenance input. The
  trusted token type and issuer MUST NOT be public exports, ordinary construction MUST
  fail, and only machine-config normalization may issue it using a private capability.
- **FR-002**: Trusted policy MUST contain a stable machine/policy authority identity,
  canonical policy digest, schema version, policy revision, exact target scope,
  repository allowlist entry, approved canonical receipt-payload digest, approved
  target-platform manifest/configuration digests, platform, provenance constraints,
  signature mode, and application topology constraints.
- **FR-003**: Project and receipt input MUST NOT supply, replace, or widen any trusted
  policy authority; attempted channel substitution MUST refuse. Project intent MUST
  come only from the selected primary project descriptor; global, override, and label
  layers MUST NOT add, inherit, or replace it.
- **FR-004**: V1 MUST accept only policy-approved `intended_private` canonical
  `ghcr.io/<owner>/<repository>` identities and MUST reject alternate registries or
  ambiguous repository spelling; it MUST NOT claim registry visibility was observed.
- **FR-005**: The approved image MUST be an exact lowercase `sha256` digest of a
  target-platform OCI image manifest qualified by its repository.
- **FR-006**: Tags, OCI index digests, foreign-platform manifests, derived child
  identities, mutable aliases, and mixed application image identities MUST refuse.
- **FR-007**: The receipt payload identity MUST be the digest of a bounded canonical
  receipt payload that excludes the identity field itself.
- **FR-008**: Trusted policy MUST separately approve the exact canonical receipt
  payload digest and exact repository-qualified image identity.
- **FR-009**: Receipt verification MUST compare exact repository, manifest digest,
  configuration digest, platform, source repository, source revision, build identity,
  provenance values, and receipt schema. `builder_id`, `workflow_id`, `invocation_id`,
  `materials_digest`, and `build_identity` are exact lowercase SHA-256 digests.
  `source_repository` is canonical lowercase owner/repository without traversal or dot
  segments; `source_revision` is exact lowercase 40 or 64 hex. Arbitrary metadata,
  paths, token/authorization/API-key shapes, and diagnostics are not accepted or retained.
- **FR-010**: V1 signature mode MUST be exactly `not_required`; verification MUST NOT
  infer signature presence, validity, or publisher identity.
- **FR-011**: Project topology MUST contain one non-empty persistent application-service
  partition and one disjoint one-shot partition, each unique and bounded.
- **FR-012**: The primary hosted service MUST be present in the persistent partition.
- **FR-013**: Every selected service MUST be declared by the project and allowed by
  machine policy; dependency and unknown services MUST refuse.
- **FR-014**: Every selected persistent and one-shot service MUST bind the same exact
  approved repository, manifest digest, configuration digest, and platform.
- **FR-015**: Input schemas MUST be closed and versioned; unknown fields, unknown
  versions, malformed values, duplicate identities, and impossible relationships
  MUST refuse.
- **FR-016**: Every input string, collection, nesting level, document, diagnostic, and
  output MUST have a finite declared bound; reaching a bound MUST be non-authorizing.
- **FR-017**: Successful verification MUST return exactly one closed, versioned,
  secret-free `VerifiedImagePlan`.
- **FR-018**: The plan MUST bind one canonical `DeliveryIdentityProjection` containing
  target scope, registry, owner/repository, repository-qualified manifest digest,
  configuration digest, platform, and topology, together with the machine/policy
  authority identity, canonical policy digest and revision, canonical receipt payload digest,
  provenance, repository-qualified manifest and configuration digests, platform,
  persistent/one-shot partitions, signature mode, and plan schema.
- **FR-019**: `plan_digest` MUST cover every authority-bearing plan field through one
  canonical serialization that excludes `plan_digest` itself.
- **FR-020**: Identical semantic inputs MUST produce identical canonical plan bytes
  and digest; any authority-bearing change MUST change the digest or refuse.
- **FR-021**: Refusal MUST return only a stable versioned result class and bounded safe
  field locations; arbitrary input values MUST NOT be echoed.
- **FR-022**: Verification MUST NOT read or resolve credentials, access the network,
  invoke Docker, launch processes, connect to a remote, read time or randomness, or
  create/update/delete persistent or runtime state.
- **FR-023**: Verifier composition MUST expose no dependency capable of an FR-022
  effect; an attempted effect dependency MUST fail construction or invocation.
- **FR-024**: Consumers MUST validate the entire closed plan schema, canonical form,
  digest, and exact `DeliveryIdentityProjection` before use, MUST preserve its repository
  representation, and MUST NOT reinterpret trust, signatures, provenance, platform,
  topology, or image identity.
- **FR-025**: Old Feature 047 image planes/journals/receipts and Feature 048 state MUST
  remain untouched and MUST NOT authorize a plan.
- **FR-026**: Existing non-opt-in hosting behavior and public contracts MUST remain
  unchanged.
- **FR-027**: Documentation MUST distinguish machine approval, deterministic identity
  equality, later artifact observation, registry authentication, signature proof,
  staging proof, runtime proof, and production proof.
- **FR-028**: Returned values and any downstream-retained copies MUST exclude
  credentials, private paths, raw environment, mutable tags, source contents, raw
  untrusted diagnostics, and reversible secret-derived values.

### Key Entities

- **Machine Trust Policy**: Trusted, machine-owned authority identity and digest plus
  exact receipt/image/platform/provenance/topology approvals.
- **Release Receipt Payload**: Bounded canonical non-secret release description whose
  separately computed digest is approved by policy.
- **Project Image Intent**: Untrusted policy selector and declared application-service
  partitions that may only narrow machine authority.
- **OCI Image Identity**: Canonical repository plus target-platform manifest digest,
  configuration digest, and platform.
- **DeliveryIdentityProjection**: Single canonical target-scope, registry,
  owner/repository, repository-qualified digest, config, platform, and topology value
  copied unchanged into Feature 050 proof and compared unchanged by Feature 051.
- **Application Topology**: Exact persistent and one-shot service partitions.
- **VerifiedImagePlan**: Closed immutable handoff containing the full trust decision
  and its canonical plan digest.
- **Verification Result**: Stable success/refusal envelope with no effect authority.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All equivalent valid input permutations produce byte-identical plans and
  the same digest.
- **SC-002**: The complete invalid/substitution/boundary matrix produces no plan and
  reaches zero effect witnesses.
- **SC-003**: Every successful plan covers exactly all selected application services
  once and binds one repository-qualified target-platform image identity.
- **SC-004**: Any single authority-bearing field change changes the plan digest or
  produces refusal.
- **SC-005**: Secret/privacy inspection finds zero forbidden values in all success and
  refusal outputs and downstream-safe serialized copies.
- **SC-006**: Legacy Feature 047/048 state fixtures remain byte-for-byte unchanged and
  never alter a verification result.
- **SC-007**: A consumer can distinguish verified policy equality and intended-private
  declaration from Feature 050 registry visibility/access observation, signature,
  artifact-presence, runtime, and production proof using one result.

## Assumptions

- The machine owner receives the receipt payload digest and image identity from an
  authorized release process before configuring policy.
- All selected application services for v1 use one image identity.
- Feature 050 and Feature 051 validate the complete plan and do not reinterpret it.
- Live artifact, registry, remote, deployment, and production proof occur only in
  later authorized phases.

## First-activation provisioning requirement

- **FR-029**: Protected `host image provision --provision-phase machine-policy` MUST
  derive the exact v2 policy only from the closed receipt, complete explicit bindings,
  and public machine authority. It atomically installs the policy and public activation
  companion owner-only; replay is idempotent, conflict refuses, and private keys are
  never accepted or exposed.
