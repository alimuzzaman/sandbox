# Phase 0 Research: Owned Storage Authority

> **Planning status: NOT READY.** The retained lifecycle design is conditional
> on a public transaction port that does not currently exist. See
> [analysis.md](./analysis.md).

## Sources and current constraints

The design starts from the current contracts and code, not from a greenfield
storage system:

- Spec 033 already owns screened capture, canonical request/generation
  reconciliation, immutable-generation intent, and current-generation policy.
- Spec 032 already owns durable jobs, immutable terminal results, workspace
  identity, cleanup policy, and bounded CLI/MCP compatibility.
- Spec 041 supplies the shared redaction/disclosure boundary and the rule that
  MCP exposes only registered, bounded operations.
- Spec 045 supplies the closest repository precedent for a dedicated service
  identity, fixed lifecycle, exact peer observation, closed-by-default support,
  and the distinction between local implementation and reviewed live Linux
  evidence.
- `sandbox/transports/remote_sync.py` currently publishes under the submitting
  remote user. `sandbox/application/workspace_service.py` correctly stops final
  cleanup with `workspace_identity_bound_removal_unavailable` after emptying the
  quarantined checkout because its final parent/name boundary is not privately
  owned by a separate authority.

Primary platform references used for the Linux mechanism are the Linux
[`openat2(2)`](https://man7.org/linux/man-pages/man2/openat2.2.html),
[`renameat2(2)`](https://man7.org/linux/man-pages/man2/renameat2.2.html), and
[`unix(7)`](https://man7.org/linux/man-pages/man7/unix.7.html) interfaces; the
systemd [`systemd.exec`](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml)
service/identity contract; and SQLite's
[`atomic commit`](https://www.sqlite.org/atomiccommit.html) and
[`WAL`](https://www.sqlite.org/wal.html) documentation. These sources support
the mechanism choice only. Live qualification remains mandatory.

## Decision 1: Dedicated static service identity and private root

**Decision**: Install one named, non-login system account for the owned-storage
service through a fixed sysusers asset. The service account owns one fixed state
root and one runtime socket directory. The submitting owner, CI workload, and
ordinary Sandbox process receive no write permission to the root, database,
object parents, staging, quarantine, or authority records. The service unit
runs unprivileged with no ambient capabilities, `NoNewPrivileges`, a strict
filesystem allowlist, a private temporary directory, and only `AF_UNIX`
networking.

**Rationale**: A separate static UID creates the missing filesystem ownership
boundary and remains stable across service restarts. systemd documents that
dynamic UIDs are recycled after a unit stops; although `StateDirectory` can
mitigate that, the feature needs simple stable owner/recovery evidence for
retained objects and human review. A named static account keeps the observed UID
host-local without hard-coding a numeric UID.

**Alternatives considered**:

- Existing submitting user: rejected because it preserves both verified gaps.
- Root service: rejected because the authority must not become a general host
  mutation boundary.
- `DynamicUser=yes`: rejected for the initial contract because UID recycling
  complicates persistent object ownership evidence and recovery review.
- Per-project service UID: rejected as unnecessary account proliferation; exact
  project isolation is enforced by typed scopes and private records inside one
  bounded service.

## Decision 2: Application policy stays outside the storage mechanism

**Decision**: Existing sync, job, workspace, and resource application services
remain the policy authorities. A new application port resolves the registered
remote/project and authenticates the caller, then passes immutable typed
decisions from inside one fixed supervised application-controller process. The
ordinary CLI and workloads cannot connect to the storage socket. The controller
owns one authenticated connection epoch and monotonic sequence and issues one
durable authorization identity per canonical operation; its exact process,
lifecycle, config, connection, and message credentials are independently
checked before request fields are parsed. Compromise of that separately
reviewed controller identity is the explicit local trust boundary. The
mechanism rechecks exact scope,
canonical digest, stored future policy, stored cleanup policy, service-owned
reference leases, and object identity before effects. CLI/MCP never receives a
mechanism object, path, callback, repository, or raw socket handle.

**Rationale**: Sync knows when a generation is screened and may become current;
jobs/workspaces know terminal state, source-access mode, and cleanup policy;
resources may request observation but must not become deletion policy. Moving
those rules into filesystem code would duplicate owners and let transports
invent policy.

**Alternatives considered**:

- Let the authority read existing sync/job/workspace repositories directly:
  rejected by module boundaries and because cross-repository schema knowledge
  would create a second policy owner.
- Let application code delete files after an authority check: rejected because
  the final mutation would return to the submitting identity.
- Use a generic privileged helper: rejected because a path/command helper would
  exceed the bounded storage authority.

## Decision 3: Canonical typed local protocol with no path fields

**Decision**: The service uses a versioned canonical `AF_UNIX` protocol with
strict schemas, finite messages/deadlines, `SO_PEERCRED` peer identity, and
per-message credentials where the selected socket type supports them. The sole
policy/control peer is a fixed, separately supervised application-controller process. Before
parsing requests, the authority also matches its UID/GID, PID/start identity,
executable digest, unit/cgroup identity, sealed config digest, and current
connection against lifecycle-provisioned expectations. A submitting user,
ordinary CLI process, workload process, or another process with only the same
numeric UID is not an authenticated peer. Requests
contain only operation type, caller/project/relationship/workspace/job/object
identities, request ID/digest, manifest or policy digests, counts, timestamps,
and bounded stream metadata. Publication payload bytes travel on a length-
bounded operation stream after the typed request is accepted. No request field
contains a host path, argv, shell fragment, unit, user, arbitrary JSON, resolver
input, or cleanup selector.

**Rationale**: Linux exposes peer credentials for connected Unix sockets and
supports credential ancillary data. A numeric UID alone cannot distinguish a
trusted controller from an untrusted same-UID process, and caller-supplied
authorization digests are not credentials. Exact process/lifecycle identity,
the authenticated connection, application authorization, and durable scope
must all match. Strict exact schemas stop an opaque ID from becoming ambient
authority.

**Alternatives considered**:

- SSH command strings operating on remote paths: rejected because that is the
  current same-owner boundary and accepts path authority.
- Filesystem inbox/spool writable by the caller: rejected because replacement
  and link races would enter the authority root.
- HTTP/TCP service: rejected because storage authority needs no network or
  resolver capability.
- MCP upload payloads: rejected; MCP remains a bounded control/status adapter,
  while existing sync transport owns source streaming.
- Direct CLI or submitting-user socket access: rejected because the user can
  create another same-UID process and forge caller-supplied fields.

## Decision 4: SQLite journal plus private object tree

**Decision**: Use one SQLite database in the service-owned root for canonical
requests, operations, objects, bindings, policies, references, previews, and
outcomes. Enable foreign keys, bounded busy timeout, WAL, and
`synchronous=FULL`; treat the database and payload filesystem as a recoverable
state machine rather than pretending they form one atomic multi-resource
transaction. Every effect has a durable intent and phase before mutation and a
terminal outcome after it. Payload files and containing directories are
flushed at publication boundaries.

**Rationale**: The repository already uses SQLite for durable job/workspace
state. SQLite provides serialized transactional rows and crash recovery. The
filesystem still needs explicit phase records because database commit cannot be
atomic with a tree rename/removal.

**Alternatives considered**:

- JSON journal: rejected for high-concurrency request replay, relational
  uniqueness, bounded previews, and recovery queries.
- Store payload blobs in SQLite: rejected because generation trees and CI
  workspace interiors need filesystem access and bounded streaming.
- Treat a successful syscall as durable completion without an intent row:
  rejected because lost acknowledgement/restart recovery would be ambiguous.

## Decision 5: Immutable publication under a private parent

**Decision**: The authority alone creates a random staging object under its
private root, receives bounded screened bytes, verifies every manifest member,
digest, count, size, type, and mode, flushes the tree, and moves it with a
no-replace rename into a generation object name derived internally from its
opaque object ID, then flushes the destination parent directory. Only after
that durable filesystem boundary does it record accepted state and the
relationship's current object in one repository transaction. Accepted payload entries are read-only;
the service contract has no edit/replace operation. Exact replay returns the
original receipt and never overwrites a generation.

**Rationale**: `renameat2(RENAME_NOREPLACE)` prevents clobbering an existing
target, while a private parent prevents caller substitution. Consumers select
an exact accepted object and receive read-only access. If the service commits
acceptance but the application loses the reply, Spec 033 reconciliation can
project the original accepted result from the same request identity.

**Alternatives considered**:

- Current symlink in caller-owned storage: rejected because publisher ownership
  defeats immutability.
- Modify accepted files in place for replay or metadata repair: rejected;
  repair creates a new object or remains indeterminate.
- Linux immutable inode flag: rejected as an extra capability-heavy mechanism;
  the private parent and service state machine provide the product boundary
  while still allowing authorized retention cleanup.

## Decision 6: Writable CI interior is namespace-mounted, not path-granted

**Decision**: A CI materialization is an authority-owned object root with a
separate writable `work` interior. The root, record, control metadata, accepted
generations, and managed source stay authority-owned. On a distinct
purpose-bound local channel, the authority passes only exact opened `O_PATH`
directory FDs with `SCM_RIGHTS` to one fixed supervised runtime mount
controller. The controller has mount authority only inside a dedicated
user/mount namespace, never the initial host user namespace, accepts no path,
and exposes only `work` read-write plus accepted source read-only in the exact
job namespace. The authority authenticates its process/lifecycle identity and
independently verifies returned mount identity and access-mode evidence before
recording one exact
materialization lease bound to project, job, workspace, object, lifecycle
generation, mount identity, and deadline. Closing/revoking that lease removes
workload access before cleanup. Modes that cannot prove this private mount and
access revocation are unsupported.

**Rationale**: Merely changing Unix modes on a host path cannot distinguish an
authorized workload from another process running under the same submitting
UID. A namespace-scoped mount lets the workload edit its declared interior
without giving it the service-owned parent name needed to replace/delete the
root or reach another object.

**Alternatives considered**:

- Chown the whole materialization to the submitting user: rejected because the
  submitter could replace the root and retain access after terminal state.
- Group-write the private parent: rejected because sibling/root replacement
  becomes possible.
- Allow current unisolated host/`act` execution: rejected until a live adapter
  proves equivalent namespace and lifecycle controls.
- Give the storage service host `CAP_SYS_ADMIN`: rejected because physical
  storage ownership must not become general host mount authority.
- Return a private path to job/application code: rejected because it would
  bypass the descriptor- and identity-bound mount contract.

## Decision 7: Identity-bound final cleanup uses private quarantine

**Decision**: Cleanup is serialized by canonical object ID. After a durable
intent and fresh policy/reference checks, the Linux adapter opens the private
root with `openat2` beneath/no-symlink constraints, opens the exact object by
directory FD, compares stored device/inode/mount/marker evidence, and uses
`renameat2(RENAME_NOREPLACE)` to move it into an operation-specific quarantine
owned by the service. It recursively unlinks beneath the opened quarantine FD
without following links. The final `rmdir` is by an internally generated name
under a private, locked service-owned parent; no caller can insert a
replacement. Device/inode and authority marker evidence are rechecked before
the final name removal. After descendants are gone, the authority flushes a
`final_remove_intent` binding the exact empty entry, opened identity, and private
parent generation; it then removes the name and flushes the parent. Recovery
may finalize absence only from that committed phase or an existing terminal
receipt. Unknown or changed evidence retains the object.

**Rationale**: Directory-FD APIs avoid path-prefix races, and Linux `openat2`
can prohibit escapes and symlinks. The current implementation cannot prove the
last name-based removal because its parent is not a separate private authority.
Moving the whole lifecycle under a private parent is what makes the final step
safe; a file descriptor alone does not make `rmdir` identity-based.

**Alternatives considered**:

- Continue after recursively emptying a caller-owned quarantine: rejected; it
  reproduces the current explicit failure.
- Infer safety from pathname, UID, age, or missing directory: rejected by the
  spec.
- `rm -rf`, broad find/prune, or storage-pressure cleanup: rejected as
  path-based and unbounded.
- NFS/network filesystems: rejected for the initial support matrix because
  rename failure/replay semantics and server-side identity need separate proof.

## Decision 8: Replay and restart recovery are operation-owned

**Decision**: Canonical request identity is unique within the exact operation
scope. Reuse with a different digest is a stable conflict. State transitions
record `reserved`, `receiving`, `verified`, `published`/`quarantined`, and a
terminal outcome. Startup begins mutation-closed, checks service UID/root/db
identity, reconciles incomplete staging and quarantine rows, and only then may
open mutation admission under one shared predicate: future policy,
`qualification:null`, active binding, exact promotion/scope/revisions, and
either supported/proven/complete or validation-pending/implemented-unproven/
pending-ordinary exact disposable-fixture state; otherwise only an exact
qualification admission may mutate.
An absent object is `completed` only when a matching durable terminal receipt,
or a matching flushed `final_remove_intent` for the exact empty private entry,
proves its removal; otherwise it is `indeterminate` and retained as an
audit/recovery item.

**Rationale**: This matches the current Spec 033 lost-ack rule and Spec 032
durable acceptance rule. Recovery never invents a second request identity or
guesses from path absence.

**Alternatives considered**:

- Blindly retry transfer/removal after timeout: rejected because the first
  effect may have completed.
- Delete orphan staging/quarantine at startup by age: rejected because age is
  not authority.
- Roll back an accepted generation after application projection failure:
  rejected; reconcile the projection instead.

## Decision 9: Current/reference and cleanup truth remain separated

**Decision**: The authority owns the durable accepted object and its internal
relationship-current selection. Spec 033 remains the application projection
and job-launch policy owner. The authority owns cleanup intent/outcome and
measured reclaimed bytes; Spec 032's immutable terminal result remains
unchanged and its cleanup projection is additive. Reference leases and current
selection are checked both before preview and immediately before cleanup.

**Rationale**: One component must own the physical object truth, while existing
application contracts must remain compatible. A lost projection update is
recoverable from the authority receipt. A cleanup failure must never rewrite a
successful/failed job result.

**Alternatives considered**:

- Make the existing sync journal the storage owner: rejected because it is
  writable by the submitting identity and not on the remote private boundary.
- Let cleanup completion determine job success: rejected by Spec 032.
- Use resource inventory as deletion truth: rejected; inventory is observation
  only and incomplete observations retain.

## Decision 10: Future-only compatibility and rollback

**Decision**: `legacy` remains the default policy. A confirmation- and
request-ID-gated policy transition to `future` is permitted only on a proven,
human-approved platform and affects later objects only. Switching back to
`legacy` stops new authority creation but does not relocate, adopt, copy back,
or delete existing authority objects. Owned objects remain service-managed even
when creation is paused.

**Rationale**: This preserves all existing outcomes and provides a bounded
rollback control without false legacy ownership. It satisfies Constitution VI
and the feature's explicit non-adoption scope.

**Alternatives considered**:

- Automatic adoption/migration: rejected as out of scope and unsafe with
  incomplete ownership evidence.
- Immediate global default: rejected because live proof and human review are
  explicit adoption gates.
- Disable the service and copy objects to caller-owned paths on rollback:
  rejected because it destroys the immutability boundary.

## Decision 11: Qualification, ordinary proof, and promotion are separate

**Decision**: Capability reports distinguish `unavailable`, `unsupported`,
`implemented_unproven`, `proven`, and `drifted`; only `proven` is generally
adoptable by normal project policy. Initial primitive proof uses one separately authorized,
short-lived qualification admission on a new Ubuntu 24.04/systemd 255
disposable remote. The admission is minted by the supported lifecycle for one
exact clean source/installed revision, controller identity, remote, project,
fixture, operation budget, deadline, and evidence candidate. It is consumed
only by the fixed acceptance harness through the authenticated controller,
cannot set `adoptable=true` or normal policy `future`, cannot name arbitrary
objects/paths, and is closed after the bounded run. Failed or incomplete
cleanup rejects the proof and retains evidence; it never broadens admission.

Under that admission, proof requires private UID/root evidence, the
caller/workload negative matrix, atomic publication, 100 interruption/restart
trials, 100 cleanup replay/race trials, namespace write boundaries, measured
reclamation, service/package lifecycle, restart recovery, and unrelated-state
preservation. This closes one immutable evidence candidate but does not prove
the normal `future` policy branch.

The protected remote lifecycle, not the storage authority, owns candidate
closure, review, promotion, revocation, and capability projection. A human
review binds the exact closed candidate/close generation/digest, cleanup
digest, source and installed revisions, controller identities, disposable
scope, reviewer authorization, decision, freshness, request ID, and canonical
request digest. This review is unique and replay-safe in the lifecycle
repository and consumes no storage operation or qualification-admission budget.
Rejection terminates the candidate; revocation is a separate replay-safe
operation over an existing promotion.

The cross-store handoff follows the Feature 050 custody pattern rather than
claiming a transaction across repositories. Under the existing Feature 051
shared target transaction, the lifecycle durably reserves the review and
preallocates the decision, promotion, authority-binding identities, and binding
digest. The storage authority records that exact non-authorizing `prepared` adoption
binding containing exact candidate, promotion, revision, scope,
lifecycle-generation, expiry, and revocation identities. The lifecycle then
atomically commits its review decision, promotion receipt, and capability
projection as a closed nested value through the shared hosting
`RecoveryRepository`. Exact replay activates the matching authority binding
only after that receipt exists. `adoptable=true` requires both records;
any mixed or unknown state remains non-adoptable. Revocation commits
non-adoptable lifecycle state first, then deactivates the binding; a lost second
acknowledgement stays fail-closed and replay-reconcilable.

The validation promotion remains `implemented_unproven`/non-adoptable and may
open `future` only for the exact disposable fixture. After promotion, that
project sets real `future` policy.
Ordinary `sync once` and `ci run` use normal public schemas and reach the storage
service with `qualification:null`; objects bind the policy plus current
promotion/evidence, never the closed qualification admission. The journey
proves normal publication, materialization, cleanup, exact replay/conflict,
job-result preservation, unrelated-state preservation, and rollback to
`legacy`. A protected replay-safe acceptance-finalization operation accepts
only promotion/request identity and confirmation, derives every evidence item
through typed read-only ports, and under shared target CAS either commits the
closed ordinary evidence plus `supported`/`proven`/adoptable or commits failure
and non-adoptable before binding revocation. Any failure triggers protected revocation before support is claimed.
Review/promotion/revocation are never authority-service or MCP operations.
Expiry, revision skew, later drift, or revoke closes adoption while retaining
objects.

**Rationale**: Spec 045 explicitly distinguishes implementation from live
kernel/systemd evidence. Storage ownership and final removal depend on the same
kind of live facts. A capability probe is evidence input, not self-promotion.

**Alternatives considered**:

- Promote after unit/contract tests: rejected by Constitution IV.
- Treat qualification as full ordinary-path proof: rejected because its hidden
  admission does not exercise `future + qualification:null` routing.
- Persist review as a storage-authority `review` operation: rejected because
  storage mechanism ownership must not become support/promotion authority and
  because review must not consume qualification budget.
- Claim one atomic write across lifecycle and authority repositories: rejected;
  one semantic owner plus a prepared non-authorizing binding is fail-closed and
  replayable without hiding split durability.
- Create a Feature 052 hosting state database/file: rejected; Feature 051's
  shared `RecoveryRepository` remains the sole outer parser/writer/lock/fsync
  owner, and Feature 052 only validates a closed nested value through its port.
- Expose a force/unproven flag on ordinary policy or publication: rejected
  because it would be a reusable adoption bypass rather than a proof seam.
- Allow an operator force flag on unsupported hosts: rejected because no
  preference can supply missing ownership/final-removal guarantees.
- Let storage qualification imply resolver qualification: rejected; the
  authorities and proof sets are disjoint.

## Decision 12: Packaging and lifecycle are fixed and revision-bound

**Decision**: Ship the storage service, policy controller, mount controller, and
their fixed systemd/socket/sysusers assets in both npm and runtime tarball manifests. Spec Kit artifacts remain
pruned. Installation/upgrade uses the supported remote Sandbox lifecycle only,
derives fixed destinations, verifies source and installed revision/digests,
creates/observes the named service account and root, starts closed, reconciles,
then reports capability. Stop closes mutation admission, drains bounded
operations, preserves owned objects/database, proves process/socket absence,
and never deletes retained storage. Uninstall or data removal is a separate
destructive, confirmation-gated lifecycle outside this feature.

**Rationale**: A code-only service that is absent or skewed remotely cannot
support the protocol. Fixed assets avoid caller-selected units/properties, and
preserving state on stop keeps restart and rollback safe.

**Alternatives considered**:

- Raw SSH edits or ad hoc systemd commands: rejected by repository lifecycle
  and revision rules.
- Install through the public command as a side effect of status/publication:
  rejected; privilege grants and rollout require separate authorization.
- Remove the private root during ordinary stop/update: rejected as destructive
  and incompatible with retained object truth.

## Decision 13: Public evidence is bounded and secret/path-free

**Decision**: Status, preview, policy, publication, reconciliation, retention,
and cleanup projections contain stable opaque identities, digests, counts,
lifecycle/policy/outcome codes, safe timestamps, known aggregate bytes,
capability tier, revision, and evidence ID. They omit source contents, entry
names, credentials, request bodies, argv/environment, raw UID/GID/PID, unit
properties, socket addresses, filesystem paths, host configuration, and
unrelated state. CLI and MCP use the shared redaction service and the same
allowed-field projectors. Unknown bytes never enter reclaimable totals.

**Rationale**: The evidence must explain decisions without becoming a storage,
secret, or host-discovery interface. This follows Spec 041's parity boundary and
the bounded result conventions in Specs 032/033/045.

**Alternatives considered**:

- Expose internal paths for operator debugging: rejected; use local privileged
  lifecycle diagnostics under separate authority if implementation needs them.
- Return raw exceptions/service journal: rejected because they can contain
  sensitive host details.
- Treat redaction as permission to emit arbitrary fields: rejected; fields are
  allowlisted first, then redacted defensively.

## Decision 14: Resolver authority remains physically absent

**Decision**: The service permits only `AF_UNIX`, accepts no hostname/IP/domain,
does not invoke resolver/network/ingress helpers, and has no resolver adapter or
module dependency. The remote application transport selects an already
registered remote before contacting its co-located storage service. Capability
and evidence schemas report storage and resolver support as separate fields and
never infer one from the other.

**Rationale**: Resolver mutation has different ownership, atomicity, and cleanup
contracts. Excluding networking from the service unit makes the boundary easier
to verify, not merely a documentation claim.

**Alternatives considered**:

- Combine storage and resolver helpers because both may need systemd: rejected
  as an authority expansion.
- Let storage cleanup remove resolver records for a deleted workspace: rejected;
  resolver cleanup remains its owning service's separately authorized action.

## Decision 13: Current immutable public ports cannot carry the lifecycle value

**Decision**: Planning remains NOT READY. Independent analysis inspected the
actual Feature 051 interfaces after the semantic design was repaired.
`target_mutation_port()` accepts only its existing fixed capability registry,
which has no owned-storage lifecycle member. `activation_host_state_port()` is
activation-specific and commits only the closed `image_activation` value. It
cannot store the Decision 11 lifecycle value without an immutable hosting or
schema edit, a private helper bypass, or reuse of an authority name with the
wrong meaning.

**Rationale**: A plausible state machine is not an executable plan when its
only persistence seam rejects the required state. Treating an accepted
capability as a generic lock would weaken the explicit capability boundary and
hide the missing authority.

**Alternatives considered**:

- Reuse `sync` or `activate`: rejected because neither capability grants
  lifecycle review/promotion authority.
- Import private `RecoveryRepository` record/write helpers: rejected because it
  bypasses the public owner and lock/CAS contract.
- Store a Feature 052 hosting sidecar: rejected by FR-058 and the single-owner
  requirement.
- Silently extend Feature 051 or `sandbox/hosting/**`: rejected by the explicit
  immutable-input instruction.

## Clarification status

The planning blocker is RESOLVED. Under explicit human authorization (Option 2),
FR-058 is amended to select a dedicated `StorageAuthorityLifecycleRepository` as the
durable semantic owner for owned storage lifecycle, decoupling it from OCI hosting
(`hosts.json` / `RecoveryRepository`). Protected paths (`sandbox/hosting/**`,
`specs/048-051/**`) remain 100% immutable. Planning and task generation are
unblocked; implementation remains gated.
