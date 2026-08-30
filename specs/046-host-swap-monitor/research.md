# Research: Remote Host Swap and Memory Monitor Commands

## R1 - Command shape and integration surface

**Decision**: add four flat actions to the existing global `resources` command:
`swap-status`, `swap-plan`, `swap-apply`, and `swap-history`. `swap-plan` takes
`--operation enable|disable`; `swap-apply` accepts only a stored `--plan-id` and
`--confirm`.

**Rationale**: `sandbox/commands/resources.py` already owns a single required action and
strictly rejects action-specific flags. Flat additive choices preserve existing parsing,
help, command ownership, and status/plan/cleanup semantics. Apply cannot accept size,
operation, or path inputs, so the caller cannot change the reviewed intent at execution.

**Alternatives considered**: a new top-level `swap` command (rejected because the feature
belongs to the established resource family); nested `resources swap ...` parsers (rejected
because they require a wider parser refactor); overloading storage `status/plan/cleanup`
(rejected because disk cleanup and host swap have different safety models).

## R2 - Controller/remote service split

**Decision**: controller-side `HostMemoryService` owns plan construction, local immutable
plan persistence, output semantics, and same-intent reconciliation. A co-located Linux
provider behind the authenticated `/resources` endpoint owns host observation, root lock,
fixed artifact rendering, phase journal, mutation, verification, receipt, history, and
rollback. The request contains typed policy and identity fields only.

**Rationale**: the current resource adapter already uses fixed authenticated control HTTP.
Keeping host mechanics co-located allows each consequential phase to re-read live state and
durably record progress even if the controller disconnects. It also avoids executable source,
shell, argv, or arbitrary path input on the wire.

**Alternatives considered**: direct SSH commands (rejected by the specification); shipping
a controller-generated script (rejected as an unrestricted privileged surface); computing
and executing every phase controller-side (rejected because transport loss would erase the
only phase authority).

## R3 - Remote service ownership and revision proof

**Decision**: every host-memory control response carries strict service evidence:
`ownership_marker`, `runtime_revision`, `resource_schema`, and `host_memory_schema`. The
controller compares the first two to the registered remote service record and its own
runtime digest before accepting any observation or mutation. Missing, malformed, old, or
mismatched evidence returns a typed refusal and never falls back to SSH. Feature 046 does
not migrate or repair the remote service.

**Rationale**: the existing service lifecycle contract already records a Sandbox ownership
marker and a digest over the shipped CLI/MCP Python surface. Surfacing those facts through
the authenticated endpoint gives this feature a bounded preflight without the current
SSH-based service inspection path. The new protocol is unavailable until the supported
Sandbox lifecycle update has installed a matching runtime.

**Alternatives considered**: trusting only successful bearer authentication (rejected
because an old but authenticated service may not implement the contract); invoking remote
service status over SSH (rejected by the no-direct-host-access boundary); silently accepting
resource schema 1 without host-memory capability (rejected as client/controller skew).

## R4 - Plan identity, freshness, and replay

**Decision**: plans are immutable, owner-only, valid for 15 minutes, and bound to the
registered target identity, service marker/revision, operation, requested/effective policy,
canonical observation digest, eligibility calculations, intended artifact digest set, and
rollback scope. `plan_id` is a canonical SHA-256 identity; confirmed apply derives one stable
operation identity from it. A used plan remains replayable only for that same operation so
unknown delivery can reconcile rather than create a second intent. Expiry gates first
acceptance only; a journaled operation that was accepted while current remains reconcilable
under the same identity after the plan's review window closes.

**Rationale**: the existing cleanup store demonstrates atomic target-bound plans, but its
single-use terminal model cannot reconcile an ambiguous privileged operation. A dedicated
repository preserves the same-intent record and remote journal until a truthful terminal
state exists. Apply revalidates all live inputs even inside the freshness window.

**Alternatives considered**: a caller-selected request ID (rejected because it could detach
identity from the reviewed plan); single-use plans (rejected because ambiguous delivery
would force unsafe guessing); unlimited freshness (rejected because RAM, disk, swap, and
ownership drift quickly).

## R5 - Fixed Linux ownership model

**Decision**: the provider manages one fixed root-owned swap file at
`/var/lib/sandbox/host-memory/sandbox.swap`; state at
`/var/lib/sandbox/host-memory/{receipt,operation}.json`; a transient lock at
`/run/lock/sandbox-host-memory.lock`; a swap unit whose exact name is derived with
`systemd-escape --path --suffix=swap` from that fixed file; the fixed sysctl drop-in
`/etc/sysctl.d/90-sandbox-host-memory.conf`; fixed monitor service/timer and logrotate files
named `sandbox-host-memory-monitor`; the fixed helper
`/usr/local/libexec/sandbox-host-memory-monitor`; and owned history rooted at
`/var/log/sandbox/host-memory.jsonl`. It validates every ancestor, type, owner, mode, link
count, content digest, and current effective state before use. Unknown or conflicting
artifacts and any active/persistent unowned swap are observed but never adopted. The swap
unit provides boot configuration; the sysctl drop-in owns global `vm.swappiness=15`; a
oneshot service and timer own sampling. Raw paths remain internal and are never status,
history, receipt-display, or error fields.

