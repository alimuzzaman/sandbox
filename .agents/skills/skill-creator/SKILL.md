---
name: skill-creator
description: Author a new Sandbox skill — when the user asks to create/add a skill, capture a reusable playbook, or save a procedure for next time. Teaches the house format and the write→load→verify loop.
---

# skill-creator

Use this when you've worked out a reusable procedure and want to persist it as a
Sandbox skill, or when the user says "create a skill / save this as a skill".

## What a skill is

A foldered Markdown playbook: `<scope>/skills/<slug>/SKILL.md` (never a flat
`skills/foo.md`). The **frontmatter `description` is the trigger** — it's the only
thing in baseline context; the body loads on demand when the description matches the
task.

```markdown
---
name: Human Readable Title
description: One line — what it does AND when to use it (this is the match key).
---

# Title

Body: the actual playbook. Steps, gotchas, commands.
```

## Authoring (use the tool, don't hand-create)

```
./sb skill write --title "Repro Flaky Import" \
                 --desc "reproduce the X import bug, then verify the fix" \
                 --scope project --file body.md       # or --file - for stdin
```

- **scope**: `project` (the focused plugin's `.Codex/skills/` — default when in a
  project), `personal` (`~/.Codex/skills/`), or `sandbox` (the shared `skills/`).
- **slug** is derived from the title (lowercased, hyphenated).
- **conflicts**: `--on-conflict fail` (default; suggests a free slug), `replace`
  (same-scope only), or `rename` (auto-suffix). A built-in sandbox slug can't be
  silently shadowed — rename instead.
- Precedence when the same slug exists in several scopes: **project > personal >
  sandbox** (most-specific wins).

## Verify (don't declare done from writing it)

```
./sb skill list           # confirm it appears with the right scope + description
./sb skill show <slug>    # confirm the body + frontmatter
```

The agent loads a skill's full body on a description match via `load_skill(<slug>)`.

## What to include / exclude

- Include: the trigger (in `description`), the concrete steps, real gotchas, exact
  commands/paths. Keep it operational.
- Exclude: backstory, anything derivable from the code, one-off details that won't
  recur.
