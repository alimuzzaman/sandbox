# Implementation Plan: Owned Storage Authority

**Branch**: `codex/owned-storage-authority-planning-repair` | **Date**: 2026-09-02 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from
`specs/052-owned-storage-authority/spec.md` and the reviewed PRD in the same
directory.

**Planning Readiness**: **NOT READY — PUBLIC PORT BLOCKED**. The bounded design
text closes the three original semantic blockers, but two fresh independent
post-edit analyses found that its lifecycle persistence boundary cannot be
implemented through the immutable Feature 051 public ports. The fixed target
mutation registry has no owned-storage lifecycle capability, while
`activation_host_state_port()` reads and writes only the closed
`image_activation` value. Implementing this plan would therefore require a
forbidden hosting/schema change, a private repository bypass, or reinterpretation
of an accepted capability. No tasks are published. See [analysis.md](./analysis.md).
Implementation, service installation, privilege grants, live qualification,
promotion, rollout, and production adoption are not authorized.

## Summary

Add an opt-in, Linux-only owned-storage authority for newly published remote
sync generations and newly materialized disposable CI workspaces. A persistent
service running as a dedicated static system UID owns one private storage root,
the object/operation journal, accepted generations, materialization roots, and
final quarantine/removal. Existing sync, workspace, job, cleanup, and legacy
storage behavior remains unchanged until a project selects the future-object
policy.

The application services remain the owners of publication, job, retention, and
reference policy. They call a typed storage port with durable identities,
digests, counts, and policy projections. The storage authority owns only the
mechanisms needed to stage, verify, publish, expose, retain, quarantine, remove,
and recover exact registered objects. Neither its public nor internal protocol
accepts a caller-selected path or command. Resolver/DNS authority remains a
separate subsystem and is not used by the storage service.

## Technical Context

**Language/Version**: Python 3.12+ for the existing importable `sandbox/`
package and the installed authority service. Linux system-call adapters target
the reviewed Ubuntu 24.04/systemd 255 remote matrix and remain isolated from
unsupported clients.

**Primary Dependencies**: Python standard library (`dataclasses`, `hashlib`,
`json`, `os`, `pathlib`, `socket`, `sqlite3`); existing sync, job, workspace,
resource, redaction, command-manifest, MCP-composition, remote transport, and
runtime-revision services; Linux `AF_UNIX` peer credentials and `SCM_RIGHTS`, `openat2`,
directory-FD operations, and `renameat2(RENAME_NOREPLACE)`; fixed systemd
service/sysusers lifecycle assets. No new third-party runtime dependency is
planned.

**Storage**: One authority-owned root under the supported service lifecycle,
containing a single SQLite authority repository plus private `staging/`,
`objects/`, and `quarantine/` trees. The repository uses foreign keys, bounded
busy handling, crash-safe transactions, and `synchronous=FULL`. Payload files
and their parent directories are flushed before acceptance. Existing sync and
job/workspace repositories remain authoritative for their application domains
and receive additive authority projections only.

**Testing**: Standard-library `unittest` unit, contract, codec, repository,
race, recovery, CLI, MCP, packaging, and architecture-boundary suites; at least
100 interruption/restart publication trials and 100 cleanup replay/race trials;
one newly created disposable Ubuntu 24.04 remote fixture exercising both the
sealed qualification path and a post-promotion normal `future` policy path with
`qualification:null`; independent human review of the exact revision and live
evidence before support is claimed outside that fixture.

**Target Platform**: Provisioned Linux remotes whose exact operating mode has
passed the owned-storage capability probe and human-reviewed live acceptance.
The initial qualification target is Ubuntu 24.04 with systemd 255 and a local
filesystem supporting the required directory-FD and no-replace operations.
macOS, Windows, Herd, Compose-local, generic host-job, NFS, unqualified
filesystems, and any mode without private workload mounts fail closed for
authority-dependent mutation. A macOS/Linux developer client may use a
qualified Linux remote.

**Project Type**: Modular Python CLI plus MCP/control-plane services, one
separately supervised unprivileged Linux storage service, one fixed policy
controller, and one job-namespace-confined mount controller.

**Performance Goals**: At least 95% of bounded status/preview requests over up
to 10,000 authority and legacy projections complete within 30 seconds. Public
pages default to 100 and cap at 500 records; a preview may cover at most 10,000
objects and expires after 15 minutes. Publication/cleanup throughput is not
claimed until live measurement; all transfer, operation, and observation calls
have finite deadlines and byte/record limits.

