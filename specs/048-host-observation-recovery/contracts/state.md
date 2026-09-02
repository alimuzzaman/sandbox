# State Contract

The owner-only managed hosting state remains schema version 1 with additive fields.

Each target may add:

```json
{
  "generation": 4,
  "active_operation": null,
  "recovery_provisional": null,
  "hosting_operation": {"schema_version": 1, "digest": "sha256:..."},
  "consumed_observation_authority": {"request_id": "...", "starting_generation": 3},
  "recovery_attempts": [{"schema_version": 1, "request_id": "...", "accepted_at": 0, "started_at": 0, "completed_at": 0}],
  "recovery_tombstones": {"request": {"request_digest": "sha256:..."}}
}
```

Unknown/missing additive fields remain readable by existing status/apply code but never
authorize recovery. Writers acquire the shared state transaction lock and per-target lock,
reload state, compare generation and
request digest, write attempt plus hosting receipt in one atomic replacement, then release.

Full attempts are capped at 64 per target. Every retained attempt and tombstone is validated
against an exact bounded field set and action/effect/family/class/generation relationship
before lookup or replay. Unknown fields, private material, malformed nested objects, and
impossible terminal combinations invalidate the target. Replay reconstructs only that fixed
safe schema, so persisted extras can neither escape nor authorize. Compaction replaces an old
full attempt with a fixed-field tombstone; request identity is never deleted or reusable. Any
invalid type, oversized collection, duplicate identity, or unsupported schema is non-authorizing.
When both full-attempt and tombstone capacity are exhausted, `retention_full` refuses
before an active owner or edge effect is started.
Commit constructs the complete timestamped prospective terminal and validates/reconstructs
it before changing generation, receipts, provisional state, uncertainty fences, or attempts.
Invalid or private phase fields therefore leave in-memory and durable state unchanged.

An active recovery owner includes an explicit `phase` and `effect_entered` boolean. Only the
same observation request and digest at `observation_pending` with `effect_entered: false` may
resume its initial observation after process death. A matching
`reconciliation_provisional` owner resumes only the immediate post-write observation. Its
bounded `recovery_provisional` marker is explicitly `authorizing: false`, advances no
generation, creates no terminal attempt/receipt, and cannot authorize edge continuation.
Only a third durable commit promotes matching pre/post evidence to success. Changed post-write
evidence atomically records `evidence_changed` and removes the provisional marker. Edge entry
is durably changed to `effect_entered` before calling
the adapter. Any non-null malformed active owner or uncertainty fence is invalid state and
cannot authorize another request.
An active or provisional request ID or digest may not intersect a retained attempt or
tombstone. This is checked before replay for observation-pending, provisional, and
effect-entered owners.
Validated terminal replay is resolved before live job lookup. A never-seen identity still
requires an eligible job and creates no state or lock artifacts. The operation independently
persists its expected one-shot initializer phase names; evidence must be an exact bounded,
unique list of `{phase,state}` records bound to those names and topology, with only `pending`
or `complete` states. Pending is non-authorizing; malformed, duplicate, or missing phases fail.
The durable operation also stores the manifest-derived persistent-service and initializer-service
partitions. Persistent services must be non-empty, both lists must be bounded, unique, and
disjoint, topology must equal their exact union, and initializer phases must equal the exact
`init:<service>` projection. Fresh service and one-shot phase identities must match those
partitions; omitting both initializer projections or supplying empty persistent services refuses.
The shared reader accepts Feature 047's schema-v2 host-state document without downgrading it.
Recovery preserves image planes and other sibling fields opaquely; their presence cannot
authorize observation reconciliation or edge continuation.

