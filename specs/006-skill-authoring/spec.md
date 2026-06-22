# Feature Specification: In-Product Skill Authoring (Auto-Matched Playbooks)

**Feature Branch**: `feat/agent-tooling-specs`

**Created**: 2026-06-22

**Status**: Draft

**Input**: User description: "Steal from Novamira #4 — skills are read-only today; add
in-product skill authoring plus a skill that teaches the agent to write skills, with
description-based auto-matching."

## Context

The Sandbox can load skills/workflows on demand, but the agent cannot *create or
refine* one when it learns something reusable, so hard-won knowledge evaporates at
the end of a session. This feature adds the write half — the agent can persist,
edit, and delete skills — keeps discovery cheap by matching on a one-line
description and loading the full body only on a match, ships a skill that teaches the
house style, and resolves the same slug appearing in multiple sources with a clear
precedence. Skills stay file-based Markdown in the repo (reviewable, diffable,
team-shared), not a database.

Implementation detail (tool/CLI names, parser internals, exact directories) is
deferred to `plan.md`.

## Clarifications

### Session 2026-06-22

- Q: When the same skill slug exists in multiple sources, which wins? → A: **project > personal > sandbox** — most-specific wins: a focused-plugin skill overrides the dev's personal skill, which overrides the generic sandbox built-in.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Agent persists a learned playbook (Priority: P1)

After working out a non-obvious repro/fix/build sequence, the agent saves it as a
skill so the next session starts from it.

**Why this priority**: Capturing reusable knowledge is the whole point; without the
write path nothing else matters.

**Independent Test**: Have the agent author a skill and confirm it is saved with a
valid title/description/body and a derived slug, then is discoverable.

**Acceptance Scenarios**:

1. **Given** a learned procedure, **When** the agent saves it as a skill (title,
   description, body, scope), **Then** a foldered skill is written with valid
   metadata and a slug derived from the title.
2. **Given** a slug collision, **When** saving, **Then** the agent can choose to
   rename (auto-suffixed), replace (same-scope user/project only), or fail with a
   suggested free slug.
3. **Given** a built-in sandbox skill slug, **When** an authored skill would shadow
   it, **Then** it cannot silently overwrite — it must rename.

### User Story 2 — Description drives matching (Priority: P1)

The agent loads a skill's full body only when its one-line description matches the
task, not eagerly.

**Why this priority**: Keeps baseline context cheap and makes the catalog scale;
core to the design.

**Independent Test**: Confirm only slug + description (not bodies) are present in
baseline context, and the body loads on demand by slug.

**Acceptance Scenarios**:

1. **Given** the skill catalog, **When** the agent is working, **Then** it sees each
   skill as slug + source + one-line description, with bodies not loaded.
2. **When** a description matches the task, **Then** the agent loads that skill's full
   body by slug.

### User Story 3 — Choose where a new skill lives (Priority: P2)

The agent or dev selects the scope of a new skill (the focused plugin, the dev's
personal collection, or the shared sandbox set).

**Why this priority**: Right home = right sharing/precedence; convenience over the
core write capability.

**Independent Test**: Save the same skill to different scopes and confirm it lands in
the corresponding location and is discovered.

**Acceptance Scenarios**:

1. **Given** a scope choice, **When** the skill is saved, **Then** it is written to
   the matching location (focused-plugin / personal / sandbox), defaulting to the
   focused plugin when one is in focus, else sandbox.
2. **Given** a new skill, **When** discovery runs, **Then** it appears without a
   restart.

### User Story 4 — A skill that teaches skill authoring (Priority: P2)

A built-in skill teaches the agent the house style for writing a good skill.

**Why this priority**: Raises quality/consistency of authored skills; supporting, not
core.

**Independent Test**: Load the skill-creator skill and confirm it returns guidance on
metadata, description-as-trigger, foldered layout, and the write→load→verify loop.

**Acceptance Scenarios**:

1. **Given** the skill-creator skill, **When** loaded, **Then** it explains the
   required metadata, description-as-trigger, the foldered layout rule, and the
   author→load→verify workflow.

### Edge Cases

- A disabled skill is omitted from the matchable catalog.
- The writer refuses to write outside the allowed scope roots (path-jailed).
- A skill must be a folder with an uppercase entry file — flat single-file skills are
  rejected.
- A collision across sources surfaces the winning source by precedence, optionally
  reporting shadowed duplicates.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The agent MUST be able to create a skill from a title, description,
  body, and scope; the slug MUST be derived from the title.
- **FR-002**: The agent MUST be able to edit and delete an existing skill.
- **FR-003**: Saving MUST handle slug collisions with explicit rename / replace / fail
  behavior, and MUST NOT let an authored skill silently shadow a built-in slug.
- **FR-004**: The system MUST list all skills across sources with their source and
  one-line description; on a slug collision, precedence MUST be **project > personal >
  sandbox**, and the body MUST load only on demand by slug.
- **FR-005**: New skills MUST be written as a folder with an uppercase entry file
  (never a flat single file), confined to the chosen scope root (path-jailed),
  defaulting to the focused plugin when one is in focus, else the sandbox set.
- **FR-006**: A new or edited skill MUST become discoverable without restarting the
  session.
- **FR-007**: The system MUST ship a built-in skill-creator skill that teaches the
  authoring conventions.
- **FR-008**: The CLI MUST offer parity for list/create/edit/delete/show.
- **FR-009**: Disabled skills MUST be excluded from the matchable catalog.

### Key Entities

- **Skill**: a foldered Markdown playbook with metadata (slug, one-line description,
  enabled flag) and a body; the unit of authoring and matching.
- **Source**: a location skills come from — focused-plugin, personal, or sandbox —
  ordered by precedence for collision resolution.
- **Catalog**: the lightweight slug + description + source listing used for matching
  without loading bodies.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An agent can save a learned procedure as a skill and load it back by
  slug in the same session, with no restart.
- **SC-002**: Baseline context carries only slug + description per skill (no bodies),
  and a body is fetched only on a description match.
- **SC-003**: A slug collision across sources resolves deterministically as project >
  personal > sandbox 100% of the time.
- **SC-004**: The writer rejects 100% of attempts to write outside an allowed scope
  root or as a flat single file.
- **SC-005**: A built-in skill-creator skill is present and loadable.

## Assumptions

- Skills are file-based Markdown in the repo / dotfiles (no database); the enabled
  flag is metadata, and a disabled skill is simply omitted from the catalog.
- Workflows authoring (a parallel write path for workflows) is out of scope for v1.
- The existing on-demand skill/workflow loading + per-plugin/personal discovery is
  reused; this feature adds the write half and the precedence rule.