**Constraints**: Dedicated static service UID; private service-owned parent and
authority journal; typed operations and opaque IDs only; exact peer, project,
relationship/workspace/job/object authorization; only fixed supervised policy
and runtime mount controller processes may connect on distinct purpose-bound
channels, authenticated by UID/GID, PID/start, executable, unit/cgroup, config,
and connection identity; mount authority exists only inside a dedicated
job user/mount namespace; no arbitrary paths, shell,
resolver, network, credential, container, package, or host mutation; accepted
generations never change in place; CI writes are limited to a namespace-mounted
writable interior whose parent/root remain authority-owned; unknown evidence
means retain; terminal job truth is immutable; public evidence is bounded,
redacted, secret-free, and path-free; no production install, rollout, migration,
release, or privilege change occurs during specification or implementation
without separate authorization.

**Scale/Scope**: Up to 10,000 projected authority/legacy records per bounded
observation; one canonical operation row per request identity and digest;
relationship-scoped serialization for publication/current selection;
object-scoped serialization for cleanup; newly authority-created objects only.
No adoption, relocation, or cleanup of legacy/foreign storage.

## Constitution Check

*GATE: Passed before Phase 0 research and re-checked after Phase 1 design.*

| Constitution gate | Pre-design | Post-design evidence and consequence |
|---|---|---|
| I. Per-project instance model | PASS | Every request binds a registered remote, project identity, and relationship/workspace/job scope. No global or fallback instance exists. |
| II. Registry source of truth | PASS | Application services resolve registered identities through existing repositories. The authority owns only its private object/operation repository and never reads registry JSON, legacy workspace JSON, or another service's SQLite directly. |
| III. Single entry, modular package | PASS | `sb` remains one entry file. Domain, repository, protocol, Linux adapter, application port, command group, MCP group, and service executable register through explicit manifests/contracts. No new consumer of `sandbox_core.py`, `sandbox.registry.COMMANDS`, `sandbox.hermes.facade`, or MCP `app.py` helpers is introduced. |
| IV. Live-stack proof | SATISFIED BY PLAN; LIVE GATE OPEN | A protected fixture-validation promotion remains `implemented_unproven`/non-adoptable and is limited to the exact disposable fixture until post-promotion acceptance completes. The normal `future` policy, `sync once`, and `ci run` commands carry `qualification:null` and use the same routing as later adoption. Protected finalization derives the evidence before `proven`/adoptable; failure revokes first. No live proof is claimed by this plan. |
| V. Idempotency and docs-with-code | PASS | Canonical request digests, durable operation rows, relationship/object locks, intent-before-effect, recovery, exact replay, and matching contracts/operator docs are required. Packaging and lifecycle changes land with code. |
| VI. Feature parity before removal | PASS | The feature is additive and future-only. Existing sync, job, workspace, cleanup, legacy replay, and non-authority storage remain rollback controls. Switching policy back to `legacy` changes only later object creation and never adopts, deletes, or rewrites existing objects. |
| Additional boundaries and secrets | PASS | No `runtime/wp/` or `vendor/` changes. Public/durable evidence excludes source contents, credentials, unrestricted environment/host configuration, and sensitive paths. Spec Kit assets remain pruned from shipped releases. |
| Shipping and human authority | PASS | Planning creates no deployment or privilege change. Implementation may prepare lifecycle assets, but installation, live qualification, support promotion, remote update, release, or production adoption require explicit human authorization and exact revision evidence. |

The design adds no constitutional violation and needs no complexity waiver.

## Project Structure

### Documentation (this feature)

```text
specs/052-owned-storage-authority/
├── prd.md
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│   ├── requirements.md
│   └── implementation.md
├── contracts/
│   ├── authority-service-v1.md
│   ├── capability-evidence-v1.md
│   └── cli-mcp.md
└── analysis.md
```

### Source Code (repository root)

