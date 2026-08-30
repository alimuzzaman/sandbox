# CLI and MCP Contract: Agent-Aware Remote Development Sync

The CLI and MCP surfaces use the same redacted envelope. The exact command and
tool registration are implementation-owned, but the public fields below are
stable and must be versioned through the project manifest.

## Operations

| Operation | Purpose | Mutation |
|---|---|---|
| `sync start` | Start or resume `live` or `checkpoint` mode for one selected relationship. | Relationship state only; starts transfer only when mode/request requires it. |
| `sync once` | Request one bounded synchronization without changing persistent mode. | Source generation and remote workspace. |
| `sync status` | Read redacted relationship, generation, job, and error state. | None. |
| `sync stop` | Stop future automatic transfers while preserving accepted source and pending state. | Relationship state only. |
| `sync resolve` | Apply an explicitly authorized divergence resolution. | Confirmation-gated remote/source state. |

The feature may expose the same operations through an MCP group named by the
manifest. No caller may provide a raw remote path, shell fragment, or secret
value.

## Success envelope

```json
{
  "ok": true,
  "status": "accepted|pending|stopped|diverged",
  "relationship": {
    "id": "rel_<opaque>",
    "mode": "live|checkpoint|off",
    "lifecycle": "active|stopped|conflicted|diverged",
    "project_identity": "<opaque>",
    "remote": "<safe-name>",
    "workspace_id": "<opaque>"
  },
  "generation": {
    "id": "gen_<opaque>",
    "sequence": 12,
    "state": "accepted|pending|refused|failed",
    "commit": "<full-sha-or-null>",
    "file_count": 12,
    "byte_count": 12345
  },
  "job": {"active_generation": "<opaque-or-null>"},
  "error": null
}
```

The envelope must omit source names, diff text, contents, credentials, raw
paths, environment values, SSH targets, and process arguments.

## Failure envelope

```json
{
  "ok": false,
  "status": "refused|failed|conflicted|unknown",
  "code": "credential_detected|ownership_conflict|remote_unavailable|unstable_capture|divergence|transport_unknown",
  "message": "bounded actionable guidance",
  "relationship": {"id": "rel_<opaque>", "remote": "<safe-name>"},
  "request_id": "<replay-safe-id>",
  "accepted_generation": "<opaque-or-null>",
  "pending_generation": "<opaque-or-null>",
  "retryable": false
}
```

Credential findings use `credential_detected` and MUST be returned before any
remote source mutation. A transport failure with uncertain acknowledgment uses
`status=unknown` and the same request ID for reconciliation.

Staged bytes are never prepared, extracted, or published by free-standing
remote shell path operations. The transport sends the bounded archive on stdin
to the controller's path-free internal `workspace publish-sync` operation with
opaque workspace/generation identities, manifest bindings, and the preflight
index generation. The controller acquires the workspace operation lock,
re-reads ownership, readiness, and live source binding, then holds that lock
through extraction and atomic generation publication. Destroy, migration,
adoption, or ownership drift after preflight therefore refuses publication.

The controller validates the exact directory inventory through no-follow
directory handles, including every directory identity, broken links, and
non-file entries. It repeats the full snapshot and revalidates identities and
digests across generation rename and the conditional `current` pointer commit.
A failed pointer commit leaves a complete fingerprint receipt beside the exact
published generation, so replay can revalidate and complete without restaging.
Lost-ack reconciliation uses that receipt under the workspace lock and repeats
the full exact-tree validation; `current` plus manifest metadata alone is never
acceptance proof. Filesystem failures use bounded typed errors and never include
protected controller paths.

## Job launch boundary

Remote job submission MUST include the accepted generation ID in its durable
request/acceptance record. If a newer generation is pending, launch waits for
that generation. A source-mutating job request must identify `isolated_copy` or
be rejected before launch; `managed_read_only` is the default.
