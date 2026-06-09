---
name: ship-fix
description: Full bug-fix delivery cycle — from a FluentBoards/GitHub issue to a merged PR. Wraps skills/fix/SKILL.md (the isolated fix loop) and adds the front-end (triage, ticket context) and back-end (branch, commit, push, PR, card close). Use when the user hands you a ticket URL or issue number and says "fix this."
---

# ship-fix — from ticket to merged PR

**Load this workflow when the user gives you a bug to fix and ship.**
It wraps `skills/fix/SKILL.md` (the one-pass fix loop) and handles
everything else: reading the ticket, staging the delivery, and wiring
GitHub + FluentBoards.

For isolated debugging without delivery (no PR, no card to close) —
use `skills/fix/SKILL.md` directly. For net-new features — use
`workflows/build-feature/WORKFLOW.md`.

---

## Real examples from this repo

These fixes were shipped using this exact cycle:

| Fix | Root cause | Evidence type |
|---|---|---|
| `instance delete` left stale registry entry | `registry_remove` never called in delete path | `db_query` → registry file diff |
| `cmd_update` died on `main` not in registry | No guard before registry lookup | `./sb update` before/after |
| `_compose` ignored `--project-directory` | Missing flag in the compose helper | `wp_cli plugin list` cross-instance |

Reference these if you need to calibrate what "EVIDENCE.before/after"
should look like.

---

## Phase 0 — TRIAGE (before any code)

Goal: understand the bug well enough to know what "fixed" looks like.
One read pass only; no edits.

1. **Load context:**
   ```
   focus_get(project_dir)            # focused plugin + CLAUDE.md
   git log --oneline -10             # recent history
   ```
2. **Read the ticket.** If the user gave a FluentBoards card URL
   (`fbs-<ID>` short-link or wp-admin deep-link), load
   `skills/fluentboards/SKILL.md` and read the card + its comments.
   If a GitHub issue number, `gh issue view <N>`.
3. **Classify the fix:**

   | Class | Shape |
   |---|---|
   | **Isolated** | one function, one file, no migration |
   | **Cross-surface** | 2-3 files; one layer (e.g. REST + admin only) |
   | **Deep** | DB schema change, block `save()` change, or migration required |

4. **Emit triage (one short block):**

   > **TRIAGE**
   > **Card/issue:** `<link or N/A>`
   > **Reporter:** `<name or handle>`
   > **Symptom:** `<what the user sees>`
   > **Suspected surface:** `<file:function or area>`
   > **Class:** Isolated | Cross-surface | Deep
   > **Snapshot needed?** Yes (Deep or destructive DB) | No

If Class is **Deep**, call `./sb snapshot fix-<slug>` before step 1 of
the fix loop. Non-Deep fixes don't need a snapshot.

---

## Phase 1 — FIX (delegate to the skill)

Call `load_skill('fix')` and run the one-pass loop in full:

1. Reproduce live → `EVIDENCE.before`
2. Map every call site (one read pass)
3. Form the complete edit plan
4. Apply all edits in one pass
5. Verify live, end-to-end → `EVIDENCE.after`
6. Report **FIXED** or **BLOCKED**

Do not advance to Phase 2 until the skill reports `STATUS: FIXED`.
A `STATUS: BLOCKED` surfaces to the user immediately — stop and ask
for what's missing.

---

## Phase 2 — SHIP (branch → commit → push → PR → card)

**Never run Phase 2 steps without the user's word.** Each action below
is a checkpoint. Stop, name what you're about to do, wait for "yes."

### 2a. Branch
```bash
git checkout -b fix/<short-slug>   # e.g. fix/registry-stale-entry
```
Use the card ID when present: `fix/fbs-42-registry-stale-entry`.
If already on a feature branch, skip — commit onto it.

### 2b. Stage + show diff
```bash
git diff --stat
git diff
```
Name every changed file and one-line reason before asking to commit.
Never `git add -A` — stage only the fix files explicitly:
```bash
git add <file1> <file2> …
```

### 2c. Commit (ask first)
Commit message format:
```
fix(<scope>): <imperative summary under 72 chars>

<optional body: root cause, why the previous code was wrong, any
BC note. Omit if the summary is self-contained.>

Fixes: <card URL or issue #>
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```
No emojis. `<scope>` = the plugin slug or subsystem (e.g. `registry`,
`mcp`, `test-harness`).

### 2d. Push (ask first)
```bash
git push -u origin fix/<short-slug>
```

### 2e. Open PR (ask first)
```bash
gh pr create \
  --title "fix(<scope>): <same summary as commit>" \
  --body "$(cat <<'EOF'
## Root cause
<one paragraph — what was wrong and why>

## Fix
<one paragraph — what changed and why it's correct now>

## Evidence
```
before: <trimmed EVIDENCE.before from the fix loop>
after:  <trimmed EVIDENCE.after from the fix loop>
```

## Test plan
- [ ] <manual or automated step the reviewer can run>

Fixes: <card URL or issue #>
🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### 2f. Close the FluentBoards card (ask first)
Load `skills/fluentboards/SKILL.md`. Move the card to the Done
column and post a comment:

> Fixed in PR <URL>. Root cause: <one sentence>. Evidence: before →
> after as shown in the PR description.

---

## Output contract

After Phase 2 completes, emit one short block:

> **STATUS: SHIPPED**
> **Branch:** `fix/<slug>`
> **PR:** `<URL>`
> **Card:** moved to Done (or `N/A`)
> **Root cause:** `<one sentence>`
> **Files:** `<space-separated list>`

---

## What this workflow does NOT do

- Does not create the branch or commit without asking.
- Does not push or open a PR without asking.
- Does not close the card without asking.
- Does not touch `main` directly.
- Does not skip the live-evidence requirement from `skills/fix/SKILL.md`.

---

## Abort criteria

Stop immediately and surface to the user if:

- The fix loop returns `STATUS: BLOCKED` (user must supply missing
  condition before SHIP can proceed).
- `git diff --stat` is empty after the fix loop (something went wrong
  — the edits didn't land or were on the wrong branch).
- The PR needs a reviewer who owns the changed surface — name them in
  the PR body and remind the user to assign.
