# Feature Specification: In-Product Skill Authoring (Auto-Matched Playbooks)

**Feature Branch**: `feat/agent-tooling-specs`

**Created**: 2026-06-22

**Status**: Draft

**Input**: Novamira parity #4 — "In-product skill authoring + a skill that teaches the
agent to write skills. We have skills, but theirs are first-class, stored, and
auto-matched by description."

## Summary

The Sandbox already has read-only skills: `skills/<name>/SKILL.md` and
`workflows/<name>/WORKFLOW.md`, loaded on demand via the `load_skill` /
`load_workflow` MCP tools, plus per-plugin packs discovered through `focus_get`.
What's missing is the **write half**: the agent cannot *create or refine* a skill
when it learns something reusable, so hard-won knowledge evaporates at end of
session.

Port Novamira's skills model (minus the parts we already have):

1. **Agent-authored skills** — `skill_write` / `skill_edit` / `skill_delete` MCP
   tools (+ `./sb skill …` CLI) so the agent can persist a playbook it just
   validated, with conflict handling (fail / replace / rename).
2. **Description-keyed lazy discovery** — keep only `slug + description (+ source)`
   in baseline context; the agent loads the full body via `load_skill` **only when
   a description matches the task**. (We already inject the skill list in the MCP
   `instructions`; formalize it as the match key — Novamira's
   `Catalog\inject()` on the discover-abilities instructions.)
3. **A `skill-creator` skill** that teaches the agent how to author a good Sandbox
   skill (frontmatter, description-as-trigger, the write→load→iterate loop).
   Adapted from Anthropic's skill-creator, as Novamira did.
4. **Multi-source, priority-ordered registry** — Sandbox `skills/` (built-in),
   the focused plugin's `.claude/skills/` (project), and `~/.claude/skills/`
   (personal), unioned and de-duplicated, mirroring Novamira's pluggable
   `novamira_skill_lookup_sources` filter.

**Key divergence from Novamira:** they store user skills in a WP CPT
(`novamira_skill`). We stay **file-based** — skills are Markdown in a git repo, so
they're reviewable, diffable, shippable, and team-shared (the whole point of our
`skills/` + per-plugin `.claude/skills/` layout). Agent writes go to files, not a
DB.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Agent persists a learned playbook (Priority: P1)

After working out a non-obvious repro/fix sequence, the agent saves it as a skill
so the next session starts from it.

**Acceptance**:
1. **When** the agent calls `skill_write(title, description, body, scope,
   on_conflict)`, **Then** a `skills/<slug>/SKILL.md` (or the chosen scope's dir)
   is written with valid frontmatter (`name`, `description`) and the slug is
   `sanitize_title(title)`.
2. **Given** a slug collision, **When** `on_conflict="rename"`, **Then** it writes
   `<slug>-2`; `"replace"` overwrites only a same-scope user/project skill;
   `"fail"` returns the conflict + a suggested free slug. (Novamira
   `resolve_conflict` / `find_free_suffix`.)
3. A built-in Sandbox skill slug cannot be silently shadowed
   (`exists_in_external_source` guard) — must rename.

### User Story 2 — Description drives matching (Priority: P1)

The agent loads a skill body only when its description matches the task, not
eagerly.

**Acceptance**:
1. The MCP `instructions` (and a `list_skills` tool) expose every skill as
   `- <slug> (<source>) — <description>`; bodies are **not** in baseline context.
2. The agent calls `load_skill(slug)` on a match and gets the full SKILL.md +
   parsed frontmatter (existing behavior, now the documented contract).

### User Story 3 — Scope selection (Priority: P2)

The agent (or dev) chooses where a new skill lives.

**Acceptance**:
1. `scope ∈ {sandbox, project, personal}` → writes to `skills/`,
   `<focused-plugin>/.claude/skills/`, or `~/.claude/skills/` respectively.
   Default `project` when a plugin is focused, else `sandbox`.
2. `focus_get` continues to enumerate project + personal skills; new ones appear
   without restart (re-globbed each call).

### User Story 4 — skill-creator (Priority: P2)

A built-in skill teaches the agent the house style.

**Acceptance**:
1. `skills/skill-creator/SKILL.md` exists; `load_skill("skill-creator")` returns
   guidance on frontmatter, description-as-trigger, foldered layout (CLAUDE.md
   rule: never flat `skills/foo.md`), and the write→load→verify loop.

## Requirements

- **FR-1** `skill_write(title, description, body, *, scope="project|sandbox|personal",
  enable=true, on_conflict="fail|replace|rename", project_dir)` MCP tool → writes
  a foldered `SKILL.md`; returns `{ok, slug, path, action: created|updated|renamed}`.
- **FR-2** `skill_edit(slug, *, description?, body?, project_dir)` and
  `skill_delete(slug, scope, project_dir)`.
- **FR-3** `list_skills(project_dir)` → `[{slug, description, source, path}]`
  across all sources, priority-ordered (personal < project < sandbox built-ins
  by Novamira's model — but document and pick our precedence in plan.md).
- **FR-4** Reuse the existing frontmatter parser path used by `load_skill`; if
  none is exposed, factor Novamira-style lenient parsing (`---` fence, `key:
  value`, recognizes `name|description|enable`; `stripcslashes` on body to undo
  AI clients' double-escaped `\n`).
- **FR-5** Foldered output only (`skills/<slug>/SKILL.md`) — enforce CLAUDE.md's
  "never create flat `skills/foo.md`" rule in the writer.
- **FR-6** CLI parity: `./sb skill list|write|edit|delete|show`.
- **FR-7** Built-in `skill-creator` skill shipped in `skills/`.
- **FR-8** Writer refuses to escape the chosen scope's root (path jail), never
  writes outside `skills/`, the focused plugin's `.claude/skills/`, or
  `~/.claude/skills/`.

## Design notes

- **Description-as-trigger** is the load-bearing idea: keep baseline context cheap
  (slug + one-line description), pull the body only on match. We already do the
  injection; this spec makes authoring + the match contract first-class.
- **No CPT, no DB.** Files in git. This also means `enable` is advisory metadata
  in frontmatter, not a stored flag — a disabled skill is simply omitted from the
  injected catalog.
- **Workflows too?** Same pattern could grow `workflow_write`; keep v1 to skills,
  note workflows as a follow-up.
- This composes with spec 003: if the in-instance Abilities layer ships, the same
  skill registry can *also* be exposed as `skill-get` abilities to external MCP
  clients. Not required for v1.

## Integration points

- MCP: new tools in a `tools/skills.py` (or extend `tools/context.py` which hosts
  `load_skill`); reuse `focus_get`'s source discovery.
- CLI: `skill` command module under `sandbox/commands/`, self-registering.
- Docs: CLAUDE.md "Adding a new skill or workflow" section (note agents can now do
  it), MCP-surface table, MCP `instructions`.

## Tasks

1. Factor a shared skill-source resolver (sandbox / project / personal) + parser.
2. `skill_write` / `skill_edit` / `skill_delete` / `list_skills` MCP tools with
   slug/conflict/path-jail logic.
3. `./sb skill …` CLI.
4. Author the built-in `skill-creator` SKILL.md.
5. Formalize the description-keyed catalog in the MCP `instructions`.
6. Live verification: agent writes a skill, it appears in `list_skills` +
   `focus_get`, loads by slug; rename + replace + fail conflict paths.
7. Docs.
