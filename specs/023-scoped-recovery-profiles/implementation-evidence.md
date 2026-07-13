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
Ran 44 tests — OK
```

No production archive, Drive object, schedule, deletion, or restore has been created/applied. Remote profile materialization, disposable restore application, scheduler/retention, and fresh-server proof remain gated by their later tasks.
