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
- Final checkpoint: `bc4ff51` (merged into `latest` at `b350374`)
- Status: **PLANNING REPAIRED AND MERGED INTO LATEST**
- Evidence: `specs/052-owned-storage-authority/analysis.md`, `tasks.md` (53 tasks)

On 2026-09-04, Option 2 was authorized: FR-058 was amended to establish a dedicated,
crash-safe `StorageAuthorityLifecycleRepository` with generation CAS and advisory
locking, completely decoupling owned storage lifecycle from OCI hosting infrastructure.
Specifications, data models, contracts, research notes, plan, and 53 dependency-ordered
tasks were generated and aligned with zero diff on protected OCI hosting paths. Code
review findings resolved and merged into `latest` (`b350374`).

## Feature 053: instance-scoped server configuration fragments

- Remote branch: `origin/codex/server-config-fragments-work`
- Final checkpoint: `02bf39d` (merged into `latest` at `5b7e132`)
- Status: **COMPLETE AND MERGED INTO LATEST**
- Evidence: `specs/053-server-config-fragments/acceptance-evidence.md`

All phases (T001–T108) completed: live disposable OpenLiteSpeed feasibility probe (T004),
foundation and models (T005–T018), validation and preflight (T019–T040), nginx and OLS
renderers and reloads (T041–T060), CLI inspection and application (T061–T082), dry-run
and exact inspection (T083–T090), documentation and regression gates (T091–T098),
security review and human approval (T099–T101), live acceptance on `scaleway-sandbox`
(T102–T108). Merged into `latest` (`5b7e132`).

## Feature 046: bounded host swap provisioning and memory monitoring

- Remote branch: `origin/codex/feat-046-us2-planning`
- Current checkpoint: `4a3a77d`
- Status: **PHASES 1–5 COMPLETE (US3 FAIL-CLOSED SAFETY GATE GREEN)**
- Evidence: `specs/046-host-swap-monitor/acceptance-evidence.md`

Phases 1–5 completed (T001–T047). Controller-owned planning, policy refusal matrix,
provider preflight without side effects, strict underscore wire action allowlists,
and Feature 047 projection-only composition tests are fully GREEN (46 focused tests
passed, zero diff on protected paths). Protected apply remains strictly unregistered
and unreachable pending Phase 6 implementation.

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
