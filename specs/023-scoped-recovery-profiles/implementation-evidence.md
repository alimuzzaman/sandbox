# Implementation Evidence: Scoped Recovery Profiles

## Trace

- Task class: security/data-loss-sensitive recovery architecture and implementation
- Planning: Hermes Sol/high recommendation incorporated; Spec Kit specify/clarify/plan/tasks/analyze completed
- Writer: one local owner; remote Hermes used for bounded read-only state and planning support
- Branch: `codex/hermes-public-access`; baseline commit `e52eb8d`
- Protected actions: production restore, schedule activation, Drive deletion, commit, and push remain gated

## Legacy baseline

- `./sb hermes status --remote scaleway-sandbox --json`: configured, Hermes Agent v0.18.2, zero running sessions.
- `./sb hermes health --remote scaleway-sandbox --json`: healthy; V2 gate passed; gateway intentionally inactive; one stale session reported.
- `./sb hermes drive list --remote scaleway-sandbox --json`: two legacy full-scope manifests exist. No legacy object was deleted.
- Existing local Hermes backup/restore and broad Drive methods remain behind compatibility code. The new recovery catalog has no import or selection path to the broad Drive command builder.

## Planning checkpoint

Commands:

```text
./sb recovery profiles --json
./sb recovery plan --remote scaleway-sandbox --json
```

Results:

- five profiles represent four recovery targets;
- containers/images, disposable development WordPress state, caches/logs/sockets are globally excluded;
- full `amarsonar-bangla` WordPress directory plus database is planned;
- `lenzora` production PostgreSQL and `/app/storage` are planned while development/cache volumes are excluded;
- `alimuzzaman-me` is a clean Git checkout with no persistent mount, so code recovers from Git rather than a duplicate archive;
- remote discovery is read-only and reports no environment values or file contents.

Tests:

```text
.cli-venv/bin/python -m unittest tests.test_recovery_catalog \
  tests.test_recovery_planner tests.test_recovery_inventory -q
Ran 4 tests — OK
```

Interface inventory: CLI 68 commands (one new feature-owned command); MCP 53 tools (two new read-only tools). Existing MCP registration suite passes.

## Residual gates

## Fixture capture checkpoint

The new recovery module now has fixture-only proof for the capture pipeline:

- native PostgreSQL/MariaDB command construction rejects non-transactional logical dumps and never accepts credentials as arguments;
- archive inputs and archive members are root-bounded and traversal/link-escape checked;
- Git records remote/revision and classifies sensitive dirty state separately from eligible unpublished state;
- GnuPG receives a fixture passphrase through an inherited descriptor (not argv), encrypts/decrypts a fixture payload, and verifies its SHA-256;
- ciphertext is published first, downloaded and hash-checked, and only then receives the complete manifest; injected encryption/verification failures leave no complete manifest and remove owner-only staging.

Evidence command (local, fixture-only; no remote rclone or production profile invoked):

```text
python3 -m unittest discover -s tests -p 'test_recovery*.py'
Ran 55 tests — OK
```

No production archive, Drive object, schedule, deletion, or restore has been created/applied. Remote profile materialization, disposable restore application, scheduler/retention, and fresh-server proof remain gated by their later tasks.

## Disposable restore checkpoint

Fixture restore plans verify manifest integrity/compatibility, selected-profile dependencies, and target free-space before any adapter is called. A disposable two-profile file-swap drill injects a verification failure in the second target and proves the first target is restored from its checkpoint. CLI and MCP restore surfaces are plan-default; apply is confirmation-gated and has no live target adapter.

```text
python3 -m unittest tests.test_recovery_restore tests.test_recovery_restore_apply \
  tests.test_recovery_interfaces -q
Ran 8 tests — OK
```

## Schedule and retention read-only checkpoint

Remote planning was verified against `scaleway-sandbox` without activation, deletion, or
capture:

```text
./sb recovery profiles --remote scaleway-sandbox --json
./sb recovery plan --remote scaleway-sandbox --json
./sb recovery schedule --remote scaleway-sandbox --json
./sb recovery retention --remote scaleway-sandbox --json
```

Results:

- five catalog profiles were returned and the remote plan contained no secret values or file
  contents;
- the schedule rendered disabled systemd service/timer units with a non-blocking lock and a
  15-minute randomized delay;
- retention returned zero candidates, required confirmation, and protected no incomplete or
  unverified objects;
