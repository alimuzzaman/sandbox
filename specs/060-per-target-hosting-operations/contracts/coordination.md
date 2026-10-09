# Contract: Hosting coordination

## CLI

```
./sb host apply|sync|login-url|... --project-dir P --environment E --remote R [--wait SECONDS] [--hold-id ID]
./sb host hold claim   --project-dir P --environment E --remote R --purpose TEXT [--duration SECONDS] [--json]
./sb host hold renew   ... --hold-id ID [--duration SECONDS]
./sb host hold release ... (--hold-id ID | --break-hold --reason TEXT)
./sb host operations --remote R [--json]          # per-remote listing
./sb remote build-cap R [--set N --confirm] [--json]
./sb host convert-state --remote R [--confirm] [--json]
```

- `--wait`: integer 0..3600, default 600; 0 refuses immediately. `--lock-wait` remains a deprecated alias.
- `SANDBOX_HOLD_ID` in the environment is equivalent to `--hold-id`.
- MCP: `host_hold`, `host_operations`, `remote_build_cap`, `host_convert_state`; hosting tools accept `wait_seconds` and `hold_id`. Same `data` shapes.

## Results

- Waiting (non-JSON): one line per holder change: `waiting on <target>: <operation> <request> from <controller> since <time>` or `held by <hold_id> (<purpose>) until <time>`.
- Refusal: `{ok:false, code, target, holder?, hold?, cap?, build_holders?, wait_seconds, remedy}`; also retained (054 pre-admission) and visible in `sb delivery inspect`.
- Listing: `{remote, build_cap, build_holders:[...], shared_lease:{step, since}|null, targets:[{target, state: idle|leased|held|fenced, operation?, holder?, since?, hold?:{hold_id, purpose, expires_at, controller}, queue:[{operation, controller, enqueued_at}]}]}`; ≤64 targets, ≤32 queue entries each.

## Remote program operations

`admit`, `renew`, `release`, `phase-report`, `hold-claim`, `hold-renew`, `hold-release`, `hold-break`, `build-acquire`, `build-release`, `shared-acquire`, `shared-release`, `cap-get`, `cap-set`, `list`, `register-controller`, `capability`.

Each runs under `coord.lock`, completes within 15 s, emits one JSON line validated by `client.py`, and never emits secrets. All payload keys are part of the 061 FR-006 shape manifest.

## Seam for 064

`RemoteWideLease(remote, state_key, step).acquire(bound_s<=60)` / `.release()`; raises `EdgeLeaseUnavailable` on timeout.
