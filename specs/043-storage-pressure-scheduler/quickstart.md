# Quickstart: storage-pressure monitor and safe-tier reaper

## Prerequisites

- Feature 042 shipped (`sb resources status --deep`, `sb resources plan --tier safe`).
- A configured remote (e.g. `scaleway-sandbox`) or nothing at all for the local machine.

## 1. See where you stand (read-only, no configuration)

```bash
./sb resources monitor --remote scaleway-sandbox --dry-run
```

Expected: a `normal` / `warning` / `critical` line with free bytes, total, free share, the
thresholds in force, and a reap dry-run count. `--dry-run` guarantees nothing is deleted
regardless of configuration. A record is written under
`$SANDBOX_HOME/runtime/resources/monitor/`.

## 2. Confirm the warning surfaces where you already look

```bash
./sb doctor | sed -n '/Storage pressure/,/^$/p'
```

Expected: one line per configured target plus `local`, each carrying the recorded level,
numbers, and record age. No network is used. A target with no record is a failed check with
the command that fixes it.

## 3. Read the schedule without installing it

```bash
./sb resources schedule --remote scaleway-sandbox
```

Expected: `enabled: false`, the platform, the cadence, the exact argv, the file(s) that
*would* be written, and both the activate and deactivate commands. Verify nothing appeared:

```bash
ls ~/Library/LaunchAgents/ | grep storage-monitor    # macOS — expect no match
systemctl --user list-timers | grep storage-monitor  # Linux — expect no match
```

## 4. Prove activation is gated

```bash
./sb resources schedule --remote scaleway-sandbox --activate
```

Expected: refusal with `protected_operation`, exit 1, and still no file. Activation happens
only with `--activate --confirm`, and only when the operator asks for it.

## 5. Opt a target in (only when you mean it)

In `$SANDBOX_HOME/sandbox.local.yml`:

```yaml
remotes:
  scaleway-sandbox:
    storage_monitor:
      auto_enabled: true
      auto_ratio: 0.08
      reap_enabled: true
```

Verify the policy resolves as expected without touching the host:

```bash
./sb resources monitor --remote scaleway-sandbox --dry-run --json | python -c \
  'import json,sys; d=json.load(sys.stdin)["data"]; print(d["auto"], d["reap"])'
```

Then prove the unsafe configuration is impossible: set `auto_tier: all` and re-run — expect
`invalid_auto_tier` and no measurement at all.

## 6. Tier parity through MCP

```python
resource_cleanup_plan(tier="safe", remote="scaleway-sandbox")   # same payload as the CLI
resource_cleanup_apply(tier="safe", remote="scaleway-sandbox")  # refused: confirmation_required
resource_cleanup_plan(tier="safe", scope="cache")               # refused: invalid_mode
```

## 7. Tests

```bash
python -m unittest tests.test_storage_monitor_policy \
                   tests.test_storage_monitor_schedule \
                   tests.test_storage_monitor_runner \
                   tests.test_mcp_resource_tier
```

Use targeted module names. A repo-wide `unittest discover` aborts on a pre-existing `sb`
argparse error unrelated to this feature (feedback `6ef03d44`).
