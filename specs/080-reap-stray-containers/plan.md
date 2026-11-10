# Implementation Plan: Reclaim Stray Unlabeled Containers Owned by a Workspace

**Branch**: `fix/reap-stray-containers` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/080-reap-stray-containers/spec.md`

## Summary

Workspace reclaim fails on a checkout whose only leftover is a standalone container a
job started without Compose (feedback `43e3d4e5`, spec 077 R10). One stdlib-only
ownership rule, `assess_stray_container`, decides from a container's inspect record
whether it is a stray owned by one workspace checkout, and fingerprints the evidence
that decision used. The probe runs it while building reclaim entries; the controller
planner turns owned strays into `remove_containers` on the candidate and protects the
workspace when any bound container is not owned; the host cleanup re-enumerates the
containers bound to the checkout, refuses on any unplanned or changed one, records an
intent per stray, stops and removes only the planned strays, then lets the existing
project teardown and source removal run. Docs separate the cache/stale and tier
plan-id forms, and a tier cleanup refuses a plan whose stored tier differs.

## Technical Context

**Language/Version**: Python 3 (controller); the injected probe/teardown code is
stdlib-only and runs under the host's `python3` (3.8 syntax).

**Primary Dependencies**: Docker CLI on the host (`docker ps`, `docker inspect`,
`docker stop`, `docker rm`); no new dependency.

**Storage**: Deletion manifests `$SANDBOX_HOME/runtime/resources/deletions/<run>.jsonl`
(existing); tier plans in the existing plan store.

**Testing**: stdlib `unittest`, run on xspeed-dev through `sb job-start --remote`.

**Target Platform**: Linux remote hosts (xspeed-dev) and local hosts using the same
probe through `LocalProbeAdapter`.

**Project Type**: CLI tool (`sb`), module `sandbox/resources/`.

**Performance Goals**: Cleanup adds three Docker calls per workspace candidate (one
`ps`, one bounded mount inspect over all containers, one full inspect of the bound
containers), plus one stop and one remove per stray.

**Constraints**: Deletion safety first: fail closed on any missing or malformed
evidence; never prune; no change to `host_stack.py` or the hosting job paths.

**Scale/Scope**: At most 10 strays per candidate (`MAX_STRAYS_PER_CHECKOUT`).

## Constitution Check

| Principle | Gate | Status |
|---|---|---|
| I. Per-project instances | No instance model change | Pass |
| II. Registry is the source of truth | Registry and index reads unchanged; no direct registry JSON reads added | Pass |
| III. Single entry, modular package | Code lives in `sandbox/resources/` | Pass |
| IV. Live-stack verification | Unit tests on xspeed-dev plus a dry `resources plan` there; the new probe reaches xspeed-dev only after the orchestrator's `sb remote service migrate`, so live cleanup evidence is the orchestrator's post-merge step | Pass with recorded limit |
| V. Idempotency and docs-with-code | Rerun after any crash point converges (FR-009); `docs/resource-monitoring.md`, `CLAUDE.md` gotcha 23 updated in the same change | Pass |
| VI. Parity before removal | Nothing removed; the legacy refusal stays for plans without the new field | Pass |

Re-checked after design: unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/080-reap-stray-containers/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/stray-containers.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/resources/
├── workspace_teardown.py   # assess_stray_container (new), teardown_workspace_stack (strays mode)
├── remote.py               # probe attaches stray evidence; reclaim_run passes strays; injection
├── reclaim.py              # ReclaimCandidate.remove_containers; planner protects non-owned
└── reclaim_service.py      # passes remove_containers; refuses tier/plan mismatch
docs/resource-monitoring.md # plan-id forms; stray reclaim; migrate note
CLAUDE.md                   # gotcha 23 sentence
tests/test_reclaim_stray_containers.py   # new
```

**Structure Decision**: Extend the existing modules; the ownership rule lives once in
`workspace_teardown.py` and is injected into the host program like the existing
helpers, so planner and cleanup cannot disagree.

## Complexity Tracking

None.
