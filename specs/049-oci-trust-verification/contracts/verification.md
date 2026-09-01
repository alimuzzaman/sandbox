# Contract: Pure OCI Verification

## Input

Three already-bounded channels:

1. trusted `MachinePolicyAuthority` plus policy payload;
2. untrusted `ProjectImageIntent`;
3. untrusted `ReleaseReceiptPayload` plus its claimed external digest.

No channel contains credentials or an effect callback.

## Success

```json
{
  "schema_version": 1,
  "ok": true,
  "result_class": "verified",
  "plan": {"schema_version": 1, "plan_digest": "sha256:..."}
}
```

The complete plan follows [verified-image-plan.md](verified-image-plan.md).

## Refusal

```json
{
  "schema_version": 1,
  "ok": false,
  "result_class": "policy_mismatch",
  "locations": ["receipt.manifest_digest"]
}
```

Stable classes include `input_invalid`, `input_too_large`, `authority_substitution`,
`policy_mismatch`, `receipt_mismatch`, `provenance_mismatch`, `image_invalid`,
`platform_mismatch`, `topology_mismatch`, `signature_mode_unsupported`, and
`plan_invalid`. Locations are allowlisted schema paths; values are never echoed.

## Effect Contract

Verification has no credential, filesystem, network, Docker, subprocess, remote,
clock, randomness, or persistence interface. Success and refusal both perform zero
effects.
