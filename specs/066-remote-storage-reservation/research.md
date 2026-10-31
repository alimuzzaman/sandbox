# Research: Remote Storage Reservation

## R1 Admission seam
- **Decision**: call the storage-floor gate inside `deploy_exact_working_tree` (`sandbox/core/_remote.py`) right after the network-capacity admission and before `ensure_deploy_repo`.
- **Rationale**: every remote submission path deploys through it; the network gate already proves "before any remote write" there, and `remote_jobs._deploy` already translates admission errors.
- **Alternatives**: a readiness row (rejected: the gate reuses cached proofs and tolerates `unknown`; the floor needs a fresh fail-closed measurement).

## R2 Measurement
- **Decision**: one inline python program over ssh (same pattern as the network-capacity probe, no installed-runtime dependency): `os.statvfs` on `$SANDBOX_HOME/deploy-src` (or `$HOME`) and on Docker's data root when readable; free = the minimum.
- **Rationale**: single-filesystem remotes report one number; split layouts are protected by the tighter one.

## R3 Estimate
- **Decision**: maximum size of the up-to-3 most recent `deploy-src` entries whose name starts with the repository's workspace prefix, via bounded `du -sk` (20 s total); otherwise a 5 GB default flagged `estimate_source: default`.
- **Rationale**: observed 2-3 GB per workspace; maximum keeps large recent outliers conservative. Volumes are not counted per workspace (shared node-store); the floor margin absorbs them.

## R4 Reservations
- **Decision**: under `flock` on `runtime/storage/reservations.lock`, prune expired entries, compute `free - sum(reserved) - estimate`, and on admit append `{id, target, estimate_bytes, created_at, expires_at}` with a 45-minute window.
- **Rationale**: a reservation covers the gap between admission and the workspace actually occupying disk (push, checkout, image pull, stack start). After that window `df` already reflects real use, so holding the estimate until the job ends would double-count. This refines FR-006 ("until terminal") to "until the footprint has materialized, bounded window"; spec updated accordingly.

## R5 Fail-closed scope
- **Decision**: no floor configured means no check (FR scenario 7). With a floor, any probe failure, ambiguous output, or lock timeout refuses with `remote_storage_floor` and `reason: measurement_unavailable`.

## R6 Finished-job idle
- **Decision**: in the remote inventory (`sandbox/resources/remote.py`), non-terminal jobs keep the `retained_job` reference. Terminal retained jobs contribute `retained_job` only while `finished_at` is within the idle threshold or missing; otherwise they add `finished_job_idle`. A path with any non-terminal job or an unreadable registry keeps protection. The entry gains `finished_job_idle: true`; `reclaim._entry_tier` maps it to `safe` with reason `finished_job_idle`.
- **Threshold**: 6 h default; remote env `SANDBOX_FINISHED_JOB_IDLE_HOURS` overrides (no payload change).
- **Rationale**: the scheduled routine runs the `safe` tier; this is the narrow widening the PRD confirmed. Registry unavailability already yields an incomplete inventory, so nothing is reaped (FR-008).

- Admission also holds `runtime/resources/reclaim-host.lock` while recording its reservation. Reclaim holds that guard and rechecks the job registry and live source-family reservations before teardown; unknown evidence keeps the workspace. Refusals do not prune the ledger.

## R7 Teardown
- **Decision**: in `reclaim_run` for worktree candidates, inspect each `stop_containers` entry for its compose project; keep only projects that pass `owner()`, do not start with `sandbox-host-`, and match the workspace directory name (same prefix rule as volumes). For each: remove all its containers, then volumes labelled with the project that no other container uses, then its networks. Any failure keeps the source, outcome `failed`, reason `stack_teardown_incomplete`; replay repeats idempotently.
- **Rationale**: postgres/job-service containers do not bind the workspace directory, so they were never in `stop_containers` (feedback e31fe787). Deriving the project on the host avoids a control-protocol payload change.

- Authorized CI job cleanup retains its broker policy/identity checks, holds the host reclaim guard through broker deletion, rechecks sibling jobs, and calls the same stack helper before reference-absence validation and source removal. Unsupported non-workspace checkouts keep their existing absence requirement.

## R8 Heal
- **Decision**: `sb host heal --remote R [--project P --environment E] [--confirm] [--json]`. Over ssh: list containers whose compose project starts with `sandbox-host-`, inspect, select `State.Status == exited`, `HostConfig.RestartPolicy.Name == on-failure`, `MaximumRetryCount > 0` and `RestartCount >= MaximumRetryCount` and nonzero exit status. Dry run lists them; `--confirm` re-probes free space (strictly above floor; refuse if no floor configured? → floor 0 means "above 0") and re-selects, then `docker start` only ids present in both preview and re-selection.
- **Rationale**: start (not recreate) keeps the container and its volumes untouched.

## R9 Phase B
- **Decision**: `phase-b-isolation.md` compares loop-mounted test filesystem, build-cache cap, and production volume relocation with downtime, rollback and approval points. No tooling.
