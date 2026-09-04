# Resume unmerged Sandbox feature work

Updated: 2026-09-02

This note records the clean stopping point after reviewing the remaining Sandbox
feature branches. `latest` was intentionally left unchanged at `c6c06e5` except
for this handoff documentation. Do not infer live, deployment, release, or
production readiness from the local evidence below.

## Hard boundary: preserve accepted immutable OCI work

Features 048–051 and their hosting implementation are accepted inputs. Do not
redesign, duplicate, weaken, or reopen them while resuming the work below.

Protected scope includes:

- `specs/048-host-observation-recovery/`
- `specs/049-oci-trust-verification/`
- `specs/050-secure-image-staging/`
- `specs/051-immutable-activation-recovery/`
- `sandbox/hosting/`
- `sandbox/transports/remote_hosting_activation.py`
- `sandbox/transports/remote_hosting_images.py`

Before each future checkpoint, prove those paths have zero unintended diff from
the accepted `latest` base. Feature 051 remains the active Spec Kit, `AGENTS.md`,
and `CLAUDE.md` pointer. Its human, registered-host, edge, deployment, release,
and production gates remain separate and open.

## Spec 033: agent-aware remote synchronization

- Remote branch: `origin/codex/finish-spec033-local`
- Final checkpoint: `67d7f9b` (merged into `latest`)
- Status: **COMPLETE AND MERGED INTO LATEST**
- Evidence: 117 focused sync, transport, and job tests passed; 2026-09-03 live
  remote acceptance on `scaleway-sandbox` passed (generation acceptance, replay
  idempotency, credential negative screening, mode transitions, CLI/MCP parity,
  clean workspace release/destruction/purge). Detailed evidence recorded in
  `specs/033-agent-aware-remote-sync/quickstart.md`. Tasks T001-T068 completed.

## Feature 052: owned storage authority

- Remote branch: `origin/codex/owned-storage-authority-planning-repair`
- Safe checkpoint: `40b018c`
- Status: **PLANNING REPAIRED (Option 2 Authorized)**
- Evidence: `specs/052-owned-storage-authority/analysis.md` on the branch

On 2026-09-04, the operator authorized Option 2: FR-058 was amended to establish
a dedicated, crash-safe `StorageAuthorityLifecycleRepository` with generation CAS
and advisory locking, completely decoupling owned storage lifecycle from OCI
hosting infrastructure (`RecoveryRepository` / `hosts.json`). Specifications,
data models, contracts, research notes, and plan were repaired and aligned.
Protected paths (`sandbox/hosting/**`, `specs/048-051/**`) remain 100% immutable
with 0 diff. Next step is Spec Kit task generation and consistency analysis.

## Feature 053: instance-scoped server configuration fragments

- Remote branch: `origin/codex/server-config-fragments`
- Safe checkpoint: `a92272278bf94dd00d217491ea5de246e3bcc88c`
- Status: reviewed foundation only; T005–T018 complete, T004 and T019–T108 open
- Evidence: 95 source-only tests passed; independent Sol High review PASS;
  compile and `git diff --check` passed

The checkpoint contains typed models, immutable instance incarnation identity,
bounded input, fail-closed common policy, owner-only descriptor-based repository,
mutation locking, transaction retention, adapter contracts, and nginx/
OpenLiteSpeed manifest boundaries.

Resume with the explicit T004 disposable exact-image OpenLiteSpeed feasibility
probe. It requires authorization for live disposable mutation. Stop and redesign
if no stable vhost inclusion, isolated validation boot, canary, or fixed reload
path can be proven. Continue T019–T108 only after T004 passes. Human security
approval, disposable nginx/OpenLiteSpeed acceptance, final exact-SHA review,
merge, deployment, and release remain open.

## Stable stopping-state evidence

At wrap-up:

- `latest` and `origin/latest` matched `c6c06e5` before this documentation commit.
- The primary and `main` worktrees were clean.
- Temporary feature worktrees and local feature branches were removed after
  confirming the remote checkpoints above.
- The managed Python environment compiled `sandbox`, MCP, and tests.
- 371 focused architecture, modularity, configuration, hosting, recovery,
  activation, sync, and job tests passed on `latest`.
- No live remote mutation, deployment, release, production change, or OCI
  redesign occurred.

## Safe resume order

1. Fetch and verify the exact remote checkpoint before creating a new isolated
   worktree.
2. Recheck `origin/latest`, installed remote revision when relevant, and all
   protected OCI paths before editing.
3. Finish Spec 033 external acceptance first; merge only if every declared gate
   passes.
4. Make the explicit Feature 052 public-port-versus-redesign decision before
   restarting its Spec Kit workflow.
5. Run Feature 053 T004 before broader implementation.
6. Preserve separate human/live/deployment evidence boundaries in every status
   report and merge decision.