**Rationale**: fixed logical artifact IDs and exact receipts keep authority narrow and make
rollback enumerable. A swap unit avoids editing unrelated `/etc/fstab` lines. Because
swappiness is global, any unmanaged swap or conflicting persistence must refuse the entire
lifecycle.

**Alternatives considered**: arbitrary user paths (rejected as an unrestricted root file
surface); adopting a pre-existing swap file (rejected because ownership is unprovable);
editing `/etc/fstab` in place (rejected because rollback could damage unrelated entries);
third-party swap daemons (rejected by scope and dependency constraints).

## R6 - Eligibility and transition ordering

**Decision**: pure policy evaluates size 1-8 GiB inclusive, no more than 50% of physical RAM
or 10% of the relevant filesystem, while leaving at least max(10 GiB, 15% of filesystem)
free. Disable requires available RAM to be strictly greater than current swap use plus
max(1 GiB, 10% of RAM). Equality follows the specification exactly. The provider locks,
journals prior state, revalidates before each phase, stages and validates owned artifacts
before activation, verifies each effective state, and delays destructive removal until
swapoff and final-state proof are safe.

**Rationale**: pure calculations give deterministic boundary tests; host-side repetition
prevents a stale controller plan from authorizing a changed host. Ordering minimizes the
number of phases where rollback would require removing active swap. No phase reports success
from command exit alone.

**Alternatives considered**: controller-only validation (rejected as race-prone); best-effort
cleanup after any failure (rejected because swapoff can create memory pressure); accepting
equal disable headroom (rejected because the requirement says strictly greater).

## R7 - Monitor data, freshness, warning, and retention

**Decision**: each sample is strict JSON containing UTC time, validity, aggregate
`/proc/meminfo` RAM/swap counters, aggregate PSI memory values when supported, and a bounded
allowlist of cumulative `/proc/vmstat` pressure counters. No unknown keys are retained.
Sampling defaults to five minutes and must finish in five seconds; the service/timer cannot
overlap itself. Freshness includes the exact boundary through two intervals plus one minute.
A sustained-use warning requires three consecutive valid samples with swap used at least
512 MiB. Current plus eight owned weekly files are rotated before exceeding either eight
historical files or 32 MiB total.

**Rationale**: kernel aggregate files answer operational pressure questions without process,
argv, environment, or path data. Strict schemas make privacy testable and prevent malformed
foreign content from becoming output. PSI remains separately labeled so swap use alone is
never called thrashing.

**Alternatives considered**: process tools such as `ps`/`top` (rejected by privacy); system
journal as the only store (rejected because per-feature file/count/byte retention cannot be
proved); treating swap use as pressure (rejected as technically misleading).

## R8 - Bounded history contract

**Decision**: history reads accept an optional UTC range and a limit from 1 through 1,000
(default 288), return newest matching samples, and cap the complete response at 1 MiB. The
result reports requested/observed ranges, valid/malformed/missing counts, freshness,
completeness, and truncation. Oversized, malformed, unowned, or foreign files are counted as
bounded evidence and never copied through.

**Rationale**: 288 covers one day at the default cadence; 1,000 supports longer incident
review while keeping transport and rendering bounded. The contract can truthfully report a
partial window without inventing gaps.

**Alternatives considered**: unbounded tail output (rejected); returning whole retained
files (rejected because eight weeks can be large); silently skipping malformed records
(rejected because it would overstate completeness).

## R9 - Spec 043 and Feature 047 composition

**Decision**: Spec 043 remains the controller-side disk-pressure scheduler/reaper and keeps
its own config, lock, record, and schedule. Feature 046 adds no calls between the two.
Feature 047 may receive an injected, read-only `HostMemoryStatusProjection` from Feature 046
but cannot call its planner/provider/apply paths or read its files.

**Rationale**: disk capacity and RAM/swap pressure have different metrics, schedules,
authority, and recovery. Sharing only the resources family and envelope avoids a second
command namespace without conflating ownership. A typed projection lets later governance
consume evidence while preserving one owner for swap mutation.

**Alternatives considered**: extending the Spec 043 monitor record with RAM (rejected
because it runs on and stores state on the controlling machine); allowing Feature 047 to
reconcile swap (rejected because it would create a second privileged lifecycle owner).

## R10 - Test secrecy and release proof

**Decision**: unit and contract tests build narrow synthetic environment mappings and
strict fake host outputs. They never snapshot, print, assert, serialize, or inherit the full
process environment. Local tests prove policy, protocol, privacy, and state machines only.
Release acceptance requires human review and a separately authorized disposable or
explicitly approved Linux remote; reboot proof is a separate authorized run.

**Rationale**: privileged host code and security-boundary output require stronger evidence
than static/local tests, while test environment leakage is itself a credential incident.
The constitution also reserves "done" for observed running behavior.

**Alternatives considered**: copying `os.environ` into fixtures (rejected as secret
exposure); claiming root-provider behavior from mocks (rejected as insufficient); rebooting
during ordinary acceptance (rejected because reboot is explicitly out of scope without
separate authority).
