# Research: Remote Development Execution Readiness

## R1. How a network gets a subnet without daemon pools

- **Decision**: For every network the instance's effective Compose config
  creates (non-external), Sandbox writes a generated override that sets
  `networks.<name>.ipam.config: [{subnet: <allocated>}]`, and passes it last
  in the `-f` chain. The effective config comes from
  `docker compose config --format json`, run on the remote. The built-in
  WordPress template (`render_compose`) gets the same override for its
  default network.
- **Rationale**: Docker accepts a user-defined bridge network with an
  explicit subnet without any `default-address-pools` configuration, and
  without a daemon restart. An override leaves the project's own Compose
  files unchanged.
- **Alternatives considered**:
  - Pre-creating networks with `docker network create --subnet` and marking
    them external in Compose. Rejected: it changes project semantics, and
    teardown would have to remove them separately.
  - Editing `daemon.json`. Rejected: that is the restart path this feature
    exists to avoid.

## R2. Where range state lives and how allocation stays atomic

- **Decision**: State lives in remote `$SANDBOX_HOME/runtime/network-ranges/state.json`
  (0600, directory 0700). It is changed only by one fixed Python program,
  sent over `ssh_run` under `fcntl.flock` on `state.lock`. The operations are
  `inventory`, `assign`, `allocate`, `release-owner` and `list`.
  `list` is read-only: it creates no directory and takes only a shared lock.
- **Rationale**: This is the same proven pattern as 061 pins. It works on old
  runtimes for listing, and with a flock two concurrent allocations for the
  last subnet serialize (SC-006).
- **Alternatives considered**: a control-service (MCP) endpoint. Rejected
  because it would exist only on new runtimes, and assignment must be able to
  report "runtime predates ranges" precisely (FR-002). The program probes for
  the installed control-protocol marker (R10) and returns a typed limitation when it
  is absent.

## R3. Overlap classes for assignment and proposal

- **Decision**: `inventory` returns these candidate conflict sets:
  - the subnets of every observed Docker network (`docker network inspect`);
  - IPv4 host routes (`ip -j route`);
  - Docker's built-in default pools (`172.17.0.0/16`, `172.18.0.0/16` through
    `172.31.0.0/16`, and `192.168.0.0/16` in /20 blocks, as documented
    defaults);
  - `100.64.0.0/10` (Tailscale CGNAT).

  Any unreadable source makes the inventory partial, and a partial inventory
  refuses as `unknown`. A proposal takes the first free /20 inside
  `10.200.0.0/14`, with a default subnet size of /26 (64 subnets per range).
- **Rationale**: These are exactly the classes FR-002 names. `10.200.0.0/14`
  is rarely used by clouds or VPNs and sits outside the Docker defaults and
  CGNAT. A /26 is ample for one Compose network.
- **Alternatives considered**: letting Docker pick. Rejected, because that is
  the default-pool behavior that cannot be proven.

## R4. Admission and allocation as one step

- **Decision**: `remote_network_capacity_admission` sends a single program.
  It probes pools as today and, when ranges exist, allocates `required`
  subnets for the owner under the same flock. It returns the pool evidence
  plus `range: {capacity, allocated, granted: [opaque ids]}`. The evaluator
  sums usable pool capacity and unallocated range capacity. If the total is
  short, the program allocates nothing and the refusal is
  `docker_network_subnet_exhausted`, carrying the allocation table: opaque
  owner id, owner kind, workspace and age, with no subnets.
- **Rationale**: This closes the race between the gate and the reservation,
  and it keeps today's fail-closed rules. Pool evidence and range evidence
  are evaluated together; built-in default pools never count.

### R4 revision (2026-10-10, during T009/T013)

- **Finding**: The pre-deploy admission check runs before staging, so it
  knows neither the workspace id nor the network names the stack's effective
  Compose config creates. It cannot allocate per `(workspace_id, network)`.
  `docker compose` for a remote instance runs on the remote, in the installed
  runtime (`sandbox/core/_docker.py::compose`), not in the controller.
