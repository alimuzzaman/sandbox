# Feature Specification: Single Swappable Per-User Base for All Sandbox Machine-State

**Feature Branch**: `feat/agent-tooling-specs`

**Created**: 2026-06-23

**Status**: Draft

**Input**: User description: "Relocate ALL Sandbox machine-state out of the repo checkout into a single, swappable per-user base directory (`SANDBOX_HOME`, default `~/sandbox`). Move runtime/ plus all instance-related generated config and secrets under it; one base every path derives from; idempotent migration; existing instances keep working; nothing machine-specific left in the repo."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Existing setup keeps working after upgrade (Priority: P1)

A developer who already has instances, snapshots, a download cache, and per-machine
config inside their repo checkout pulls this change and runs any normal Sandbox command.
The Sandbox detects the old in-repo state, relocates it once under the per-user base,
and the developer's existing instances boot and serve exactly as before — no manual
steps, no data loss, no rebuild.

**Why this priority**: This is shared, critical tooling used daily. If an upgrade
strands existing instances or loses snapshots/secrets, every developer who pulls is
broken. Backward compatibility is the highest-value outcome.

**Independent Test**: With a populated in-repo `runtime/` and existing config/secret
files, run a single ordinary command (e.g. status/ensure) and confirm the state is
migrated under the base and a previously-registered instance still boots and serves its
site, with snapshots and cache intact.

**Acceptance Scenarios**:

1. **Given** a checkout with existing in-repo machine-state and registered instances,
   **When** the developer runs a Sandbox command for the first time after upgrading,
   **Then** the state is moved under the per-user base, the repo working tree no longer
   shows machine-state, and every previously-registered instance still resolves and boots.
2. **Given** the migration already ran once, **When** the developer runs commands again,
   **Then** no second migration occurs and the relocated state is used in place (the
   migration is safe to encounter repeatedly).
3. **Given** a partially-migrated state (interrupted mid-move), **When** the developer
   re-runs, **Then** the migration completes without corrupting or duplicating state.

---

### User Story 2 - Fresh clone uses the per-user base with no repo pollution (Priority: P1)

A developer clones the repo fresh (or onto a new machine) with no prior Sandbox state.
The first time they create or boot an instance, all generated state and per-machine
config land under the per-user base — never inside the checkout.

**Why this priority**: The core purpose is decoupling machine-state from the code
checkout so re-clones, worktrees, and `git clean` are safe. A fresh clone is the
canonical proof the coupling is gone.

**Independent Test**: From a clean clone with an empty base, create/boot one instance and
confirm: the checkout's working tree stays clean (no generated runtime, config, or secret
files), and all such artifacts appear under the per-user base.

**Acceptance Scenarios**:

1. **Given** a fresh clone and an empty per-user base, **When** the developer creates an
   instance, **Then** its WordPress install, generated compose/orchestration files,
   registry entry, and per-instance config all reside under the per-user base.
2. **Given** a fresh clone, **When** the developer inspects the repo working tree after
   creating an instance, **Then** no machine-state (runtime, per-machine config, secrets)
   is present or staged in the repo.
3. **Given** two worktrees of the same repo, **When** each is used, **Then** they share
   the same per-user base and the same registry (state is keyed by project root, not by
   checkout location).

---

### User Story 3 - Relocate the whole base by pointing at a new location (Priority: P2)

A developer wants their Sandbox state on a different disk or path. They set the base
location override to a new directory; the Sandbox relocates everything there and keeps
all instances working — no path remains pinned to the old base.

**Why this priority**: Proves the "single swappable base" invariant end-to-end: if any
path is baked rather than derived, swapping the base exposes it. Important but secondary
to upgrade and fresh-clone correctness.

**Independent Test**: With a working base, set the override to a new empty directory,
run the relocation, and confirm instances boot from the new location and nothing
references the old base.

**Acceptance Scenarios**:

1. **Given** a populated base and a new target directory, **When** the developer points
   the base at the new directory and triggers relocation, **Then** pure-data artifacts
   are moved, regenerated artifacts are rebuilt for the new base, and all instances boot.
2. **Given** the base has been relocated, **When** any command runs, **Then** no artifact
   (orchestration files, environment shims, generated config, virtual tool environment)
   still points at the previous base.
3. **Given** both the CLI and the in-session tool surface are used against the same
   project, **When** the base is overridden, **Then** both resolve the identical base and
   operate on the same state (they never disagree about where state lives).

---

### Edge Cases

- **Both new and old locations exist** (e.g. migration ran, then someone restored an old
  in-repo `runtime/`): the system MUST treat the per-user base as authoritative and MUST
  NOT silently merge or overwrite, surfacing the conflict instead.
