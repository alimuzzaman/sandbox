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
    "build_identity": "...",
    "provenance": {}
  },
  "image": {
    "registry": "ghcr.io",
    "repository": "owner/repository",
    "manifest_digest": "sha256:...",
    "config_digest": "sha256:...",
    "platform": {"os": "linux", "architecture": "amd64"}
  },
  "delivery_identity_projection": {
    "target_scope": {"remote": "...", "project": "...", "environment": "..."},
    "registry": "ghcr.io",
    "repository": "owner/repository",
    "repository_qualified_digest": "ghcr.io/owner/repository@sha256:...",
    "manifest_digest": "sha256:...",
    "config_digest": "sha256:...",
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
- Arrays are canonical unique sorted service names.
- `delivery_identity_projection` is the single cross-feature identity. Repository
  storage is always owner/repository; the qualified digest is a derived equality check.
- `intended_visibility` records machine policy only and does not prove GHCR visibility.
- `plan_digest` is a domain-separated digest of canonical JSON excluding itself.
- The envelope contains no credential, path, raw environment, mutable tag, or effect authority.
- Consumers validate schema and digest only. They never reinterpret trust semantics.
