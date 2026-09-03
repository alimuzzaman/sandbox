# Immutable multi-image trust plan v2

The schema-version 2 flow is an additive bridge for a hosted-production
multi-image release receipt. It does not change the v1 single-image policy,
plan, staging, activation, or recovery schemas.

Trust verification is read-only:

```sh
./sb host image verify \
  --machine-plan-set-policy /owner-only/machine-plan-set-policy.json \
  --signed-receipt-directory /path/to/hosted-production-receipt \
  --json
```

The receipt directory is closed. It must contain only `receipt.json`,
`receipt.sha256`, `receipt.bundle`, and the `queue`, `web`, and `worker`
`.payload.json` and `.bundle` pairs. Symlinks are refused. The checksum, each
payload digest, each bundle digest, source revision, workflow claims, platform,
and complete service/image bindings are checked before a plan set exists.
Cosign runs with `verify-blob --offline --new-bundle-format` and exact policy-pinned
GitHub workflow certificate claims. No credential or verifier output is persisted.

Upstream workflow producer
--------------------------

Cosign v3 `sign --bundle` signs a new-format in-toto Statement. It does not
export the legacy image-signature payload required by this contract, and
`cosign generate` is not a substitute. For each image, the release workflow
must instead create a deterministic JSON blob with this exact closed shape:

```json
{"critical":{"identity":{"docker-reference":"ghcr.io/OWNER/REPOSITORY"},"image":{"docker-manifest-digest":"sha256:MANIFEST"},"type":"cosign container image signature"},"optional":{}}
```

The repository and manifest digest must be the exact values later recorded in
`receipt.json`. Sign that blob directly with Cosign v3:

```sh
cosign sign-blob --yes --bundle queue.bundle queue.payload.json
cosign sign-blob --yes --bundle web.bundle web.payload.json
cosign sign-blob --yes --bundle worker.bundle worker.payload.json
```

Each output bundle must be the Sigstore protobuf JSON media type
`application/vnd.dev.sigstore.bundle.v0.3+json` with a `messageSignature`.
Sandbox parses these bundles with dedicated byte, nesting, node, key, and value
limits sized for Sigstore certificate and transparency-proof material. The
smaller canonical policy-document limits do not apply to bundle internals.
Passing structural validation does not establish trust: the offline Cosign
signature and exact workflow certificate checks remain authoritative.
Record the SHA-256 digest of each exact payload and bundle in the corresponding
receipt image row. After the receipt is complete, sign its exact bytes the same
way and produce the exact checksum file:

```sh
cosign sign-blob --yes --bundle receipt.bundle receipt.json
sha256sum receipt.json > receipt.sha256
```

The workflow must verify each payload and the receipt with
`verify-blob --offline --new-bundle-format` plus the same certificate identity,
issuer, repository, ref, and SHA constraints used by Sandbox before publishing
the closed receipt directory. Registry-attached `cosign sign` artifacts may be
published separately, but they are not inputs to this verifier.

The machine policy is a closed schema-version 2 object. It pins:

- `authority_id`, `policy_revision`, `policy_digest`, and `target_scope`;
- `approved_receipt_digest`, `source_repository`, `source_ref`,
  `source_revision`, `platform`, and the complete `workflow` identity;
- sorted `persistent_services`, `one_shot_services`, and exhaustive sorted
  `service_image_bindings` rows shaped as `{service,image}`;
- exact sorted `activation_environment_bindings` rows shaped as
  `{image,environment_variable}`; and
- `signature_mode: cosign_keyless_offline_bundle_v1`.

Each receipt-bound machine policy is stored under an immutable,
content-addressed path. A later release installs a new policy without replacing
or conflicting with the prior release policy; replaying the same receipt is
inert. The target's rollback-signing authority remains a separate stable file
and must match exactly across release rotation.

For the current Lenzora overlay the machine-owned activation bindings are
`queue -> LENZORA_PRODUCTION_QUEUE_IMAGE`,
`web -> LENZORA_PRODUCTION_WEB_IMAGE`, and
`worker -> LENZORA_PRODUCTION_WORKER_IMAGE`. These values are deployment
authority and must not be inferred from or added to the signed release receipt.

Success emits schema version 2 with `result_class: verified` and a closed
`plan_set`. The plan set carries three exact image identities, exhaustive
per-service immutable image refs, the activation environment bindings, receipt
and workflow identity, verified-signature claims, and
`plan_set_digest = sha256(domain || NUL || canonical JSON)` under domain
`sandbox.hosting.images.verified-plan-set.v2`.

Write only the nested `plan_set` object to the file passed to the next command;
the outer verification envelope is not stage input.

Batch staging
-------------