- **Base directory not writable / disk full mid-migration**: the operation MUST fail
  loudly and leave the original state usable (no destructive move before the copy/target
  is confirmed), so a retry can complete.
- **Override points at a relative path, a path needing `~` expansion, or a non-existent
  parent**: the base MUST resolve to a single absolute location, creating it as needed,
  or fail with actionable guidance.
- **An instance whose WordPress install moved**: its orchestration mounts and any
  per-instance environment shims MUST be regenerated to the new absolute location, since
  these cannot simply be moved.
- **Secrets during migration**: per-machine secret files MUST be relocated with their
  restricted permissions preserved and MUST NOT be echoed, logged, or copied into the
  repo or any tracked file.
- **A second process / tool surface running during migration**: concurrent access MUST
  NOT corrupt the move (the migration is serialized or guarded).
- **Stale repo ignore rules / leftover empty `runtime/` dir** after migration: the repo
  MUST end up free of machine-state references and leftovers.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Sandbox MUST derive every machine-state location from a single base,
  defaulting to a per-user directory (`~/sandbox`) and overridable via one documented
  environment variable (`SANDBOX_HOME`).
- **FR-002**: All generated runtime state MUST live under the base: per-instance
  WordPress installs, generated orchestration/compose files, snapshots, the shared
  download cache, seeds, the project→instance registry, lock/marker files, host-side
  environment shims, the test suite/tools, the proxy/cert material, and the shared
  command-line tool binary.
- **FR-003**: All instance-related generated configuration MUST live under the base —
  per-instance config blocks and any per-machine config the system writes — so no
  generated instance config remains in the repo checkout.
- **FR-004**: The previously machine-global user config and the previously repo-root
  per-machine config and secrets MUST be consolidated under the base (a single config
  file, a per-machine config file, and a secrets file), rather than split across the
  home config area and the repo root.
- **FR-005**: The repo checkout MUST contain NO machine-state after this change —
  no generated runtime, no per-machine config, no secrets — and the now-obsolete repo
  ignore entry for in-repo runtime MUST be removed.
- **FR-006**: Both the standalone command-line tool and the in-session tool surface
  (two separate processes) MUST resolve the SAME base for the same environment, so they
  never operate on divergent state. The base MUST propagate to the in-session tool
  surface (or both MUST default identically).
- **FR-007**: On first run after upgrade, the Sandbox MUST perform a one-time migration
  that detects existing in-repo runtime state and the old config/secret locations and
  relocates them under the base. The migration MUST be idempotent (safe to re-encounter)
  and MUST preserve secret-file permissions.
- **FR-008**: After migration, every instance previously registered MUST remain
  resolvable and bootable, serving its site as before, with snapshots and cache intact.
- **FR-009**: Artifacts that bake an absolute path MUST be REGENERATED or RECREATED for
  the active base rather than moved — specifically the orchestration/compose files (with
  absolute mount paths), the host-side environment shims, the local proxy routing config,
  and the virtual environment used for tooling. Pure-data artifacts MUST be moved as-is.
- **FR-010**: Generated orchestration files MUST reference state by absolute paths under
  the base so they remain valid when the base is outside the repo checkout.
- **FR-011**: The project→instance registry MUST continue to key state by project-root
  path; those project-root paths are independent of the base and MUST remain valid across
  any migration or relocation. (Amended for multi-instance-per-root: a project root MAY
  own more than one instance, distinguished by a `label`; the registry key becomes
  `<project-root>::<label>`, so "by project-root path" now means "by project-root path,
  further scoped by label" rather than a strict one-to-one root→instance mapping. See
  `docs/multi-instance-spec.md`.)
- **FR-012**: Setting the base override to a different directory MUST relocate all state
  there (move pure-data, regenerate/recreate baked artifacts) such that instances boot
  from the new base and nothing references the old base.
- **FR-013**: If both the new base and an old in-repo location contain state, the base
  MUST be treated as authoritative; the system MUST NOT silently merge or overwrite and
  MUST surface the conflict.
- **FR-014**: Plugin sources that are bind-mounted at a fixed absolute host path MUST
  continue to resolve at that same absolute path after the move (this behavior is
  unaffected by relocating the base).
- **FR-015**: A backward-compatibility fallback MUST read state/config from the old
  locations when the new base locations are absent, so an un-migrated environment is not
  immediately broken before migration runs.
- **FR-016**: Documentation that names in-repo machine-state locations (developer guide,
  the governing principles/constitution references, and any operator docs) MUST be
  updated in the same change to name the base-relative locations, so docs do not drift.

### Key Entities *(include if feature involves data)*

- **Base location (`SANDBOX_HOME`)**: the single per-user root (default `~/sandbox`) from
  which every machine-state path is derived; settable via one environment variable.
