# Contract: Owned Storage Capability and Evidence v1

> **Draft and NOT READY.** The lifecycle transaction dependency is blocked on
> the immutable Feature 051 public-port boundary. See
> [../analysis.md](../analysis.md). Do not implement this contract.

## Purpose

Report whether one exact remote platform/mode can safely accept future
authority-owned objects. This report is observation, not installation,
qualification, migration, repair, or promotion authority.

## Report shape

```json
{
  "capability": "owned-storage-authority-v1",
  "remote_identity": "opaque",
  "platform_mode": "ubuntu-24.04-systemd-255-private-root-v1",
  "support_tier": "unavailable|unsupported|implemented_unproven|proven|drifted",
  "adoptable": false,
  "service_revision": "opaque-version-or-digest",
  "evidence_id": null,
  "ordinary_evidence_id": null,
  "acceptance_state": "pending_ordinary|complete|failed|null",
  "promotion_id": null,
  "authority_binding_id": null,
  "binding_generation": null,
  "observed_at": "2026-08-31T12:00:00Z",
  "expires_at": "2026-08-31T12:15:00Z",
  "checks": {
    "dedicated_identity": "pass|fail|unknown",
    "private_root": "pass|fail|unknown",
    "caller_non_mutation": "pass|fail|unknown",
    "canonical_peer_auth": "pass|fail|unknown",
    "controller_process_identity": "pass|fail|unknown",
    "mount_controller_identity": "pass|fail|unknown",
    "descriptor_only_mount_channel": "pass|fail|unknown",
    "no_arbitrary_path_protocol": "pass|fail|unknown",
    "no_replace_publication": "pass|fail|unknown",
    "durable_restart_recovery": "pass|fail|unknown",
    "private_workload_mount": "pass|fail|unknown",
    "read_only_generation_mount": "pass|fail|unknown",
    "active_reference_observation": "pass|fail|unknown",
    "identity_bound_final_removal": "pass|fail|unknown",
    "bounded_secret_free_evidence": "pass|fail|unknown",
    "packaging_revision_parity": "pass|fail|unknown",
    "human_review": "pass|fail|unknown"
  },
  "storage_authority": {
    "owner_identity_digest": "sha256:...|null",
    "root_identity_digest": "sha256:...|null"
  },
  "resolver_authority": {
    "included": false,
    "qualified": false
  },
  "reason_code": "implemented_unproven"
}
```

## Rules

- During the authorized acceptance run, only the exact fixture-validation
  promotion plus active binding may use `future` while
  `support_tier=implemented_unproven`, `adoptable=false`, and
  `acceptance_state=pending_ordinary`. This is explicit validation authority,
  not qualification ancestry or general support.
- Support outside that fixture requires `support_tier=proven`, `adoptable=true`,
  `acceptance_state=complete`, non-null current primitive and ordinary evidence
  plus promotion IDs, an exact active authority binding/generation, and every
  required check `pass`.
- Code, unit files, config, a running service, successful connection, local
  tests, or a self-reported probe cannot promote support.
- A separately authorized exact-fixture qualification admission may collect the
  live proof matrix while tier is `implemented_unproven`, but it cannot set
  `adoptable=true`, change normal policy, or survive its bounded acceptance run.
- Any stale, missing, mixed-revision, failed, unknown, drifted, unreviewed, or
  lifecycle/authority-binding mismatch makes `adoptable=false` and closes
  mutation admission.
- A client/service revision mismatch is `authority_revision_mismatch`; the
  caller must use the supported remote lifecycle, not raw SSH repair.
- Safe read-only capability/status may remain available when mutation is
  closed. Partial evidence is explicitly `complete:false` at the enclosing
  response.
- Raw UID/GID/PID, unit/service properties, paths, mounts, sockets, host
  configuration, source content, credentials, and unrestricted diagnostics are
  never public. Their reviewed observations are represented by fixed states and
  opaque digests only.
- Storage proof never qualifies resolver/DNS/ingress/network mutation. The
  resolver block is always the explicit excluded value shown above.

## Required final live evidence bundle

The final accepted evidence identity combines the primitive candidate evidence
used for protected promotion with the post-promotion ordinary evidence. It must
bind all of the following to one exact clean source and installed revision:

1. Newly created disposable Ubuntu 24.04/systemd 255 fixture identity and safe
   package/kernel/filesystem version summary.
2. Static service UID, private root/database/object-parent ownership, unit
   sandboxing, and absence of caller mutation rights.
3. Exact post-promotion CLI-to-application-to-service ordinary product path,
   including one new normal `future` sync generation and CI materialization
   with `qualification:null` and promotion/policy ancestry. The service side
   authenticates the sole supervised controller by exact UID/GID, PID/start,
   executable, unit/cgroup, config, and connection identity; same-UID and direct
   socket callers are refused.
4. Atomic generation publication and publisher/workload edit/rename/replace/
   remove refusal with unchanged manifest/count/bytes/content digests.
5. At least 100 interruption/restart publication trials with no partial current
   generation and no duplicate acceptance.
6. Lost-ack exact replay and every-field mismatch refusal.
7. Writable CI interior plus refusal of root, authority record, accepted source,
   managed source, and cross-workspace mutation.
8. Terminal cleanup with exact private quarantine/removal, measured known
   bytes, unchanged immutable job result, and preserved unrelated state.
9. At least 100 cleanup interruption/replay/replacement races with at most one
   terminal outcome and zero replacement removals.
10. Negative authorization, lifecycle, reference, retention, degraded-index,
    storage-exhaustion, and unsupported-platform matrix.
