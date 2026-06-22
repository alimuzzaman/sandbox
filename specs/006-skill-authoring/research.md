# Research: In-Product Skill Authoring

## Decision: file-based skills, not a database/CPT

- **Decision**: Authored skills are foldered Markdown (`<scope>/skills/<slug>/SKILL.md`), not DB rows.
- **Rationale**: skills are git-reviewable, diffable, shippable, and team-shared — the whole point of the existing `skills/` + per-plugin `.claude/skills/` layout. (Novamira uses a WP CPT; we deliberately diverge.)
- **Alternatives considered**: a CPT/option store — opaque, not reviewable, lost on instance teardown.

## Decision: precedence project > personal > sandbox

- **Decision**: On a slug collision, the focused-plugin skill wins over the dev's personal `~/.claude` skill, which wins over the generic sandbox built-in. (Clarification 2026-06-22.)
- **Rationale**: most-specific-wins — plugin-specific knowledge is most relevant for that plugin's work; personal overrides shared built-ins; built-ins are the generic fallback.
- **Alternatives considered**: personal-first (a dev's stale skill would mask a curated plugin skill); sandbox-first/Novamira's built-ins-authoritative (blocks per-project specialization).

## Decision: description-as-trigger, lazy body loading

- **Decision**: Baseline context carries only `slug + one-line description + source` per skill; the full body loads on demand via `load_skill(slug)` only when a description matches the task.
- **Rationale**: keeps baseline cheap and the catalog scalable; the description is the match key (mirrors Novamira's catalog-inject + skill-get split, and how the MCP `instructions` already list skills).

## Decision: extend the existing parser (not just reuse); lenient frontmatter

- **Decision**: The parser `_parse_skill_metadata` lives in `mcp/wp-server/app.py` (not `context.py`) and currently recognizes only `name`/`description`. **Extend it to add `enable` (default true)** and share it via the new `tools/skills.py`. Un-escaping of `\n` applies ONLY when a body has no real newlines but contains literal `\n` sequences (a wholly-escaped blob from some clients) — never to code-fence content in a normal body (analysis C11).
- **Rationale**: one parser for read + write; `enable` is required by FR-009; the narrow un-escape avoids corrupting legitimate `\n` in code blocks.
- **Note (analysis C5)**: existing built-in `skills/*/SKILL.md` have no frontmatter (they open with `# H1`), so they list with empty descriptions today; a task retrofits `name`/`description` frontmatter into them.

## Decision: catalog mechanism (analysis C3)

- **Decision**: The agent-facing catalog is the `list_skills` tool (live, re-globs per call). The MCP server instructions (`SANDBOX_INSTRUCTIONS`, static, built once at process start) carry a startup snapshot + a pointer to call `list_skills`/`load_skill`. New skills authored mid-session appear in `list_skills`/`focus_get` immediately (no restart); only the static instructions snapshot is stale until restart.
- **Rationale**: avoids depending on dynamic server-instructions (which FastMCP fixes at startup and which gotcha #4 ties to a restart), while still keeping baseline context cheap.

## Decision: `sanitize_title` is a new Python helper (analysis C6)

- **Decision**: There is no host-side `sanitize_title` (it's a WP PHP function). Write a small Python slugifier: lowercase, spaces/underscores→hyphens, strip non-`[a-z0-9-]`, collapse repeats, trim hyphens.
- **Rationale**: deterministic slugs from titles without a WP round-trip.

## Decision: slug + conflict handling

- **Decision**: slug = `sanitize_title(title)`; `on_conflict ∈ {fail (default, returns a suggested free slug), replace (same-scope user/project only), rename (auto-suffix `-2`,`-3`…)}`; a built-in sandbox slug cannot be silently shadowed.
- **Rationale**: predictable authoring without clobbering curated built-ins.

## Decision: foldered output + path jail

- **Decision**: Always write `<scope>/skills/<slug>/SKILL.md` (uppercase entry, foldered); refuse flat single-file skills; the writer cannot escape the chosen scope root.
- **Rationale**: enforces the CLAUDE.md "never flat `skills/foo.md`" rule and prevents path traversal.

## Open questions

None — precedence resolved (clarification); workflows-authoring deferred to a later spec.
