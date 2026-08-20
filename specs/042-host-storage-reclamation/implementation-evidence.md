# Implementation evidence

**Date**: 2026-08-16. **Target**: `scaleway-sandbox` (`alim@212.47.72.49`).
**Mode**: read-only. No deletion, prune, lease write, or config change was
performed on the host. The host's state after the manual 2026-08-16 cleanup was
118 GB free with 17 deploy-src entries, and it was unchanged by this verification.

## Live: whole-host classification in one command

```
./sb resources --remote scaleway-sandbox status --deep --budget 180 --json
```

```
reclaim status complete
classes: PROTECTED 12 entries 7.38 GiB (12 measured, 0 unmeasured)
         BASE       5 entries 0.94 GiB (5 measured, 0 unmeasured)
volumes: 0 eligible, 39 protected
tiers:   safe 0 candidates | tmp 0 | all 1 (38.4 MiB)
drift:   42 indexed but absent; 4 present but unindexed
```

All 17 entries were classified, each exactly once, with size, mtime, lease
state, and reason. This reproduces the manual audit's conclusion that everything
remaining is a KEEP: the only `all`-tier candidate is
`amarsonar-editorial-closeout`, a 38 MiB base deployment last modified
2026-08-05 — outside the 7-day window, with no registered instance, hosted site,
or active job. It is not proposed by `safe` or `tmp`.

The 42-versus-4 drift is the real, pre-existing index disagreement in both
directions that motivated FR-004.

## Live: `--fast` degrades honestly

```
./sb resources --remote scaleway-sandbox status --fast
  reclaim inventory: partial (container_inventory_unavailable)
    PROTECTED  12 entries  3.0 GiB (6 unmeasured)
      UNKNOWN   5 entries  654.6 MiB (2 unmeasured)
    tier totals: safe 0 (0.0 B) | tmp 0 (0.0 B) | all 0 (0.0 B)
    PARTIAL: 8 entries unmeasured
```

`--fast` skips the engine inventory, so LIVE/STOPPED cannot be proved; those
entries report `UNKNOWN` and every tier reports zero candidates. A fast report
never authorises a deletion.

## Live: plan is side-effect free

```
./sb resources --remote scaleway-sandbox plan --tier safe --budget 120
  candidates: 0; estimated 0.0 B; skipped: 56
./sb resources --remote scaleway-sandbox plan --tier all --budget 120
  candidates: 1; estimated 38.4 MiB
    38.4 MiB worktree BASE amarsonar-editorial-closeout
             [one_shot_base_expired] mtime=2026-08-05T11:47:16Z
  skipped: 55
./sb workspace reap --remote scaleway-sandbox --dry-run --budget 120
  dry-run: True
    worktree amarsonar-editorial-closeout [one_shot_base_expired]
```

Host free space before and after the three runs: 123,641,950,208 →
123,643,248,640 bytes (the difference is unrelated host activity; nothing was
removed). Entry count stayed 17.

Every protected volume appeared in `skipped` with its rule, including the four
that motivated the deny-by-default rule and the production hosted-site volumes
`sandbox-host-templately-astro-production_*-node-modules`.

## Two defects the live run caught

1. **A single unmeasured directory blinded the whole classification.** The first
   live run reported five entries as `UNKNOWN
   (container_inventory_unavailable)` because one directory's size measurement
   timed out and the block status degraded to `partial`. Whether the *container*
   inventory is trustworthy is a different question from whether every directory
   could be measured; the block now carries `engine_complete` separately and the
   second run reported `complete` with BASE/PROTECTED classes.

2. **A truncated Compose project name almost orphaned a live workspace's
   volume.** The first live run marked
   `sandbox-lenzora-workspace-37a8ee_lenzora-sandbox-node-modules` (184 MB)
   eligible as `workspace_scoped_volume_orphaned`, because Compose had truncated
   the project name and no directory matched it exactly — while
   `lenzora-workspace-37a8eec1ce1968` was live and registry-protected. Owning-
   workspace matching is now prefix-aware, and the case is pinned by
   `test_truncated_compose_project_name_does_not_orphan_a_live_workspace`.

Both were found only because planning was verified against the real host, not a
fixture.

## Tests

```
PYTHONPATH=. python3 -m unittest discover -s tests -t tests \
    -p "test_resource_reclaim*.py"     -> Ran 83 tests, OK
PYTHONPATH=. python3 -m unittest discover -s tests -t tests \
    -p "test_workspace_retention.py"   -> Ran 9 tests, OK
PYTHONPATH=. python3 -m unittest discover -s tests -t tests \
    -p "test_resource*.py"             -> Ran 142 tests, OK (pre-existing suite)
PYTHONPATH=. python3 -m unittest discover -s tests -t tests \
    -p "test_workspace*.py"            -> Ran 82 tests, OK (pre-existing suite)
PYTHONPATH=. python3 -m unittest discover -s tests -t tests \
    -p "test_architecture*.py"         -> Ran 16 tests, OK
```

The probe tests in `tests/test_resource_reclaim_service.py` run the real shipped
probe program in a subprocess against a temporary `SANDBOX_HOME`, so the
host-side protections (path escape, hosts subtree, managed root, non-workspace
volume, mtime drift, manifest-before-delete, already-absent resume) are
exercised as shipped rather than mocked.

## Known limitation on this host

`sb workspace list --remote scaleway-sandbox` still fails, because it runs `sb`
on the host and the host's `sb-src` predates the `116a63b` fix. Nothing in this
feature depends on it: `resources status|plan|cleanup` and `workspace
release|ttl|reap` ship their probe over the connection. Index reconciliation
after a real cleanup does use the host `sb`, and reports `index_pending` with
status `partial` when it is unavailable rather than implying the index is clean.
