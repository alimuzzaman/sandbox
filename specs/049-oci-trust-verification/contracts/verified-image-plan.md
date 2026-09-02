# Contract: VerifiedImagePlan v1

```json
{
  "schema_version": 1,
  "authority": {
    "authority_id": "machine-policy/...",
    "policy_revision": 1,
    "policy_digest": "sha256:...",
    "target_scope": {"remote": "...", "project": "...", "environment": "..."}
  },
  "receipt": {
    "payload_digest": "sha256:...",
    "source_repository": "...",
    "source_revision": "...",
    "build_identity": "sha256:...",
    "provenance": {
      "builder_id": "sha256:...",
      "workflow_id": "sha256:...",
      "invocation_id": "sha256:...",
      "materials_digest": "sha256:..."
    }
  },
  "image": {
    "registry": "ghcr.io",
    "repository": "owner/repository",
    "manifest_digest": "sha256:...",
    "config_digest": "sha256:...",
    "manifest_media_type": "application/vnd.oci.image.manifest.v1+json",
    "platform": {"os": "linux", "architecture": "amd64"}
  },
  "delivery_identity_projection": {
    "target_scope": {"remote": "...", "project": "...", "environment": "..."},
    "registry": "ghcr.io",
    "repository": "owner/repository",
    "repository_qualified_digest": "ghcr.io/owner/repository@sha256:...",
    "manifest_digest": "sha256:...",
    "config_digest": "sha256:...",
    "manifest_media_type": "application/vnd.oci.image.manifest.v1+json",
    "platform": {"os": "linux", "architecture": "amd64"},
    "topology": {"persistent_services": ["web"], "one_shot_services": ["migrate"]},
    "intended_visibility": "private"
  },
  "topology": {
    "persistent_services": ["web"],
    "one_shot_services": ["migrate"]
  },
  "signature_mode": "not_required",
  "plan_digest": "sha256:..."
}
```

Rules:

- Exact closed schema; no unknown or omitted field.
- Provenance is the exact four-field lowercase SHA-256 identity above, and
  `build_identity` is also lowercase SHA-256. `source_repository` is canonical
  lowercase owner/repository without traversal or dot segments; `source_revision` is
  exact lowercase 40 or 64 hex. Arbitrary annotations, URLs, paths, tokens,
  authorization/API-key shapes, diagnostics, or environment values have no schema.
- Arrays are canonical unique sorted service names.
- `delivery_identity_projection` is the single cross-feature identity. Repository
  storage is always owner/repository; the qualified digest is a derived equality check.
- `manifest_media_type` is exactly the OCI image-manifest media type. An OCI index,
  Docker manifest alias, or unknown media type is not accepted as this identity.
- `intended_visibility` records machine policy only and does not prove GHCR visibility.
- `plan_digest` is a domain-separated digest of canonical JSON excluding itself.
- The envelope contains no credential, path, raw environment, mutable tag, or effect authority.
- Consumers validate schema and digest only. They never reinterpret trust semantics.
