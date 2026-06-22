# Implementation Plan: In-Product Skill Authoring (Auto-Matched Playbooks)

**Branch**: `feat/agent-tooling-specs` | **Date**: 2026-06-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/006-skill-authoring/spec.md`

## Summary

Add the *write half* to the Sandbox skill system: MCP tools + `./sb skill …` for the
agent to create/edit/delete foldered Markdown skills, with description-keyed lazy
discovery (slug + one-line description in baseline context, body loaded on match),
multi-source precedence **project > personal > sandbox**, a built-in `skill-creator`
skill, and path-jailed foldered writes. Skills stay file-based (git-reviewable) — no
database.

## Technical Context

**Language/Version**: Python 3 (`sandbox/` package + `mcp/wp-server/`); Markdown skills with YAML-ish frontmatter.

**Primary Dependencies**: the existing skill plumbing lives in `mcp/wp-server/app.py` (`_parse_skill_metadata`, `_list_sandbox_skills`, `SANDBOX_SKILLS_DIR`, `SANDBOX_INSTRUCTIONS`); `load_skill`/`focus_get` are in `tools/context.py` and import those. The shared resolver/parser + the new write tools are added in a new `tools/skills.py` reusing the `app.py` helpers (parser **extended** to recognize `enable`). Note: `SANDBOX_INSTRUCTIONS` is a static string built once at process start (analysis C3); the live catalog is `list_skills`. No `sanitize_title` exists — a new Python slug helper is required (analysis C6).

**Storage**: files only — `skills/<slug>/SKILL.md` (sandbox), `<focused-plugin>/.claude/skills/<slug>/SKILL.md` (project), `~/.claude/skills/<slug>/SKILL.md` (personal). No DB.

**Testing**: live-stack verification (constitution IV) — agent writes a skill, it appears in `list_skills` + `focus_get`, loads by slug; conflict paths exercised.

**Target Platform**: macOS/Linux; the MCP server + `sb`.

**Project Type**: host CLI/MCP extension (single-entry `sb` + `sandbox/` package + `mcp/wp-server/`).

**Performance Goals**: catalog stays cheap — only slug + description + source in baseline context; body fetched on demand.

**Constraints**: foldered output only (never flat `skills/foo.md`); writer path-jailed to the chosen scope root; no DB; disabled skills omitted from the catalog.

**Scale/Scope**: 4 MCP tools (`skill_write/edit/delete`, `list_skills`) + `./sb skill` CLI + 1 built-in `skill-creator` skill + a shared source resolver/parser.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Per-Project Only** — PASS. Scope resolution keys off the focused project (`focus_get`); "project" scope writes into that plugin's `.claude/skills/`. No global instance state introduced.
- **II. Registry SoT** — N/A for skills (no instance routing); project/personal/sandbox roots resolved via `focus_get` + known dotfile paths.
- **III. Single Entry, Modular** — PASS. New `sandbox/commands/skill.py` + MCP tools in `mcp/wp-server/tools/`; `sb` stays single-entry.
- **IV. Live-Stack Verification** — PASS. quickstart writes/loads a real skill and checks discovery + conflicts.
- **V. Idempotency & Docs-With-Code** — PASS. Writes are deterministic/idempotent per slug; CLAUDE.md "Adding a skill" section + MCP table land with code.
- **VI. Parity Before Removal** — PASS. Additive over the existing read-only loader; nothing removed.
- **Boundaries / Secrets** — PASS. Writes confined to skill roots (repo `skills/`, the focused plugin's `.claude/skills/`, `~/.claude/skills/`); no secrets.

No violations — proceed.

## Project Structure

### Documentation (this feature)

```text
specs/006-skill-authoring/
├── plan.md
├── research.md          # file-based vs CPT, precedence, description-as-trigger, parser reuse
├── data-model.md        # Skill, Source, Catalog entities
├── quickstart.md        # write → discover → load → conflict paths
├── contracts/
│   └── cli-contract.md  # skill_write/edit/delete, list_skills + ./sb skill
└── tasks.md
```

### Source Code (repository root)

```text
mcp/wp-server/
├── app.py               # extend _parse_skill_metadata (add `enable`); shared helpers live here
└── tools/
    ├── skills.py        # NEW: skill_write, skill_edit, skill_delete, list_skills + shared resolver/slug helper
    └── context.py       # existing load_skill/focus_get (imports app.py helpers)
sandbox/commands/
└── skill.py             # NEW: ./sb skill list|write|edit|delete|show
skills/
├── skill-creator/SKILL.md   # NEW built-in skill
└── */SKILL.md               # retrofit existing built-ins with name/description frontmatter
```

**Structure Decision**: Host-side, file-based. Reuses the existing loader/parser;
adds the write surface + a shared source resolver shared by MCP and CLI.

## Complexity Tracking

No constitution violations — none.

## Phase 0 — Research

See [research.md](./research.md): file-based (not CPT) rationale, the
project>personal>sandbox precedence, description-as-trigger lazy loading, parser
reuse + lenient frontmatter, and slug/conflict handling.

## Phase 1 — Design & Contracts

- [data-model.md](./data-model.md): Skill, Source, Catalog.
- [contracts/cli-contract.md](./contracts/cli-contract.md): tool + CLI signatures.
- [quickstart.md](./quickstart.md): write → discover → load → rename/replace/fail.
- Agent context: SPECKIT block points at this plan.

## Phase 2 — Tasks

Generated by `/speckit-tasks`.
