# Contract: Staging Policy v1

Machine-owned closed policy binds:

- exact Feature 049 `plan_digest`;
- remote/project/environment plus stable machine/daemon identity;
- helper artifact digest, fixed entry, installed runtime revision;
- broker recipient/binding/version and opaque credential-reference revision;
- exact `ghcr.io` repository-read operation;
- capability revision and policy digest.
- exact Feature 049 `DeliveryIdentityProjection`, including canonical owner/repository
  representation and topology;
- systemd/cgroup-v2 staging capability revision and exact unit-name derivation policy.
- activation proof-custody capability revision, 64-live-lease/pin capacity, 64 total full-proof
  capacity, 4096 tombstone capacity, and 16 MiB serialized per-target authority limit.

Project/caller values may not supply or widen these fields. Any mismatch refuses before
credential resolution/helper launch.

## Supported bundle provisioning

`host image provision --provision-phase stage-bundle --confirm` accepts an exact verified
plan, expected stage generation, registered `SOURCE/KEY`, and finite expiry. It performs
authenticated identity/helper/daemon observation, derives owner from registered policy,
and installs `runtime/hosting/image-staging/policies/<selector>.json` mode `0600`. It does
not contact the registry or return source bytes, resolved credentials, or config.
