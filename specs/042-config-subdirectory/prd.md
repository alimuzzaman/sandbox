# Product Requirements Draft: Sandbox Config Subdirectory

**Status**: Discovery

**Created**: 2026-08-13

**Last Refined**: 2026-08-13

**Input**: "Allow the entire Sandbox config set, including sandbox.config.json, machine overrides, and labeled layers, to live in a conventional subdirectory instead of the project root."

**Drafting Model**: Runtime-selected GPT-5 configuration (exact model and effort not exposed)

**Final Validation**: `PASS` — `gpt-5.6-sol` High

**Validated On**: 2026-08-13

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Sandbox currently requires its project configuration family at the repository root.
Projects that organize tooling configuration under a dedicated directory must keep
Sandbox's primary config, project-local override, and version-matrix label files in
the root anyway. This creates avoidable root clutter and makes reorganizing those files
unsafe: Sandbox does not discover the moved family. This feature prevents cross-home
merging and duplicate-primary ambiguity, but it does not change the existing silent
fallback when an optional override or label file is absent from the selected home.

Developers need one conventional alternate home for the entire Sandbox config family so
the files can move together without changing their names or weakening existing projects.

## Users and Desired Outcomes

- **Project maintainers**: Keep all project-local `sandbox.config*` configuration under
  one conventional subdirectory and
  retain the same project behavior and supported file formats.
- **Developers and CI operators**: Receive a clear failure when a project ambiguously
  defines primary Sandbox configuration in both the root and the conventional directory.
- **Existing Sandbox projects**: Continue using root-level configuration without any
  migration or behavior change.

## Goals

- Allow the complete Sandbox project config family to live in one fixed conventional
  project-local subdirectory.
- Apply the same discovery behavior to WordPress and generic Compose projects.
- Preserve all current filenames, supported formats, layer precedence, label semantics,
  and root-level compatibility within the selected config home.
- Prevent ambiguous or partially migrated configurations from silently selecting the
  wrong home.
- Ensure user-facing guidance documents both supported layouts and safe migration.

## Non-Goals

- Supporting an arbitrary user-configured config directory.
- Searching multiple nested directories or walking the project tree for config files.
- Mixing the primary config from one home with overrides or label layers from another.
- Changing whether missing optional label layers warn or fail.
- Renaming config files, changing their schema, or changing layer precedence.
- Automatically moving or deleting existing project files.
- Relocating `.wp-env.json`, `$SANDBOX_HOME/config.json`,
  `$SANDBOX_HOME/sandbox.local.yml`, or other machine-owned state.

## Product Scenarios

### Scenario 1 — Entire config family in the conventional directory

- **Starting state**: A project has no root-level primary Sandbox config and keeps its
  primary config plus any overrides and label layers under the conventional directory.
- **User action**: The user runs normal Sandbox commands from the project.
- **Expected outcome**: Sandbox recognizes the project and uses the complete config family
  from the conventional directory exactly as it would have used the root-level family.

### Scenario 2 — Existing root-level project

- **Starting state**: A project has its primary Sandbox config and optional related files
  at the project root.
- **User action**: The user upgrades Sandbox and runs existing workflows.
- **Expected outcome**: The existing root-level configuration remains authoritative and
  behavior is unchanged.

### Scenario 3 — Ambiguous primary configs

- **Starting state**: A project contains a supported primary Sandbox config in both the
  project root and `.config/sandbox/`.
- **User action**: The user runs a command that loads project configuration.
- **Expected outcome**: Sandbox stops before runtime side effects and identifies both
  locations with actionable guidance to retain exactly one config home.

### Scenario 4 — Incomplete migration

- **Starting state**: The primary config is in one supported home while an override or
  label layer remains in the other.
- **User action**: The user loads or runs the project.
- **Expected outcome**: Sandbox reads related layers only from the primary config's home;
  it never silently combines the two homes. An orphan layer in the other home remains
  ignored without warning under the existing optional-layer policy. Documentation tells
  users to move the family together.

### Scenario 5 — Equivalent format support

- **Starting state**: A project uses any currently supported primary, override, or label
  extension in the conventional directory.
- **User action**: The project configuration is loaded.
- **Expected outcome**: The same format support and precedence rules apply as at root.

### Scenario 6 — Nested invocation and legacy marker coexistence

- **Starting state**: The native primary config is in the conventional directory, the
  user invokes Sandbox from a nested project directory, and the root may also contain a
  legacy `.wp-env.json` fallback marker.
- **User action**: The user runs a project-scoped Sandbox command, whether or not the
  project is a Git checkout.
- **Expected outcome**: Sandbox identifies the same project root and selects the native
  primary config; the legacy fallback does not create ambiguity with native config.

