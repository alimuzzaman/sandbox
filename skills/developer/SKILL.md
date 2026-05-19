---
name: sandbox-developer
description: Develop and fix WordPress plugin code inside the Sandbox runtime.
---

# Developer role

You modify plugin code in `runtime/plugins/<plugin>` (symlinked to the real
repo) and verify changes inside the live WordPress runtime.

## Tools (from wp-mcp)

- `wp_cli` — anything wp-cli can do
- `tail_log` — read `wp-content/debug.log`
- `db_query` — read-only SQL
- `wp_rest` — call the REST API
- `activate_plugin` / `deactivate_plugin`
- `import_content` — load WXR seeds

Plus filesystem (Read/Edit/Write) on the host.

## Loop

1. **Reproduce.** Activate the plugin, trigger the bug via `wp_cli` or REST,
   read `tail_log`. Do not start fixing until you've seen the failure.
2. **Locate.** Identify the file:line in the symlinked plugin repo. The
   path on the host is `runtime/plugins/<slug>/...`.
3. **Fix.** Edit the file directly. PHP changes are live (no rebuild needed).
   JS/CSS may need `npm run build` inside the plugin repo.
4. **Verify.** Re-run the same reproduction. Confirm `tail_log` is clean.
5. **Regression scan.** If the change touches a hook or shared helper, run a
   quick check of sibling plugins that use it.
6. **Hand off.** Commit *only* when the user says so. Default to leaving
   changes in the working tree.

## Rules

- Never commit without explicit user approval.
- Never modify `runtime/wp/` core files. Only `runtime/plugins/<slug>`.
- If the bug came from a FluentBoards ticket, follow `workflows/ship-fix/WORKFLOW.md`
  and update the card at the end using the core `skills/fluentboards/SKILL.md` skill.
- Document non-obvious fixes by appending a note to `memory/plugin-behavior/`.
