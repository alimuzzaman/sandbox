# Data Model: OCI Trust and Verification

All entities are immutable, closed, bounded, and secret-free.

## MachinePolicyAuthority

- `schema_version`: supported integer
- `authority_id`: stable opaque machine/policy identity
- `policy_revision`: positive integer
- `policy_digest`: digest of canonical policy payload excluding this field
- `target_scope`: exact remote/project/environment selector
- Validation: constructed only from trusted machine input; never project/receipt input.

## OCIImageIdentity

- `registry`: exactly `ghcr.io`
- `repository`: canonical owner/repository path (never stored as a full registry string)
- `manifest_digest`: target-platform `sha256` OCI image-manifest digest
- `config_digest`: exact `sha256` image configuration digest
- `platform`: canonical OS, architecture, optional variant
- Validation: no tag/index/alias/derived or foreign-platform form.

## DeliveryIdentityProjection

- `target_scope`: canonical remote/project/environment values from machine policy
- `registry`: literal `ghcr.io`
- `repository`: canonical owner/repository path
- `repository_qualified_digest`: exactly `ghcr.io/<repository>@<manifest_digest>`
- `manifest_digest`, `config_digest`, `platform`
- canonical persistent and one-shot topology
- `intended_visibility`: literal `private` policy declaration, not an observation
- Validation: closed, domain-separated, and copied byte-for-byte into Feature 050 proof.

## ReleaseReceiptPayload

- `schema_version`
- `repository`, `manifest_digest`, `config_digest`, `platform`
- `source_repository`, `source_revision`, `build_identity`
- bounded canonical `provenance` mapping
- `signature_mode`: exactly `not_required`
- Identity: domain-separated digest of the canonical payload; the digest is external.

## ProjectImageIntent

- `policy_selector`: machine-owned lookup key, not authority
- `persistent_services`: unique non-empty ordered tuple containing primary service
- `one_shot_services`: unique ordered tuple disjoint from persistent services
- Validation: every service declared and policy-allowed; dependencies excluded.

## ApplicationTopology

- exact persistent and one-shot partitions
- exact service-to-`OCIImageIdentity` bindings
- Validation: one identity for every selected service, no missing/extra/duplicate service.

## VerifiedImagePlan

- `schema_version`
- `authority_id`, `policy_revision`, `policy_digest`, `target_scope`
- complete `delivery_identity_projection`
- `receipt_payload_digest`, normalized provenance
- `image`: `OCIImageIdentity`
- `topology`: `ApplicationTopology`
- `signature_mode`: `not_required`
- `plan_digest`: domain-separated digest over every other field
- State: immutable value; no lifecycle or persistence owned by Feature 049.

## VerificationResult

- success: exact plan and stable `verified` class
- refusal: stable bounded class and safe field locations, no partial plan

## Relationship Rules

1. Machine policy approves one receipt payload digest and image identity.
2. Receipt facts equal policy facts exactly.
3. Project intent narrows policy topology.
4. Topology maps all selected services to the one image identity.
5. Plan digest binds the complete result.