- no scheduler, Drive, filesystem, database, or production state was changed.

## Validation and review checkpoint

The canonical project environment passed the full local suite:

```text
.cli-venv/bin/python -m unittest discover -s tests -p 'test_*.py' -q
Ran 676 tests in 37.071s — OK (skipped=1)
./sb selftest
Ran 676 tests in 37.120s — OK (skipped=1); selftest: passed
python3 -m unittest discover -s tests -p 'test_recovery_*.py' -v
Ran 55 tests — OK
python3 -m unittest tests.test_mcp.TestMcpServerSplit -v
Ran 2 tests — OK
git diff --check
```

The MCP schema snapshot was refreshed to include the five committed Hermes authorization
tools; the focused MCP registration tests and the full suite then passed. Correctness review
covered catalog fail-closed validation, deterministic planning, archive/path confinement,
credential-channel handling, manifest-last publication, restore checkpoints and rollback,
schedule non-overlap, retention safety floors, CLI/MCP confirmation gates, and compatibility
boundaries. Security/data-loss review found no unresolved issue in the local implementation.

## Protected-operation boundary and prepared schedule plan

No real recovery set has been created, no production restore or fresh-server drill has been
applied, no legacy Drive object has been deleted, and no schedule or public-access mutation has
been activated. The prepared schedule activation remains:

1. create and verify one current-passphrase scoped set;
2. complete the disposable fresh-server drill and acceptance checks;
3. review the disabled units and exact profile selection;
4. activate only after separate explicit scheduling approval;
5. monitor the first run and record the result.

The CLI continues to fail closed for protected actions until those prerequisites and
confirmations exist. No commit or push was performed by this implementation pass.

## Standards/convergence review

The local implementation was reviewed against the current primary documentation for
PostgreSQL logical dumps, MariaDB `--single-transaction` dumps, GnuPG loopback/passphrase-fd
handling, GNU tar path/metadata controls, rclone immutable copy semantics, and Git bundle
verification. The review confirmed the existing database, crypto, Drive, Git, and path
confinement choices. It also found and fixed one convergence gap: scheduler policy inputs for
randomized delay and timeout were accepted but discarded. `SchedulePolicy` now retains both
values; the generated systemd timer uses the requested `RandomizedDelaySec`, and the service
uses the requested `TimeoutStartSec`, with regression coverage in
`tests/test_recovery_scheduler.py`.

The same review also added fail-closed validation for systemd unit-field newlines/NULs,
lowercase policy slugs, and systemd time-span values; the schedule renderer now accepts only
the fixed recovery command and cannot interpolate arbitrary unit text.

The final adapter review also rejects control characters in inherited GnuPG passphrases and
path traversal in configured rclone destinations. Focused crypto/Drive/scheduler tests pass,
and the full suite was rerun afterward.

No new convergence tasks were required after that fix. The review did not activate any
protected operation or change external state.

## Retention and manifest-binding review

Competitor research covered restic/resticprofile, borgmatic, Borg, Kopia, and Duplicati.
The highest-value in-scope gap was retention policy semantics, not a new product boundary.
Retention planning now accepts keep-count and minimum-age floors with a deterministic
timezone-aware clock, protects malformed timestamps, and remains confirmation-gated.
Restore planning also rejects ciphertext objects that are not canonically bound to their set ID.
The focused recovery suite passes 61 tests; no capture, restore, schedule activation, or
remote deletion was performed.

The staging coordinator now optionally preserves a verified encrypted artifact in an owner-only
pending directory when remote publication fails; incomplete runs still never publish a complete
manifest, and staging cleanup remains guaranteed.

Restore apply now rolls back the active profile as well as previously completed profiles when
verification or a later operation fails; the disposable file-swap drill confirms both targets
return to their pre-restore contents.

Recovery listing now classifies complete, incomplete, legacy, locally pending, and
unverifiable objects without decrypting archives; the existing pending response key remains
available as an incomplete-object compatibility alias.

Listing now inventories the configured remote destination root rather than only the new sets
prefix, allowing legacy objects outside that prefix to be reported without mutation.

Database capture now validates PostgreSQL custom-dump signatures and MariaDB/MySQL SQL-shaped
output after the native command succeeds, rejecting non-empty but invalid dump artifacts.

GnuPG descriptor writing now loops until the inherited passphrase buffer is fully transferred,
with regression coverage for partial writes and no secret exposure in command arguments.

