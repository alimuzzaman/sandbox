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
./sb recovery data --postgres-operation status --remote REMOTE --profile PROFILE --request-id CAPTURE_ID --json
./sb recovery data --postgres-operation capture --remote REMOTE --profile PROFILE --request-id CAPTURE_ID --backup-id BACKUP_ID --confirm --json
./sb recovery data --postgres-operation restore-plan --remote REMOTE --profile PROFILE --request-id RESTORE_ID --backup-id BACKUP_ID --json
./sb recovery data --postgres-operation restore --remote REMOTE --profile PROFILE --restore-plan PLAN.json --confirm --json
./sb recovery data --postgres-operation inspect-restore --remote REMOTE --profile lenzora-dev --restore-plan PLAN.json --json
./sb recovery data --postgres-operation reopen-restore --remote REMOTE --profile lenzora-dev --restore-plan PLAN.json --reopen-plan REOPEN.json --confirm --json
./sb recovery data --postgres-operation verify-restore --remote REMOTE --profile lenzora-dev --restore-plan PLAN.json --confirm --json
./sb recovery data --postgres-operation readiness --remote REMOTE --profile PROFILE --target-volume VOLUME --json
```

`status` inspects the original retained request without consuming database credentials
or returning archive bytes. An incomplete local `lenzora-dev` capture can be resumed
with its same request and backup IDs plus `--resume-capture --confirm`, after status
reports `retained_without_result`. The helper locks the request directory, checks
the exact original request and source, and returns an existing archive unchanged.
It never replaces a completed archive. This exception applies only to read-only
development capture; restore, production and storage uncertainty cannot resume.
Snapshot capture creates its session-local temporary accumulator before importing
the read-only transaction, so it performs no forbidden DDL inside that transaction.

`inspect-restore` compares a retained isolated development restore with its exact
encrypted backup. It requires the same plan, archive, owner-labelled container
and volume, no network or ports, no other volume consumer, and no active importer.
It reports bounded comparison results without reimporting, restarting, stopping,
or marking the restore verified. An incomplete restore remains retained.
An expired isolated drill returns `restore_target_stopped`, `all_match=false`,
and a `reopen_plan`. Save that exact plan as an owner-only JSON file. The separate
confirmed `reopen-restore` operation checks the original restore plan/archive,
exact daemon and container IDs, pinned image, stopped state, initialized data
markers, isolated configuration and sole owned data volume before startup.
It starts only that existing container and PostgreSQL data directory. It never
imports, initializes a database, recreates a target, removes a PID file, or
recreates the transient initialization password.

Reopening is a write operation: PostgreSQL startup may perform crash recovery
on the retained files. A natural exit (0) or an explicitly stopped, non-OOM
container exit (137) can be planned; the full stopped state is digest-bound.
An existing verified restore receipt refuses reopening. Startup is permitted
on the isolated restored cluster. It is limited to local `lenzora-dev` drills;
production, legacy transfers and storage cannot use it. A reopen intent is
retained under the original request before effects. Repeating the exact plan
only recovers positive running/database evidence; it never repeats an uncertain
start. An unresolved intent blocks another generation. After a completed reopen,
a later expired drill needs a fresh inspected plan and explicit confirmation.
The per-restore history is limited to 16 generations. Reopen records are separate
from verified restore receipts, and `restore_reopened` does not satisfy readiness.
Run `inspect-restore` immediately after reopening, then `verify-restore` only
when every comparison succeeds. No new restore request or reimport is needed.

For a schema mismatch, inspection also compares private schema records from the
registered source and retained target. The source's original digest must still
equal the captured digest; otherwise it reports `source_schema_changed`. A target
that changes between reads reports `target_schema_changed`. Successful diagnosis
returns component digests, counts, ordering flags and at most 32 changed object
identifiers/field names, with a total and truncation flag. Definitions, SQL
literals and rows never leave the helper. Private metadata is bounded to 8 MiB
and 10,000 records per component. Column order within each table remains part of
the comparison. Diagnostics do not change the failed schema acceptance gate.
If every comparison matches, `verify-restore --confirm` repeats those checks under
the original request lock, stops only that isolated target, and retains the verified
receipt. It never imports again, repairs a mismatch, or replaces the target.
Inspection and reopen refusals expose only closed reason codes; raw startup
errors, configuration values and database rows remain private.

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
Successful capture retains its validated non-secret destination against the exact
source. Later readiness checks reuse that channel to verify ciphertext and the
restore receipt, without loading the encryption passphrase or decrypting data.
Legacy credentials are consumed only by a destination-specific broker callback,
passed privately to an owned client tmpfs, and never returned or written into a
source descriptor. Captured database bytes stay within private recovery staging;
only encrypted publication and bounded evidence leave that workflow. Capture
uses one exported PostgreSQL snapshot for the native dump and comparison data.
Storage captures twice and compares file manifests before publication.

A restore plan binds the ciphertext, source and exact new target. A drill creates
a new owned volume and networkless container with no ports. Existing target
volumes are refused. The database restore compares major, schema, migration
checksums, table counts and exact constraint validation states (including intentional
`NOT VALID` source constraints); storage compares file
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
