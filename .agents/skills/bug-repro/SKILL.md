---
name: bug-repro
description: Reproduce a Sandbox or WordPress bug safely with a baseline snapshot, live evidence, and a repeatable report.
---

# Bug Reproduction

The core sandbox loop. Use whenever the user reports a bug, references a
FluentBoards card, attaches a stack trace, or asks "why is X broken."

The rule: **never fix what you haven't reproduced.** Reading code is not
reproduction. Simulating in your head is not reproduction. A failing call
against the running stack is.

---

## Standard loop

1. **Snapshot a clean baseline** if you don't have one yet:
   `./sb snapshot pre-repro` — gives you a one-command rollback if
   the repro mutates state in surprising ways.

2. **Capture the broken state.** Pick the shortest tool that triggers the
   reported behavior:

   | Bug surface | First-choice tool |
   |---|---|
   | REST endpoint returns wrong shape | `wp_rest` |
   | Shortcode / oEmbed output wrong | `wp_cli eval-file` or render a draft post via `wp_rest` then fetch the rendered HTML |
   | Admin save flow | `wp_rest` POST against the relevant endpoint with the exact payload from devtools |
   | Block/widget editor bug | Cannot reproduce headless — say so, get the user to open the editor and screenshot, then mirror their state via `db_query` + `wp_cli post update` |
   | PHP fatal / warning | Reproduce the trigger then `tail_log` for the exact stack |
   | Cron / scheduled task | `wp_cli cron event run <hook>` |
   | Email never sent | `wp_cli eval 'wp_mail(...)'` then `mail_list` |
   | DB-shaped wrong | `db_query` with `mutate:false` against the suspect table |

3. **Save the broken output** verbatim. JSON response, HTML snippet, log
   line — paste into the response or into `memory/repros/<card>-<slug>.md`.
   This is the "before" half of the evidence pair the user wants in reports.

4. **Fix in the plugin source.** Plugin code lives under `${plugins_home}/` and
   is bind-mounted live — edits take effect on the next request, no rebuild.

5. **Re-run the exact same trigger.** Same `wp_rest` call, same shortcode,
   same cron hook. Compare to step 3. If you ran any DB writes you weren't
   sure about, `./sb restore pre-repro` first.

6. **Report broken-then-fixed.** Both halves, side-by-side, in the response.
   Without the "before" half the user can't verify the fix actually changed
   anything.

---

## Matching the reported stack — minimally

A bug report lists everything (WP, PHP, theme, 30 plugins). Copy only what the
bug plausibly depends on. Every extra pin is a variable you now own forever.

- **`phpVersion`** — pin it when the report implicates PHP (fatals, deprecations,
  type errors, 8.x behavior). Cheap and reversible.
- **`wpVersion`** — leave it OUT by default. The pin is **exact**, not a line:
  `"7.0"` installs 7.0.0 and sits there while 7.0.4 ships. Pin it only to
  reproduce a version-specific report or bisect a regression, and then write the
  FULL `X.Y.Z` you actually mean. "The reporter said WP 7.0" is not a reason to
  pin — it is a reason to test on current WordPress unless the bug disappears
  there.
- **Plugins** — add the ones in the reported interaction, not the whole list.

Drop a pin the moment it stops earning its place, then
`./sb apply --project-dir <DIR>`: apply reconciles WordPress core in place
(pin → that exact build, no pin → the current release, both followed by
`wp core update-db`), so unpinning fixes the LIVE site, not just future ones.
Say in the report which versions the repro actually ran on.

---

## When headless repro is impossible

Some bugs only manifest in the editor, in real browser layout, or under
real user interaction (drag/drop, focus management, hover states). Say so
explicitly — never claim a fix is verified from source reading alone.

In that case:
- Mirror the user's data state via `db_query` / `wp_cli post update` so when
  the user reloads, they hit the same scenario.
- Snapshot first (`./sb snapshot before-editor-test`) so the user
  can hand the state back to you cleanly if needed.
- Report: "verified by code inspection; needs browser verification on your
  end" — never claim "verified" for UI work without an actual browser run.

---

## Anti-patterns

- "I read the code and the fix should be X" — not a repro.
- Fixing without capturing the broken output first — you lose the proof.
- Mutating DB or settings without snapshotting — debugging a fix becomes
  debugging two changes at once.
- Restoring a snapshot mid-loop without telling the user — they may have
  added test data that's now gone.
