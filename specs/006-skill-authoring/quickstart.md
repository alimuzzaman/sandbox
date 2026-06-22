# Quickstart: In-Product Skill Authoring — live verification

Prerequisites: the MCP server / `sb` available; a focused plugin for the "project"
scope checks. All checks are live (constitution IV).

## 1. Agent persists a learned skill

- `skill_write(title="Repro flaky import", description="reproduce the X import bug", body="…", scope="project", project_dir=…)`
- Expect: `{ok, slug:"repro-flaky-import", path:"<plugin>/.claude/skills/repro-flaky-import/SKILL.md", action:"created"}` with valid frontmatter.

## 2. Discovery is description-keyed

- `list_skills(project_dir=…)` includes the new skill with its source + one-line description; bodies are NOT in the listing.
- `focus_get` enumerates it (project scope), no restart.

## 3. Load on match

- `load_skill("repro-flaky-import")` returns the full SKILL.md + parsed frontmatter.

## 4. Conflict paths

- Re-write the same title with `on_conflict="fail"` → returns conflict + a suggested free slug.
- `on_conflict="rename"` → writes `repro-flaky-import-2`.
- `on_conflict="replace"` on a same-scope skill → overwrites; on a built-in → refused.

## 5. Precedence

- Create the same slug in `sandbox` and `project`; `list_skills`/`load_skill` resolve the **project** one as the winner (project > personal > sandbox).

## 6. Guards

- A flat single-file write attempt is rejected (foldered only).
- A write targeting a path outside the scope root is rejected (path jail).
- A disabled skill (`enable:false`) does not appear in `list_skills`.

## 7. skill-creator

- `load_skill("skill-creator")` returns guidance on frontmatter, description-as-trigger, the foldered layout rule, and the write→load→verify loop.
