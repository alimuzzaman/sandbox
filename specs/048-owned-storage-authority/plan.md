# Implementation Plan: Owned Storage Authority

**Branch**: `codex/owned-storage-authority` | **Date**: 2026-08-31 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from
`specs/048-owned-storage-authority/spec.md` and the reviewed PRD in the same
directory.

**Planning Readiness**: **NOT READY**. A third independent review found the
unresolved blockers listed in [Planning Blockers](#planning-blockers). Do not
generate tasks or begin implementation from this plan.

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
one newly created disposable Ubuntu 24.04 remote fixture exercising the
ordinary product path; independent human review of the exact revision and live
evidence before support or adoption is enabled.

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
| IV. Live-stack proof | BLOCKED | The qualification harness does not yet prove a post-promotion ordinary `future` sync/CI journey without its hidden admission, so the current promotion contract could accept a broken normal-policy routing branch. |
| V. Idempotency and docs-with-code | PASS | Canonical request digests, durable operation rows, relationship/object locks, intent-before-effect, recovery, exact replay, and matching contracts/operator docs are required. Packaging and lifecycle changes land with code. |
| VI. Feature parity before removal | PASS | The feature is additive and future-only. Existing sync, job, workspace, cleanup, legacy replay, and non-authority storage remain rollback controls. Switching policy back to `legacy` changes only later object creation and never adopts, deletes, or rewrites existing objects. |
| Additional boundaries and secrets | PASS | No `runtime/wp/` or `vendor/` changes. Public/durable evidence excludes source contents, credentials, unrestricted environment/host configuration, and sensitive paths. Spec Kit assets remain pruned from shipped releases. |
| Shipping and human authority | PASS | Planning creates no deployment or privilege change. Implementation may prepare lifecycle assets, but installation, live qualification, support promotion, remote update, release, or production adoption require explicit human authorization and exact revision evidence. |

The design adds no constitutional violation and needs no complexity waiver.

## Project Structure

### Documentation (this feature)

```text
specs/048-owned-storage-authority/
├── prd.md
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│   └── requirements.md
└── contracts/
    ├── authority-service-v1.md
    ├── capability-evidence-v1.md
    └── cli-mcp.md
```

`tasks.md` is intentionally not created by this workflow.

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
policy and call the authority through a narrow typed port. CLI/MCP and remote
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
   policy/reclaim operations. Add the sealed, exact-fixture acceptance seam;
   it remains unavailable to normal policy and cannot mark support proven.
   Re-run CLI/MCP parity and no-path/no-secret scans.
7. Run the complete quickstart on a new disposable Ubuntu fixture at the exact
   accepted revision under one separately authorized proof-candidate admission.
   Preserve a before/after unrelated-state inventory and close the admission.
8. Require independent human review of source, contracts, live evidence,
   packaging, service privileges, recovery, and cleanup. The protected remote
   lifecycle records the decision and atomically issues or revokes the
   revision/evidence-bound promotion receipt. Only a current accepted receipt
   may promote support or allow future-object adoption.

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

No constitution violation or complexity waiver is proposed. The unresolved
design blockers below prevent tasks, implementation, or merge readiness.

## Planning Blockers

1. **Normal adoption proof is incomplete**: qualification exercises `sync once`
   and `ci run` through a hidden sealed admission. After promotion, the
   quickstart changes policy to `future` but does not create and verify one new
   ordinary sync generation and CI materialization without that admission.
   Therefore SC-013 and the normal-policy routing branch remain unproven.
2. **Protected review binding is ambiguous**: `review` is a canonical operation,
   but the operation model has only an admission/candidate pair defined for
   active qualification. Review needs a closed evidence-candidate and decision
   binding that consumes no admission budget, plus explicit replay uniqueness.
3. **Review operation ownership is contradictory**: the capability contract
   calls review/promotion non-authority operations, while the authority contract
   defines an internal `review` operation persisted by the authority. The next
   planning pass must choose and document one owner and public/internal boundary.

The independent reviewer also reported that the protected-review test was
absent from the quickstart. Current evidence disproves that item:
`tests.test_owned_storage_review` is present in the focused command. No further
correction was made because the two P1 blockers above require another explicit
planning decision.