- **Decision**: Split R4 in two.
  - Pre-deploy admission stays count-only: pools plus unallocated range
    capacity against `required` (T011/T012). It refuses exhaustion with the
    allocation table before any transfer (FR-005).
  - Allocation happens in the runtime, immediately before the stack's first
    `up`. The runtime reads `docker compose config --format json`, allocates
    the created networks through the same fixed range program run locally
    (all-or-nothing, idempotent per `(workspace_id, network)` under the
    flock), writes the override from `sandbox/remote_network/override.py`, and
    passes it last in every later `compose` call for that instance.
  - A race between admission and allocation therefore refuses at allocation
    with `docker_network_subnet_exhausted` and the table, before any network
    is created. It never yields a half-allocated stack.
  - The owner is the instance (kind `instance`, `instance:<name>`),
    attributed to its deployment root's workspace name so the exhaustion
    table's release commands name a real `workspace release` target. Owning
    by workspace instead would let one labelled instance's teardown free its
    sibling's subnets. The hooks live in `compose()` itself (and in the
    generic Compose adapter's `ensure`/`apply`/`destroy`): prepare before `up`, the override
    on every call, release after a successful `down -v`/`--volumes`. Every
    destroy path (instance destroy, data reset, uninstall, plugin-check
    teardown) goes through that call. With no range state file on the host
    the hooks spawn nothing (`sandbox/remote_network/runtime.py`).
  - Admission reads the range's counts with a read-only `stats` program op,
    and only when the pool outcome is missing evidence or exhaustion, the two
    outcomes range capacity can change. Unallocated range capacity covering
    the run admits.
- **Alternatives considered**: Allocating at admission with a placeholder
  network list. Rejected: names are only known after staging, and a
  placeholder grant would leak when the stack used other names.

## R5. Freeing allocations

- **Decision**: Allocations are unique per `(workspace_id, network)` and
  `allocate` is idempotent for that pair, so jobs, previews and repeated runs
  in one workspace stack reuse its subnets (see data-model owner-kind
  mapping). Three paths free allocations, each with `release-owner`:
  - `workspace release` frees the workspace's own and its jobs' allocations;
  - `workspace reap` frees the allocations of reaped workspaces;
  - retention expiry (the existing 7-day TTL) frees them on the reap path.

  A killed job's allocation stays attributed and counted until one of those
  runs (FR-006). Revised after the merge-gate review: `workspace release`
  only marks the lease and destroys nothing, and reap removes containers
  without `compose down`, leaving the stack networks. So the runtime frees in
  two places: the `compose down -v` hook of the R4 revision for instance
  teardown, and reap's `reconcile_after_removal`, which on every run sweeps
  instance owners missing from the registry (paged, never truncated), removes
  their networks and releases each only once they are gone. A kept owner is
  reported `partial` and retried by the next reap; owners younger than ten
  minutes or with a held lifecycle lock are skipped, liveness and the owner's
  networks are re-read under its lock, and a missing registry stops the
  sweep. Reconciliation removes registry records by their own root and
  label, so labelled instances of a reaped root stop counting as live. The operator remedy is `workspace release` followed by
  `workspace reap --confirm`.
- **Rationale**: The workspace lifecycle already owns retention; no new timer
  is needed.

## R6. Readiness rows and their sources

| Row | Source | not_ready when | unknown when |
|---|---|---|---|
| registration | `TargetService.resolve` | `unknown_remote`, `ambiguous_remote`, `remote_not_provisioned` | never |
| reachability | bounded `ssh_run true` (10 s) | SSH refuses or authentication fails | timeout |
| runtime compatibility | 061 `admitted(status)` | verdict not ok | status unavailable |
| capacity | read-only `inventory` plus pool probe, without allocating | neither pool nor range evidence (`missing_pool_evidence`), or zero usable | partial evidence |
| ownership repair | read-only check that the repair helper exists and is executable; `not_applicable` when there is no instance | helper missing | probe timeout |
| handoff | recorded ensure-to-exec round trip at the installed revision | never | none recorded |

- **Decision**: All rows run concurrently under one 60-second deadline, and
  unfinished rows become `unknown`. The proof file records the installed
  runtime revision, the time and the rows. A submission reuses the proof
  within 300 s only when the revision still matches.
- **Rationale**: The rows reuse existing probes, so there is no new remote
  surface. A proof bound to the revision honors FR-018.