Filesystem capture now archives an in-root symlink as a symlink rather than following it into
target contents, while still resolving the target for allowed-root confinement.

Archive validation now rejects duplicate member names and device/FIFO nodes in addition to
traversal and escaping-link checks.

Schedule rendering now carries selected profile IDs and the reviewed remote into the fixed
recovery command, with explicit confirmation in the disabled unit; activation remains a
separate protected operation.

An injectable filesystem restore adapter now provides checkpoint, decrypt, safe archive
extraction, atomic swap, member verification, and rollback behavior. Its target must be
explicitly supplied by the caller; no production restore target was discovered or mutated.

Recovery context now composes the GnuPG/rclone capture coordinator only when both the approved
destination and inherited passphrase channel are present. It passes configured staging/pending
roots and leaves capture unavailable when the secret channel is absent.

Planner output now warns when host-manifest roots remain symbolic or a profile combines multiple
source kinds, making clear that the result is review-only until explicit adapter materialization.

RecoveryService.create now revalidates the selected catalog profiles and planner warnings before
invoking capture, rejecting unknown/missing profiles, unresolved materialization, and empty
artifact sets without invoking the adapter.

The CLI now routes explicit repeated NAME=PATH artifact inputs, set ID, and profile selection
through RecoveryService.create; malformed declarations fail before adapter invocation.

The MCP recovery_create tool now routes explicit backup_id, profiles, and artifact-path inputs
through the same service boundary; confirmation, materialization warnings, and configured-secret
checks remain service-owned.

Archive member validation now rejects dot segments and normalized path aliases before restore,
closing an ambiguity in traversal and duplicate-member checks; filesystem regression tests cover
both cases.

Filesystem restore extraction now opts into Python's `tarfile` data filter on Python 3.12+ while
retaining the validated Python 3.11 path for the supported baseline.

Recovery listing now reuses manifest verification, including ciphertext SHA-256 and canonical
set-object binding, instead of treating matching object size as sufficient evidence of completeness.
The verifier also rejects non-object JSON manifests with the stable invalid-manifest error path.

Retention planning is now wired through the service and CLI/MCP surfaces: fixture coverage proves
that complete sets are manifest-verified, decrypted with the current crypto adapter, and reduced
to deterministic keep-count candidates without deleting remote objects. Verified sets with stale
or unavailable passphrase material, or invalid timestamps, remain explicitly unclassified rather
than being silently omitted.

## Subsequent local hardening audit

The follow-up review applied the same research-derived fail-closed and durability rules across
the remaining boundaries. Artifact capture now rejects symlink/non-regular destinations and
sources, detects source mutation, bounds inherited GnuPG passphrases, streams database-format
validation, and rejects unsafe archive/control text. Git and rclone outputs reject symlink
destinations, option-like tokens, NUL text, and malformed object keys. Recovery manifests,
catalogs, profile selections, schedules, source paths, and remote inventory uncertainty are
validated or surfaced explicitly rather than coerced.

Hermes and its dashboard companion now use typed authorization-state validation, crash-durable
local/remote writes, compare-and-swap guards, transactional prompt/state ordering, rollback-safe
expiry, and guarded template requests. Dashboard approval bodies and cron inputs fail closed.

The final recovery-adapter pass now rejects malformed rclone listing payloads, control characters
in destinations and object keys, non-regular or empty downloaded objects, and malformed fixture
artifact maps/manifests. These checks preserve stable recovery errors and prevent untrusted remote
or fixture data from being silently treated as an empty or valid recovery set.

The retention CLI now prints protected, candidate, and unclassified sets in human-readable mode;
JSON output and the confirmation-gated deletion boundary are unchanged, making reviewable prune
plans visible without requiring machine parsing.

The retention apply boundary also rejects malformed freshness snapshots and non-callable deletion
adapters before invoking any delete operation, preserving stable errors for protected workflows.

