# Feature Specification: Per-Project-First Instance Model & Modular `sb`

**Feature Branch**: `001-per-project-modular`

**Created**: 2026-06-21

**Status**: Draft

**Input**: User description: "Per-project-first instance model and modular sb package: remove the legacy main/DEFAULT_INSTANCE model and split the sb monolith into a sandbox package with one module per feature"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Commands act on the project I'm standing in (Priority: P1)

A developer working in a plugin checkout runs `sb` commands (status, wp, doctor,
snapshot, …) with no `--instance` flag and they operate on that project's own
instance — never on a shared/phantom instance.

**Why this priority**: This is the core correctness goal. Today a bare command can
silently target the implicit `main` instance, acting on the wrong WordPress stack —
the single most damaging behavior to remove.

**Independent Test**: From a registered project dir, run `sb status` / `sb wp plugin
list`; confirm the reported instance is that project's instance and the output reflects
that stack. Verifiable on its own without any other story.

**Acceptance Scenarios**:

1. **Given** my cwd is a registered project, **When** I run `sb status` with no flag,
   **Then** it targets that project's instance.
2. **Given** I set `$SANDBOX_INSTANCE` or pass `--instance X`, **When** I run a command,
   **Then** the explicit choice overrides the cwd-derived instance.
3. **Given** my cwd is NOT a registered project, **When** I run an instance command,
   **Then** it fails with guidance ("cd into a registered project or run `sb init` /
   `sb ensure`") and does NOT boot or target any fallback instance.

---

### User Story 2 - No phantom `main` instance anywhere (Priority: P1)

A developer never encounters an implicit `main` instance in any surface — CLI listing,
dashboard, web UI, MCP server, or app-password storage. Each instance shown/operated on
corresponds to a real registered project.

**Why this priority**: Removing `main` is the explicit goal; a half-removed `main`
(present in one surface but not another) is worse than either consistent state.

**Independent Test**: `sb instances`, the web/TUI dashboards, and the MCP tools all list
only registered project instances; no `main` row appears; deleting any instance needs no
special-case exception.

**Acceptance Scenarios**:

1. **Given** a fresh machine with no `instances:` block, **When** I run `sb instances`,
   **Then** the list contains only registered projects (no synthesized `main`).
2. **Given** any registered instance, **When** I delete it via CLI/dashboard/web,
   **Then** it deletes without a "refusing to delete main" guard.
3. **Given** the MCP server resolves an instance, **When** it reads the application
   password, **Then** it reads the per-instance key (no legacy `main`-only path).

---

### User Story 3 - Feature parity preserved across the change (Priority: P1)

A developer's existing registered instances keep working through and after the change:
status, up/down, install, wp, doctor, snapshot/restore, domains, secure, dashboard, MCP
tools — all behave as before for per-project instances.

**Why this priority**: The change is a refactor of critical shared tooling; any
regression blocks every developer who pulls it. Parity is a release gate, not a nicety.

**Independent Test**: Run the full command set against an existing registered instance
before and after; outputs match (modulo the intended `main`-removal behavior).

**Acceptance Scenarios**:

1. **Given** an existing registered instance, **When** I run each lifecycle/data/network
   command with no `--instance`, **Then** each succeeds as it did before.
2. **Given** the MCP server, **When** a tool that needs the app password runs against a
   registered project, **Then** it authenticates using the per-instance password.

---

### User Story 4 - The CLI is maintainable as independent feature modules (Priority: P2)

A maintainer can find, read, and change one feature (e.g. snapshots, domains, instances)
in its own module without scrolling a single ~7000-line file, and add a new feature by
adding a module that self-registers — without editing a central dispatch by hand.

**Why this priority**: Maintainability/extensibility is the stated "every feature is a
module" goal. It's P2 because it changes structure, not user-visible behavior; it must
not regress P1 behavior.

**Independent Test**: Each feature lives in its own module; the CLI builds its command
set from a registry; `sb` itself remains a single launch file; the installed/symlinked
CLI and the release tarball still work.

**Acceptance Scenarios**:

1. **Given** the refactor, **When** I inspect the codebase, **Then** each feature group
   is its own module and `sb` is a thin entry file.
2. **Given** the global symlink install and the npm/tarball install, **When** I run the
   installed `sb` from any directory, **Then** it resolves its package and runs identically.

---

### Edge Cases

- A directory that is a project marker (`.git`) but has no registered instance → helpful
  error, not a `main` boot.
- A stale `$SANDBOX_INSTANCE` naming a deleted instance → error listing valid instances.
- A machine that historically had a real `main` instance with content → see Assumptions
  (disposability) and Open Question on a one-shot migrator.
- Project-routed commands (`init`, `ensure`, `test`, `mcp`, `smoke`, `apply
  --project-dir`) run from a not-yet-registered dir → still work (they carry their own
  `--project-dir`), not gated by the instance resolver.
- Concurrent `sb` invocations across two project dirs → each resolves its own instance.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Instance resolution MUST follow the precedence: explicit `--instance` →
  `$SANDBOX_INSTANCE` → the registry instance for the cwd's project → error. No `main`
  fallback.
- **FR-002**: A command requiring an instance, run outside any registered project, MUST
  error with actionable guidance and perform no side effects.
- **FR-003**: The system MUST NOT synthesize or expose a `main` instance in any surface
  (CLI, TUI dashboard, web UI, MCP server).
- **FR-004**: Application-password storage and retrieval MUST use the per-instance
  location in both the CLI and the MCP server; the legacy `main`-only key MUST be removed.
- **FR-005**: The system MUST remove the legacy pre-multi-instance migration and all
  `main` special-cases (delete guards, name reservation) once parity is verified.
- **FR-006**: All existing per-project instance features MUST continue to work unchanged
  for registered instances (no regressions).
- **FR-007**: Each CLI feature MUST be a self-contained module registered through a
  command registry; adding/removing a feature MUST NOT require editing a hand-maintained
  central dispatch table.
- **FR-008**: `sb` MUST remain a single entry file; the global symlink, npm bin shim, and
  release tarball MUST continue to resolve and run the CLI from any directory.
- **FR-009**: Dev-process tooling (spec-kit `.specify/` and `speckit-*` skills) MUST be
  excluded from the shipped product.
- **FR-010**: Each removal of old-model code MUST be a separately verifiable change, gated
  on live-stack proof of the per-project replacement (per constitution Principle VI).

### Key Entities

- **Instance**: a per-project WordPress stack (containers/host, DB, WP dir, ports),
  identified by an instance name, owned by exactly one project root.
- **Registry entry**: the authoritative project-root → instance mapping plus cached
  metadata, stored in `runtime/registry.json`.
- **Instance config**: per-instance settings (ports, server, domain, wp_config,
  multisite, app password) under `sandbox.local.yml` `instances:`.
- **Feature module**: a self-contained unit of CLI functionality that registers its
  subcommand(s) and handler with the command registry.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Running any instance command from a registered project dir with no flag
  targets that project's instance in 100% of cases (no `main` ever selected).
- **SC-002**: Searching the codebase for the legacy model (`DEFAULT_INSTANCE`, synthesized
  `main`, legacy app-password key, legacy migration) returns zero load-bearing references.
- **SC-003**: Every command in the existing surface produces equivalent results before and
  after the change for a registered instance (zero regressions in the verification matrix).
- **SC-004**: The installed CLI (global symlink AND tarball/npm) runs identically from an
  arbitrary directory after modularization.
- **SC-005**: No single source file in the CLI exceeds a maintainable size threshold; each
  feature is locatable in its own module.

## Assumptions

- The registry (`runtime/registry.json`) and per-instance `sandbox.local.yml` blocks are
  already populated for active projects (written by `ensure_instance`/`apply_config`), so
  removing the synthesized `main` loses no per-project configuration.
- `main` is disposable on developer machines (no production-critical content lives only in
  a `main` instance). Whether to ship a one-shot `main`→project migrator is an open
  question for `/speckit-clarify`.
- The MCP tool surface and per-instance on-disk layout do not change; only the resolution
  model, app-password location, and code organization change.
- The existing polyglot single-file `sb` bootstrap and `sys.path`-based import of sibling
  Python modules remain the supported distribution mechanism.
- Snapshot/restore from the WP dashboard is tracked as a separate feature (spec 002) and
  is out of scope here.
