# Quickstart: storage-pressure monitor and safe-tier reaper

This quickstart covers the monitor, policy, and schedule-plan paths. Scheduling
is local to the controller; no timer is activated by the examples below.

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

## 3. Render a schedule (install-free)

Render the disabled plan for the controller. This writes no unit and does not
contact the monitored target:

```sh
./sb resources schedule --remote scaleway-sandbox --json
```

The plan reports the exact `sb resources monitor --scheduled --json` argv, the
systemd or launchd unit, install path, and reverse command. It always says
`enabled: false`. Systemd can enforce the configured timeout; launchd is render-only and
activation refuses with `schedule_timeout_unenforced`. Systemd activation and removal are
protected local operations and require `--activate --confirm` or `--deactivate --confirm`;
do not run them until the target policy and the still-unrun live gate in T023 are reviewed.
No `scaleway-sandbox` command has been run as evidence for this quickstart.

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
                   tests.test_storage_monitor_schedule \
                   tests.test_storage_monitor_runner
```

Use targeted module names. A repo-wide `unittest discover` aborts on a pre-existing `sb` argparse error
unrelated to this feature (feedback `6ef03d44`).
