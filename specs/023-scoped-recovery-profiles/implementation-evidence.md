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

Database capture now validates PostgreSQL custom-dump signatures and MariaDB/MySQL SQL-shaped
output after the native command succeeds, rejecting non-empty but invalid dump artifacts.

Filesystem capture now archives an in-root symlink as a symlink rather than following it into
target contents, while still resolving the target for allowed-root confinement.

Schedule rendering now carries selected profile IDs and the reviewed remote into the fixed
recovery command, with explicit confirmation in the disabled unit; activation remains a
separate protected operation.