- **Runtime state**: all generated, machine-specific artifacts (instance WP installs,
  orchestration files, snapshots, download cache, seeds, locks/markers, environment
  shims, test suite/tools, proxy material, shared tool binary) living under the base.
- **Per-machine config & secrets**: the consolidated user config file, per-machine config
  file, and secrets file, relocated under the base.
- **Registry**: the project-root → instance mapping; authoritative, keyed by project root
  (independent of the base), living under the base. (Amended for multi-instance-per-root:
  the authoritative key is `<project-root>::<label>`, so one root MAY back one-or-more
  instance entries; a v1 (root-only-keyed) registry MUST be auto-migrated in place to the
  v2 composite-key shape on first read, preserving every existing instance's identity,
  ports, and status. See `docs/multi-instance-spec.md` §1.)
- **Pure-data vs. baked artifacts**: pure-data artifacts (registry, config, snapshots,
  cache, seeds, instance files, certs) move cleanly; baked artifacts (orchestration files,
  environment shims, proxy config, tooling virtual environment) must be regenerated for
  the active base.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After upgrading an existing setup, 100% of previously-registered instances
  remain resolvable and boot to a serving site with no manual relocation steps.
- **SC-002**: After any instance is created or migrated, the repo working tree shows zero
  machine-state files (no generated runtime, per-machine config, or secrets) — verifiable
  as a clean status with no new ignored-or-tracked machine artifacts.
- **SC-003**: From a fresh clone with an empty base, creating one instance places 100% of
  its generated state and per-instance config under the base and none in the checkout.
- **SC-004**: Setting the base override to a new directory relocates all state such that
  every instance boots from the new location and zero artifacts reference the previous
  base.
- **SC-005**: The standalone tool and the in-session tool surface resolve the identical
  base for the same environment in 100% of cases (no divergent-state class of bug).
- **SC-006**: The one-time migration is safe to encounter repeatedly: running ordinary
  commands any number of times after the first never re-migrates or duplicates state.
- **SC-007**: No secret value is written to the repo, any tracked file, logs, or stdout
  during migration or relocation; secret-file permissions are preserved.

## Assumptions

- The default base is `~/sandbox` (the user's chosen layout), not an XDG data dir; the
  single override variable is `SANDBOX_HOME`.
- A prior audit established that pure-data artifacts (registry, per-machine config,
  snapshots, download cache, seeds, instance WP files, proxy certs) contain no baked
  in-repo or in-runtime absolute paths and therefore move cleanly; the only baked-path
  artifact requiring recreation for a runtime move is the tooling virtual environment,
  plus regeneration of orchestration files, environment shims, and the proxy routing
  config which are produced from config on every reconcile.
- Databases live in container-managed volumes (not under the base), so they are
  unaffected by moving the base; instances reattach to their volumes after orchestration
  files are regenerated.
- The in-session tool surface is launched via a registered configuration into which the
  base location can be propagated (or it defaults to the same `~/sandbox`).
- External integrations that reference the code checkout (privilege-helper rules, any
  OS-level launch agent) point at the repo, not at runtime, and are therefore unaffected
  by relocating the base; only a repo move (out of scope here) would touch them.
- The host-driver (Herd) path for instances follows the same base-derivation rule; its
  environment shims are regenerated like other baked artifacts.
- Removal of any legacy in-repo path handling follows the project's parity-before-removal
  rule: old-location reads are kept as a fallback until the migrated path is proven.

## Convergence amendment — 2026-08-13 (PHP extension build state)

The WordPress PHP-extension requirement is project configuration, but the generated
build context and its provenance are machine state. This amendment keeps that state
inside the same swappable base and does not change the configuration-home placement
decision in Spec 042.

- Extension build contexts and recreatable caches MUST live below
  `$SANDBOX_HOME/runtime/build/php-extensions/<content-digest>/`; they MUST NOT be
  written into a checkout, a tracked spec, or a project-owned source directory.
- The extension cache key MUST include the normalized requirement document, immutable
  profile/catalog revision, parent image digest, PHP version, web-server flavor,
  platform, and architecture. A change to any input MUST select a different digest.
- Cache entries MUST record only non-secret source/artifact provenance, resolved image
  digests, package/artifact versions, and last observations. They MUST be safe to
  recreate or discard during base relocation; no cache entry may be treated as the
  source of truth for project data.
- Relocation MUST move pure metadata safely and regenerate path-bearing build contexts
  for the new base. It MUST preserve the existing database volumes, uploads, snapshots,
  and project files; extension reconciliation is limited to web/runtime artifacts.
- The CLI and MCP processes MUST derive this path from the same active base, and cache
  diagnostics MUST redact credentials, tokens, and private source paths.
