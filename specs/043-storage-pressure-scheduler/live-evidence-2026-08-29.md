# Spec 043 read-only live evidence — 2026-08-29

Scope: the real configured `scaleway-sandbox`, using the supported `./sb` CLI from
source revision `7d8c15a4159d3cffea7988811002be4a691d548d`. No cleanup confirmation was
provided, no deletion ran, and no timer or schedule file was installed.

## Monitor dry run

`./sb resources monitor --remote scaleway-sandbox --dry-run --json` reached the host at
`2026-08-29T11:34:32.132181Z` and reported:

- level `normal`;
- 50,131,816,448 free bytes of 206,900,281,344 total (`free_ratio: 0.242299`);
- automatic cleanup disabled and not run;
- reap disabled and dry-run only, with zero candidates;
- `inventory_status: partial` and `reclaim_inventory_unavailable`.

The command therefore proves bounded read-only measurement and honest partial-state
reporting. It does not prove complete reclaim inventory health.

## Render-only schedule

`./sb resources schedule --remote scaleway-sandbox --json` returned a launchd plan with
`enabled: false`, `activation_supported: false`, and no write. The plan reported that
launchd cannot enforce the configured timeout.

`./sb resources schedule --remote scaleway-sandbox --activate --json` was then run without
confirmation. It refused with `protected_operation` before any scheduler write or
activation. No confirmed lifecycle command was run.
