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
- Safe checkpoint: `7424b8089e412fbe86e5e7a69dae314ea65b3e70`
- Status: local/source implementation complete; independent Sol High review PASS
- Evidence: 280 tests passed; five race/handoff tests passed 20 repeated runs
  each; compile and `git diff --check` passed

The checkpoint adds fail-closed generation projection and authority, durable
pending-generation job binding, replay-safe launch ownership, bounded detached
supervisor handoff, terminal pin cleanup, interruption recovery, redaction, and
compatibility coverage.

Do not merge yet. Remaining gates are the disposable remote and hosted-app
acceptance, full recovery/credential/divergence/parity/cleanup quickstart,
evidence-bound feedback reconciliation, and final merge only after those pass.
Remote synchronized execution must remain fail-closed until the authoritative
controller adapter is composed and the installed revision/capability is proven.

## Feature 052: owned storage authority

- Remote branch: `origin/codex/owned-storage-authority-planning-repair`
- Safe checkpoint: `e9111cfe14cfd88521289e8e39302b95cee0774c`
- Status: **NOT READY — PUBLIC PORT BLOCKED**
- Evidence: `specs/052-owned-storage-authority/analysis.md` on the branch

Independent analysis proved that the accepted Feature 051 public recovery ports
cannot persist Feature 052 lifecycle state. The capability registry has no
owned-storage lifecycle member, and the activation host-state port accepts only
its closed `image_activation` value. Private-helper or direct-state workarounds
are forbidden. The non-executable draft task list was removed.

Resume only after an explicit design decision authorizes either:

1. a bounded public lifecycle transaction-port extension, reviewed as a public
   contract change without reworking accepted OCI behavior; or
2. an FR-058 redesign that selects another durable semantic owner.

After that decision, rerun the complete Spec Kit planning, task generation, and
independent analysis flow. Do not merge the current checkpoint into `latest`.

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
