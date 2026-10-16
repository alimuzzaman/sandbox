# Receipt Contracts

Both schemas are closed canonical JSON, at most 256 KiB, self-identified by `sha256`. Unknown fields, duplicates, invalid identities, unsupported versions, unsafe paths, or nested digest inconsistency refuse.

## Local Receipt v1

Namespace: `sandbox.host.local-images.v1`. Archive names are basename-only beneath the validated receipt directory. Archive size/hash and image metadata are revalidated before transfer.
`project` and `environment` use the hosted target slug contract
(`[a-z0-9][a-z0-9-]*`); `compose_project` is the matching
`sandbox-host-<project>-<environment>` namespace and a valid Compose name.
Storage paths are constructed only from validated components and must remain
beneath the registered Sandbox home.

## Remote Receipt v1

Namespace: `sandbox.host.local-images.remote.v1`. The receipt embeds the local receipt, target binding, and completed observations. It is stored owner-only under a digest-derived filename; symlinks, extra links, non-owner files, and oversized data refuse.

## Exact comparator

A reference matches only if present, Docker `.Id` equals the exact local engine
portable image ID recorded in the receipt, platform equals `linux/amd64`, and
the canonical ordered `RootFS.Layers` digest matches. The portable image ID is
the saved archive configuration digest. The builder engine's observed `.Id` is
retained separately as `builder_image_id` because Docker 29's containerd store
can expose a distinct store-local identity. That builder-only value, manifest,
tag, or another digest alias is never accepted as the destination `.Id`.

Apply additionally renders Compose on the remote with the actual environment,
file set, and override. Every declared service must resolve to the receipt's
reference, and those references must still satisfy the exact comparator before
Compose mutation. Local rendering refuses image references that depend on
Docker client selection variables.

## Interruption

Stream completion is not proof. Every load attempt is followed by fresh observation. A remote receipt publishes only while the aggregate deadline remains valid and all requirements verify.
All observations, storage admission, lease acquisition, daemon probes, uploads,
and publication consume one remaining budget. A deadline exceeded before any
remote write refuses as `local_images_transfer_timeout`; after a possible write
it is `local_images_transfer_unknown`. The remote mutation worker and Docker
load child hold a load fence; new engine owners refuse as busy until it clears,
even after the original controller lease disconnects.
