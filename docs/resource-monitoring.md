# Resource Monitoring and Safe Cleanup

`sb resources` reports host storage, builds reviewable cleanup plans, and
applies only the exact candidates in a current confirmed plan. It is global:
run it from any directory, with no instance boot required.

## Inspect storage

Use fast status for capacity and cheap inventory:

```sh
./sb resources status --json
./sb resources status --remote scaleway-sandbox --json
```

Use a bounded thorough scan when worktrees, private engine volumes, backups, or
other slow categories need measurement:

```sh
./sb resources status --thorough --budget 60 --json
./sb resources status --remote scaleway-sandbox --thorough --budget 300 --json
```

A scan reports raw total, used, available, attributed, unknown, and estimated
reclaimable bytes. Each resource includes an owner or ownership gap, lifecycle
classification, measurement state, and decision evidence. Timed-out and
unavailable measurements remain unknown; they are never reported as zero.
Thorough human output prints the category currently being measured.

Remote thorough scans also read the remote instance registry and durable-job
registry through their typed read-only repositories. They use that evidence to
distinguish active or retained workspaces from exact stale candidates. Host filesystem
roots and Docker storage roots are reported separately from nested
workspace/volume/image/build-cache detail so nested bytes do not inflate
capacity attribution.

For a large unknown bucket, increase the budget until `host_filesystem`,
`docker_storage`, `instance_registry`, `job_registry`, and the relevant managed
roots are complete. The remaining unknown bytes are then filesystem
accounting differences, inaccessible paths, deleted-open files, or categories
outside the measured roots—not assumed cleanup candidates.

## Review a cleanup plan

Planning is read-only:

```sh
./sb resources plan --scope cache --thorough --budget 60 --json
./sb resources plan --scope stale --thorough --budget 90 --json
```

`cache` may include positively owned download cache, expired terminal-job
artifacts, stopped temporary containers, unused managed networks, and exact
unused Sandbox-labelled images. A provisioned named remote may also include
immutable build-cache records that its engine explicitly marks reclaimable;
each record must also be an unused private root record and is planned and
revalidated by exact build-cache ID. Buildx logical record sizes can overlap,
so cleanup receipts use the host capacity delta as the authoritative physical
reclamation result. It never
includes named persistent volumes,
host logs, package caches, retained backups, active resources, or unmanaged
data.

`stale` is a separate higher-risk scope. A worktree or named volume is eligible
only with positive Sandbox ownership, a measured size, and complete evidence
that no registry, live container, retained job, backup, permanent host, or
mount protects it. Names and age alone are insufficient. Remote providers
read remote registry/job evidence through typed repositories and exclude persistent resources
when that evidence is missing, invalid, or unavailable.

Plans expire after 15 minutes, bind to one host identity, and store internal
locators only in owner-readable records under
`$SANDBOX_HOME/runtime/resource-plans/`.

## Apply a reviewed plan

Confirmation is mandatory:

```sh
./sb resources cleanup --plan-id PLAN_ID --confirm --json
```

Apply accepts no caller-supplied paths or engine IDs. It loads the stored plan,
checks target identity and expiry, re-observes every candidate, and uses only
exact path or engine removal. If ownership, liveness, retention, or mount
evidence changed, the item is skipped. Broad Docker prune operations are not
used.

Completed, expired, replayed, mismatched, and indeterminate plans are refused.
A remote timeout is not retried automatically because the deletion result may
be ambiguous. Cleanup output itemizes removed, skipped, failed, timed-out, and
already-absent resources, compares capacity before and after, discloses drift,
and writes an owner-readable run receipt.

Do not confirm a plan until its candidates and exclusions have been reviewed.
Use a disposable fixture for mutating validation; monitoring and planning are
safe against permanent hosts.

## MCP parity

The explicit `resources` MCP group exposes:

- `resource_status`
- `resource_cleanup_plan`
- `resource_cleanup_apply`

These adapters use the same service and result envelope as the CLI.
`resource_cleanup_apply` refuses missing confirmation before resolving a
provider.
