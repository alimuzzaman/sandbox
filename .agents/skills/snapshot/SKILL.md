---
name: snapshot
description: Capture, restore, and reset local Sandbox WordPress state safely using named snapshots and the install baseline.
---

# Snapshots

Save and restore DB + uploads state. The unlock for fast bug repro, QA
loops, and any "I'm about to do something that might break things."

```bash
./sb snapshot <name> [--db-only] [--force]
./sb restore  <name> [--yes]
./sb snapshots
```

Snapshots live under `runtime/snapshots/<name>/` and contain `db.sql` +
`uploads.tgz` + a `META` file recording the active project at save time.
Gitignored — they're a per-machine convenience, not shared state.

---

## Fast DB rollback — `./sb reset` (spec 008)

For the common "undo what this test did to the DB" case you don't need a named
snapshot. Each instance keeps a reserved **`@install` baseline** (a db-only
snapshot captured once after final provisioning (and refreshed after a successful
onboarding seed), stored as `__install__` — shown separately by `./sb snapshots`
as a protected reset target):

```bash
./sb reset              # drop the DB + restore the post-install baseline (keeps uploads)
./sb reset --rebaseline # re-capture the baseline from the CURRENT DB instead
```

MCP: `snapshot(name, db_only:true)` captures just the DB; `wp_reset(confirm:true)` /
`wp_reset(rebaseline:true)` resets it. Use `reset` for a fast in-place rollback; use a **named snapshot** when you need to capture and
restore arbitrary points (and uploads). `db reset/import` run via the `wpcli`
service (the fpm web image has no mysql client — see
`memory/plugin-behavior/restore-needs-mysql-client.md`).

**Auto-captured on first create/recreate** (Docker; spec 008): the db-only
`__install__` baseline above, **plus** a FULL named snapshot `install-baseline`
(DB + uploads) for a complete post-install rollback — `./sb restore install-baseline`.
Both are captured once after plugins/themes are wired (and an onboarding seed
refreshes them to include its fixture); a `recreate` wipes the
snapshots dir first, so they refresh to the fresh install. A failed capture is
logged and leaves no half-written dir (no more 0 KB `__install__`).

Snapshot database capture and restore stream through the `wpcli` service's
standard output/input. The dump is opened host-side with exclusive `0600`
permissions, so no snapshot directory is bind-mounted into the container and
no MariaDB `mysql` UID chown or cross-UID permission window is needed.

## From wp-admin (spec 002)

Named snapshots are also take/restore/list/delete-able from **Tools → Sandbox
Snapshots** in wp-admin — same format as the CLI (interchangeable). Take has an
**overwrite** and a **DB only** toggle (db-only skips the uploads archive), and
the list shows a **Type** column (full / db-only, from each snapshot's META).
Restore and reset actions carry an explicit confirmation boolean through the
nonce- and capability-checked AJAX proxy before the bridge accepts a job.
Docker only (not herd).

## Restore confirmation

Named restore drops and recreates the current database. A non-interactive CLI
call must include `--yes`; an interactive call prompts with a default-deny
answer, so cancellation leaves the instance unchanged. The bridge equivalent
requires `confirm=true` before it accepts the asynchronous job. The protected
`@install` baseline is a reset target only: it is listed separately and cannot
be restored or deleted as an ordinary named snapshot.

MCP mutation responses are bounded metadata (`instance`, safe snapshot or
operation identifier, mode/confirmation, and outcome). They do not return CLI
command lines, host paths, credentials, database dumps, or archive contents.

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
- Right after `./sb clean && setup` — that's already your clean
  baseline; snapshotting it is duplicate work.
- For long-term storage. Snapshots are gitignored and not portable across
  machines (uploads may contain absolute paths from this dev's machine).
  For shareable fixtures, write a WXR or a `wp_cli` seed script and check
  it into the plugin's `tests/fixtures/` or `runtime/seeds/`.

---

## Restore safety

`restore` overwrites the live DB and uploads dir. Before running it, pass
`--yes` in non-interactive contexts or answer the interactive prompt with an
explicit `y`/`yes`; the default answer is cancellation. A DB-only snapshot
does not contain `uploads.tgz`, so restoring one leaves existing uploads alone.

Before running a restore:

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
