# Quickstart validation

1. `./sb remote set-storage-floor xcloud-london --gb 30` then `./sb remote list` shows the floor.
2. Validate refusal arithmetic, unavailable probes and concurrent reservations with `tests/test_storage_floor.py`; do not change the live floor or submit a probe job.
3. `./sb remote service migrate xcloud-london --confirm`, then `./sb workspace reap --remote xcloud-london --dry-run`: idle finished workspaces appear with reason `finished_job_idle`; no `sandbox-host-*` resource appears.
4. `./sb host heal --remote xcloud-london`: lists only restart-exhausted hosted containers (expected empty when healthy); restarts nothing.
