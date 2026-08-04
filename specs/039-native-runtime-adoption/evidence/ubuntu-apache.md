# Ubuntu 24.04 Apache: live provisioning (039 T047)

**Host**: Ubuntu 24.04.4 LTS, systemd 255, AppArmor 4, x86_64. 2026-08-04.

**Status**: partial. The Apache variant provisions end to end alongside an nginx instance
on the same host. The hostile-probe matrix, egress grants and resource exhaustion are NOT
proven and remain open under T047.

## Provisioning

Run by `tests/live_native_acceptance.py`, which requires the two instances to use
different web servers (`server_pair: true`) and distinct canonical project roots
(`real_sibling: true`). Both provisioned in one run:

```text
ensure_both: true          nginx primary + Apache sibling, concurrently
cleanup_primary: true      cleanup_sibling: true      cleanup_repeated: true
host_preflight_stable: true
foreign_host_service_baseline: true
host_veth_sentinel_active: true
preflight_timing: true
proof_candidate_truthful: true
source_diff_clean: true    config_restored: true
```

The host's own nginx, apache2, mysql and mariadb units were `inactive` before and after,
and Caddy kept ports 80/443 throughout — the managed instances take neither.

## Not covered

`entry_paths` 2/11, `grants` 2/5 and `resources` 0/8 did not pass. Those probes had never
run before this session, because provisioning had never reached a running machine, so
their failures are un-triaged: each needs the same read-the-error treatment the
provisioning path just had. `status_timing`, `warm_converged` and `warm_start_timing` were
measured before the warm-ensure convergence fix landed and should be re-read on the next
run.
