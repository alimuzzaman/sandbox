# Hosted data recovery

`recovery data` provides bounded database and storage checkpoints for image
conversion. `recovery postgres` is an alias. It uses registered remotes, the
existing encrypted staging/publication coordinator and native capture validation.
These are component restore drills, not full control-plane disaster restores.

Recovery evidence accepts up to 1 MiB of JSON, 10,000 entries per collection,
100,000 values, and 32 nesting levels. Duplicate keys and non-integer numbers
are rejected. Database table inventories do not use the image-plan service cap.

The source descriptor is owner-only JSON with a closed schema. PostgreSQL binds
`schema_version=1`, `profile` (`lenzora-dev`, `lenzora-prod`, or
`lenzora-prod-legacy`), registered `remote`, `compose_project`, full observed
`container_id`, exact `volume`, `database`, `role`, observed source `image_id`,
and `client_image_id`. Both images are local content IDs (`sha256:...`), never
moving tags; the local profile requires the same image. A legacy external source
also names a registered `credential_reference` and, for final transfer, a
`target_password_reference` matching the destination hosting password; other database sources set both
to null. Select a client major compatible with the observed server; a newer
server must never be downgraded. No image is pulled by recovery.

`recovery plan --remote REMOTE` includes `remote_inventory.container_bindings`
alongside its mounts. These bounded records expose the full container and image
IDs, Compose project, running state, and only the non-secret `POSTGRES_DB` and
`POSTGRES_USER` fields when present. They never expose other environment values.
Use the same container record and observed mount when preparing a source.
For the legacy external database, bind the production application container and
its `/app/storage` volume; it can be stopped for the final quiescent capture.
The brokered database client is separate from that application container.

Storage uses `profile=lenzora-prod-storage`, the same remote/project/container,
volume and image bindings, `mount_path=/app/storage`, and a null credential
reference. It omits the database, role and client image fields. Capture mounts
only that observed volume read-only in an owned, networkless reader.

```sh
./sb recovery data --postgres-operation register --remote REMOTE --profile PROFILE --source-binding SOURCE.json --json
./sb recovery data --postgres-operation register --remote REMOTE --profile PROFILE --source-binding SOURCE.json --confirm --json
./sb recovery data --postgres-operation observe --remote REMOTE --profile PROFILE --request-id OBSERVATION_ID --json
./sb recovery data --postgres-operation capture --remote REMOTE --profile PROFILE --request-id CAPTURE_ID --backup-id BACKUP_ID --confirm --json
./sb recovery data --postgres-operation restore-plan --remote REMOTE --profile PROFILE --request-id RESTORE_ID --backup-id BACKUP_ID --json
./sb recovery data --postgres-operation restore --remote REMOTE --profile PROFILE --restore-plan PLAN.json --confirm --json
./sb recovery data --postgres-operation readiness --remote REMOTE --profile PROFILE --target-volume VOLUME --json
```

Observe an uninstalled descriptor by adding `--source-binding SOURCE.json` to
`observe`. This lets the operator establish the legacy server major before
registering its matching client image. The observation still uses the registered
remote and broker, and does not install or replace a source.

Registration does not replace an existing different binding. Requests are
immutable. A lost response reuses the same request; a partial retained operation
without a terminal record reports uncertainty rather than overwriting data or
choosing another identity. Run long capture/restore operations through durable
jobs with finite timeouts and retained job IDs.

Capture requires the normal configured recovery encryption and destination.
Legacy credentials are consumed only by a destination-specific broker callback,
passed privately to an owned client tmpfs, and never returned or written into a
source descriptor. Captured database bytes stay within private recovery staging;
only encrypted publication and bounded evidence leave that workflow. Capture
uses one exported PostgreSQL snapshot for the native dump and comparison data.
Storage captures twice and compares file manifests before publication.

A restore plan binds the ciphertext, source and exact new target. A drill creates
a new owned volume and networkless container with no ports. Existing target
volumes are refused. The database restore compares major, schema, migration
checksums, table counts and validated constraints; storage compares file
manifests. Verified target containers are stopped and retained with their
volumes. Cleanup requires a separate explicit decision.

A final production database transfer is separate from a drill. Add
`--target-volume sandbox-host-lenzora-production_lenzora-postgres-data` to a
`lenzora-prod-legacy` restore plan, review it, then confirm that exact plan.
The plan explicitly selects destination database and role `lenzora`; legacy
source names may differ and remain bound in the backup evidence. The volume must not exist; the backup must have been captured with production
containers stopped, and the target project must still have no running containers.
This does not assert that unrelated external database clients cannot write:
operator review must account for them before final capture. The command does not
activate the application or overwrite an existing production database.

`readiness` returns a source-bound verified backup/restore receipt for the volume
selected by deployment. For the legacy production profile it requires the
confirmed production transfer receipt. For development and retained storage it
requires a verified isolated drill for the same observed source volume. These
receipts prove the recorded checkpoint, not arbitrary subsequent writes.

## Source verification

On 2026-09-08, the full local suite passed 5,625 tests with 13 skips in
655.034 seconds (durable job `5534497d0c99e2d6d37cacc53d18de87`). Retained
output is complete, with integrity digest
`63d82d670da96b5c8d007fc2f75b1f8fcd87d0d43f21ebedc6dbb61fbf39f04d`.
All 2,290 observed source files were unchanged during that run. The earlier
full run found one optional-authority compatibility error in stage provisioning;
the correction passed 28 focused tests before this full rerun.

This verifies source behavior, not a live restore, installed controller or
production transfer. The new authority, containment and recovery paths require
human review before release. Use the supported remote service migration and
verify its installed revision before relying on these interfaces.
