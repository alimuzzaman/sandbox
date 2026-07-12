# Research: Scoped Recovery Profiles

## Consistent relational database capture

**Decision**: Use a database-native logical dump through the declared application service.
For InnoDB MariaDB/MySQL data, use a single transaction plus streaming/quick behavior and
explicitly include required routines/events. Reject or quiesce profiles with non-transactional
tables unless a profile declares a lock/snapshot method.

**Rationale**: MariaDB documents `--single-transaction` as a consistent snapshot for
transactional tables without prolonged table locking, while warning that schema-changing DDL
and non-transactional engines invalidate that guarantee. Logical dumps are portable and fit
the current expected data size. [MariaDB mariadb-dump](https://mariadb.com/docs/server/clients-and-utilities/backup-restore-and-import-clients/mariadb-dump)

**Alternatives considered**: copying live database volumes (rejected as inconsistent and
container-specific); full physical hot backup (deferred for a profile whose size/RTO proves
logical restore insufficient); shutting down every production app (unnecessarily disruptive).

## Filesystem archive semantics

**Decision**: Archive only validated roots or explicit relative paths, do not cross mount
boundaries by default, preserve ACL/xattr data when supported, record numeric ownership, and
validate member paths before extraction into a staging root.

**Rationale**: GNU tar provides explicit controls for filesystem boundaries, ACLs, extended
attributes, and numeric ownership. These controls match a recovery artifact better than a raw
container filesystem copy. [GNU tar options](https://www.gnu.org/s/tar/manual/html_section/All-Options.html)

**Alternatives considered**: full `/` archives (too broad and secret-prone); Docker export
(loses named-volume/application consistency and captures reproducible layers); following every
symlink (violates allowed-root boundaries).

## Critical unpublished Git state

**Decision**: Record remote URL and exact revision for ordinary repositories. If critical refs
are not reachable from the remote, create a self-contained Git bundle and a separate working-tree
patch/untracked-file artifact after explicit classification, then verify the bundle.

**Rationale**: Git documents self-contained bundles and a `verify` operation that checks format
and prerequisite connectivity. [Git bundle documentation](https://git-scm.com/docs/git-bundle)

**Alternatives considered**: archive every checkout (duplicates remotely recoverable code and
may capture credentials/build output); assume every dirty file is valuable (unsafe and noisy);
automatically push changes (changes external Git state and remains a human-owned action).

## Passphrase encryption

**Decision**: Use supported GnuPG symmetric encryption in batch/loopback mode with the passphrase
read from a dedicated inherited file descriptor. Run a decrypt-and-hash verification before
upload, use an isolated GnuPG home where practical, and never use passphrase arguments.

**Rationale**: GnuPG explicitly warns against command-line passphrases and documents loopback
pinentry requirements for unattended descriptor/file input. [GnuPG manual](https://www.gnupg.org/documentation/manuals/gnupg26/gpg.1.html)

**Alternatives considered**: plaintext Drive upload (rejected); committed key/file (rejected);
rclone crypt as the only layer (would require managing another persistent credential and makes
the existing recovery-passphrase contract less direct); custom cryptography (rejected).

## Drive publication and verification

**Decision**: Upload immutable ciphertext, verify it using remote-supported hash/size or a
downloaded check, then publish the manifest. Use copy semantics, not sync, so unrelated or older
sets are never deleted during capture.

**Rationale**: rclone documents that `check` is non-mutating and compares size/hashes, with
download verification available when a backend lacks hashes; `copy` does not delete destination
files. [rclone check](https://rclone.org/commands/rclone_check/), [rclone copy](https://rclone.org/commands/rclone_copy/)

**Alternatives considered**: manifest-first publication (advertises incomplete sets); sync
(could delete unrelated recovery objects); trust process exit alone (insufficient end-to-end proof).

## Scheduling and retention

**Decision**: Generate a systemd user service/timer that invokes the same Sandbox recovery
command. Use an advisory non-blocking lock, randomized scheduling window, resource preflight,
and a separate retention plan/apply flow. Failed or skipped runs do not trigger pruning.

**Rationale**: This integrates with the existing host-native Sandbox/Hermes model and avoids a
second cron implementation. Retention safety is easier to test when candidate calculation and
deletion are separate.

**Alternatives considered**: Hermes prompt scheduling (model/tool approval and concurrency are
not a reliable backup scheduler); application cron (couples disaster recovery to the app being
recovered); automatic pruning during upload (couples success and destruction).

## Initial profile policy

**Decision**:

- `control-plane`: Sandbox machine state, Hermes safe state/routing/policies/unit declarations,
  Cloudflare declarative identifiers/policies, rclone configuration required for recovery, and
  approved credentials inside the encrypted artifact; exclude sessions, caches, logs, jobs,
  downloaded source/runtime environments, and disposable WordPress snapshots.
- `lenzora-prod`: PostgreSQL-consistent production database plus the discovered persistent
  `/app/storage` volume; exclude development volumes and caches; code from Git.
- `alimuzzaman-site`: Git provenance only at present because discovery found a clean checkout and
  no persistent mount; add partial filesystem paths if future discovery identifies non-Git state.
- `amarsonar-bangla-prod`: consistent WordPress database and the full WordPress directory,
  because uploads and local files are valuable; exclude only reviewed transient cache paths.

**Rationale**: This directly follows the owner's value classification and avoids the rejected
behavior of snapshotting every registered WordPress development instance.

**Alternatives considered**: one full-server archive (too broad and hard to restore selectively);
one profile per container (containers are runtime mechanisms, not business-value boundaries).
