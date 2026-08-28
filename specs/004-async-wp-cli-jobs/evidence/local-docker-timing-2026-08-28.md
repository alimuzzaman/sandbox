# Local Docker timing evidence — 2026-08-28

Scope: local `xspeed-released` Docker instance through the supported `./sb`
surface. This is measured local output for T021 review. It is not remote proof or
independent acceptance.

## Long job acceptance and immediate cancellation

Command:

```text
/usr/bin/time -p ./sb wp --instance xspeed-released --async eval 'sleep(30); echo "spec004-final";'
```

Measured output:

```text
started background job baa37b422de7eeb0
baa37b422de7eeb0
real 1.56
user 0.52
sys 0.09
```

The exact runtime identity was
`sb-job-xspeed-released-baa37b422de7eeb0`. Immediate supported polling reported
`running`; supported kill reported `killed`; repeat polling reported
`completed (exit 143)`.

Two earlier same-day samples measured 1.25 seconds for an immediate-kill run
(`6632ca5113be1e98`, terminal 143) and 1.27 seconds for a completion run
(`8aed84ead87e9a94`, terminal 0 with both output markers retained).

## Post-hardening sample

After tri-state observation and verified-cleanup hardening, the same supported
long-job command returned job `93282075eef46b08` in `real 1.20` seconds
(`user 0.49`, `sys 0.07`). Immediate supported polling reported `running`, kill
reported `killed`, and repeat polling reported `completed (exit 143)`. Its exact
runtime identity was `sb-job-xspeed-released-93282075eef46b08`.

After cleanup-receipt and whole-PGID hardening, two further supported runs returned:

- `cdf56389e6b736a3`: `real 2.13`, `user 0.56`, `sys 0.13`; immediate poll was
  running, kill succeeded, and repeat poll completed with exit 143.
- `5a83c62299df5025`: `real 1.26`, `user 0.51`, `sys 0.07`; immediate poll was
  running, kill succeeded, and repeat poll completed with exit 143.

The 2.13-second observation misses SC-001. T021 therefore remains open: this
local evidence does not prove the strict target consistently even though the
second same-build sample passed.
