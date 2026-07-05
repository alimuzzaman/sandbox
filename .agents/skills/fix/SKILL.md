# fix — one-pass bug-fix loop

**Load this skill whenever the user asks you to fix a bug, debug an
issue, or "make X work" inside a plugin running in the Sandbox.** Skip it
for net-new features (use the slicing rule), trivial one-liners, or
pure refactors.

The skill exists for one reason: to kill the slow
fix → test → fix → test → fix → test cycle that wastes 20+ minutes per
bug. Replace it with a single deliberate pass.

---

## Hard contract

When this skill is active you commit to **one pass**: reproduce, then
fix everything, then verify everything, then report. You do not edit a
file, run a test, find the next breakage, edit, test, repeat. That loop
is what we are eliminating.

You finish with one of two outcomes:

**FIXED** — bug is reproduced, fixed, and verified live. Report:

```
STATUS: FIXED
SUMMARY: <one-line root cause + the fix>
FILES:
  <path>: <one-line per file>
EVIDENCE:
  before: <MCP command + observed broken output, trimmed>
  after:  <same MCP command + observed fixed output, trimmed>
```
(Diff is already in the working tree — the user reads it directly.)

**You may not emit STATUS: FIXED unless BOTH of these are true:**
1. `EVIDENCE.before` was produced by a real MCP call against the
   running stack **before your first Edit**.
2. `EVIDENCE.after` was produced by re-running that same MCP call
   **after your last Edit**, and shows the bug is gone.

A FIXED block missing either is auto-invalidated. If you find
yourself about to emit FIXED without both captures, stop and go back
to step 1 — you skipped the contract. "I read the code and the fix
looks right" is not evidence. A passing PHP lint is not evidence.
A grep that doesn't match the old pattern anymore is not evidence.
Only a live MCP call counts.

**BLOCKED** — you could not finish. Report:

```
STATUS: BLOCKED
REASON: <one sentence on what stopped you>
PROGRESS: <what's complete>
NEXT: <decision the user needs to make>
```

No third option. No "I think this should work." No "you may want to
test." Either you verified it live, or you are BLOCKED.

---

## The one-pass loop (in order, no interleaving)

1. **Reproduce live.** Use sandbox MCP tools (`wp_cli`, `wp_rest`,
   `db_query`, `tail_log`, `wp_exec`, `visit`) to trigger the broken
   behavior against the running stack at `http://localhost:8188`.
   Capture the exact output as `EVIDENCE.before`.

   **If repro requires a data condition the sandbox doesn't have by
   default — a specific `.mo` translation, a particular DB row, a
   license state, a seed dataset, a third-party plugin — your first
   move is to PROVISION that condition, not to give up.** Use the
   right tool: `fs_write` for a `.mo` / config / fixture file,
   `db_query` (with `mutate=true`) for missing rows, `wp_cli` to
   install a plugin or activate a state, `import_content` for WXR
   seeds, `wp_exec` for anything else. Snapshot first if the
   provisioning is destructive. Then re-attempt repro.

   Only return `STATUS: BLOCKED` if provisioning is genuinely
   infeasible (e.g. the bug requires a paid third-party plugin you
   don't have, or a customer's specific data shape that can't be
   inferred). In that case, name the missing condition precisely in
   `NEXT:` so the user knows exactly what to supply — don't bail with
   "couldn't reproduce." A diagnosis without a live repro is never a
   `STATUS: FIXED`, no matter how confident the code reading feels.

2. **Map every call site in one read pass.** Grep the focused plugin
   for every function, hook, block name, REST route, CSS class, JS
   handle, template partial involved. **If the focused plugin has a
   `-pro` sibling repo** (`embedpress` ↔ `embedpress-pro`, `betterdocs`
   ↔ `betterdocs-pro`, etc.), grep BOTH repos at this step — Pro
   commonly overrides free's rewrite rules, hooks, REST handlers, and
   filters, and a fix that ignores Pro will pass one verification round
   and fail the next when Pro's override re-asserts the broken behavior.
   Read all hits **before** you edit anything. If you find yourself
   opening a file mid-edit because something else broke, you are
   already losing — stop, back up, read the rest.

