# Data Model: In-Product Skill Authoring

Files only — no database.

## Skill

A foldered Markdown playbook: `<scope-root>/skills/<slug>/SKILL.md`.

| Field | Source | Description |
|-------|--------|-------------|
| slug | `sanitize_title(title)` | folder name + identity; uppercase entry file `SKILL.md` |
| title | frontmatter `name` | human title |
| description | frontmatter `description` | one-line **match key** surfaced in the catalog |
| enable | frontmatter `enable` (default true) | when false, omitted from the catalog |
| body | Markdown after frontmatter | the full playbook, loaded on demand |

## Source

A location skills come from, ordered by precedence (collision resolution):

| Source | Root | Precedence |
|--------|------|-----------|
| project | `<focused-plugin>/.claude/skills/` | highest |
| personal | `~/.claude/skills/` | middle |
| sandbox | repo `skills/` (built-ins) | lowest |

On a slug present in multiple sources, the highest-precedence one is the winner
surfaced/loaded; `list_skills` may also report shadowed duplicates with their source.

## Catalog

The lightweight discovery list: `list_skills` returns `[{ slug, description, source,
path }]` for all enabled skills across sources; a startup snapshot of the same is
embedded in the MCP server instructions (static, refreshed on restart — analysis C3).
Bodies are NOT in the catalog; `load_skill(slug)` fetches the winning skill's full
SKILL.md + parsed frontmatter on demand. The parser must recognize `enable` (default
true) so disabled skills are excluded (analysis C1).

## Operations + state

- create → write a new foldered SKILL.md (slug from title); collision → fail/replace/rename.
- edit → update description/body of an existing skill.
- delete → remove the skill folder in a given scope.
- A disabled skill exists on disk but is excluded from the catalog.