```text
sandbox/
├── owned_storage/
│   ├── __init__.py
│   ├── models.py                 # exact requests, objects, outcomes, reason codes
│   ├── repository.py             # private SQLite operation/object journal
│   ├── protocol.py               # canonical bounded local service codec
│   ├── service.py                # storage mechanisms; consumes sealed policy decisions
│   ├── recovery.py               # restart reconciliation and quarantine recovery
│   ├── evidence.py               # bounded path-free status/capability projections
│   ├── manifest.py               # explicit owned-storage component registry
│   └── adapters/
│       └── linux.py              # openat2/dirfd/no-replace/private-root mechanics
├── application/
│   ├── owned_storage_service.py  # authorization/policy port and result projection
│   ├── sync_service.py           # publication/current-policy integration only
│   ├── job_service.py            # immutable job result + cleanup projection
│   └── workspace_service.py      # materialization/reference/policy integration
├── owned_storage_lifecycle/
│   ├── models.py                 # candidate/review/promotion/revocation state
│   ├── repository.py             # nested-value codec behind shared RecoveryRepository
│   ├── service.py                # protected review/finalize/revoke reconciliation
│   └── manifest.py               # explicit lifecycle component registration
├── sync/                         # existing relationship/capture policy remains owner
├── jobs/                         # existing job/result/lease policy remains owner
├── workspaces/                   # existing durable workspace/index policy remains owner
├── resources/                    # preview trigger only; never direct deletion
├── transports/
│   └── remote_owned_storage.py   # strict revision/capability/envelope decoder
└── commands/
    ├── owned_storage.py          # thin CLI adapter
    ├── remote.py                 # protected review/promotion lifecycle integration
    └── manifest.py               # explicit command registration

mcp/wp-server/tools/
├── owned_storage.py              # thin typed MCP adapters
└── manifest.py                   # explicit owned-storage tool group

tools/
├── owned-storage-service.py      # fixed service entry point, no public path/command API
├── owned-storage-controller.py   # sole supervised policy/control peer
└── owned-storage-mount-controller.py # descriptor-only job-namespace mounts

config/systemd/
├── sandbox-owned-storage.service
├── sandbox-owned-storage.socket
├── sandbox-owned-storage-controller.service
├── sandbox-owned-storage-controller.socket
├── sandbox-owned-storage-mount.service
└── sandbox-owned-storage.sysusers

scripts/
├── install-remote.sh             # supported lifecycle integration only after approval
└── make-release.sh               # runtime assets ship; specs/.specify stay pruned

tests/
├── test_owned_storage_models.py
├── test_owned_storage_repository.py
├── test_owned_storage_protocol.py
├── test_owned_storage_linux.py
├── test_owned_storage_recovery.py
├── test_owned_storage_application.py
├── test_owned_storage_cli.py
├── test_owned_storage_review.py
├── test_owned_storage_mcp.py
├── test_owned_storage_packaging.py
├── test_owned_storage_architecture.py
├── test_sync_owned_storage.py
├── test_job_owned_storage.py
├── test_workspace_owned_storage.py
└── acceptance/test_owned_storage_authority.py
```

**Structure Decision**: A feature-owned `sandbox/owned_storage/` package owns
only storage mechanisms, its private journal, its codec, and exact Linux
filesystem operations. Existing sync/job/workspace application services retain
policy and call the authority through a narrow typed port. The separate
`sandbox/owned_storage_lifecycle/` module is the sole owner of
candidate/review/promotion/revocation semantics. It consumes the existing public
Feature 051 `RecoveryRepository` target-mutation and host-state transaction ports
without editing `sandbox/hosting/**`, owning outer persistence, or importing private
repository helpers. It uses a prepared, non-authorizing authority binding instead
of direct cross-repository writes. CLI/MCP and remote
transport modules validate and project results only. The standalone service is
packaged as a fixed executable with fixed lifecycle assets; no generic root
helper, callback, path, or command channel is added.

## Design and Delivery Gates

1. Land pure contracts, models, capability projection, and closed defaults.
2. Land the private repository, canonical request/replay state, and restart
   recovery with synthetic filesystem adapters; keep support unproven.
3. Land the dedicated-UID service lifecycle and Linux private-root adapter.
   Lifecycle installation remains a separately authorized action.
4. Integrate immutable publication with sync while preserving current
   non-authority publication as the default compatibility path.
5. Integrate namespace-mounted CI materializations and exact final cleanup with
   job/workspace policy; unisolated host execution remains unsupported.
6. Add read-only status/preview first, then confirmation- and request-ID-gated
   policy/reclaim operations. Add the sealed, exact-fixture qualification seam;
   it remains unavailable to normal policy and cannot mark support proven.
   Re-run CLI/MCP parity and no-path/no-secret scans.
7. Run the sealed qualification matrix on a new disposable Ubuntu fixture at
   the exact accepted revision. Preserve unrelated-state inventory, close the
   admission, and freeze one evidence candidate. The remote lifecycle owns the
   candidate; the storage authority owns only its object/operation receipts.
8. Require a protected human review of the closed candidate. The remote
   lifecycle reserves the exact replay-safe review and preallocates the
   decision, promotion, binding IDs and binding digest, asks the storage authority
   to prepare a non-authorizing adoption binding, then atomically commits its
   own review decision, fixture-scoped validation promotion receipt, and
   `acceptance_state=pending_ordinary` capability projection while the public
   tier stays `implemented_unproven`/non-adoptable. Exact
   replay may activate the prepared binding only after that lifecycle receipt
   exists. Review/promotion consumes no qualification-admission budget.
9. Prove the exact active fixture-validation promotion plus active authority binding,
   then use the ordinary policy command to select `future`. Run new ordinary
   `sync once` and `ci run` journeys outside the acceptance harness, with no
   proof arguments and `qualification:null` at the storage-service boundary.
   Record policy/promotion ancestry, replay/conflict behavior, normal cleanup,
   preserved job truth and unrelated state, and rollback to `legacy` only after
   the new owned objects exist.
