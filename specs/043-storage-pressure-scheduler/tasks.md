# Tasks: Scheduled storage-pressure monitor and safe-tier reaper

**Input**: [plan.md](./plan.md), [spec.md](./spec.md), [data-model.md](./data-model.md),
[contracts/](./contracts/)

Ordering is by dependency. `[P]` marks tasks touching disjoint files that may run in
parallel.

## Phase 1 — Configuration (foundation; everything else consumes it)

- [X] **T001** Create `sandbox/config/storage_monitor.py` with
  `StorageMonitorConfigError`, `DEFAULTS`, and pure `normalize_storage_monitor(raw)`
  implementing every rule in `contracts/config.md`: safe-only tier, threshold ordering,
  strict booleans, duration parsing, unknown-key rejection. No I/O.
- [X] **T002** Register the provider in `sandbox/config/manifest.py` as a new explicit
  `MACHINE_CONFIG_PROVIDERS` tuple with `apply_machine_config(result)`, mirroring the
  existing project-scoped tuple's `(key, provider, owner, order)` shape.
- [X] **T003** Add `resources: monitor:` machine defaults to `sandbox.yml` with a comment
  block stating that `auto_enabled` and `reap_enabled` authorise deletion and default off.

## Phase 2 — Policy resolution and the run record (user stories 1, 3)

- [ ] **T004** Create `sandbox/resources/monitor.py` with `resolve_policy(remote)` layering
  built-in defaults, `sandbox.yml`, `sandbox.local.yml`, and
  `remotes.<name>.storage_monitor`, raising `unknown_target` for an unregistered name.
- [ ] **T005** In the same module, add the record store: `record_path(target)`,
  `write_record(record)` (atomic replace, mode 0600), `read_record(target)`, and
  `record_age_seconds(record, now)`.
- [ ] **T006** In the same module, add `monitor_lock(target)` — an O_EXCL lock with stale
  detection by age and PID, returning `lock_held` rather than blocking.
- [ ] **T007** [P] Add `storage_doctor_checks()` returning `{label, ok, hint}` rows for the
  local target and every configured remote, from records only, treating a missing or stale
  record as a failed check with its age and the refresh command.

## Phase 3 — Schedule rendering and activation (user story 2)

- [ ] **T008** Create `sandbox/resources/schedule.py` with pure `build_schedule_plan(policy,
  target, platform)` rendering systemd `.service` + `.timer` (with `flock -n`) or a launchd
  `.plist`, refusing any command other than the fixed monitor argv, and always reporting
  `enabled: false`.
- [ ] **T009** In the same module, add `activate(plan, confirm)` / `deactivate(plan,
  confirm)`: refuse without confirmation with code `protected_operation`; on confirmation
  write the unit(s) 0600/0644, run the bounded enable/disable command, and report every path
  written and the reverse command. Activation of an identical existing schedule returns
  `unchanged`.

## Phase 4 — The scheduled run (user stories 3, 4)

- [ ] **T010** Add `ReclaimService.monitor(policy, *, trigger, dry_run, budget_seconds)` to
  `sandbox/resources/reclaim_service.py`: measure with `directory_cache="cache_only"`,
  classify through `policy.disk_capacity_pressure` with the resolved ratios, run the safe
  tier only when the gate is satisfied and not `dry_run`, always run the reap (dry unless
  `reap_enabled` and not `dry_run`), and return the MonitorRunRecord shape.
- [ ] **T011** Ensure the automatic cleanup call is `tier="safe", trigger="scheduled_auto"`
  and that a non-safe configured tier propagates the existing `invalid_auto_tier` refusal
  before any provider call.

## Phase 5 — CLI surface (user stories 1, 2, 3, 4)

- [ ] **T012** Add `monitor` and `schedule` to the `resources` action choices in
  `sandbox/commands/resources.py` with `--scheduled`, `--dry-run`, `--activate`,
  `--deactivate`, and the existing `--confirm`/`--json`; reject invalid flag combinations.
- [ ] **T013** Add `_emit_monitor()` and `_emit_schedule()` renderers carrying free bytes,
  total, free percentage, threshold crossed, and the next command on warning/critical, and
  adding no warning line on `normal`. Exit 1 on `critical`/`unknown`/refusal.
- [ ] **T014** Add the read-only "Storage pressure" section to `sb doctor`
  (`sandbox/commands/lifecycle.py`), consuming `storage_doctor_checks()` in the same
  `check(label, ok, hint)` shape the remote-target section already uses.

## Phase 6 — MCP tier parity (user story 5)

- [ ] **T015** Add `tier` to `resource_cleanup_plan` and `resource_cleanup_apply` in
  `mcp/wp-server/tools/resources.py`, routing tiers through the reclaim service and refusing
  tier+scope, neither, and unknown tiers.
- [ ] **T016** Declare `reclaim_service_factory` for the `resources` group in
  `mcp/wp-server/tools/manifest.py` and provide it in `mcp/wp-server/server.py`.

## Phase 7 — Tests

- [ ] **T017** [P] `tests/test_storage_monitor_policy.py` — defaults; every validation
  rejection; layer precedence; classification at exactly `warn_ratio`, just above, exactly
  `critical_ratio`, `0` free, `unknown` capacity; the automatic gate on/off.
- [ ] **T018** [P] `tests/test_storage_monitor_schedule.py` — plan renders with
  `enabled: false` and writes nothing; activation and deactivation refused without confirm;
  fixed argv enforced; both platforms render; idempotent activation.
- [ ] **T019** [P] `tests/test_storage_monitor_runner.py` — default configuration deletes
  nothing at every level; auto path runs only `safe` and only when eligible; non-safe tier
  refused before any provider call; reap dry by default and real when opted in; record
  written with the full contract; lock held yields `skipped`.
- [ ] **T020** [P] `tests/test_mcp_resource_tier.py` — tier plans route to the reclaim
  service; apply without confirm refused; tier+scope, neither, and unknown tier refused.

## Phase 8 — Docs and verification

- [ ] **T021** Extend `docs/resource-monitoring.md` with the monitoring/scheduling section,
  the config keys and defaults, the activation gate, and the doctor output.
- [ ] **T022** Update `README.md`, `CLAUDE.md` (gotcha 23 subsection only), and
  `skills/sandbox-cli/SKILL.md` with the two new actions and the off-by-default rule.
- [ ] **T023** Verify read-only against the real `scaleway-sandbox`: `resources monitor
  --dry-run`, `resources schedule` (renders only), and an unconfirmed activation refusal.
  Record the evidence. No deletion; no timer activated.
