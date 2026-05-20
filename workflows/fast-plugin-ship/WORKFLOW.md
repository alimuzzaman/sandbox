# Fast Plugin Ship Workflow

Use this workflow for WPDeveloper plugin work: bug fixes, features, QA fixes, and release prep.

The goal is to move from issue to verified fix to ready-to-ship with the smallest safe loop. Plugin-specific `CLAUDE.md` files and skills override this workflow when they give more precise instructions.

## Start Every Task

1. Run `focus_get`.
2. Read the focused plugin's `CLAUDE.md`, if present.
3. Check recent repo activity:
   - `git status --short`
   - `git log -10 --oneline`
4. Identify the task type:
   - bug fix
   - feature
   - QA/regression
   - release/package
   - docs/config/tooling

## Fast Loop

1. Reproduce or confirm the issue in the local WordPress stack when possible.
2. Locate the smallest owned surface.
3. Make the narrowest complete fix.
4. Run the smallest relevant verification first.
5. Broaden checks only when the changed area or risk requires it.
6. Report evidence, changed files, skipped checks, and remaining risk.

## Verification Ladder

Use the first matching checks, then add more only if needed.

### PHP-only

- `php -l <changed files>`
- Plugin PHPUnit tests if available.
- PHPCS if the repo uses it.

### JS/CSS/admin/block

- Package lint/type/build commands from `package.json`.
- Targeted unit tests if available.
- Browser smoke check for UI behavior.

### Gutenberg block output

- Verify old saved content does not break validation.
- Check PHP and JS output shape match.
- Add or update deprecation handling if markup changes.

### REST/API/database

- Verify with `wp_rest`, `wp_cli`, or `db_query`.
- Confirm permissions, nonce/auth, and failure cases.

### Release/package

- Production build.
- Zip/package command.
- Inspect zip contents.
- Verify version, changelog, and readme where applicable.

## Done Criteria

A task is not done until the agent reports:

- what changed
- files touched
- commands/tests run
- live evidence when applicable
- skipped checks and why
- whether docs or plugin instructions need updating
