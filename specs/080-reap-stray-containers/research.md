# Research: Reclaim Stray Unlabeled Containers

## R1. Where the ownership rule runs

- **Decision**: One stdlib-only function, `assess_stray_container(container, checkout)`
  in `sandbox/resources/workspace_teardown.py`, injected into the host program next to
  `teardown_workspace_stack`. The probe calls it while building reclaim entries; the
  cleanup calls it again on a fresh inspect.
- **Rationale**: The probe holds the full `docker inspect --size` objects; the
  controller only sees the projected records. Running the same function on both sides
  means the plan and the cleanup apply one rule.
- **Alternatives**: Deciding on the controller from extra projected fields (two copies
  of the rule); deciding only at cleanup (the plan could not show evidence).

## R2. What "unlabeled" means

- **Decision**: A container is never a stray when any label key starts with
  `com.docker.compose.`, `com.docker.stack.` or `com.docker.swarm.`, or contains
  `sandbox` (case-insensitive), or its name starts with `sandbox-`. Other labels
  (image labels such as `org.opencontainers.image.*`, which Docker copies into
  `Config.Labels`) are allowed.
- **Rationale**: The reported container's image carries OCI labels, so "no labels at
  all" would never match. The probe's `owner()` returns no project for Compose
  projects outside Sandbox, so the rule reads raw labels, not the projected project.
- **Alternatives**: Allowlist of label keys (brittle across images).

## R3. Mount and network rules

- **Decision**: Every entry in `Mounts` must be `Type: bind` with both the source as
  written and `os.path.realpath(source)` equal to, or below, the checkout (also taken
  both as written and resolved). Any `volume` mount (named or anonymous) protects with
  `named_volume`; any other type protects with `unsupported_mount`. `HostConfig.NetworkMode`
  must be `default` or `bridge`, and every key of `NetworkSettings.Networks` must be
  `bridge`, else `non_default_network`. `HostConfig.RestartPolicy.Name` other than
  empty or `no` protects with `restart_policy`.
- **Rationale**: Owner brief; a restart policy marks a service someone meant to keep.

## R4. Evidence fingerprint and what counts as "changed"

- **Decision**: sha256 over canonical JSON of: `Id`, `Name`, `Created`, `Config.Image`,
  `Image`, `Config.Labels`, the sorted mount list (type, source, destination, name, rw),
  `HostConfig.NetworkMode`, sorted network names, `HostConfig.RestartPolicy.Name`,
  `State.StartedAt`, `RestartCount`. `State.Running` and `State.FinishedAt` are left
  out so a crash between stop and remove does not turn a rerun into a refusal.
- **Rationale**: Covers "new binds, restarted, labels" from the brief; a same-name
  replacement has a new `Id` and `Created`.

## R5. Detecting unplanned bound containers at cleanup

- **Decision**: `docker ps -aq --no-trunc`, then one
  `docker inspect --format '{{json .Id}} {{json .Mounts}}'` over all ids to find those
  bound to the checkout, then a full `docker inspect` of the bound ones. A container
  that vanished between calls (`No such object` only) is skipped; any other failure
  refuses the candidate.
- **Rationale**: The `volume=` filter of `docker ps` matches destinations, not bind
  sources, so it cannot find binds of a host path.
- **Alternatives**: Full inspect of every container per candidate (tens of calls per
  candidate on a busy host).

## R6. Compatibility

- **Decision**: The candidate gains `remove_containers` (list). Teardown runs the new
  mode only when the field is present; without it (older controller) the legacy
  refusal of unlabeled containers stays. An older runtime ignores the field and
  refuses as today. The probe adds a `stray` object to container records; an older
  controller ignores it, and a new controller plans a record without it as today (listed in
  `stop_containers` only, refused by the host), so a new controller against an
  older runtime behaves exactly as before. `sandbox/resources/remote.py` is not in the protocol
  shape sources, so no `CONTROL_PROTOCOL_SPOKEN` bump.

## R7. Plan-id forms

- **Decision**: Fix the docs (no-`--tier` form is cache/stale only; tier plans need
  `--tier`), and refuse `--tier X --plan-id P` when P's stored tier is not X, before
  `begin`, with `plan_kind_mismatch`.
- **Rationale**: Accepting a tier plan without `--tier` would route by plan contents
  across two services; the docs fix is smaller, and the equality check closes a path
  where `--tier safe` could run an `all` plan.