11. Stop/start/update/recovery behavior and package/revision verification.
12. CLI/MCP parity and bounded no-secret/no-path evidence scan.
13. Independent human review decision for the exact source, service privilege
    surface, contracts, evidence, recovery, and support tier, plus exact
    lifecycle-promotion/authority-binding reconciliation.

## Protected review and promotion lifecycle

Review, promotion, and revocation are owned only by the protected remote
lifecycle application service. They are not authority-service or MCP
operations. After the qualification admission is closed, an independently
authorized human uses the protected lifecycle:

```text
sb remote service owned-storage-review NAME --project-identity ID
                 --evidence-candidate-id ID
                 --decision accepted|rejected
                 --request-id ID --confirm --json

sb remote service owned-storage-revoke NAME --project-identity ID
                 --promotion-id ID --reason SAFE_CODE
                 --request-id ID --confirm --json

sb remote service owned-storage-acceptance-finalize NAME --project-identity ID
                 --promotion-id ID --request-id ID --confirm --json
```

The lifecycle derives reviewer identity from its protected operator
authorization; it accepts no caller-selected reviewer identity or support tier.
It rechecks the exact closed admission, candidate close generation, cleanup
digest, complete evidence-bundle digest, source/installed/contract revisions,
controller identities, fixture, freshness, and current non-drifted
observations. At reservation it preallocates exact review-decision,
validation-promotion, authority-binding IDs and the canonical binding digest;
accepted review prepares only those exact values and byte-compares them on
activation. Rejected review terminates without preparing a binding. Review is unique on `(remote, project, review, request_id)` and
the canonical digest covers all of those fields plus the decision. Exact replay
returns/resumes the same result; changed input refuses before effect. One closed
candidate tuple has at most one terminal accepted/rejected review. Rejection
requires a new candidate. Review consumes no qualification budget.

Accepted review uses an explicit prepared-binding handshake:

1. In the dedicated `StorageAuthorityLifecycleRepository`, the lifecycle
   durably reserves the review request and exact proposed IDs/digest.
2. The authority records one exact `prepared` adoption binding. It grants no
   policy or mutation authority.
3. The lifecycle atomically commits review decision, fixture-validation
   promotion receipt, primitive evidence ID, and
   `acceptance_state=pending_ordinary` in the lifecycle repository record. Public tier
   remains `implemented_unproven` and `adoptable=false`.
4. Exact replay activates the matching authority binding only after proving the
   committed lifecycle receipt.
5. Only the exact disposable fixture may use the active validation binding;
   general capability remains non-adoptable.

No cross-repository atomic transaction is claimed. Missing acknowledgement or
mixed state is non-adoptable and exact replay reconciles only the original
binding. The lifecycle command never maps to an authority `review` operation.

Revocation is a separate lifecycle operation referencing an existing promotion
ID. It commits `adoptable=false` and a non-proven tier first, then marks the
exact authority binding revoked. Lost deactivation acknowledgement remains
fail-closed. Expiry, revision skew, or later drift follows the same order and
never deletes owned objects.

The lifecycle state is durably maintained in a dedicated crash-safe
`StorageAuthorityLifecycleRepository` (`runtime/storage_authority/lifecycle.json`);
it is completely decoupled from OCI hosting (`RecoveryRepository` / `hosts.json`).
Every review, promotion, acceptance-finalize, and revocation transition uses
per-remote advisory locking and generation CAS. Cross-store lock order is
lifecycle repository lock then authority binding lock, released in reverse.

After accepted promotion and binding activation, the exact disposable fixture
must set ordinary policy `future`, create and replay one new normal sync
generation, create/run/clean one new normal CI materialization, prove promotion
and policy ancestry with no qualification ancestry, preserve unrelated state,
then return policy to `legacy`.

The protected `owned-storage-acceptance-finalize` input contains no evidence or
support tier. After confirmation and derived operator authorization, the
lifecycle reads exact sync, CI, workspace, cleanup, replay, ancestry, rollback,
revision, and unrelated-state evidence through typed read-only ports. Its
canonical request is unique on `(remote, project, acceptance_finalize,
request_id)`, binds the promotion and starting target generation, and advances
through `reserved`, `observing`, `evidence_closed`, `committed`, and `terminal`.
Exact replay resumes/returns the same result; changed input conflicts. Success
commits a lifecycle-owned immutable ordinary-evidence identity,
`acceptance_state=complete`, validation promotion `supported`, and only then
`proven`/adoptable. Failed or contradictory evidence atomically commits
`failed`/non-adoptable and enters revocation-pending before the authority binding
is revoked. Crash replay resumes only the recorded phase/generation.

## Support matrix baseline

| Platform/mode | Initial tier | Mutation behavior |
|---|---|---|
| Qualified Ubuntu 24.04/systemd 255 private-root mode after reviewed proof, active binding, and completed post-promotion ordinary fixture journey | `proven` | May permit future policy. |
| Exact promoted disposable fixture while ordinary acceptance is pending | `implemented_unproven` | Only its fixture-validation promotion and active binding may permit `future`; no general adoption/support claim. |
| Same mode before reviewed proof | `implemented_unproven` | Refuse normal authority mutation; permit only a separately authorized bounded qualification admission. |
| Linux without required syscall/filesystem/private-mount proof | `unsupported` | Retain; no fallback claim. |
| NFS/network/unknown filesystem | `unsupported` | Retain; separate qualification required. |
| macOS, Windows, Herd, Compose-local, generic host-job | `unsupported` | Existing legacy path only. |
| Missing/skewed/drifted service | `unavailable` or `drifted` | Close mutation; retain owned objects. |

Client operating system does not matter when the target is a separately
qualified Linux remote.