### Scenario 7 — Escaping conventional-directory symlink

- **Starting state**: The conventional config directory is a symlink whose resolved
  target escapes the project root.
- **User action**: The user runs a command that loads project configuration.
- **Expected outcome**: Sandbox fails before runtime side effects with safe guidance that
  project configuration must remain inside the project root.

## Proposed Product Behavior

- Sandbox recognizes exactly two project config homes: the project root and the fixed
  conventional `.config/sandbox/` subdirectory.
- The home containing the project's primary Sandbox config owns the entire related config
  family for that load.
- A root-only project and a conventional-directory-only project are both valid.
- Primary configs in both homes are an ambiguity error, reported before runtime mutation.
- Related override and label layers are never merged across homes.
- The selected home's existing layer precedence and optional-label behavior remain
  unchanged.
- Documentation and example naming conventions are unchanged; any file matching an
  existing supported label filename retains its current runtime treatment.
- All supported Sandbox commands and config-loading entry points observe the same home.

The relocated project-local family is bounded as follows:

| File family | Relocated together | Product treatment |
|-------------|--------------------|-------------------|
| `sandbox.config.{json,yml,yaml}` | Yes | Native primary project config |
| `sandbox.config.override.{json,yml,yaml}` | Yes | Optional project-local override |
| `sandbox.config.<label>.{json,yml,yaml}` | Yes | Optional matching-label layer |
| `.wp-env.json` | No | Remains a root-level legacy import/fallback marker |
| `$SANDBOX_HOME/config.json` | No | Remains machine-wide configuration |
| `$SANDBOX_HOME/sandbox.local.yml` | No | Remains machine-local state and secrets |

## Constraints and Dependencies

- Root-level behavior is a compatibility surface and must not regress.
- Config resolution is shared product infrastructure; WordPress and Compose must not
  diverge.
- Discovery must remain bounded to known paths and must not accept path traversal or an
  arbitrary external directory.
- Error messages must avoid exposing secret values from configuration.
- Documentation and executable verification must land with the behavior change.
- Live-stack evidence is required before the feature is considered complete.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Subdirectory scope | Move the entire config family, including the primary config | The user selected the complete-config option rather than root bootstrap plus relocated layers | User, 2026-08-13 |
| Conventional location | `.config/sandbox/` | Provides a stable project-local home without requiring a root bootstrap file | User, 2026-08-13 |
| Existing projects | Preserve root-level config discovery | Avoids a mandatory migration and satisfies compatibility policy | Existing policy and feature request |
| Cross-home behavior | Select one complete home; never mix layers | Prevents partial migrations and stale files from producing surprising effective config | Safety requirement derived from reported failure mode |
| Duplicate primary configs | Fail with actionable ambiguity guidance | Silent precedence could run against stale configuration | Safety requirement derived from reported failure mode |
| Missing label policy | Unchanged and outside this feature | The request is config placement; strict label intent is a separate concern | Scoped product decision |

## Open Questions

- None.

## Acceptance Outcomes

- A WordPress project whose complete config family exists only under the conventional
  directory resolves distinct primary, override, and matching-label sentinel values in
  the same precedence as an equivalent root-level project.
- A generic Compose project whose complete config family exists only under the
  conventional directory resolves distinct primary, override, and matching-label
  sentinel values in the same precedence as an equivalent root-level project.
- Existing root-only WordPress and Compose projects pass their current configuration and
  runtime checks unchanged.
- A project with primary config files in both supported homes fails before runtime side
  effects and receives guidance naming the ambiguity and resolution.
- An override or label layer in the non-selected home does not affect effective
  configuration.
- JSON, YAML, and YML retain their existing support and precedence within either home.
- Absent and malformed labels retain their current observable behavior in WordPress and
  Compose projects.
- Project-root discovery works from a nested directory for a conventional-home native
  config in both Git and non-Git projects, and a root `.wp-env.json` remains only a
  fallback when native config exists.
- A conventional-directory symlink that resolves outside the project root fails before
  runtime side effects.
- User documentation gives a complete move-together migration example and explains that
  the two homes cannot be combined.
- One live WordPress instance and one live Compose instance load conventional-home
  configurations containing distinguishable layer values and report the expected final
  values.

## Risks and Assumptions

- **Risk**: Users may copy rather than move the primary file and encounter the new
  ambiguity error; migration guidance must make the required cleanup explicit.
- **Risk**: A consumer may have bypassed the public config interface and assumed a
  root-level path; repository analysis and regression coverage must identify such drift.
- **Risk**: A partially moved optional layer can remain unused without an error because
  missing optional-label behavior is unchanged; move-together guidance must be prominent.
- **Assumption**: `.config/sandbox/` does not conflict with an
  established Sandbox-owned project directory.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
