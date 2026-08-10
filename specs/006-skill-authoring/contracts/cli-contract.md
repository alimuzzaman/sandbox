# Contract: MCP tools + CLI

## MCP tools (`mcp/wp-server/tools/skills.py`)

### `skill_write(title, description, body, scope="project|sandbox|personal", enable=true, on_conflict="fail|replace|rename", *, project_dir)`
- Writes `<scope-root>/skills/<slug>/SKILL.md` (slug = `sanitize_title(title)`; foldered, uppercase entry).
- Default scope: `project` when a plugin is focused, else `sandbox`.
- `on_conflict`: `fail` → `{ok:false, conflict, suggested_slug}`; `replace` → overwrite (same-scope user/project only; never a built-in); `rename` → auto-suffix.
- Returns `{ ok, slug, source, path, action: created|updated|renamed }`; conflicts
  return `{ok:false, code, error, slug, suggested_slug?}`.

### `skill_edit(slug, description?, body?, scope?, *, project_dir)`
- Updates description/body of the resolved skill (precedence-aware).

### `skill_delete(slug, scope, *, project_dir)`
- Removes the skill folder in `scope`. Refuses to delete a built-in unless scope=sandbox explicitly.

### `list_skills(*, project_dir)`
- `[{ slug, description, source, path }]` across all sources; precedence **project > personal > sandbox**; may flag shadowed duplicates.

`load_skill(slug, project_dir?)` is the on-demand body fetch. With a project it
returns the enabled precedence winner; its response includes `source` and `path`.

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
- Built-in slugs cannot be silently shadowed (must rename), and a sandbox
  built-in cannot be replaced.
- Disabled skills omitted from `list_skills`/catalog.
- A newly written/edited skill is discoverable without a restart (sources re-globbed).
