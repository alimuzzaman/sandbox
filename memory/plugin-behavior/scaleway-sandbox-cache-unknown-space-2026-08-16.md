# scaleway-sandbox remote space: remaining ~178.6 (still un-attributed)

Status: read-only diagnostic only; no cleanup executed.

Date: 2026-08-16

Observed command:
- `./sb resources --remote scaleway-sandbox status --scope cache --json`

Result snapshot:
- Capacity: `192.700 GB total` and effectively full (`495,616` bytes available).
- `summary.unknown_bytes = 178632980065` (`~166.36 GiB`).
- `summary.attributed_bytes = 28250044831` (`~26.31 GiB`).
- `summary.reclaimable_bytes = 139288576` (`~0.13 GiB`, from `download_cache`).

What `~178.6` maps to:
- `worktree`: `resource_count 174`, `unknown_size_count 174`, `measured_bytes 0`
- `volume`: `resource_count 70`, `unknown_size_count 70`, `measured_bytes 0`
- `runtime`: `resource_count 72`, `unknown_size_count 72`, `measured_bytes 0`
- `job_artifact`: `resource_count 4`, `unknown_size_count 4`, `measured_bytes 0`
- Owner buckets that still hide size: `unknown:unknown` (`177` unmeasured sizes) and `sandbox:sandbox` (`72` unmeasured sizes).

Representative unmeasured entries:
- worktree names: `ui-t061-green-proof`, `speckit-upstream-sync-workspace-d8bb2858dd5780`, `source-truth-reconciliation-workspace-766f...`
- volume names: `sandbox-speckit-upstream-sync-workspace-d4936d17452eaa_lenzora-sandbox-node-modules`, `lenzora_pgdata`, `sandbox-host-lenzora-development_lenzora-dev-postgres-data`
- runtime names: `feedback`, `recovery`, `registry.lock`, `wp-wp-re-ci-*`, `.drive-volume-fallbacks-*`
- job artifacts: repeated `report.txt`

Next time, to shrink faster:
1. Save JSON locally: `./sb resources --remote scaleway-sandbox status --scope cache --json > /tmp/scaleway-cache.json`
2. Group unknown-size entries with jq:
   - `jq '.data.resources[] | select(.size_state=="not_measured") | {kind, owner: .owner.id, display_name}' /tmp/scaleway-cache.json`
   - then start with `kind=="worktree"` and workspace-specific owners for high-confidence cleanup candidates.
3. Run deep mode only for scoped follow-up: `./sb resources --remote scaleway-sandbox status --scope cache --deep --json`.
