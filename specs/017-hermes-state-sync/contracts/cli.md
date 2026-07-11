# CLI Contract: Hermes State Sync

## Setup

`sb hermes state setup --remote NAME --repo URL --confirm` validates and stores the
private state repository reference. It does not copy credentials.

## Sync

`sb hermes state sync --remote NAME --confirm [--json]` stages the allowlist, scans
for secrets, commits only when changed, and pushes to the configured branch.

## Restore

`sb hermes state restore --remote NAME --confirm [--json]` fetches and validates the
latest manifest, then atomically restores allowlisted files. Merge conflicts and
unsafe content are errors and cause no mutation.
