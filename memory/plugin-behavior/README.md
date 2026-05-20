# Plugin Behavior Notes

Short, durable findings about how WPDeveloper plugins (and the third-party
plugins our customers run alongside them) actually behave at runtime.

This folder is **tracked in git** — anything here is shared with the team.

## What goes here

- Cross-plugin interactions that aren't obvious from either plugin's code
  (e.g. "FluentSMTP overrides `wp_mail` in a way that breaks Mailpit
  capture unless you deactivate it").
- Non-obvious runtime behavior of a single plugin that future debugging
  sessions would otherwise have to re-discover.
- Constraints imposed by hosts / WP versions / PHP versions we've actually
  hit (not theoretical ones).

## What does NOT go here

- How a plugin is structured → goes in `<plugin>/CLAUDE.md`.
- Per-feature deep-dives → goes in `<plugin>/.claude/skills/<feature>/SKILL.md`.
- Repro state for a specific bug → goes in `memory/repros/` (gitignored).
- User-specific preferences → user's auto-memory.
- "How we fixed bug X" → git history is authoritative.

## File shape

One file per finding, kebab-cased, descriptive name:

```
memory/plugin-behavior/
├── fluentsmtp-overrides-wp-mail.md
├── elementor-pro-license-blocks-headless-tests.md
└── wp-rocket-cache-eats-rest-responses.md
```

Each file: 5-20 lines max. What you observed, where it surfaced, the
workaround. No screenshots, no logs — link those instead.
