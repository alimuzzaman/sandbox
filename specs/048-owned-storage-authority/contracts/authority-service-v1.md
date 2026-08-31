# Contract: Owned Storage Authority Service v1

## Status and adoption gate

This contract defines a future implementation boundary. It does not install,
enable, qualify, deploy, or migrate a service. Until an exact clean revision
passes the live Linux matrix and independent human review, capability is
`implemented_unproven`, `adoptable=false`, and mutation policy remains
`legacy`.

Normative missing, extra, malformed, stale, contradictory, unbounded, or
unverifiable data is a refusal or indeterminate result. It is never success.

## Authority boundary

One service process runs as the dedicated static `sandbox-owned-storage`
system identity. It owns:

- its fixed private state root and SQLite authority repository;
- staging, accepted object, and quarantine parents;
- canonical operation/replay state;
- physical immutable-generation publication and current selection;
- CI materialization roots, writable-interior leases, and physical cleanup;
- exact object identity, intent/outcome, and recovery evidence.

It does not own source-screening policy, job success, cleanup-policy selection,
workspace identity policy, retention policy, resource-pressure policy, remote
selection, resolver/DNS/ingress/network state, container lifecycle, credentials,
packages, or general host mutation. Existing application services own those
decisions and supply typed projections.

The service accepts no arbitrary path, command, argv, environment, callback,
module name, unit name, UID/GID, socket address, hostname/IP, resolver record,
or caller-selected storage root.

## Service lifecycle

The fixed lifecycle is:

```text
install fixed runtime assets and static system identity (separate approval)
  -> verify executable/unit/config/sysusers digests
  -> start service mutation-closed
  -> observe exact service, policy-controller, mount-controller, socket, root, database, and filesystem mode
  -> reconcile incomplete operations and quarantines
  -> publish read-only capability/status
  -> open normal mutation admission only when support is proven and policy permits
  -> or open one bounded exact-fixture proof admission under separate approval
  -> close admission on stop, drift, expiry, repository/root error, or revision skew
  -> drain bounded operations
  -> stop and prove process/socket absence
  -> retain the private root, database, objects, and recovery rows
```

Ordinary stop/update never removes authority data. The storage service has no
mount capability; the mount controller has authority only inside the dedicated
job user/mount namespace and cannot mount in the initial host namespace.
Uninstall/data purge is a
separate destructive lifecycle not defined here. The service has no Internet
address family and no resolver dependency.

## Transport and encoding

- Local Linux `AF_UNIX` only, from a fixed service-owned runtime directory.
- The sole policy/control connection peer is the fixed supervised application controller.
  Before parsing, the authority requires `SO_PEERCRED`, per-message credentials,
  and matching UID/GID, PID/start, executable, unit/cgroup, sealed config, and
  current connection identity. Socket permissions and a numeric UID are defense
  in depth, not authorization. Direct CLI, submitting-user, and workload socket
  access is refused.
- Control frames are canonical UTF-8 JSON: sorted keys, no insignificant
  whitespace, duplicate keys, floats, booleans in integer positions, unknown
  fields, or strings outside documented bounds.
- Control frame maximum: 64 KiB. Default request deadline: 30 seconds; status
  and preview maximum: 30 seconds. Callers may lower, never raise, fixed limits.
- Publication bytes use a service-issued one-operation stream after `publish`
  admission. Declared length, entry count, total bytes, and deadline are fixed
  before the first byte. The stream carries archive bytes only and cannot name
  a destination.
- The service derives every private locator from stored opaque identities.
- Every response uses a strict versioned envelope and shared path-free evidence
  projector. Raw exceptions and service logs are not protocol responses.

## Common request binding

Every mutating request includes exactly:

```json
{
  "protocol": "owned-storage-authority-v1",
  "operation": "publish",
  "request_id": "opaque-replay-safe-id",
  "request_digest": "sha256:...",
  "remote_identity": "opaque",
  "project_identity": "opaque",
  "authorization": {
    "authorization_id": "opaque-one-operation-id",
    "controller_epoch": "opaque-process-epoch",
    "sequence": 41,
    "caller_identity_digest": "sha256:...",
    "application_policy_digest": "sha256:...",
    "policy_generation": 7,
    "expires_at": "2026-08-31T12:00:00Z"
  },
  "qualification": null,
  "deadline_unix_ms": 1788177600000,
  "input": {}
}
```

Normal operations require `qualification:null`. For an acceptance-harness
operation the authenticated controller substitutes an exact object containing
only `admission_id`, `evidence_candidate_id`, and `fixture_id`; those values
must match its sealed inherited admission and cannot come from public input.

The authority validates the authenticated controller connection/epoch/sequence,
one-operation authorization identity, kernel peer, registered project/remote scope, operation
permission, durable future policy or exact qualification admission, canonical
digest, expiry, and operation-specific evidence before mutation. Possession of
any ID or digest is not authorization.

## Proof-candidate admission

