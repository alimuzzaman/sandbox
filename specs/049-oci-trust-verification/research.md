# Research: OCI Trust and Verification

## Decision 1 — Trust input channels stay separate

- **Decision**: Treat machine policy as the only trusted input. Project intent and
  receipt/provenance are untrusted and cannot carry authority identity or policy digest.
- **Rationale**: Prevents repository content from self-approving a release.
- **Alternatives considered**: One merged document; project-selected digest. Rejected
  because channel confusion would be indistinguishable from policy.

## Decision 2 — Target-platform manifest identity only

- **Decision**: Accept only a policy-approved intended-private canonical GHCR repository
  plus exact target-platform OCI image-manifest digest, configuration digest, and explicit
  platform. Emit a canonical `DeliveryIdentityProjection`; do not claim visibility.
- **Rationale**: An index can resolve differently by host; tags are mutable.
- **Alternatives considered**: Tag, index digest plus child resolution, image ID alone.
  Rejected because resolution would require registry/daemon effects or later reinterpretation.

Registry visibility and authenticated-versus-anonymous access are observable only in
Feature 050's authorized registry interaction.

## Decision 3 — Receipt payload digest is external to payload

- **Decision**: Canonicalize a closed receipt payload without an identity field, hash
  those bytes, and compare the result to the separately machine-approved digest.
- **Rationale**: Avoids circular or presentation-dependent identity.
- **Alternatives considered**: Self-containing digest; raw file checksum. Rejected.

## Decision 4 — Signature mode is an explicit non-claim

- **Decision**: V1 accepts only `not_required` and never emits signature/publisher proof.
- **Rationale**: The authorized scope requires digest verification, not a verifier/tool
  supply chain.
- **Alternatives considered**: Cosign/offline bundle. Deferred to a contract change.

## Decision 5 — Canonical plan is the authority boundary

- **Decision**: `VerifiedImagePlan` uses a closed schema and domain-separated digest
  covering machine/policy identity, policy digest, receipt/provenance, exact image,
  platform, and topology.
- **Rationale**: Features 050/051 can validate rather than reinterpret trust.
- **Alternatives considered**: Loose dictionaries or separate partial receipts. Rejected.

## Decision 6 — Purity is enforced by composition

- **Decision**: The verifier accepts only values and exposes no injected callback.
- **Rationale**: Runtime conventions are weaker than an interface with no effect seam.
- **Alternatives considered**: Injected filesystem/config readers. Rejected.

## Decision 7 — Legacy state is outside the input model

- **Decision**: Old Feature 047/048 state has no parser path into verification.
- **Rationale**: Preserves state while preventing accidental trust adoption.
- **Alternatives considered**: Migration/adoption. Deferred to Feature 051 explicit adoption.
