# Implementation Evidence: Observation-Only Hosting Recovery

**Date**: 2026-08-31

## Implemented source contract

- Public `sb host recover` observation/reconciliation arguments and schema-1 JSON.
- Exact canonical failed-job, request, `sb host apply --confirm` argv, project,
  target, clean source, and pre-effect operation binding.
- Fixed supervisor child identity context without environment enumeration/copying.
- Owner-only broker-generated opaque secret metadata, secret-file epoch, and key-identity invalidation;
  recovery never reads or parses secret values.
- Descriptor-relative, no-follow, size-before-JSON broker metadata reads with exact root,
  inode, mode, link-count, and nested-shape validation.
- Target and shared-state transaction locks, reload-under-lock generation CAS,
  one-use observation authority, immutable timestamped attempts, bounded compaction,
  non-reusable tombstones, persisted active owner, and uncertainty fence.
- One bounded observer with start/end runtime inventory epochs, remote Git head/branch,
  full Git-clean status, every Compose source/generated config digest, exact immutable image coverage,
  topology/health/source checks, and one-shot receipt digest.
- Receipt-only reconcile with a durable non-authorizing provisional marker, immediate
  post-write epoch observation, and separate atomic promotion, exact observation
  replay as `already_reconciled`, exact edge replay as its recorded terminal edge result, and stable refusal,
  uncertainty, and failure classes.
- Separately confirmed edge continuation behind unchanged evidence/generation, sole
  pending edge, authorizing governance, and the narrow existing Caddy/Cloudflare edge
  adapter. Reviewed Feature 047 source `ea9d3dad862555b770cb58034a287c873e4827bc`
  exposes an operation-local immutable-image edge journal, but no host-recovery
  governance projection. Feature 048 does not reinterpret that journal or its image
  state as approval, so the public path refuses
  with `governance_unavailable`; the adapter is an inactive, tested activation seam.
  No source/Compose/build/initializer/migration reachability exists in it.
- `host status --json` generation and bounded latest recovery summary.
- Exact bounded retained-attempt/tombstone schemas and sanitized replay; explicit recovery
  project/environment selectors; synthetic lock-free local and remote Git probe environments;
  and fail-closed default edge governance.
- Repository-config-resistant Git probes, prospective-terminal validation before mutation,
  terminal/owner identity-intersection refusal, and single-envelope JSON selector errors.
- Tracked-submodule dirty detection, exact bounded initializer-phase binding, replay before
  live job lookup, and nonblocking metadata inode inspection.
- Manifest-bound persistent/initializer partitions with exact topology, fresh service, and
  completed initializer-phase identity checks.
- Compatibility with Feature 047's schema-v2 shared host state: image planes and sibling
  fields are preserved opaquely and never treated as recovery governance.

## Local focused evidence

```text
python3 -m unittest \
  tests.test_host_recovery_models \
  tests.test_host_recovery_policy \
  tests.test_host_recovery_repository \
  tests.test_host_recovery_service \
  tests.test_host_recovery_cli \
  tests.test_job_service \
  tests.test_job_supervisor

Ran 110 tests in 5.452s - OK
```

Thirteen exact targeted hosting tests passed in 1.617s, covering target/shared
single-flight, apply/sync/login active-owner refusal, coherent source observation,
status/diagnose compatibility, and
partial/timeout receipt behavior.

The focused selectors include direct-apply durability ordering, opaque environment identity,
observation bounds, status/diagnose compatibility, and active-owner writer fencing.

The generated remote observer Python program compiled. Changed Python compiled with
`compileall`. `git diff --check` passed.

## Direct security review

- Auth/binding: environment context is transport only; canonical durable snapshot,
  exact job ID/request/project/source and structural apply argv are authoritative.
  Registered target identity binds normalized SSH/control endpoints, transport,
  Tailscale host, MCP port, remote name, and runtime home while excluding bearer tokens.
  Feature 046's authenticated stable machine identity is separate mandatory authority;
  missing/legacy, rebuilt, and repointed identities refuse. Recovery re-resolves the
  registration after target ownership and holds its shared writer guard through commit.
  The remote projection now hashes a validated machine ID with domain separation instead
  of hashing the hostname; raw machine IDs never enter hosting evidence.
  Registration-derived apply plan, DNS/origin facts, and Cloudflare preconditions are
  recomputed inside the guard. Canonical non-secret edge intent is persisted and compared
  during observation and immediately before edge authority; the adapter uses its bound
  records. A concurrent same-machine set-origin fixture proves old-origin DNS is unused.
  Certificate hostnames are canonical bound intent and reach proxied Origin CA issuance.
  Tests prove missing reusable certificate state reaches that bounded path and that edge
  collection/64 KiB or total-operation/128 KiB overflow mints no recovery authority,
  binding key, or metadata directory.
- Secret privacy: eligible apply prepares a key/version in memory, proves the exact complete
  envelope bound, then creates separate owner-only opaque HMAC metadata;
  recovery reads only its ID, key version, and non-secret file epoch. Missing/stale,
  environment-backed, manually changed, missing, or symbolic-link secret sources refuse
  without value reads. Only safe owner-only regular sources have epochs, and only safe
  owner-only regular binding keys have an authorizing key identity.
  Broker metadata additionally requires an exact `0700` root and descriptor-relative
  no-follow read of an owner-only `0600` regular single-link inode; bytes and nested shape
  are bounded before they can authorize recovery.
