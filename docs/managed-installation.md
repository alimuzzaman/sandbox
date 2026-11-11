# Managed installation authority

Managed mode is opt-in. Personal `~/sandbox`, explicit `SANDBOX_HOME` and the
existing personal bootstrap hint keep their selection behavior. The accepted
managed state root is `/var/lib/sandbox`; executable releases belong to root at
`/usr/local/lib/sandbox/releases/<revision>`, policy to root under `/etc/sandbox`,
and ephemeral files to `/run/sandbox`. The fixed non-login service identity is
`sandbox:sandbox`. Actual host allocation and cutover need explicit operator
approval; no CLI reader installs accounts or moves state.

Administrator plans and journals use the separately root-owned
`/var/lib/sandbox-installation` with mode0700. Shared operation lock anchors use
root-owned `/run/lock/sandbox-installation`. These paths keep administrator proof
outside the service-writable state ancestor. Every directory parent and actual
opened descriptor is checked; records publish atomically with directory fsync,
and held locks must still match the same path/inode after acquisition and use.

## Genuine identity in both layouts

The supported owner service lifecycle initializes private
`runtime/installation/identity.json` before starting the selected service.
Explicit initialization is also available to an authorized server operator:

```sh
./sb installation initialize --layout owner --ownership-marker OBSERVED_24_HEX --confirm --json
```

The marker must come from the authenticated service or its approved lifecycle
record. The command returns durable identity only, not a claim that a service is
running. Repeating it preserves random `installation_id` and `incarnation`.
Different machine, layout or service authority refuses replacement.

Full binding reads require actual service context with its current
`SANDBOX_REMOTE_MCP_MARKER`; missing marker never falls back to disk. Query
`GET /hosting/installation` through the authenticated control transport. A
standalone `sb installation status --json` outside that service context can refuse
with `installation_service_unavailable`; it does not guess the live marker.

Both owner and managed layouts expose schema1 identity, current canonical machine
identity, service marker, actual runtime revision, policy revision/digest and
installed versioned execution capabilities. The raw machine-id, source paths,
credentials, listener paths and file descriptors are not in the response. Missing,
malformed, foreign-owned or unsafe metadata refuses without creating identity.

Python consumers use:

```python
from sandbox.installation.binding import read_installation_binding, validate_current
binding = read_installation_binding(home=selected_server_home)
# Revalidate immediately before a bound operation; actor/grant checks are separate.
validate_current(binding, home=selected_server_home)
```

`InstallationBindingError.code` distinguishes uninitialized state, invalid
metadata/authority, unavailable live service, machine/service mismatch, invalid
policy and binding drift. These ports do not authorize a caller or replace the
server's ControlGate/actor/grant policy. 086 owns actual credential-consuming
operations; 085 owns worker/listener mechanisms. Never substitute `OwnerLocal`.

## Policy, capability and maintenance behavior

The initial policy is `admitted-owner-v1`, with `admitted_operator=owner` and
`approved_sudo=true`. Existing admitted tailnet operators retain their intended
owner authority. Policy revision or canonical digest changes invalidate prior
bindings. Owner policy is private and owner-controlled; managed policy is
root-controlled `/etc/sandbox/server-policy.json` and cannot be replaced by a job.
Users with full sudo remain host administrators; directory separation does not
isolate secrets from root.

Trusted installed composition may register a capability through
`installation.extensions.register_capability(name, version, verifier)`. Discovery
advertises it only when its installed verifier returns true for the current
binding. Default is empty. A descriptor, configured name, context frame or future
plan does not prove installed handler/worker/listener readiness. Live descriptors
and sockets remain sibling-owned; no arbitrary client image, uid/home, socket or
path becomes authority.

`installation.extensions.read_extension(name, binding)` reads only source-manifest
names `hosting.baseline.worker.v1` and `hosting.baseline.listeners.v1` from
root-controlled `/etc/sandbox/extensions/<name>.json`. It validates current core
installation authority before and after reading, exact descriptor fields and a
canonical SHA256 digest. The record holds metadata only; supervisor-held socket
objects remain out of band. Worker image ID and source digest are immutable
references, never pull instructions. Listener metadata pins the worker digest,
front door and explicit ingress UIDs. Root registration must grant the selected
service read access without granting write access or widening personal state.
No execution capability follows from a valid registration alone.

Managed HTTP admission is closed unless root's `admission.json` explicitly opens
it. Closed admission keeps authenticated GET installation/diagnostics/inventory
available and refuses effects, including broad MCP. This is maintenance, not a
new reduced operator role. Existing owner-mode admission is unchanged. This
HTTP guard alone does not fence legacy SSH, timers or already-running jobs;
cutover requires independent proof all writers are stopped or fenced.

Supported CLI operations, MCP tool calls including delayed streaming sessions,
HTTP effects, admission ticks and detached job supervisors hold an installation
shared lease. Lifecycle holds its exclusive counterpart. Busy writers prevent
maintenance from closing admission underneath their nested operations. Legacy
writers additionally require the selected old service to stop, explicit
administrator-quiescence acknowledgement, a scoped read-only source seal and
zero open handles, writable mappings, relevant live bind namespaces or unknown
kernel observations. Active workload binds refuse before a workload is touched.
Kernel metadata observation does not choose or replace a container backend.
None of this promises isolation from a root administrator deliberately bypassing
the maintenance window while retaining the intended full sudo policy.
The root journal persists closed admission outside service-writable state;
`prepare_start` recreates ephemeral anchors from that durable record after reboot
and never infers permission to open admission. The current maintenance/kernel
implementation is an unreviewed source checkpoint. Root-run checks and the
install/migration CLI composition remain required before use.

Generic legacy uninstall refuses managed mode/path even if identity is missing.
It must not delete managed volumes/state/credentials/backups. Actual managed
install/update/removal and migration must use reviewed lifecycle adapters, with
explicit root authority and completed prerequisites. Source implementation is
not live acceptance.

## Cutover requirements

Before any real install/migration, pin client/server source and runtime revisions;
verify compatible protocol through the actual supported status path. Stage an
immutable, dependency-prepared root-owned release and confirm numeric identity,
Docker/helper authority, protected policy/broker source registration and private
control credential provisioning through supported broker operations. No secrets
are copied into chat, argv, parent environment or reports. Do not read secret files.

Inventory project/workspace/stack IDs, jobs/deliveries/leases, volume/network/domain
ownership, current bind paths and mixed numeric ownership through supported
adapters. Verify encrypted off-host recovery AND a disposable restore drill bound
to the same inventory. Old lock inodes and live binds cannot be copied as authority.
Missing adapter, coverage, available bytes/inodes, writer proof or parity refuses
cutover. Root Nginx/xCloud 80/443, apex /mcp and shared dev/setup-plan/guided remain
protected. There is no assumed free host test slot.

Use one request/plan identity for staging, verification, activation and recovery;
retain source and receipts. Unknown effect acceptance requires supported
reconciliation, never blind retry. Exactly one service owner may become active,
initially with admission closed. Reconnect controllers to stable installation
identity and verify existing workloads before opening writes. Pre-write rollback
requires fencing the new owner. After new or uncertain writes, stale-source
rollback refuses; reconcile and plan forward repair or proved reverse migration.

Current source-only acceptance and per-host cutover inputs are tracked in
`specs/089-managed-server-installation/tasks.md`. Unchecked runtime tasks remain
unverified. No live migration, restart, permission change or deployment follows
from this document.
