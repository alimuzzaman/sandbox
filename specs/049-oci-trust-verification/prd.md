# Product Requirements Draft: OCI Trust and Verification

**Status**: Validated

**Created**: 2026-08-31

**Last Refined**: 2026-09-01

**Input**: "Verify machine-approved OCI release receipts, provenance, exact target-platform digests, configuration identity, and declared application topology as a pure effect-free policy decision that emits one immutable VerifiedImagePlan and performs no credential access, Docker work, remote process launch, or state mutation."

**Drafting Model**: `gpt-5.6-sol` High (configured planning worker; Terra Medium was not active)

**Final Validation**: PASS — gpt-5.6-sol High

**Validated On**: 2026-08-31

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Private image delivery needs a trust decision before any registry credential or
remote host can be reached. Mutable tags, a successful login, cached image names,
and repository declarations are not release authority. A later staging or activation
layer also cannot safely reinterpret provenance, platform, topology, or digest rules.

Sandbox needs one small, deterministic verifier that turns bounded, machine-approved
release evidence plus project intent into one immutable plan. The same input must
always produce the same decision and plan identity. Invalid, partial, unknown, or
conflicting input must fail without side effects.

## Users and Desired Outcomes

- **Machine owner**: Defines a stable machine/policy authority identity, canonical
  policy digest, exact intended-private GHCR repository, approved release-receipt identity,
  target platform, and allowed application-service topology.
- **Release operator**: Receives a clear allow/refuse decision and a canonical plan
  that later stages can consume without re-deciding trust.
- **Project maintainer**: Selects only declared primary, background, and one-shot
  application services within the machine policy boundary.
- **Security reviewer**: Can audit a pure decision surface and prove it has no path
  to credentials, processes, Docker, network, remote hosts, or persistent state.

## Goals

- Verify one bounded release receipt and provenance statement against one current
  machine-owned trust policy with an exact authority identity and policy digest.
- Approve only a repository-qualified target-platform OCI image-manifest digest,
  its exact configuration digest, and its declared platform.
- Bind the exact persistent and one-shot application-service partitions to the
  approved release without silently including dependencies or unknown services.
- Produce one canonical, immutable, secret-free `VerifiedImagePlan` with a stable
  digest over every authority-bearing input.
- Make verification deterministic, effect-free, bounded, and fail closed.
- Keep all trust, provenance, digest, platform, topology, and signature-policy
  interpretation in this feature so later features can only consume the result.
- Preserve existing hosting behavior for projects that do not request this plan.

## Non-Goals

- Reading or resolving credentials, contacting GHCR, pulling or inspecting a local
  image, invoking Docker, starting a process, using SSH, or mutating state.
- Staging, activation, Compose, one-shot execution, health checks, edge work,
  adoption, recovery, or rollback.
- Building, signing, publishing, promoting, copying, deleting, or scanning images.
- Accepting tags, OCI index digests, foreign-platform manifests, alternate
  registries, mixed application releases, or derived child identity.
- Making registry authentication a trust signal.
- Introducing signature verification in v1. The machine owner approves the exact
  release-receipt digest and image-manifest digest; later features may not claim a
  stronger signature result.
- Trusting legacy Feature 047 image journals, local cache entries, runtime receipts,
  or Feature 048 sibling state as Feature 049 authority.

## Product Scenarios

### Scenario 1 — Verify an approved release

- **Starting state**: An identified machine policy with a canonical policy digest
  approves one GHCR repository, canonical release-receipt payload digest, target
  platform, exact manifest/configuration digests, and service topology.
- **User action**: An operator verifies a matching project declaration and bounded
  release receipt.
- **Expected outcome**: Sandbox returns one canonical `VerifiedImagePlan` that binds
  the policy revision, provenance, exact image identity, platform, topology, and
  plan digest without any external effect.

### Scenario 2 — Refuse mutable or ambiguous identity

- **Starting state**: Input contains a tag, OCI index, wrong repository/platform,
  omitted or mismatched configuration digest, mixed service digest, or unknown field.
- **User action**: An operator requests verification.
- **Expected outcome**: Sandbox returns a stable bounded refusal and produces no plan.

### Scenario 3 — Refuse provenance or policy drift

- **Starting state**: The canonical receipt payload digest, subject digest, source
  revision, build identity, machine/policy authority identity, policy digest,
  allowed topology, or machine approval differs.
- **User action**: An operator requests verification or reuses prior input.
- **Expected outcome**: The decision fails closed; an old plan is never silently
  refreshed or treated as current.

### Scenario 4 — Preserve old state without granting authority

- **Starting state**: Existing hosting or Feature 048 records contain old Feature 047
  image planes, receipts, journals, or rollback generations.
- **User action**: An operator verifies a Feature 049 release.
- **Expected outcome**: Old records remain untouched and cannot approve, alter, or
  replace Feature 049 policy, receipt, provenance, topology, or plan identity.

## Proposed Product Behavior

- Verification accepts only explicit bounded values. It does not discover facts from
  a registry, daemon, host, process, environment, filesystem state, or prior receipt.