The normal protected staging command dispatches by the verified plan's exact
schema version:

```sh
./sb host stage \
  --project-dir /path/to/project \
  --environment production \
  --remote scaleway-sandbox \
  --request-id REQUEST_ID \
  --expected-generation GENERATION \
  --verified-plan /path/to/plan-set.json \
  --confirm \
  --json
```

The machine staging bundle is selected from the registered target. Its closed
outer fields remain `policy`, `binding`, and `secret_sources`; a v2 plan requires
a `StagingPolicySet` with capability
`systemd-cgroup-v2-batch-stage-v2` and helper entry
`sandbox-image-stage-helper-v2`. The installer retains v1 `manifest.json` and
adds `manifest-v2.json` in the same immutable digest-and-runtime-revision helper
directory. Confirmed remote-service migration refreshes this authority before
restarting the user service; it never rewrites an active revision's directory.

Provisioning the same plan again may return `replayed` only while the retained
owner-only bundle is structurally exact, its binding remains ready and unexpired,
the credential-source opaque revision is unchanged, the target and measured helper
still match, and the stage ledger has no active or uncertain owner. The replay
returns the retained policy digest and the current idle stage generation; it does
not mint replacement authority. Expired, malformed, mismatched, active, or uncertain
evidence refuses without overwriting the retained file or opening a staging effect.

The helper runs as an exact transient `systemd --user` unit. Its executable and
manifests are owned by that authenticated Sandbox service user with directory,
helper, and manifest modes `0700`, `0500`, and `0600`. Credential scratch space
is derived internally as `/run/user/<effective-uid>/sandbox-image-stage`, proved
owner-only on tmpfs before READY, and is never caller- or environment-selected.
Before credential delivery, Sandbox proves the exact launch Description,
effective-user cgroup path, `KillMode=control-group`, `Delegate=no`, and enabled
`NoNewPrivileges`, `RestrictSUIDSGID`, and `ProtectControlGroups`. Normal completion
accepts the exact loaded inactive attempt or systemd's exact not-found/inactive
unloaded state, then checks the helper-reported launch cgroup is empty or removed.
Cleanup of a running attempt first re-proves its launch Description. A colliding
deterministic unit name with another launch Description is never killed or stopped.

One credential lease and one measured helper stage all three exact images. The
result proves stable machine and Docker-daemon epochs plus each RepoDigest,
config digest, platform, and local image identity in one retained
`StagedImageProofSet`. Write only the successful envelope's nested `proof`
object to the file passed to activation.

Atomic activation
-----------------

```sh
./sb host image activate \
  --project-dir /path/to/project \
  --environment production \
  --remote scaleway-sandbox \
  --request-id REQUEST_ID \
  --expected-generation GENERATION \
  --verified-plan /path/to/plan-set.json \
  --staged-proof /path/to/proof-set.json \
  --admission-deadline 2030-01-01T00:00:00Z \
  --confirm \
  --json
```

The owner-only activation bundle is selected from the registered target and has
exactly these fields: `schema_version`, `compose_snapshot`, `rollback_grant`,
`rollback_grant_public_key`, and `stage_ledger`. There is no public
`--compose-snapshot` flag. `compose_snapshot` is a closed, secret-free
`PrivateComposeInputSnapshotV2` containing an opaque snapshot identity,
provider revision, target identities, plan-set digest, sorted selected services,
configuration digest, expiry, and snapshot digest. The snapshot digest domain
is `sandbox.hosting.images.private-compose-input-snapshot.v2`. Exact private
renders are represented outside the host by target-scoped HMAC identities under
`sandbox-hosting-private-compose-render.v2`; raw environment values, raw config
hashes, paths, and credentials never cross that boundary or enter durable state.

Activation verifies every local image/config/platform identity, then runs one
Compose replacement for the complete persistent service set with no build, no
pull, and no dependency expansion. A durable replacement intent is written
before the effect. Commit requires exact runtime re-observation and a fresh,
generation-bound edge receipt. Rollback selects the retained prior generation;
the caller cannot nominate an arbitrary target.

Crash recovery
--------------

```sh
./sb host image recover \
  --project-dir /path/to/project \
  --environment production \
  --remote scaleway-sandbox \
  --request-id RECOVERY_REQUEST_ID \
  --expected-generation GENERATION \
  --activation-transaction sha256:TRANSACTION_DIGEST \
  --confirm \
  --json
```

Recovery dispatches from the persisted transaction schema. Version 2
rehydrates only the retained secret-free snapshot selector, rerenders the exact
registered Compose inputs, and classifies exact-new, exact-prior, or ambiguous
runtime state without replaying the Compose effect. Version 1 remains on its
original path with no implicit conversion.