Normal unproven mutation is refused. The only exception is a sealed,
short-lived qualification admission minted through the separately authorized
supported lifecycle for one exact clean revision, installed service revision,
authenticated controller, registered disposable remote/project/fixture,
operation budget, deadline, and evidence candidate. The ordinary CLI and MCP
schemas cannot construct, widen, or reuse it.

The fixed acceptance harness consumes the admission through the authenticated
controller and ordinary application/storage path. It may exercise only the
publication, materialization, reference, cleanup, and reconciliation cases in
the accepted matrix. It cannot set normal policy `future`, set support to
`proven`, make the remote adoptable, select paths, or access another scope.
Expiry, budget exhaustion, drift, conflict, or incomplete cleanup closes the
admission and rejects the evidence candidate. Independent human review remains
the only promotion authority.

The separately protected `remote service owned-storage-review` lifecycle may
submit one exact `review` operation through the same authenticated controller.
Its input is the protected operator-authorization digest, review decision,
closed evidence candidate/digest, exact revisions/scope, request identity, and
freshness only. The authority rechecks those bindings and atomically persists
the review decision, promotion/revocation receipt, and capability projection as
defined by `capability-evidence-v1.md`. It cannot invent a review or accept this
operation from ordinary project CLI/MCP schemas.

## Operations

### `capability`

Read-only. Input names the registered remote identity and optional exact
project identity. Result follows `capability-evidence-v1.md`. It never installs,
starts, repairs, promotes, or changes policy.

### `policy_set`

Input:

```json
{
  "mode": "legacy|future",
  "confirm": true
}
```

`future` requires current `proven`/adoptable capability and is future-only.
`legacy` stops later authority creation but changes no existing object. Exact
replay returns the original transition. No policy operation adopts, moves,
copies back, or removes storage.

### `publish`

Input contains exactly:

```json
{
  "relationship_id": "rel_opaque",
  "workspace_id": "opaque",
  "generation_id": "gen_opaque",
  "manifest_digest": "sha256:...",
  "archive_manifest_digest": "sha256:...",
  "file_count": 12,
  "byte_count": 12345,
  "stream_bytes": 8192
}
```

Preconditions:

- future policy and proven capability are current, or the request is inside one
  exact active qualification admission;
- Spec 033 application projection says the generation is screened and exact;
- no existing request conflict, object conflict, or current-selection drift;
- counts and all stream bounds are within configured limits.

Effect order:

1. Reserve one operation and private random staging object.
2. Receive exactly the declared stream; reject early EOF, extra bytes, timeout,
   unsafe archive types/paths, links, devices, count/size overflow, or drift.
3. Verify exact generation/manifest/archive digest/count/bytes and every entry.
4. Flush payload files and containing staging directories.
5. Record effect intent.
6. Move staging into the private generation parent with no replacement and
   flush the destination parent directory.
7. Transactionally mark the object accepted and update the relationship current
   selection.
8. Return the durable receipt.

The accepted object is never edited or overwritten. Exact replay returns the
same object/receipt. Lost response reconciles this operation; it never uploads
under a second request identity.

### `materialize`

Input contains exact project, job, workspace, source identity or accepted
generation object ID, workspace mode, cleanup policy, and policy digest. It has
no path. Only `isolated` and `ephemeral` modes qualify.

The service creates an authority-owned root plus a distinct writable interior.
It returns an opaque materialization identity and control receipt, not a host
locator. Source initialization occurs only from an accepted authority object or
a separately bounded screened stream under the same publish rules. Legacy
objects are never adopted as materialization roots.

### `reference_open`

Input names the exact accepted generation or materialization object, consumer
kind, job/workspace, access mode, and finite deadline. Generation access is
read-only. Materialization access may be read-write only for its writable
interior. On a separate purpose-bound `SOCK_SEQPACKET` channel, the authority
passes only exact opened `O_PATH` directory FDs with `SCM_RIGHTS` to one fixed
supervised runtime mount controller. That controller has mount authority only
inside a dedicated user/mount namespace, never the initial host user namespace;
it accepts no path and binds the mount to the exact job process/cgroup. The
authority authenticates its UID/GID, PID/start, executable, unit/cgroup, config,
connection, and per-message credentials, then independently checks returned
namespace/mount/read-only evidence before recording an active lease. The public
caller receives only its safe identity/state, never a locator or descriptor.

If private mount isolation, lifecycle binding, or read-only enforcement cannot
be proven, the operation is `unsupported` before access is granted.

### `reference_close`

Closes or revokes the exact lease and records namespace/mount absence evidence.
Missing, stale, or ambiguous absence leaves the lease `indeterminate` and the
object protected.

### `status`

Read-only bounded page over authority objects and supplied legacy projections.
Default 100, maximum 500, opaque exclusive cursor, stable ordering. A partial,
timed-out, or degraded observation stays `ok:true` only when clearly marked
`complete:false`; it never supplies cleanup authority.

### `preview`

Read-only. Input filters by exact project plus optional object kind and bounded
page/overall limit. It records an immutable preview containing at most 10,000
object decisions, current inventory/policy generations, exact evidence digests,
known bytes, and a maximum 15-minute expiry. Unknown bytes are excluded from
estimated reclaimable totals. `complete:false` previews cannot execute.

