# Contract: Recovery Manifest v1

Required top-level fields: `schema_version=1`, immutable `id`, `status=complete`, timestamps,
host/catalog provenance, selected profiles, artifact records, exclusions, ciphertext object,
ciphertext SHA-256 and size, and required restore-tool compatibility.

For staged archive manifests, `profile_bindings` is a non-secret object keyed by each
selected profile ID. Each binding records declared dependency IDs, the restore target,
and allowed-root labels. Restore planning compares those dependencies with the active
catalog before it can produce an apply-capable plan.

The manifest contains no passphrase, API token, database credential, decrypted secret, command
line, or sensitive file content. A manifest is valid only after all artifacts validate and the
ciphertext remote object is verified. Restore rejects unknown schema versions or non-complete
status without changing state.

The ciphertext object is canonically bound to the manifest ID: it must be
sets/<id>/archive.bin or sets/<id>/archive.tar.gpg. Cross-set object references are invalid
even when their supplied hash and size match.