10. Any post-promotion failure requires the lifecycle to commit
    non-adoptable/revoked first and then deactivate the authority binding.
    Missing acknowledgement remains fail-closed and replay-reconcilable. Only
    after the complete fixture journey passes and a protected replay-safe
    acceptance-finalization operation derives the required evidence, commits
    its immutable identity, and changes state to `complete`/`supported` may tier
    become `proven`/adoptable. General rollout remains separately authorized.

## Review and Adoption State Sequence

```text
lifecycle: reserve review and preallocate decision/promotion/binding IDs + digest
    -> authority: prepare binding (non-authorizing)
    -> lifecycle: commit review + fixture-validation promotion + capability generation
    -> authority: activate exact binding on committed-receipt replay
    -> public capability: implemented_unproven/non-adoptable, exact fixture only
    -> post-promotion ordinary future sync/CI/cleanup/rollback on fixture
    -> protected acceptance-finalize derives evidence through read-only ports
    -> lifecycle: complete => supported/proven/adoptable

revoke/failure:
lifecycle: commit non-adoptable + revocation
    -> authority: revoke exact binding
    -> exact replay reconciles acknowledgement; mixed state stays closed
```

The existing shared hosting `RecoveryRepository` remains the sole outer target
parser/writer/locker/fsync and generation-CAS owner. Feature 052 validates and
serializes only a closed nested lifecycle value and proposes transitions
through that shared transaction port; it creates no second hosting state file
or database and performs no direct outer-state write. Lock order is shared
target lock then authority-binding lock, released in reverse. The authority
repository never receives reviewer identity as authority, never decides a
support tier, and never reads lifecycle storage directly. The authenticated
controller carries only the exact typed receipt/binding data. This preserves
the prepared-custody pattern from Features 050–051 and does not reinterpret
Feature 048 observation receipts, Feature 049 trust plans, Feature 050 proof
custody, or Feature 051 activation/recovery as owned-storage authority.

## Compatibility and Rollback

- Default policy is `legacy`; it creates no authority-owned object and changes
  no existing result.
- `future` applies only to objects created after the durable policy transition
  on a qualified remote. It never adopts or moves an older object.
- Returning the policy to `legacy` is the rollback control for later creation.
  Already authority-owned objects remain readable and retained under the
  authority; they are never copied back or deleted as part of rollback.
- If the service, revision, proof, repository, root ownership, or platform
  becomes unavailable or drifts, mutation admission closes. Existing owned
  objects remain retained; safe path-free status may report partial evidence.
- Existing public fields and exit semantics remain stable. Authority details
  are additive and omitted for legacy objects/callers.
- Any future compatibility-facade removal requires separate parity evidence and
  explicit human approval under Constitution Principle VI.

## Complexity Tracking

No constitution violation or complexity waiver is proposed. Cross-store state
does not pretend to be one transaction: the remote lifecycle is the single
semantic owner, while the storage authority holds only a non-authorizing
prepared/active/revoked enforcement binding reconciled by exact replay.

## Resolved Planning Decisions

1. **Normal adoption proof**: after protected promotion on the exact disposable
   fixture, the quickstart sets the real `future` policy and creates a new sync
   generation and CI materialization through ordinary commands outside the
   acceptance harness. These operations have `qualification:null`, bind the
   policy plus current promotion/evidence, and exercise the normal routing
   branch. They prove replay/conflict, job-result preservation, cleanup,
   unrelated-state preservation, and rollback before support is claimed.
2. **Protected review binding**: the review binds the closed candidate identity,
   candidate close generation and digest, cleanup digest, exact source/service
   revisions, controller identities, remote/project/fixture scope, reviewer
   authorization digest, decision, freshness, request ID, and canonical request
   digest. Its replay key is unique in the lifecycle nested state behind the
   shared target repository and one
   candidate tuple has at most one accepted/rejected terminal review. Exact
   replay returns the same result; changed input refuses. Rejection requires a
   new candidate. Revocation is a separate operation over a promotion. Review,
   promotion, and revocation never reserve a storage operation or decrement
   qualification budget.
3. **Single review owner**: the protected remote service lifecycle exclusively
   owns evidence-candidate closure, review, promotion, revocation, and the
   capability projection. The storage authority has no
   `review`, `promote`, or `revoke` operation. It persists only storage
   operations, exact object receipts, and an internal adoption binding whose
   phases are `prepared`, `active`, or `revoked`. Preparation grants no mutation.
   CLI/MCP project tools cannot call the protected lifecycle or construct or
   activate the binding.

The independent review gate for this repaired plan is recorded in
`analysis.md`, not as self-authored proof. The retained source structure and
delivery sequence are a conditional design record only. Task generation must
not resume until a human chooses and authorizes a feasible public lifecycle
state owner/transaction port without weakening the immutable Feature 048–051
contracts. All live lifecycle, privilege, promotion, rollout, and production
steps remain separate explicit human gates.
