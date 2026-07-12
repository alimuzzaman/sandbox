# Contract: Recovery Manifest v1

Required top-level fields: `schema_version=1`, immutable `id`, `status=complete`, timestamps,
host/catalog provenance, selected profiles, artifact records, exclusions, ciphertext object,
ciphertext SHA-256 and size, and required restore-tool compatibility.

The manifest contains no passphrase, API token, database credential, decrypted secret, command
line, or sensitive file content. A manifest is valid only after all artifacts validate and the
ciphertext remote object is verified. Restore rejects unknown schema versions or non-complete
status without changing state.
