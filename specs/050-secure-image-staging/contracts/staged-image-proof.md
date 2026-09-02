# Contract: StagedImageProof v1

```json
{
  "schema_version": 1,
  "request": {"request_id": "...", "request_digest": "sha256:..."},
  "plan_digest": "sha256:...",
  "staging_policy_digest": "sha256:...",
  "target": {"machine_identity": "...", "target_identity": "...", "daemon_identity": "..."},
  "helper": {"artifact_digest": "sha256:...", "runtime_revision": "...", "capability_revision": "..."},
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
  "observed_identity": {
    "repository": "owner/repository",
    "repo_digest": "ghcr.io/owner/repository@sha256:...",
    "config_digest": "sha256:...",
    "platform": {"os": "linux", "architecture": "amd64"},
    "local_image_id": "sha256:...",
    "topology_digest": "sha256:..."
  },
  "registry_access_observation": {"anonymous_exact_manifest": "denied", "authenticated_exact_manifest": "succeeded", "observation_digest": "sha256:..."},
  "observation_id": "sha256:...",
  "staging_generation": 1,
  "proof_digest": "sha256:..."
}
```

Closed canonical schema. `proof_digest` covers every other field. It contains no
credential, path, argv, environment, output, mutable tag, or activation authority.
The delivery projection is byte-identical to Feature 049; `repository` always means
owner/repository, never a second full-string representation.

Exact completed replay returns byte-identical proof while the full proof is retained.
At most 64 total full proofs including leased/pinned proofs, 4096 tombstones, 64 live proof
leases/pins, and 16 MiB total
serialized authority per target are retained. A prepared or accepted
activation proof lease pins its full proof until the exact activation terminal authority is
durable. Compaction selects only unleased/unpinned proofs and writes a permanent
request/proof-digest tombstone. Replay then returns stable `proof_expired` non-success;
Feature 051 never reconstructs, weakens, or authorizes from a tombstone. Tombstones are
never deleted or recycled. Existing replay reads retained authority. A new unique request
always returns `retention_full` when `tombstone_count == 4096`; otherwise acceptance may
compact only unleased/unpinned proofs and reserve capacity when the resulting state stays
within every count and byte bound. Refusal precedes owner creation and effects.

Feature 051 must not validate from a naked lookup. Feature 050 first creates a durable
prepared proof-custody lease bound to the exact activation/proof identities and durable
activation-owner/request holder; creation immediately pins the full proof. Its finite
admission deadline never auto-unpins, cannot authorize a new acceptance after expiry, and
does not block exact promotion when the bound acceptance already exists. The
stage-ledger lock is held across validation and durable host-state acceptance, after which
the same lease becomes an accepted pin. Crash replay reconciles the lease against atomic host
state. Only the exact durable terminal activation owner can release it; the compactor owns
no lease/pin transitions.
