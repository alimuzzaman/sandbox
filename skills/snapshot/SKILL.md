# Snapshots

Save and restore DB + uploads state. The unlock for fast bug repro, QA
loops, and any "I'm about to do something that might break things."

```bash
./wp-sandbox snapshot <name> [--force]
./wp-sandbox restore  <name>
./wp-sandbox snapshots
```

Snapshots live under `runtime/snapshots/<name>/` and contain `db.sql` +
`uploads.tgz` + a `META` file recording the active project at save time.
Gitignored — they're a per-machine convenience, not shared state.

---

## When to snapshot

- **Before reproducing a bug** that may mutate state (most bugs do).
  Convention: `pre-repro` or `pre-<card-id>`.
- **After landing on a clean QA fixture** you don't want to rebuild.
  Convention: `qa-<project>-base`.
- **Before running a migration / upgrade flow** under test.
  Convention: `pre-migrate-<version>`.
- **Before destructive DB work** via `db_query mutate:true`.
  Convention: `pre-sql`.

If you snapshot more than 3 times in a session, you're not snapshotting —
you're working through a state machine. That's fine; just name them in
sequence: `step1-fresh`, `step2-content-imported`, `step3-after-license`.

---

## When NOT to snapshot

- Read-only work (browsing, code review, log tailing). No state to lose.
- Right after `./wp-sandbox clean && setup` — that's already your clean
  baseline; snapshotting it is duplicate work.
- For long-term storage. Snapshots are gitignored and not portable across
  machines (uploads may contain absolute paths from this dev's machine).
  For shareable fixtures, write a WXR or a `wp_cli` seed script and check
  it into the plugin's `tests/fixtures/` or `runtime/seeds/`.

---

## Restore safety

`restore` overwrites the live DB and uploads dir. Before running it:

1. If you've made code changes you care about, they're safe — restore
   only touches DB + uploads, not plugin source. (Source is in your git
   working tree.)
2. If you have unsaved test data the user might want (e.g. they manually
   created posts in the admin during the session), tell them before
   restoring. Restore is reversible only if you snapshotted that state too.

---

## Naming hygiene

Snapshot names accept `[A-Za-z0-9._-]+`. Good names describe **state**, not
**time**:

- ✓ `pre-repro`, `qa-embedpress-base`, `step2-content-imported`
- ✗ `2026-05-20`, `snap1`, `temp`, `test`

Time-named snapshots accumulate; state-named ones get overwritten with
`--force` when the same scenario recurs.

---

## Pairs with `bug-repro`

The bug-repro loop's "snapshot pre-repro → reproduce → fix → restore →
verify-fix-from-clean-state" is the headline use case. See
`skills/bug-repro/SKILL.md`.