3. **Form the complete edit plan.** Every file, every line. If the
   change touches a Gutenberg block `save()`, plan the `deprecated[]`
   entry. If it touches a REST route, plan nonce + capability. If it
   touches a DB schema, plan the migration AND the upgrade-path test.
   Do not skip the deprecation/migration because the bug report didn't
   mention it.

4. **Apply all edits in one pass.** Edit/Write every planned file. No
   verification between edits.

5. **Verify live, end-to-end.** Re-run the exact reproduction from
   step 1 — confirm the broken output is now the expected output. Then
   verify adjacent surfaces you might have broken: other blocks
   sharing the changed code, the admin page that loads the changed
   asset, the REST route that calls the changed function. `tail_log`
   to catch PHP warnings/notices triggered by your change.

6. **Report FIXED or BLOCKED.** Stop.

---

## Tool selection — pick the lightest tool that captures the bug live

Headless Chromium (`visit`) is the heaviest tool — seconds to load, noisy
output. Don't reach for it unless the bug *requires* a real browser. For
everything else, the lightweight MCP tools give faster, cleaner evidence.

| Bug surface | Right repro / verify tool |
|---|---|
| PHP function / class behavior | `wp_cli eval` or `wp_rest` exercising it; `tail_log` clean |
| REST endpoint | `wp_rest` request; assert status + body shape |
| DB schema / migration | Snapshot → run migration → `db_query DESCRIBE`; test fresh AND upgraded data |
| SQL behavior / data query | `db_query` |
| PHP fatal in log | trigger via `wp_cli` / `wp_rest`, then `tail_log` |
| Cron / scheduled task | `wp_cli cron event run <hook>`; assert side effect |
| Email | trigger the send via `wp_cli` / `wp_rest`, inspect via `mail_list` / `mail_get` |
| **Rendered page / Gutenberg / Elementor / JS / asset loading** | `visit` (headless Chromium) — the only tool that captures browser-runtime evidence |
| Admin page (React-rendered) | `visit` on the `/wp-admin/<page>` URL (auto-login is on) |

`visit` has **auto-login** for `/wp-admin/` URLs — credentials come from
`WP_ADMIN_USER` / `WP_ADMIN_PASSWORD` in the MCP env. You have full admin
access against the sandbox WP. Don't ask the user for credentials.

If you cannot run the right verification (e.g. the bug needs a real
browser but `visit`'s tools venv isn't built, OR the bug needs a paid
third-party plugin you don't have), return `STATUS: BLOCKED` with the
precise missing piece in `NEXT:`. Do NOT downgrade to "ran PHP lint,
looks fine" or "wp_exec eval'd the format string, same error class."
Synthetic in-isolation reproductions of a failure *class* are not
evidence the *actual* bug fired against the *actual* code path.

---

## Tooling rules

- Sandbox MCP tools, not raw bash, for anything WP-touching. `wp_cli`
  not `docker compose exec wp wp …`. `wp_rest` not `curl
  localhost:8188`. `db_query` not `mysql -h`. `tail_log` not `docker
  logs`. Raw bash is fine for `git diff`/`git status`/`grep`/`find`.
- Snapshot before destructive DB work: `./sb snapshot fix-<short-name>`
  before mass UPDATE/DELETE, migrations, or license-flow changes.
- Never edit `runtime/wp/` core files or `vendor/`. Both get clobbered.
- Editor-dependent authoring goes through wp-pilot (real headless
  wp-admin), not hand-authored PHP — see `skills/wp-pilot/SKILL.md`.
- Never commit, push, tag, or open a PR. The loop ends at "verified
  fixed in the working tree." The human decides what to do next.

---

## Anti-patterns that invalidate a FIXED claim

- Reporting FIXED without running the live reproduction a second time
  after the edits.
- Reporting FIXED based on "code reading + simulation" instead of a
  real MCP call against the running stack.
- Editing one file → running one test → editing the next file. That is
  exactly the loop this skill exists to replace.
- Touching files outside the focused plugin without calling it out in
  SUMMARY with a one-line justification.
- Asking a clarifying question mid-loop when the answer is obviously
  inferable. Pick the most likely interpretation, do the work, note it
  in SUMMARY. Ask only when the choice is genuinely load-bearing and
  unfixable without input.
