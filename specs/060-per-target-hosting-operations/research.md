# Research: Per-Target Hosting Operations

## R1. Where coordination authority lives

- **Decision**:
  - **Program.** One fixed coordination program is sent over `ssh_run`,
    following the 061 pins pattern. It runs under `flock` on
    `$SANDBOX_HOME/runtime/hosting-leases/coord.lock` and keeps owner-only
    JSON state.
  - **Check.** Every call first checks the runtime's `hosting_coordination`
    capability marker, which is written by `remote service migrate` at this
    feature's revision.
  - **Failure.** A missing marker, an SSH failure or a 15-second timeout makes
    the call fail with `lease_authority_unavailable`, naming the
    migrate/repin remedy.
- **Rationale**:
  - The pins pattern is already proven under contention, and it needs no new
    long-running daemon or network listener.
  - The marker makes "runtime not migrated" exact rather than inferred.
  - A single flock gives linearizable admission across controllers.
- **Alternatives considered**:
  - A new control-service (MCP) endpoint. Rejected because it needs the
    control route on every remote, and nginx front-door remotes restrict
    routes.
  - Controller-local locks. Rejected: that is today's defect.

## R2. Lease model, renewal and fencing

- **Decision**:
  - **Admission.** `admit` creates a lease
    `{lease_id, fencing_token, holder:{operation, request_id, controller_id, session}, started_at, expires_at}`.
    The TTL is 90 s. `fencing_token` is a per-target monotonic integer.
  - **Renewal.** A daemon thread in the controller renews every 20 s.
  - **Lost lease.** A renewal that finds the lease missing, or the token
    superseded, sets a lost flag.
  - **Fenced effects.** Every protected effect checks the flag and passes the
    token to the remote:
    - the delivery phase submit;
    - the Compose step;
    - initializers;
    - activation;
    - the edge transaction.

    The remote phase launcher refuses a stale token with `lease_lost`.
  - **Returning holder.** A holder whose lease was lost records its 054
    operation as `effect_unknown` with reason `lease_lost`, and starts no
    further forward effect. Rollback is permitted only after re-admission
    with a new lease, as a recovery operation.
- **Rationale**:
  - Renewal keeps a slow but live holder (FR-016).
  - Fencing at the remote makes FR-017 hold even against a paused process.
- **Alternatives considered**: a longer TTL with no renewal. Rejected: a
  build can exceed any fixed TTL.

## R3. Expired lease with a dispatched phase

- **Decision**:
  - When a lease is admitted, the remote record carries `phase` (null, or
    `{phase_id, dispatched_at}`), updated by `phase-report` before and after
    each remote phase.
  - On expiry with a non-null phase, the target enters `fenced_pending_cessation`.
  - `admit` then checks cessation through the existing phase status probe
    (the durable job record or the activation journal on the remote). Until
    the phase is terminal or gone, `admit` refuses
    `predecessor_phase_running` with the predecessor's operation identity.
  - Once the phase has ceased, `admit` returns, and the controller-side 054
    admission runs its ordinary fence checks (054 FR-008, 062), which may
    still refuse.
  - The coordination program never clears a recovery fence.
- **Rationale**: Cessation is necessary but not sufficient (spec FR-018), and
  fences stay owned by 054 and 062.

## R4. Waiting, queueing and the caller's choice

- **Decision**:
  - **Flags.** `--wait SECONDS`: default 600, maximum 3600, and 0 means
    refuse immediately. `--lock-wait` stays as a deprecated alias.
  - **Queue entries.** A waiter registers a queue entry
    `{waiter_id, holder summary, enqueued_at, deadline}` and polls `admit`
    every 2 s. Entries carry deadlines, so an interrupted waiter's entry is
    removed at its deadline or at the next admit that sees it expired. A
    clean interrupt removes it immediately.
  - **Ordering.** Admission is FIFO among live queue entries.
  - **Reporting.** While waiting, the CLI prints one line per holder change.
    JSON mode emits one final result.
- **Rationale**: FR-012..FR-014. FIFO avoids starvation. Deadline-bound
  entries need no reaper.

## R5. Holds

- **Decision**:
  - **Model.** A hold is a lease with `kind: hold`, a purpose (at most 120
    printable characters), `hold_id` `hd-` plus 16 hex digits, `claimed_at`,
    and `expires_at` no later than `claimed_at` plus 4 h. A hold has no
    renewal thread; it is renewed explicitly.
  - **Presenting the hold.** `--hold-id` or `SANDBOX_HOLD_ID`. Operations
    presenting a matching id are admitted under the hold, and a nested
    operation lease is recorded beneath it.
  - **Expiry.** It removes only the hold. A nested phase follows R3.
  - **Break.** `--break-hold` needs `--reason` (at most 200 characters). It
    writes a history entry `{broken_hold, breaker_controller, reason, at}`.