### `cleanup`

Input contains exact `preview_id`, `object_id`, request identity, and explicit
confirmation. The object must have been `eligible` in the preview. The service
performs fresh caller/policy/lifecycle/current/reference/lease/mount/process/
container/object identity checks immediately before effect.

Effect order:

1. Commit cleanup intent and expected identity/reference evidence.
2. Open private parents with beneath/no-symlink directory-FD constraints.
3. Open and compare the exact object device/inode/mount/marker evidence.
4. Move it with no replacement into an operation-specific private quarantine.
5. Commit `quarantined` phase and flush the parent.
6. Remove only descendants of the opened quarantine FD without following
   links or crossing mounts.
7. Recheck the empty quarantine entry against the opened object evidence.
8. Commit and flush a `final_remove_intent` record that binds the exact empty
   entry, opened identity, private parent generation, and expected outcome.
9. Remove the internally generated empty name under the private locked parent
   and flush that parent directory.
10. Commit terminal outcome and observed reclaimed bytes when known.

An object outside the preview, changed object, replacement, new reference,
stale policy, missing observer, unknown byte result, or identity mismatch is
retained/refused/indeterminate as appropriate. Cleanup never changes the
terminal job outcome/result.

### `reconcile`

Read-only or recovery continuation for the exact original operation/request.
It returns the original terminal receipt, resumes only a safe recorded phase,
or reports unknown/indeterminate. It never accepts a new target or request ID.

## Success envelope

```json
{
  "ok": true,
  "protocol": "owned-storage-authority-v1",
  "operation": "publish",
  "operation_id": "operation_opaque",
  "request_id": "opaque-replay-safe-id",
  "status": "accepted|completed|already_completed|retained",
  "object": {
    "id": "object_opaque",
    "kind": "sync_generation|ci_materialization|retained_artifact",
    "lifecycle": "accepted|active|retained|removed",
    "evidence_digest": "sha256:...",
    "known_bytes": 12345
  },
  "replay": false,
  "complete": true,
  "reason_code": "null|stable_safe_code",
  "observed_at": "2026-08-31T12:00:00Z"
}
```

Fields not applicable to an operation are omitted, not set to guessed values.
`reason_code` is null for accepted/completed/already-completed and required for
retained.

## Failure envelope

```json
{
  "ok": false,
  "protocol": "owned-storage-authority-v1",
  "operation": "cleanup",
  "operation_id": "operation_opaque-or-null",
  "request_id": "opaque-replay-safe-id",
  "status": "refused|unsupported|unknown|failed|indeterminate",
  "code": "stable_safe_code",
  "message": "bounded actionable guidance",
  "retryable": false,
  "object_id": "object_opaque-or-null",
  "complete": true
}
```

Transport loss after possible effect uses `unknown` or `indeterminate`, never a
false refusal/success. Retryability never authorizes a new request identity.

## Stable reason codes

At minimum:

`authority_unavailable`, `authority_unsupported`, `authority_unproven`,
`authority_drifted`, `authority_revision_mismatch`, `caller_unauthorized`,
`caller_revoked`, `cross_project_refused`, `request_invalid`,
`request_id_conflict`, `policy_not_future`, `policy_stale`, `object_unknown`,
`object_not_owned`, `object_identity_drift`, `object_replaced`,
`generation_binding_mismatch`, `generation_already_exists`,
`unstable_capture`, `storage_exhausted`, `reference_active`,
`reference_unknown`, `workspace_active`, `workspace_lease_active`,
`workspace_index_incomplete`, `retention_missing`, `retention_active`,
`preview_incomplete`, `preview_expired`, `preview_stale`,
`object_not_previewed`, `cleanup_already_completed`, `cleanup_failed`,
`cleanup_indeterminate`, `transport_unknown`, `deadline_exceeded`, and
`internal_indeterminate`.

## Recovery rules

- Startup mutation admission is closed until root/database/service identity and
  incomplete operation reconciliation finish.
- Unaccepted staging is removed only when its exact operation row proves it was
  never effectful; otherwise it is retained as recovery evidence.
- A quarantined object is resumed only from its exact operation/object evidence.
- An absent object is completed only from a matching terminal cleanup receipt,
  or from a flushed `final_remove_intent` whose exact private parent/entry/object
  evidence proves the last recoverable name had been emptied and committed for
  removal by this operation. Absence before that phase is indeterminate.
- Database corruption, missing root, owner drift, unsupported filesystem,
  unreadable reference evidence, or revision skew closes mutation admission and
  retains objects.
- Service restart never changes an operation/request/object identity.

## No-secret/no-path evidence boundary

Public and retained protocol evidence is allowlisted before shared redaction.
It may contain opaque identities, digests, counts, safe lifecycle/policy/outcome
codes, timestamps, revision/evidence identity, and aggregate bytes. It contains
no source contents or entry names, credentials, argv/environment, raw peer
identity, process/unit/socket details, filesystem locators, host configuration,
resolver/network data, or unrelated project state.
