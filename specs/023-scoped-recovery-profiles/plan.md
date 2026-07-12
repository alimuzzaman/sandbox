# Implementation Plan: Scoped Recovery Profiles

**Branch**: `codex/hermes-public-access` | **Date**: 2026-07-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/023-scoped-recovery-profiles/spec.md`

## Summary

Add a recovery feature module on top of the new Sandbox command/MCP manifests,
Hermes backup contract, and bounded side-effect services. Profiles classify valuable
state; planners resolve them without side effects; capture adapters create consistent
database, filesystem, control-plane, and Git artifacts; a set coordinator hashes,
encrypts, uploads archive-first/manifest-last, and verifies; restore remains plan-first
with explicit checkpoints and confirmation. Legacy broad Drive backup is not reused.

## Technical Context

**Language/Version**: Python 3.11+; POSIX shell only inside bounded generated operations

**Primary Dependencies**: Sandbox command/MCP manifests, bounded process/path services,
GNU tar, GnuPG, Git, database-native dump clients, rclone, systemd user timers

**Storage**: Versioned YAML/JSON profiles and manifests; owner-only local staging;
encrypted Google Drive objects through the configured rclone destination

**Testing**: Python unittest, fixture executables/filesystems, failure injection,
MCP subprocess registration, live Sandbox/Hermes read-only checks, disposable restore drill

**Target Platform**: Existing Linux remote managed by Sandbox; local macOS development

**Project Type**: Modular CLI/MCP control-plane feature

**Performance Goals**: Stream artifacts without loading them wholly in memory; one active
capture; planning under 5 seconds excluding remote inventory; bounded output at every process

**Constraints**: No passphrase in argv/logs/files; no raw operator commands; no container
image/filesystem backup; no production mutation without protected confirmation; no legacy
deletion until a current-passphrase recovery set is verified

**Scale/Scope**: Four initial recovery targets represented by five profiles, tens of GB per set, one remote server and one Drive
destination; schema supports more profiles without central CLI/MCP edits

## Constitution Check

*GATE: Passed before research and re-checked after design.*

- Per-project ownership: production profile identities and roots are explicit; no fallback
  WordPress instance is inferred.
- Registry source of truth: runtime identity remains in the existing registry; recovery
  profile catalog stores backup policy, not a competing instance registry.
- Single entry/modular package: implementation stays under `sandbox/recovery/`; CLI and MCP
  are feature-owned specs/groups loaded through manifests.
- Live proof: fixture tests are followed by read-only remote planning, a verified capture,
  and a disposable restore drill before activation or pruning.
- Idempotency/docs: plans are side-effect-free, set IDs immutable, retries do not overwrite
  verified objects, and code/docs land together.
- Parity before removal: legacy Hermes backup/Drive functions remain behind a facade; the new
  module is proven before old Drive objects become deletion candidates.
- Secrets and authorization: secrets enter through inherited environment/stdin only;
  restore/prune/schedule activation remain individually protected.

Post-design re-check: passed. The service, manifest, profile, and restore contracts preserve
all gates. No constitution exception is required.

## Architecture Decisions

1. **Profile catalog is policy, adapters are mechanisms.** Profiles contain no shell text;
   adapters build validated argument lists through injected services.
2. **One encrypted set archive plus detached manifest.** Individual artifacts are staged and
   hashed; a deterministic set archive is encrypted. The non-secret manifest is published last
   and marks completeness.
3. **Database-native logical backups first.** MariaDB/MySQL profiles use a single-transaction
   logical dump where transactional engines permit it, include routines/events/triggers as
   declared, and validate before archive inclusion.
4. **Filesystem archives are allowlist-based.** Partial profiles enumerate relative paths;
   full profiles still use explicit roots/exclusions and do not cross filesystems by default.
5. **Git is the code backup.** Remote URL and revision are manifest provenance. Critical
   unpublished refs/deltas use a separately verified bundle/patch artifact only when present.
6. **GnuPG symmetric encryption reads passphrase from an inherited descriptor.** Never from
   an argument or persistent passphrase file. Decrypt verification runs before upload success.
7. **Drive publication is two phase.** Immutable ciphertext first, remote size/hash/download
   verification second, complete manifest last. Incomplete artifacts are listable but not restorable.
8. **Restore is a transaction coordinator.** Download/verify and plan are non-mutating;
   apply checkpoints each target, quiesces only dependencies, stages replacements, validates,
   swaps, resumes, and rolls back on failure.
9. **Scheduling wraps the same command.** A systemd user timer calls a Sandbox command with
   non-blocking lock/resource gates; no second implementation exists in cron or Hermes prompts.
10. **Retention is independent and conservative.** It computes candidates but deletion is a
    separate protected action with destination-prefix and verified-set safety floors.

## Project Structure

### Documentation (this feature)

```text
specs/023-scoped-recovery-profiles/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── profile-catalog.md
│   ├── recovery-service.md
│   ├── manifest-v1.md
│   └── cli-mcp.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/recovery/
├── __init__.py
├── models.py
├── catalog.py
├── planner.py
├── capture.py
├── database.py
├── filesystem.py
├── git.py
├── crypto.py
├── drive.py
├── restore.py
├── retention.py
├── scheduler.py
└── service.py
sandbox/commands/recovery.py
mcp/wp-server/tools/recovery.py
config/recovery-profiles.json
tests/fixtures/recovery/
tests/test_recovery_*.py
docs/recovery.md
```

**Structure Decision**: One cohesive recovery package owns policy and orchestration.
Mechanism modules depend only on shared service contracts. The CLI and MCP wrappers are thin
feature modules. The catalog is committed but contains no credentials or host-discovered secrets.

## Delivery Order

1. Models/catalog validation and read-only profile planning.
2. Fixture capture adapters and integrity manifest.
3. Crypto and Drive publication/list/verify.
4. Non-mutating restore planning and disposable apply/rollback.
5. Feature-owned CLI and MCP surfaces.
6. Read-only remote discovery to finalize initial profile paths.
7. Verified new set with current passphrase.
8. Scheduler/retention plan and fresh-server drill.
9. Only then: separately confirmed schedule activation and legacy Drive pruning.

## Rollback

Disable the feature command/tool group in their manifests and retain all remote objects. No
existing Hermes/local backup path is removed. Timer removal is exact and reversible. Restore
apply always has a per-target checkpoint; failed publication never creates a complete manifest.

## Complexity Tracking

No constitution violations require justification.
