# Contract: MCP tools + CLI

## MCP tools (`mcp/wp-server/tools/skills.py`)

### `skill_write(title, description, body, scope="project|sandbox|personal", enable=true, on_conflict="fail|replace|rename", *, project_dir)`
- Writes `<scope-root>/skills/<slug>/SKILL.md` (slug = `sanitize_title(title)`; foldered, uppercase entry).
- Default scope: `project` when a plugin is focused, else `sandbox`.
- `on_conflict`: `fail` → `{ok:false, conflict, suggested_slug}`; `replace` → overwrite (same-scope user/project only; never a built-in); `rename` → auto-suffix.
- Returns `{ ok, slug, path, action: created|updated|renamed }`.

### `skill_edit(slug, description?, body?, scope?, *, project_dir)`
- Updates description/body of the resolved skill (precedence-aware).

### `skill_delete(slug, scope, *, project_dir)`
- Removes the skill folder in `scope`. Refuses to delete a built-in unless scope=sandbox explicitly.

### `list_skills(*, project_dir)`
- `[{ slug, description, source, path }]` across all sources; precedence **project > personal > sandbox**; may flag shadowed duplicates.

(Existing `load_skill(slug)` is the on-demand body fetch — unchanged; documented here as the match→load step.)

## CLI (`sandbox/commands/skill.py`)

- `./sb skill list`
- `./sb skill write --title … --desc … [--scope …] [--on-conflict …]` (body via `--file`/stdin)
- `./sb skill edit <slug> [--desc …] [--file …]`
- `./sb skill delete <slug> [--scope …]`
- `./sb skill show <slug>`

All path-jailed to the scope roots (repo `skills/`, focused plugin's `.claude/skills/`,
`~/.claude/skills/`). New MCP tools ⇒ Claude Code restart (gotcha #4).

## Guarantees

- Foldered output only; flat single-file skills rejected.
- Built-in slugs cannot be silently shadowed (must rename).
- Disabled skills omitted from `list_skills`/catalog.
- A newly written/edited skill is discoverable without a restart (sources re-globbed).