- The machine-policy input is the sole trusted authority and is separate from
  untrusted project and receipt input. Project input can select a policy through the
  supported machine-owned channel and narrow its service set, but cannot supply,
  replace, or add the machine/policy authority identity, policy digest, repository,
  release digest, platform, provenance identity, or service authority.
- The approved image reference is canonical policy-approved, intended-private
  `ghcr.io/<owner>/<repository>` plus an exact lowercase `sha256` target-platform
  image-manifest digest. Feature 049 does not observe or prove registry visibility. OCI indexes,
  tags, alternate registries, and foreign-platform manifests are refused.
- The release-receipt identity is the digest of its bounded canonical payload; that
  payload excludes the digest field itself and binds repository, manifest digest,
  image configuration digest, platform, source repository and exact revision, build
  identity, and bounded provenance facts. The current machine policy separately
  approves that exact canonical payload digest and image identity.
- V1 signature policy is explicitly `not_required`. No signature or publisher claim
  is inferred. A future signature mode requires a separate contract change here.
- The topology contains one non-empty persistent-service partition and a disjoint
  one-shot partition, both drawn exactly from declared application services. Every
  selected service uses the same approved image identity.
- Unknown keys, duplicate services, empty required values, oversized collections,
  malformed digests, contradictory facts, and unsupported schema versions refuse.
- A successful plan contains only bounded non-secret canonical identities, a schema
  version, stable machine/policy authority identity, canonical policy digest, exact
  receipt/provenance/image/platform/topology values, and its own digest. Its plan
  digest covers every one of those authority-bearing values. It contains no
  credential or mutable source.
- Identical canonical inputs produce the same plan digest. Any authority-bearing
  change produces a different plan or refusal.
- Verification has no durable lifecycle and grants no staging or activation effect.
  Consumers must validate the complete closed plan schema and digest but may not
  reinterpret its trust decision.

## Constraints and Dependencies

- The project constitution's modularity and compatibility rules apply, but this
  feature owns policy and value semantics only.
- Repository and receipt data are untrusted inputs. Machine policy is outside the
  project and is never widened by project content.
- Inputs and outputs must have finite schema, string, collection, and byte bounds.
- No dependency injected into the verifier may expose credential, network, process,
  Docker, remote, clock, randomness, or persistence authority.
- Later Feature 050 staging must consume a complete `VerifiedImagePlan`; Feature 051
  activation/recovery must consume it without changing trust semantics.
- Local/static validation is not registry, host, deployment, or production proof.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Feature identity | Feature 049 | It is the first dependency in the split workflow | Task owner |
| Trust authority | Stable machine/policy identity plus canonical policy digest and separately approved canonical receipt-payload digest | Project and registry authentication cannot self-authorize or substitute policy authority | Task owner |
| Image identity | Repository-qualified target-platform manifest digest plus configuration digest | Avoids tag, index, and platform ambiguity | Task owner |
| Visibility claim | Policy-approved `intended_private`; authenticated/anonymous registry observations belong to Feature 050 | Pure policy cannot prove registry visibility | Consolidated review remediation |
| Application topology | One image identity across selected persistent and one-shot services | Small, auditable release unit | Task owner |
| Signature policy | Not required in v1 and never inferred | Digest verification is mandatory; Cosign is outside the smaller v1 | Task owner approval |
| Effects | None | Trust must be decided before credentials or host activity | Task owner |
| Legacy evidence | Preserved but non-authorizing | Avoids destructive migration and trust escalation | Task owner and Feature 048 contract |

## Open Questions

- None.

## Acceptance Outcomes

- Every valid canonical input yields the same plan digest across repeated and
  independently ordered presentations; every authority-bearing change changes the
  plan identity or refuses.
- Every tag, index, alternate registry, foreign platform, mixed release, unknown key,
  malformed digest, policy drift, receipt drift, provenance mismatch, and topology
  mismatch refuses before producing a plan.
- Missing or mismatched machine/policy identity, policy digest, and any attempt to
  supply policy authority through the project/receipt channel refuses before a plan.
- Effect witnesses prove that successful and refused verification perform zero
  credential, network, Docker, process, remote, or persistence operations.
- Every successful plan covers exactly all selected persistent and one-shot services,
  with no duplicate, missing, dependency, or extra service.
- Returned values and any downstream-retained copies contain no credential, private
  path, raw environment, mutable tag, or untrusted diagnostic text.
- Old Feature 047 and Feature 048 image state never changes a Feature 049 decision
  and remains untouched.

## Risks and Assumptions

- **Risk**: This verifier proves equality to a machine-approved content identity; it
  does not prove artifact presence, the relationship between actual manifest and
  configuration bytes, publisher identity, registry availability/visibility, or a signature.
  Those facts require later authorized observation or a future trust-contract change.
- **Risk**: A platform or OCI descriptor interpreted differently downstream would
  split authority. The closed plan contract must remain the only interpretation.
- **Risk**: Overbroad topology could make dependency images look trusted. Only the
  explicit application-service partitions are in scope.
- **Assumption**: The release system can provide a bounded receipt with exact source,
  build, platform, manifest, and configuration identities before Sandbox verification.
- **Assumption**: All selected application services can use one release image.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: READY FOR SPECKIT

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