- **Implementation limits** (US2):
  - The revision a proof is bound to is the one this controller recorded in
    its registration (`mcp_service.runtime_revision`). There is no installed-at
    timestamp, so a same-revision reinstall by another controller does not
    invalidate this controller's proofs. Local installs (`service migrate`,
    `provision`, `up`) and `network-range assign` delete the remote's proofs.
  - A range release on the remote host cannot reach controller proofs. That
    is safe: a release only frees capacity, a `not_ready` proof is never
    reused, and capacity admission still runs at deploy.
  - The ownership-repair row asks the remote's registry for the project's
    instances (`sb instances --project-dir <workspace> --json`), inside the
    probe deadline; remote instance names are derived and truncated, so it
    never matches a predicted name. No workspace, or no registered instance
    for it, means `not_applicable/no_instance`
    (a workspace directory can outlive a failed ensure or a deleted
    instance, so it is not evidence). An unreadable inventory falls through
    to the check that the remote `sb` exists and is executable.
  - A target resolution that fails on registration (`unknown_remote`,
    `remote_not_provisioned`) happens before any transport exists; every
    submission handler maps it to the same `remote_not_ready_registration`
    refusal (`sandbox.readiness.errors.registration_refusal`).
  - The gate takes the caller's resolved target (or the job submission), so
    an explicit `--config-file` selection is never resolved away.
  - A handoff is recorded only when the ensure's gate and the exec's gate
    proved the same runtime revision and generation, and no invalidation
    happened since.

## R7. Protocol bump

- **Decision**: The new range fields in the admission payload change the
  keys of `remote_jobs`. Bump `CONTROL_PROTOCOL_SPOKEN` to 2 and keep
  `CONTROL_PROTOCOL_OLDEST_SERVED` at 1. The new runtime still serves
  protocol-1 controllers, which send no range keys. Re-record
  `control_shapes.json`.
- **Rationale**: The 061 FR-006 guard requires the bump, and keeping the
  oldest served version at 1 preserves coexistence.

## R8. Daemon-pool plan digest

- **Decision**: The plan includes:
  - the sorted hosted targets from the hosting inventory (`project/environment`);
  - the count of other running containers;
  - `plan_digest = sha256(json(targets, count))[:16]`.

  `--confirm` now takes `--plan-digest D`. Apply recomputes the plan and
  refuses `docker_pool_plan_changed` when the digest differs, with zero
  restarts.
- **Rationale**: This binds confirmation to exactly what will restart
  (FR-011).

## R9. Local fallback

- **Decision**: Submission paths no longer fall back to a local run when the
  remote is refused. A local run happens only with an explicit `--local`
  selector, and when the declared remote was not ready it prints the
  declared remote, the failing row and its reason.
- **Current code (checked 2026-10-10)**: `TargetService.resolve`
  (`sandbox/application/target_service.py`) already refuses `unknown_remote`,
  `ambiguous_remote` and `remote_not_provisioned`, and `exec`
  (`sandbox/commands/runtime.py`) refuses instead of running local Compose.
  No path is removed: this feature adds tests that pin that behaviour on every
  submission path, enriches the refusals (FR-020) and adds `remote_selection`.
  If an implementation step finds a path that does fall back, it stops and
  records parity evidence and approval under principle VI before changing it.
- **Rationale**: Silent fallback mislabeled evidence (feedback cebec97a).

## R10. Range support marker and 061 status

- **Decision**: The marker is the installed unit's declared control protocol
  (061 `SANDBOX_REMOTE_MCP_CONTROL_PROTOCOL`, written by `remote service
  migrate`). Ranges need a declared spoken protocol of at least the number
  T013a assigns. `assign` and admission allocation check it first and return
  `range_runtime_unsupported` naming the migrate when it is lower or absent
  (FR-002); `propose`, `inventory` and `list` are read-only and need no
  marker.
  Feature 061 has shipped (`remote_runtime.verdict.admitted`,
  `CONTROL_PROTOCOL_SPOKEN = 1`), so the runtime-compatibility row uses its
  verdict; the "exact revision" branch of FR-017 no longer applies.
- **Rationale**: The program is sent inline, so it always "supports" ranges;
  only an installed marker says the runtime was migrated to a revision whose
  admission and Compose override use them.
