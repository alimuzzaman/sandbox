# Feature History

Short retros from features shipped via `workflows/build-feature/WORKFLOW.md`.
One file per feature. The agent writes these after `STATUS: SHIPPED` when
the build surfaced something non-obvious worth saving for the team.

This folder is **tracked in git** — anything here is shared with the team
and serves as institutional memory for future feature work in the same
area.

## When to write a feature-history file

After completing a build-feature workflow, write a note here IF the
build surfaced any of:

- A cross-plugin runtime quirk that wasn't documented anywhere
- A BC trap that almost fired (and how it was averted)
- A helper / pattern that you wish had existed and improvised instead
- A reuse opportunity discovered mid-build that the agent missed in
  Phase 2's reuse audit (so the next feature doesn't miss it)
- A surprise from the focused plugin's own conventions (something
  the plugin's `CLAUDE.md` didn't warn about but should have)

If the build was unremarkable — spec went smoothly, plan held, no
surprises — skip the retro. Cluttering this folder with "nothing
happened" notes makes the useful entries harder to find.

## File naming

`<feature-slug>.md` — kebab-case, matches the feature title from
Phase 1, prefixed with the plugin slug if cross-plugin:

```
betterdocs-glossary-bulk-define.md
embedpress-loom-provider.md
sandbox-build-feature-workflow.md
```

## Suggested structure (not enforced)

```markdown
# <Feature title>

**Shipped:** <YYYY-MM-DD>
**Plugin(s):** <slug>, <slug-pro>
**Branch:** <fix/feat-slug>

## What we shipped (one-line)
<the SUMMARY from the SHIPPED block>

## What surprised us
<the non-obvious thing — be specific>

## What we'd do differently
<what should change in the workflow, plugin CLAUDE.md, or scaffolding skill>

## Reusable patterns / helpers identified
<anything future feature work should reach for>
```

## What does NOT go here

- Generic plugin-structure notes → go in `<plugin>/CLAUDE.md`
- Cross-plugin runtime quirks → go in `memory/plugin-behavior/`
- Per-bug repro state (machine-specific) → goes in `memory/repros/`
  (gitignored)
- The spec itself — that lived in Phase 1 of the build and the PR
  description; no need to duplicate it here