- Secret coherence: target-to-broker lock order and metadata revision CAS cover validation
  through durable commit. The raw digest of secret-bearing `environment.env` is HMAC-blinded
  before any persisted or public evidence.
- Source coordination: the bounded broker guard also holds the canonical per-secret-file
  `.<name>.sb-secrets.lock`; legacy and generic personal-file writers share it without
  nested deadlock. Legacy job refusal happens before either target or broker artifacts.
- Config privacy: rendered environment bytes enter only an owner-keyed HMAC component;
  no unkeyed secret-derived configuration digest is persisted.
- Paths/locks: state/key symbolic links refuse; recovery effect/state locks and managed state
  use `O_NOFOLLOW` plus owner/mode/type/single-link checks, with durable directory/file creation.
  Lock directories remain exact `0700`; trusted controller-owned `0755` runtime parents are
  compatible while `hosts.json` remains exact `0600`. Parent symlinks refuse before child
  creation.
  Registration guard tests also refuse directory/file symlinks, unsafe directory mode,
  and multiply linked lock files after owner/mode/type/link-count validation.
- Command construction: observer paths and services are shell-quoted and bounded.
- Git probes: local and remote recovery Git commands receive a fixed synthetic environment
  with optional locks disabled; no caller environment is copied into these probes. All probes
  force fsmonitor and untracked-cache off, and an adversarial disposable-repository witness
  proves a configured fsmonitor executable is not entered while a modified tracked submodule
  remains visible and refuses in both local and generated remote probes.
- CAS/replay: apply/recovery share target plus state transaction locks; successful replay
  cannot advance; distinct requests cannot consume one apply authority twice; duplicate,
  cross-confirmation, torn, and retention-full identities refuse before effects. Every
  retained attempt/tombstone is exact-schema and cross-state validated before replay, whose
  envelope is rebuilt from safe fields only.
  Commit validates the complete timestamped prospective terminal before generation, receipt,
  fence, provisional, or attempt mutation. Active/provisional identities cannot intersect a
  retained terminal before replay.
- Phase evidence: expected initializer phases are persisted independently and bind an exact,
  bounded, unique `{phase,state}` list to topology. Mapping, hostile, duplicate, missing, and
  pending shapes return stable non-success without an owner wedge.
- Manifest partition evidence: persistent and initializer identities are stored independently;
  topology, fresh persistent-service IDs, and fresh completed initializer phases must match.
  Tests refuse paired initializer-projection omission, empty persistent services, and empty
  fresh services without success or an owner wedge.
- Replay/backend ordering: validated retained success replays before job lookup; unseen requests
  still require a current failed job and create no lock/state artifacts.
- Writer fencing: apply, sync, and login reload under the same target/shared lock and
  refuse a persisted recovery owner; only its exact request ID/digest may clear ownership.
  Login receipts use the durable repository writer rather than a compatibility save.
- Durability/concurrency: pre-effect apply authority/revocation uses the atomic recovery
  writer with file and parent-directory fsync. Sync watch retains only its target effect
  lease after a short state transaction, so unrelated targets remain available.
- Authority refresh: init completion and fresh operation evidence use the same durable
  writer. Every other locked apply state mutation does too. First key publication, each
  newly created authority directory, and broker metadata replacement fsync the required
  parent entry before hosts state can rely on it.
- Edge uncertainty: every exception after adapter entry becomes `effect_unknown`,
  persists a target fence, and a different request cannot repeat it.
- Crash recovery: the same observation identity may resume only from its persisted
  pre-effect phase; an exact provisional owner resumes only the post-write observation.
  Provisional state is non-authorizing and cannot expose success or edge authority. Different
  identities and effect-entered or malformed fences refuse.
- Protected-effect reachability: observation service has no source, Compose, image,
  initializer, migration, DNS, or Caddy callback. Edge has only declared edge helpers.
- Predispatch reachability: the `host` CommandSpec skips migration, finalization, Compose,
  and environment writers only for `recover`; an end-to-end refusal witness proves none
  runs before recovery dispatch. Explicit project and environment selectors are required
  before manifest or target inference.

## Remaining activation gates

This is source/local evidence only. It does not prove a live remote, Lenzora recovery,
deployment, production health, DNS, certificate, or Caddy outcome.

1. Integrate and review the exact branch/SHA.
2. Update the installed remote only through the supported Sandbox lifecycle and verify
   exact installed revision/protocol.
3. Run disposable non-production acceptance with synthetic secrets and inert domain,
   including mutation witnesses and uncertain-edge rollback/fencing.
4. Ensure an authorizing Feature 047 governance projection is present for edge work.
5. Update Lenzora's wrapper to launch current-contract durable applies and retain job,
   request, target, and generation identities.
6. Recover development first. Production remains a separate reviewed apply with
   terminal job, declared-service health, edge readiness, and direct public proof.
