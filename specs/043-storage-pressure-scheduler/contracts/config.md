# Configuration contract: storage monitor policy

Registered through `MACHINE_CONFIG_PROVIDERS` in `sandbox/config/manifest.py`; normalized by
`normalize_storage_monitor()` in `sandbox/config/storage_monitor.py`. No consumer reads these
keys directly from YAML.

## Machine defaults — `sandbox.yml`

```yaml
resources:
  monitor:
    warn_ratio: 0.15            # warn at or below 15% free
    critical_ratio: 0.05        # escalate at or below 5% free
    auto_enabled: false         # OFF: no scheduled run deletes anything
    auto_tier: safe             # only "safe" is accepted, ever
    auto_ratio: null            # null means "use critical_ratio"
    reap_enabled: false         # OFF: scheduled reap is a dry run
    reap_ttl: null              # null means the reaper's own 7-day default
    schedule_calendar: hourly
    schedule_randomized_delay: 5min
    schedule_timeout: 30min
    record_max_age_seconds: 21600
```

## Per-machine override — `$SANDBOX_HOME/sandbox.local.yml`

Same `resources: monitor:` block. Overrides `sandbox.yml` per key.

## Per-target opt-in — `$SANDBOX_HOME/sandbox.local.yml`

```yaml
remotes:
  scaleway-sandbox:
    ssh: "..."                  # existing keys unchanged
    storage_monitor:
      auto_enabled: true        # this host may run the safe tier unattended
      auto_ratio: 0.08          # ... once free space is at or below 8%
      reap_enabled: true        # ... and may reap expired workspaces
```

Only the keys listed in the data model are accepted; an unknown key under
`storage_monitor` is rejected with `unknown_key` rather than ignored, so a typo in a key
that authorises deletion cannot read as "off".

## Validation rules (all enforced before any host contact)

1. `auto_tier` MUST be `"safe"`. `tmp` or `all` raises `invalid_auto_tier`. There is no
   configuration, flag, or environment variable that lets an unattended run reach them.
2. `0 < critical_ratio <= warn_ratio < 1` and `0 < auto_ratio <= warn_ratio`. The automatic
   path can never be more eager than the warning.
3. `auto_enabled` and `reap_enabled` MUST be real booleans. `"true"`, `1`, and `yes` are
   rejected with `invalid_flag`, because a truthy-string coercion is how an off switch
   silently becomes on.
4. Durations parse through the existing `reclaim.parse_duration`; schedule time spans must
   match the systemd time-span grammar already used by the recovery scheduler.
5. `auto_enabled: true` with `auto_tier` absent resolves to `safe` — the only tier it could
   have been.

## Precedence

`built-in defaults` → `sandbox.yml: resources.monitor` → `sandbox.local.yml:
resources.monitor` → `sandbox.local.yml: remotes.<name>.storage_monitor`.

Per key, not per block: setting only `auto_enabled` on a target keeps that target's
thresholds at the machine values.
