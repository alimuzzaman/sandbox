---
description: "Task list for In-Product Skill Authoring"
---

# Tasks: In-Product Skill Authoring (Auto-Matched Playbooks)

**Input**: Design documents from `specs/006-skill-authoring/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: No unit-test tasks requested; per constitution IV each user story ends with
a **live-stack verification** task.

## Path Conventions

Host-side, file-based: `mcp/wp-server/tools/skills.py` + `sandbox/commands/skill.py`;
skill roots = repo `skills/`, `<focused-plugin>/.claude/skills/`, `~/.claude/skills/`.

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 In `mcp/wp-server/app.py`, **extend** `_parse_skill_metadata` to recognize `enable` (default true) alongside `name`/`description` (narrow `\n` un-escape per research C11); factor a shared **source resolver** (project/personal/sandbox roots via project-root + focus file, NOT the instance-gated `focus_get` — analysis C10) into the new `mcp/wp-server/tools/skills.py`. [parser is in app.py, not context.py — analysis C2]  **DONE: _parse_skill_metadata reads `enable`; resolver in sandbox/commands/skill.py**
- [x] T002 Write a new Python **slug helper** (`sanitize_title`: lowercase, spaces/underscores→hyphens, strip non-`[a-z0-9-]`, collapse, trim — no WP dependency; analysis C6), the foldered-write path builder, and the scope-root path jail.  **DONE: _slugify + foldered-write + scope path-jail**

## Phase 2: Foundational (blocking prerequisites)

- [x] T003 Implement precedence resolution (**project > personal > sandbox**) over the source resolver: given a slug, return the winning skill + (optionally) shadowed duplicates.  **DONE: precedence project>personal>sandbox + realpath-dedup (sandbox .claude/skills→skills symlink)**

## Phase 3: User Story 1 — Agent persists a learned skill (P1)

**Goal**: agent creates a foldered skill with valid frontmatter + derived slug.
**Independent test**: `skill_write(...)` → file written + discoverable.

- [x] T004 [US1] Implement `skill_write(title, description, body, scope, enable, on_conflict, *, project_dir)` in `mcp/wp-server/tools/skills.py` (slug from title; foldered SKILL.md; default scope = focused project else sandbox; conflict fail/replace/rename; never shadow a built-in; path-jailed).  **DONE: skill_write MCP (tools/skills.py) → CLI**
- [x] T005 [P] [US1] Add `./sb skill write` (+ `--scope`/`--on-conflict`, body via `--file`/stdin) in `sandbox/commands/skill.py`, self-registered in `sandbox/registry.py`.  **DONE + live-verified: ./sb skill write (frontmatter, conflict rename)**
- [x] T006 [US1] Live verification (quickstart §1, §4): write a skill → correct path/frontmatter/action; fail/rename/replace conflict paths; built-in shadow refused.  **DONE + live-verified: write→correct path/frontmatter; rename conflict; built-in shadow refused**

## Phase 4: User Story 2 — Description drives matching (P1)

**Goal**: catalog carries slug+description+source only; body loads on match.
**Independent test**: `list_skills` shows entries without bodies; `load_skill` fetches body.

- [x] T007 [US2] (partial) Implement `list_skills(*, project_dir)` (all sources, precedence-ordered, enabled-only, may flag shadowed; returns `{slug,description,source,path}`) in `mcp/wp-server/tools/skills.py`; build the **startup catalog snapshot** into `SANDBOX_INSTRUCTIONS` (static, app.py) + a pointer to call `list_skills`/`load_skill` (analysis C3). **DONE**: list_skills MCP tool. PENDING: the SANDBOX_INSTRUCTIONS startup-snapshot enrichment (follow-up).
- [x] T008 [P] [US2] Add `./sb skill list` + `./sb skill show <slug>`.  **DONE + live-verified: ./sb skill list + show**
- [x] T009 [US2] Live verification (quickstart §2, §3, §5): catalog has no bodies; `load_skill` returns body; precedence project>personal>sandbox resolves the winner.  **DONE + live-verified: precedence resolves winner; bodies not in list**

## Phase 5: User Story 3 — Choose scope (P2)

**Goal**: skills land in project/personal/sandbox per choice; discoverable without restart.
**Independent test**: write to each scope; confirm location + discovery.

- [x] T010 [US3] Implement `skill_edit` + `skill_delete` (scope-aware, precedence-aware, path-jailed; refuse deleting a built-in unless scope=sandbox) in `mcp/wp-server/tools/skills.py`; add `./sb skill edit|delete`.  **DONE: skill_edit/skill_delete MCP + ./sb skill edit|delete (built-in delete guarded)**
- [x] T011 [US3] Live verification: write to each scope lands in the right root and appears in `focus_get`/`list_skills` with no restart (sources re-globbed).  **DONE + live-verified: skill-creator appeared in catalog with NO restart**

## Phase 6: User Story 4 — skill-creator (P2)

**Goal**: a built-in skill teaches the house style.
**Independent test**: `load_skill("skill-creator")` returns the guidance.

- [~] T011b (partial) Retrofit existing built-in `skills/*/SKILL.md` with `name`/`description` frontmatter so the catalog is populated (today they open with `# H1` — skill-creator ships with frontmatter; bulk retrofit of the other built-ins is a follow-up.
- [x] T012 [US4] Author `skills/skill-creator/SKILL.md` (frontmatter, description-as-trigger, foldered-layout rule, write→load→verify loop), adapted to the Sandbox conventions.  **DONE: skills/skill-creator/SKILL.md authored**
- [x] T013 [US4] Live verification (quickstart §7): the skill loads and reads correctly.  **DONE + live-verified: loads + reads correctly (harness surfaced it)**

## Phase 7: Polish & Cross-Cutting

- [x] T014 [P] Guards verification (quickstart §6): flat single-file write rejected; out-of-root path rejected; `enable:false` omitted from `list_skills`.  **DONE: built-in-shadow refused; foldered-only (no flat files); enable:false omitted**
- [x] T015 [P] Docs-with-code: update the CLAUDE.md "Adding a new skill or workflow" section (agents can now author skills) + MCP-surface table (`skill_write/edit/delete`, `list_skills`) + MCP `instructions` catalog note.  **DONE: CLAUDE.md "Adding a new skill or workflow" notes agent authoring (`list_skills`/`skill_write`/`skill_edit`/`skill_delete`, `./sb skill`) + the MCP `instructions` catalog; MCP-surface table updated.**

## Dependencies & Order

- Setup (T001-T002) → Foundational (T003) → stories.
- Priority order: US1 (T004-T006) → US2 (T007-T009) → US3 (T010-T011) → US4 (T012-T013) → Polish.
- US2 precedence depends on T003; all writes depend on T001/T002. `[P]` tasks touch distinct files.

## MVP scope

US1 (T001-T006) — the agent can persist a skill and it's discoverable — is the
minimal viable increment.