The remote inventory adapter now validates its complete JSON shape—including typed mount and Git
repository records, safe names, counters, and warning strings—before exposing it to planning callers.
The catalog loader now rejects control characters, traversal-shaped list entries, invalid
dependency IDs, and unsafe metadata keys before profiles reach the planner.
Control-plane declaration capture now rejects invalid roots, non-tuple or duplicate declarations,
control/traversal paths, symlinks, and non-regular files before returning artifact metadata.
RecoveryService callers now preserve the stable result envelope when an adapter raises a raw
OS, type, or value error, using operation-specific failure codes rather than exposing exceptions.
The human-readable recovery list surface now prints categorized counts and paths, while retaining
the existing JSON envelope for automation and review tooling.
Human-readable verification now prints the non-secret ciphertext identity, digest, and size while
omitting manifest provenance; JSON output remains available for structured consumers.
Human-readable restore planning now prints the non-mutating action, checkpoint, and rollback
summary so an operator can review the plan before any separately protected apply operation.
Human-readable schedule planning now prints the disabled state and generated units without
installing or enabling them; activation remains separately protected.
Scheduler execution now preserves failed/skipped action results instead of converting every
non-exceptional action return into `complete`, keeping pruning eligibility fail closed.

CLI integration spot check (2026-07-16, local and read-only):

```text
./sb recovery schedule
recovery schedule: planned
  enabled: false
  ... ExecStart=/usr/bin/flock -n %t/sandbox-recovery-recovery-daily.lock ...
  ... RandomizedDelaySec=15m ...
./sb recovery retention --json -> ok=false, code=recovery_not_configured
./sb recovery list --json -> ok=false, code=recovery_not_configured
```

No Drive object, scheduler unit, filesystem, database, or production state was changed by this
spot check.

Current verification:

```text
./.cli-venv/bin/python -m unittest discover -s tests -q
Ran 795 tests in 41.409s — OK (skipped=1)
./sb selftest
✓ selftest: passed

The filesystem restore adapter now verifies regular-file content digests, symlink targets,
member types, and the complete extracted path set after the atomic swap. A disposable regression
test tampers with a restored file before verification and proves rollback returns the prior target.
It also rejects a symlink or non-directory restore target before creating a checkpoint.
The restore coordinator preflights checkpoint/quiesce/stage/swap/import/verify/resume/rollback
operations and rejects incomplete adapters before invoking any operation.
Database, control-plane, and Git restore adapters now share an injectable callback lifecycle;
database dumps receive format validation, all sources are copied into owner-only staging, and
failure-injection tests prove callback checkpoint rollback without invoking production services.
Their source snapshots also reject inode/size/mtime/digest changes during staging.

Filesystem archive capture now checks every selected tree entry and resolved link target against
the declared root device, rejecting cross-filesystem sources before tar creation.

MariaDB/MySQL validation now requires an executable SQL dump statement or recognized dump
directive, so comment-only non-empty output cannot pass capture validation.

Staged manifest artifact records now reuse the digest from the verified pre/post source snapshot
instead of re-reading the source after the mutation check.

GnuPG decrypt-and-hash verification now compares stable plaintext digests before and after the
decrypt check, rejecting source mutation rather than returning a second unbound digest.

GnuPG encryption/decryption now uses exclusive `0600` pending outputs, rejects stale pending
paths without deleting them, and enforces owner-only mode on committed outputs.

Git bundle and patch artifacts now use owner-only temporary files and atomic replacement; failed
bundle generation leaves neither a partial destination nor a pending temporary artifact.

Drive adapters now expose file downloads for large objects. Manifest ciphertext verification and
GnuPG retention checks use those temporary files and streaming hashes instead of loading a full
recovery archive into process memory; the fixture suite proves the ciphertext path uses `get_file`.

The filesystem module now includes an injected GNU-tar adapter that requests ACL/xattr and
numeric-owner preservation plus `--one-file-system`, validates the resulting archive, and
atomically publishes it with owner-only permissions. The portable Python adapter remains explicit
about its ACL/xattr limitation.

Git provenance redaction now covers scp-style userinfo in addition to URL credentials, query
strings, and fragments; the resulting metadata retains only the host/repository identity.

RecoveryService now exposes an explicit in-process `restore_apply` boundary: it accepts only a
typed plan and caller-owned adapters, re-verifies the remote manifest before confirmation-gated
execution, and returns a stable envelope. CLI/MCP apply remains intentionally unconfigured for
production targets.

RecoveryService also exposes a typed `retention_apply` boundary requiring a fresh candidate tuple,
explicit confirmation, and a caller-owned delete adapter; fixture coverage proves stale and
unconfirmed requests perform no deletion.
```

The self-test and unit suite remain fixture/local checks. The latest full verification ran 805 tests
in 42.111s with 1 skipped. T060/T061/T069/T071/T072 and the live
T021 catalog-companion acceptance check remain protected operations requiring their documented
operator authorization; no production capture, restore, deletion, schedule activation, or live
deployment was performed by this hardening audit.