- **Rationale**: Spec FR-019..FR-021, with ids that are secret-free and
  unguessable enough that they cannot be presented by accident.

## R6. Build cap

- **Decision**:
  - **State.** `remote.json` holds `{build_cap: 2, cap_history: [...], build_slots: [...]}`.
  - **Use.** `build-acquire` and `build-release` wrap the build phase only. A
    slot carries the lease id and expires with the lease.
  - **Changes.** `sb remote build-cap <remote> --set N --confirm` changes the
    cap and records the controller id and time.
  - **Reads.** The listing shows the cap and the slots.
- **Rationale**: FR-022 and FR-023. One value per remote, held remotely.

## R7. Remote-wide lease (shared steps)

- **Decision**:
  - `shared-acquire` and `shared-release` hold a single remote-wide lease
    with a hard 60 s TTL and no renewal.
  - `RemoteWideLease` implements the 064 seam
    (`acquire(bound_s)` / `release()`) and wraps:
    - Caddy and nginx edge configuration;
    - DNS changes;
    - the ingress reload.
  - The existing host-global Caddy flock stays inside it as a host-level
    guard.
  - Reclaim checks for a live remote-wide lease and for the edge and pool
    locks, and reports `host_reclaim_busy` while any is held.
- **Rationale**: FR-006..FR-008 and FR-029. Edge contention lasts seconds.

## R8. Controller state partition

- **Decision**:
  - **Layout.** `hosts.json` becomes `runtime/host-targets/<sha16(state_key)>.json`.
    Each document holds one target record plus its `state_key`.
  - **Writes.** A write takes a per-target flock (the existing effect lock),
    then an atomic replace.
  - **State lock.** `RecoveryRepository.state_lock` is kept only for
    conversion and for the legacy reader.
  - **Accessors.** `load_host_state()` / `save_host_state()` callers move to
    `load_target_state(key)` / `save_target_state(key, record)`.
  - **Enumeration.** Code that enumerates all targets (the listing, reclaim
    exclusion, and doctor) uses `iter_target_states()`, which reads each file
    independently and skips a corrupt file with a per-target error.
  - **Delivery records.** They are already in SQLite keyed by scope digest
    and need no partition. Only 062's scope-key change applies.
- **Rationale**: FR-025. A corrupt or locked file affects only its own target.
- **Implementation note (T008)**: the per-key accessors sit behind the
  existing `load_host_state()` / `save_host_state()` and `RecoveryRepository`
  load/write, so no caller changed shape. `layout.compose` reads a converted
  remote's targets from their own files; `layout.persist` writes only the
  targets whose canonical JSON changed since load, deletes removed ones, and
  refuses to replace a file that could not be read. A remote that is not
  converted keeps using `hosts.json` exactly as before. Narrowing
  `state_lock` to conversion and legacy use lands with the target lease (T012),
  which is what makes holding it across a whole operation unnecessary.

## R9. Conversion and mixed state

- **Decision**:
  - **Command.** `sb host convert-state --remote R [--confirm]` is a plan,
    then an apply. In order, it:
    1. copies each target record of R into its own file, verifying a
       byte-identical JSON round trip;
    2. rewrites 062's scope keys in a SQLite transaction;
    3. registers the controller id with the remote coordination store
       (`controllers` list);
    4. marks R as converted in `hosts-conversion.json`;
    5. removes R's records from `hosts.json` last.

    Each step is idempotent, and a re-run resumes.
  - **Mixed state.** The controller is mixed when `hosts-conversion.json` says
    `in_progress`, or when the remote store has coordination enabled and this
    controller still has R records in `hosts.json`. In that state every
    hosting mutation for R refuses `hosting_state_mixed` and names the
    command.
  - **Older controllers.** A controller older than this feature gets 061's
    `protocol_too_old` verdict, because the spoken protocol is bumped and the
    remote's oldest served protocol is raised for hosting mutations.
- **Rationale**: FR-026..FR-028. The 062 scope key rides along (plan
  decision: this feature owns the shared conversion and fixtures).

## R10. Retained refusals

- **Decision**:
  - Busy, cap, `lease_authority_unavailable`, `predecessor_phase_running` and
    `hosting_state_mixed` refusals call the 054 pre-admission refusal
    recorder, with `{code, target, holder|cap|hold, wait_seconds}`.
  - They advance no generation and create no operation that needs retiring.
  - `sb delivery inspect` shows them, and a job submission returns them as a
    typed result.
- **Rationale**: FR-015. These are spec 054 outcomes, not a parallel
  mechanism.