Opaque secret-binding details live separately in owner-only broker metadata. Managed host
state stores only its metadata ID and key version. Recovery never reads secret sources;
missing, environment-backed, changed secret-file epoch, or changed key identity metadata is
non-authorizing. A
secret source or binding key that is missing, symbolic-linked, non-regular, or not
owner-only is also non-authorizing; the source must have a safe epoch and the key a matching
opaque identity.
The broker metadata root is an exact owner-only `0700` directory. Metadata is opened
descriptor-relative with `O_NOFOLLOW` and is accepted only as an owner-only regular `0600`,
single-link inode. Its bytes are bounded before JSON parsing and its complete nested shape is
validated; symlinks, hardlinks, broad modes, wrong shapes, and oversize files are non-authorizing.
The descriptor is opened with `O_NONBLOCK` before `fstat`, so FIFO/device substitutions refuse
without waiting for a writer.
Metadata carries a monotonic broker revision. Target ownership is acquired before the
broker lock, which is held through recovery commit. The secret-bearing environment-file
digest is owner-key HMAC-blinded before state or result serialization.
The bounded broker guard also holds the personal secret source's canonical
`.<name>.sb-secrets.lock`. Apply metadata and authority acceptance occur in that same
transaction. Every locked apply state replacement fsyncs its file and parent directory.
First binding-key publication and each newly created authority-directory entry fsync its
parent before `hosts.json` may reference that authority.
Registered target identity includes normalized SSH and control endpoints, transport,
Tailscale host, MCP port, remote name, and runtime home. It excludes the bearer token.
This endpoint digest is not sufficient machine authority. Current operations also store
Feature 046's authenticated stable `target_identity`; it is mandatory in both original
authority and fresh observation. Missing/legacy identity and rebuilt or repointed machines
refuse. The remote projection derives this opaque identity from a domain-separated machine
ID digest, never hostname/endpoint configuration, and never returns the raw machine ID.
Recovery acquires target ownership before the shared registration guard, resolves
the entry inside that guard, and retains it through durable commit. Supported `put_remote`
and removal writers share the guard. Apply without this projection stores no recoverable
operation authority.
All registration-derived apply planning and preconditions are recomputed from the guarded
entry. Authority includes canonical non-secret `edge_intent` and `edge_intent_digest`;
observation and immediate pre-edge validation compare them exactly. Edge execution uses
those bound DNS records and routes, not a current unbound plan. Same-machine origin drift
is `changed_target` before effects.
`edge_intent` carries canonical `certificate_hostnames` as well as routes and records.
It permits at most 64 routes, 128 DNS records, and 64 unique certificate hostnames and
at most 64 KiB serialized. The complete hosting operation, including its digest and
secret-metadata references, is at most 128 KiB. Apply checks conservative pre-metadata
and exact post-metadata sizes before persisting authority; overflow creates no binding key,
metadata directory, or operation. A new key/version is prepared in memory, included in the
exact prospective envelope, and published only after that envelope passes the bound.
Recovery predispatch is command-owned and read-only; compatibility migration,
finalization, Compose, and environment writers are skipped. Login receipt replacement
uses the same durable repository writer under its existing lock.
Public recovery requires explicit non-empty `--project-dir` and `--environment` selectors
before manifest inference, remote lookup, target construction, or any writer. Every recovery
Git probe uses a fixed synthetic child environment with `GIT_OPTIONAL_LOCKS=0`; it never copies
the invoking process environment. Every probe also overrides repository configuration with
`core.fsmonitor=false` and `core.untrackedCache=false`. Clean-state checks include tracked
submodule worktrees, so a modified committed submodule is dirty and non-authorizing.
The shared registration lock directory and file must be canonical, owner-only, regular,
single-linked objects. Directory/file symlinks, unsafe modes/owners, and hardlinks refuse.
Recovery target/effect/state lock directories and files, plus managed `hosts.json`, enforce
the same owner/mode/type/link-count and `O_NOFOLLOW` checks.
Lock and authority directories are exact owner-only `0700`. Existing controller-owned,
non-group/world-writable runtime parents remain compatible at `0755`; `hosts.json` itself is
still exact owner-only regular `0600`, single-linked, and opened without following links.
Every existing parent component is checked before managed lock-directory creation, so a
user-controlled symlinked parent creates no child or lock inode.
