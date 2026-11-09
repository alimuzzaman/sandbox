# Qualification and Validation Guide

This is a proposed validation sequence, not evidence that it has run. Use only approved disposable Ubuntu 24.04 hosts and explicitly approved GitHub App/repository grants. Do not install, provision credentials, mutate a live host, publish, or deploy without the required task-specific authorization. Proposed CLI names below are descriptive placeholders until implementation registers them.

## Prerequisites

- Reviewed Miu upstream API/CLI contract and pinned Miu build with provenance evidence.
- Candidate RexEnv v0.9.7 (`2e92c00bd927825f1bb818a0bd47a851a0dae27b`) provenance checked on target; exact platform qualification recorded.
- Disposable Ubuntu 24.04 host; capacity and neighboring-resource inventory captured; no personal/ambient GitHub credentials available to managed service.
- Human-approved selected GitHub App, callback origin and repository grant; no key/setup-code passed to Sandbox.
- Sandbox project registrations and two disposable projects; supported RexEnv DB export/import and owned-file capture mechanism established.

## Read-only support and proposal checks

1. Inspect Sandbox host and project runtime support; expect unknown/unqualified capability to be reported without mutation.
2. Inspect Miu service ownership/build/health; expect unknown provenance to remain unknown.
3. Generate install/adoption/upgrade/removal proposals. Review owner, exact resources affected/preserved, active work, compatibility, recovery limits and consent. Confirm no host changes occurred.

## Managed service and App workflow

4. On the disposable host after explicit approval, confirm one service install proposal. Repeat the same operation identity and verify it resolves to the same result without duplicate service resources.
5. Provision a scoped principal through the local stdin-only workflow. Verify output omits the secret and stored permissions are private. Verify missing/revoked scope and unlisted repository requests refuse before effects.
6. Complete the human App connection and verify reported exact identity and selected repository grants without secret exposure. Check absent App permissions do not use env, saved token or host `gh` identity.
7. Submit one report-only QA operation bound to repository, PR, base SHA and head SHA. Verify exact checkout revisions, attributable site, bounded evidence and verdict through supported status/evidence APIs.
8. Repeat exact request identity/intent; expect existing operation. Reuse identity with changed intent; expect conflict. Simulate lost response/restart; resolve the original operation before retry. Cancel and verify capacity remains held until cessation and cleanup proof.
9. Verify no comments, publication, App registration or grant expansion without separately recorded authorization. For an unsafe PR with no explicit trust qualification, verify refusal precedes code execution.
10. Review upgrade/recovery/removal proposals while work is active; confirm unsafe cleanup is refused. After safely resolved work, exercise approved upgrade recovery and scoped removal, then verify preserved data and neighboring host resources.

## RexEnv project workflow

11. Explicitly select RexEnv for two registered disposable projects. Verify exact unique project-to-site mapping and reported capabilities/health. Verify a new/default project still selects Docker.
12. Exercise create/reconcile/start/stop/inspect/native execute/test/remove for each project. Verify every operation targets only its project and no unqualified generic exec is offered.
13. Capture a full baseline using supported upstream DB export and Sandbox-owned file capture. Confirm manifest explicitly records both. Change DB and files, restore the selected baseline, verify both content sets and retain the restore point.
14. Interrupt representative create/snapshot/restore/remove operations. Inspect original operation identity, partial effects and fences. Verify no duplicate run/site and no lease release while effects may remain.
15. Exercise unsupported platform/build, identity ambiguity, DNS/listener conflict, insufficient capacity, stale proposal and missing DB/file support. Each must refuse before effects and preserve neighbors.

## Evidence and release gate

Capture bounded CLI and applicable MCP outputs, build provenance, snapshots/coverage, operation ledger states, before/after neighbor inventory and App identity/write audit. Acceptance requires spec SC-001–008 and all applicable FRs to pass on the assembled candidate. A documentation-only plan, unit tests, API existence, or older-build evidence does not qualify runtime readiness. If full DB+file recovery or any safety gate cannot be proven, report the precise blocker and keep the whole feature phase blocked.
