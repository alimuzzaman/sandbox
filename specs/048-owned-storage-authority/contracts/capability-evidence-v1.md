# Contract: Owned Storage Capability and Evidence v1

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

- Only `support_tier=proven`, `adoptable=true`, a non-null current evidence ID,
  and every required check `pass` permit policy `future`.
- Code, unit files, config, a running service, successful connection, local
  tests, or a self-reported probe cannot promote support.
- A separately authorized exact-fixture qualification admission may collect the
  live proof matrix while tier is `implemented_unproven`, but it cannot set
  `adoptable=true`, change normal policy, or survive its bounded acceptance run.
- Any stale, missing, mixed-revision, failed, unknown, drifted, or unreviewed
  check makes `adoptable=false` and closes mutation admission.
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

## Required live evidence bundle

The evidence ID must bind all of the following to one exact clean source and
installed revision:

1. Newly created disposable Ubuntu 24.04/systemd 255 fixture identity and safe
   package/kernel/filesystem version summary.
2. Static service UID, private root/database/object-parent ownership, unit
   sandboxing, and absence of caller mutation rights.
3. Exact CLI-to-application-to-service ordinary product path. The service side
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
    surface, contracts, evidence, recovery, and support tier.

## Protected review and promotion lifecycle

Review and promotion are not authority-service or MCP operations. After the
qualification admission is closed, an independently authorized human uses the
protected remote lifecycle:

```text
sb remote service owned-storage-review NAME --project-identity ID
                 --evidence-candidate-id ID
                 --decision accepted|rejected|revoked
                 --request-id ID --confirm --json
```

The lifecycle derives reviewer identity from its protected operator
authorization; it accepts no caller-selected reviewer identity or support tier.
It rechecks the exact closed admission, cleanup digest, complete evidence
bundle digest, source/installed revisions, controller identities, fixture,
freshness, and current non-drifted observations. `accepted` atomically writes
the durable review decision, promotion receipt, evidence ID, `proven` tier, and
`adoptable=true`. `rejected` keeps the candidate non-adoptable. `revoked`,
expiry, revision skew, or later drift atomically sets `adoptable=false` and a
non-proven tier without deleting owned objects.

The request ID and canonical digest are durable. Exact replay returns the same
receipt; a changed decision/input under that ID is refused. No review can
promote another remote, project, fixture, revision, or evidence candidate.

## Support matrix baseline

| Platform/mode | Initial tier | Mutation behavior |
|---|---|---|
| Qualified Ubuntu 24.04/systemd 255 private-root mode after reviewed proof | `proven` only after review | May permit future policy. |
| Same mode before reviewed proof | `implemented_unproven` | Refuse normal authority mutation; permit only a separately authorized bounded qualification admission. |
| Linux without required syscall/filesystem/private-mount proof | `unsupported` | Retain; no fallback claim. |
| NFS/network/unknown filesystem | `unsupported` | Retain; separate qualification required. |
| macOS, Windows, Herd, Compose-local, generic host-job | `unsupported` | Existing legacy path only. |
| Missing/skewed/drifted service | `unavailable` or `drifted` | Close mutation; retain owned objects. |

Client operating system does not matter when the target is a separately
qualified Linux remote.
