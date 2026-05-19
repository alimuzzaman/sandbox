# Workflow: Ship a fix

Run by the **developer** skill after a repro doc exists.

## Inputs

- Path to repro doc (`memory/repros/<ticket-id>.md`)
- FluentBoards card ID (if applicable)

## Steps

1. **Read the repro.** Run it once exactly as written. Confirm the failure.
2. **Branch.** In the plugin's host repo (e.g. `embedpress-pro/`), create
   `fix/fbs-<id>-<slug>`. The runtime symlink will follow.
3. **Fix.** Edit files in the host repo. Keep diffs minimal — bug fixes only,
   no surrounding cleanup.
4. **Verify.** Re-run the repro steps. `tail_log` should be clean.
5. **Regression check.** If the change touches a hook or shared helper:
   - grep the host repo and sibling plugins for callers
   - re-activate the related plugins and run their smoke flows
6. **Commit** when the user approves. Use the plugin repo's existing commit
   style.
7. **Update the card** (FluentBoards via `fluentboards` skill, or Zoobbe):
   - move to "Done & Fixed"
   - add a comment with the branch name + commit hash + summary

## Rules

- Never `git push` without explicit user approval.
- Never amend a published commit.
- If the fix touches a Gutenberg block's `save()`, add a `deprecated` entry.
- If the fix changes a public hook signature, document it in
  `memory/plugin-behavior/<plugin>.md`.

## Done criteria

- Repro no longer reproduces.
- No new errors in `tail_log` after running smoke flows on sibling plugins.
- Card is in the right column with the branch comment.
