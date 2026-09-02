# Data Model: OCI Trust and Verification

All entities are immutable, closed, bounded, and secret-free.

## MachinePolicyAuthority

- `schema_version`: supported integer
- `authority_id`: stable opaque machine/policy identity
- `policy_revision`: positive integer
- `policy_digest`: digest of canonical policy payload excluding this field
- `target_scope`: exact remote/project/environment selector
- Validation: parsed only by the machine-config owner, which issues a private exact
  token using a module-owned construction capability. The token type and issuer are
  not public exports; ordinary construction and project/receipt construction refuse.

## OCIImageIdentity

- `registry`: exactly `ghcr.io`
- `repository`: canonical owner/repository path (never stored as a full registry string)
- `manifest_digest`: target-platform `sha256` OCI image-manifest digest
- `config_digest`: exact `sha256` image configuration digest
- `manifest_media_type`: exactly `application/vnd.oci.image.manifest.v1+json`
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
- `source_repository`: canonical lowercase owner/repository without traversal/dot segments
- `source_revision`: exact lowercase 40 or 64 hex
- `build_identity`: exact lowercase SHA-256 digest
- closed `provenance` identity containing exactly `builder_id`, `workflow_id`,
  `invocation_id`, and `materials_digest`; all four are lowercase SHA-256 digests
- `signature_mode`: exactly `not_required`
- Identity: domain-separated digest of the canonical payload; the digest is external.

## ProjectImageIntent

- `policy_selector`: machine-owned lookup key, not authority
- `persistent_services`: unique non-empty ordered tuple containing primary service
- `one_shot_services`: unique ordered tuple disjoint from persistent services
- Validation: issued only from an explicit declaration in the selected primary project
  descriptor; every service declared and policy-allowed; dependencies excluded.

## ApplicationTopology

- exact persistent and one-shot partitions
- exact service-to-`OCIImageIdentity` bindings
- Validation: one identity for every selected service, no missing/extra/duplicate service.

## VerifiedImagePlan

- `schema_version`
- `authority_id`, `policy_revision`, `policy_digest`, `target_scope`
- complete `delivery_identity_projection`
- `receipt_payload_digest`, closed provenance identity
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
