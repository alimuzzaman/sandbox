# Tasks: One-Click Host Storage Reclamation

**Input**: [plan.md](./plan.md), [contracts/](./contracts), [data-model.md](./data-model.md)

`[P]` = parallelisable (different files, no ordering dependency).

## Phase 1 — Policy core (blocks everything)

- [x] **T001** Create `sandbox/resources/reclaim.py` with the lifecycle classes, the
  always-protected rule table, and `classify_entry()` per policy contract §1–2.
- [x] **T002** Add `WORKSPACE_VOLUME_PATTERN` and `classify_volume()` per §3 (deny by
  default).
- [x] **T003** Add `parse_duration()`, `LeaseState`, `lease_state()`, and `in_use()` per §4
  and §7.
- [x] **T004** Add `TIERS` and `tier_candidates()` per §5, with the nesting invariant.
- [x] **T005** Add `growth_excluded()` per §6 (mtime-first).
- [x] **T006** Add `disk_capacity_pressure()` per §8 and re-export it from
  `sandbox/resources/attribution.py` so it sits beside the network pressure classification.
- [x] **T007** Add `manifest_intent()` / `manifest_outcome()` record builders per §9.

## Phase 2 — Tests for every safety rule (written against Phase 1)

- [x] **T008 [P]** `tests/test_resource_reclaim_policy.py`: classification of all six classes
  including the exact 2026-08-16 fixtures; exactly-one-class invariant.
- [x] **T009 [P]** Protected volumes: the four live-data volume names are rejected at every
  tier; the workspace-scoped names are accepted only when their workspace is a candidate.
- [x] **T010 [P]** Hosted sites: `hosts`, anything beneath it, and a registered hosted-site
  project are never candidates at any tier.
- [x] **T011 [P]** Liveness: an idle keepalive container does not protect a released or
  expired workspace; an active job does.
- [x] **T012 [P]** Growth: mtime advance excludes; size delta with equal mtime does not.
- [x] **T013 [P]** Tier nesting: `safe ⊆ tmp ⊆ all` over a randomised inventory.
- [x] **T014 [P]** Leases: release, ttl set/extend, default 7-day window, invalid duration.
- [x] **T015 [P]** Pressure thresholds and `auto_tier` restricted to `safe`.

## Phase 3 — Probe evidence and mutation

- [x] **T016** Extend `_REMOTE_PROGRAM` with `reclaim_inventory()` emitting the `reclaim`
  block on `observe` (contract `probe.md`), reusing the cached directory index for sizes.
- [x] **T017** Add the probe's `reclaim` action: host-side re-assertion of every protection
  rule, manifest-before-delete, elevation retry, absence verification, resumability.
- [x] **T018** Add the probe's `lease` action (get/set/release/list) with a path-free name
  grammar and atomic 0600 writes.
- [x] **T019** Add registry/index reconciliation to the probe's `reclaim` action and report
  the counts.
- [x] **T020** Add `RemoteResourceAdapter.reclaim()` / `.lease()` and a local runner that
  executes the same program through `sys.executable`.

## Phase 4 — Service and CLI

- [x] **T021** Widen `PLAN_SCOPES` to include `safe|tmp|all`; keep the cache rule.
- [x] **T022** Create `sandbox/resources/reclaim_service.py`: `inventory()`, `plan(tier)`,
  `cleanup(tier|plan_id, confirm)`, `reap(dry_run)`, `release()`, `ttl()`.
- [x] **T023** Wire it through `sandbox/resources/context.py` (`reclaim_service(remote)`).
- [x] **T024** Extend `sandbox/commands/resources.py`: `--tier`, tier/`--scope` exclusivity,
  reclaim rendering in `status`, plan and cleanup output per `contracts/cli.md`.
- [x] **T025** Extend `sandbox/commands/workspaces.py`: `release`, `ttl`, `reap` actions with
  `--ttl`, `--dry-run`, `--confirm`.

## Phase 5 — Service tests

- [x] **T026 [P]** `tests/test_resource_reclaim_service.py`: plan is side-effect free; plan
  ids; tier totals; skipped list.
- [x] **T027 [P]** Manifest is written before deletion; resume after interruption removes
  each candidate exactly once; second full run is a no-op.
- [x] **T028 [P]** Partial delete (path still present after removal) reports `failed`, never
  `removed`, and its bytes are excluded from the reclaimed total.
- [x] **T029 [P]** `tests/test_workspace_retention.py`: release/ttl/reap end to end against a
  fake probe runner.

## Phase 6 — Docs and live verification

- [x] **T030** `docs/resource-monitoring.md`: classes, tiers, safety rules, manifest,
  retention, thresholds, and what needs a host runtime sync.
- [x] **T031** `README.md` + `CLAUDE.md` + `skills/sandbox-cli/SKILL.md` command surface.
- [x] **T032** Read-only live verification against `scaleway-sandbox`: `status`, `plan
  --tier all`, `workspace reap --dry-run`. No deletion. Record in
  `implementation-evidence.md`.
- [x] **T033** Full `pytest` run; commit and push to `latest`.
