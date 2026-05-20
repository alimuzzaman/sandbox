# Bug Reproduction

The core sandbox loop. Use whenever the user reports a bug, references a
FluentBoards card, attaches a stack trace, or asks "why is X broken."

The rule: **never fix what you haven't reproduced.** Reading code is not
reproduction. Simulating in your head is not reproduction. A failing call
against the running stack is.

---

## Standard loop

1. **Snapshot a clean baseline** if you don't have one yet:
   `./wp-sandbox snapshot pre-repro` — gives you a one-command rollback if
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
   sure about, `./wp-sandbox restore pre-repro` first.

6. **Report broken-then-fixed.** Both halves, side-by-side, in the response.
   Without the "before" half the user can't verify the fix actually changed
   anything.

---

## When headless repro is impossible

Some bugs only manifest in the editor, in real browser layout, or under
real user interaction (drag/drop, focus management, hover states). Say so
explicitly — never claim a fix is verified from source reading alone.

In that case:
- Mirror the user's data state via `db_query` / `wp_cli post update` so when
  the user reloads, they hit the same scenario.
- Snapshot first (`./wp-sandbox snapshot before-editor-test`) so the user
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
