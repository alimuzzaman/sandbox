# Quickstart: storage-pressure monitor and safe-tier reaper

This quickstart covers the implemented monitor and policy paths only. Scheduling
rendering and activation are future work in Spec 043 (T008/T009); there is no
`resources schedule` command or activation workflow available in this checkout.

## Prerequisites

- Feature 042 shipped (`sb resources status --deep`, `sb resources plan --tier safe`).
- A configured remote (e.g. `scaleway-sandbox`) or nothing at all for the local machine.

## 1. See where you stand (read-only, no configuration)

```bash
./sb resources monitor --remote scaleway-sandbox --dry-run
```

Expected: a `normal` / `warning` / `critical` line with free bytes, total, free share, the
thresholds in force, and a reap dry-run count. The monitor is bounded and cache-only;
`--dry-run` guarantees nothing is deleted regardless of configuration, but it may write
the local last-run record and dry review-plan metadata. The last-run record is written
under `$SANDBOX_HOME/runtime/resources/monitor/`.

## 2. Confirm the warning surfaces where you already look

```bash
./sb doctor | sed -n '/Storage pressure/,/^$/p'
```

Expected: one line per configured target plus `local`, each carrying the recorded level,
numbers, and record age. No network is used. A target with no record is a failed check with
the command that fixes it.

## 3. Scheduling (pending; not runnable)

Do not run a scheduling or activation command from this quickstart. The planned
`resources schedule` renderer, confirmation gate, and platform units are not yet
implemented (T008/T009, with CLI/test gates T012/T018 still open), so there is no
schedule file or activation evidence to inspect. The future design must remain
disabled until those tasks and the live read-only verification gate (T023) are complete.

## 4. Opt a target in (only when you mean it)

In `$SANDBOX_HOME/sandbox.local.yml`:

```yaml
remotes:
  scaleway-sandbox:
    storage_monitor:
      auto_enabled: true
      auto_ratio: 0.08
      reap_enabled: true
```

Verify the policy resolves as expected while keeping automatic deletion disabled:

```bash
./sb resources monitor --remote scaleway-sandbox --dry-run --json | python -c \
  'import json,sys; d=json.load(sys.stdin)["data"]; print(d["auto"], d["reap"])'
```

Then prove the unsafe configuration is impossible: set `auto_tier: all` and re-run — expect
`invalid_auto_tier` and no measurement at all.

## 5. Tier parity through MCP

```python
resource_cleanup_plan(tier="safe", remote="scaleway-sandbox")   # same payload as the CLI
resource_cleanup_apply(tier="safe", remote="scaleway-sandbox")  # refused: confirmation_required
resource_cleanup_plan(tier="safe", scope="cache")               # refused: invalid_mode
```

## 6. Tests

```bash
.cli-venv/bin/python -m unittest tests.test_resource_interfaces \
                   tests.test_storage_monitor_runner
```

Use targeted module names. Schedule-specific tests are not available until T018 is
implemented. A repo-wide `unittest discover` aborts on a pre-existing `sb` argparse error
unrelated to this feature (feedback `6ef03d44`).
