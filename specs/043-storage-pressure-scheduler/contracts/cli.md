# CLI contract: `sb resources monitor` and `sb resources schedule`

Both are new actions on the existing global `resources` command. Existing actions
(`status`, `plan`, `cleanup`) are unchanged.

## `sb resources monitor [--remote NAME] [--scheduled] [--dry-run] [--budget N] [--json]`

Measures capacity for the target, classifies pressure with the target's resolved policy,
writes the last-run record, and then acts only as the policy allows.

- `--scheduled` marks the run's trigger as `scheduled` in the record and takes the
  non-overlap lock. It does **not** grant any additional authority.
- `--dry-run` forces observation only: the automatic tier is not executed and the reap is a
  dry run, whatever the policy says. Used for read-only verification against a real host.
- Default measurement uses the cached host directory index (`directory_cache="cache_only"`),
  so a scheduled run costs one bounded round trip and never triggers a disk walk.

Exit status is `0` for `normal` and `warning`, `1` for `critical`, `unknown`, or a refused
policy. A non-zero exit on `critical` is what makes an unattended failure visible to the
init system's own status.

Text output, warning case:

```text
resources monitor: warning (scaleway-sandbox)
  target kind=remote; name=scaleway-sandbox
  CAPACITY WARNING: 24.1 GiB free of 193.7 GiB (12.4%); threshold warn_ratio (15.0%)
    free space is below the warning threshold; run `sb resources plan --tier safe --remote scaleway-sandbox` and review the candidates
  automatic reclamation: disabled (enable with resources.monitor.auto_enabled)
  reap: dry run — 3 candidates, 4.2 GiB would be reclaimed
  record: /Users/x/sandbox/runtime/resources/monitor/<digest>.json
```

Text output, normal case (no warning noise):

```text
resources monitor: normal (scaleway-sandbox)
  target kind=remote; name=scaleway-sandbox
  123.4 GiB free of 193.7 GiB (63.7%); thresholds warn 15.0% / critical 5.0%
  reap: dry run — 0 candidates
```

`--json` returns the standard `{ok, action, status, target, data, error}` envelope where
`data` is the MonitorRunRecord.

The monitor-only CLI surface accepts `--scheduled` and `--dry-run` only with
the `monitor` action. Supplying either flag to `status`, `plan`, or `cleanup`,
or supplying cleanup/status-only flags to `monitor`, is refused with
`invalid_mode` before policy or service resolution. Policy resolution happens
before host-facing service construction; a refusal therefore performs no host
probe or deletion. `--dry-run` is non-deleting, not write-free: the run record
and any dry review-plan metadata are local evidence.

### Refusals

| Condition | Code | Effect |
|---|---|---|
| `auto_tier` is not `safe` | `invalid_auto_tier` | refused before any host contact; nothing measured, nothing deleted |
| `auto_ratio > warn_ratio` | `invalid_threshold_order` | refused before any host contact |
| named target not in `remotes:` | `unknown_target` | refused, target named, no fallback to local |
| lock already held | `lock_held` | status `skipped`, exit 0 |

## `sb resources schedule [--remote NAME] [--activate] [--deactivate] [--confirm] [--json]`

With no flags: renders the plan and **installs nothing**.

```text
resources schedule: planned (scaleway-sandbox)
  enabled: false
  platform: launchd
  cadence: hourly (randomized delay 5min, timeout 30min)
  command: sb resources monitor --scheduled --json --remote scaleway-sandbox
  would write: ~/Library/LaunchAgents/com.wpdeveloper.sandbox.storage-monitor.<digest>.plist
  activate:   sb resources schedule --remote scaleway-sandbox --activate --confirm
  deactivate: sb resources schedule --remote scaleway-sandbox --deactivate --confirm
  units:
    com.wpdeveloper.sandbox.storage-monitor.<digest>.plist:
      <?xml version="1.0" ...>
```

`--activate` without `--confirm` is refused:

```text
resources schedule: refused (scaleway-sandbox)
  protected_operation: activating a storage-monitor timer is a protected operation; re-run with --confirm
```

`--activate --confirm` writes the unit(s), enables them, and reports every path written plus
the deactivate command. Activating an identical existing schedule reports
`status=unchanged` and writes nothing. `--deactivate --confirm` disables and removes them;
`--deactivate` without `--confirm` is refused the same way.

`--activate` and `--deactivate` together is refused with `invalid_mode`.

## `sb doctor` — Storage pressure section

Read-only, offline. One check per configured target plus the local machine:

```text
Storage pressure:
  ✓ local: normal — 411.2 GiB free of 926.4 GiB (44.4%), checked 12m ago
  ✗ scaleway-sandbox: WARNING — 24.1 GiB free of 193.7 GiB (12.4%), threshold warn_ratio, checked 41m ago
      → run `sb resources plan --tier safe --remote scaleway-sandbox` and review the candidates
  ✗ other-host: no monitor run recorded
      → run `sb resources monitor --remote other-host`, or render a schedule with `sb resources schedule --remote other-host`
```

A record older than `record_max_age_seconds` fails the check with its age stated and the
refresh command — never reported as healthy.
