# Contract: Pure OCI Verification

## Input

Three already-bounded channels:

1. a private machine-boundary-issued trusted policy token;
2. untrusted `ProjectImageIntent`;
3. untrusted `ReleaseReceiptPayload` plus its claimed external digest.

No channel contains credentials or an effect callback.

The trusted token type and issuer are not public package symbols. Direct ordinary
construction fails because only the machine normalization boundary holds the private
construction capability.

Raw JSON objects are parsed only by their owning project-config, machine-config, or
receipt boundary. Those boundaries accept exact built-in JSON containers/primitives
and issue distinct immutable channel types. The pure verifier accepts only those exact
types; mappings and channel interchange refuse.

The project-config boundary reads `hostingImages` only from the selected primary
project descriptor. Global/default, override, label, and legacy-loader copies do not
create or replace this channel; primary absence issues no project intent.

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

## Supported policy provisioning

`host image provision --provision-phase machine-policy --confirm` derives selector
`sha256(remote NUL project NUL environment)`, pins receipt workflow/source/platform and
all image digests, and requires complete topology/binding inputs. It installs owner-only
`runtime/hosting/image-verification/policies/<selector>.json` plus the command-generated
public activation companion. Handwritten authority and private signing-key paths refuse.
