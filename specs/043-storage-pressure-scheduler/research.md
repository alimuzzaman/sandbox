# Research: Scheduled storage-pressure monitor and safe-tier reaper

## R1 — Where does the timer live, and on which init system?

**Decision**: the schedule is rendered for, and installed on, the **controlling machine**
(the machine that runs `sb`), not on the monitored remote. Rendering supports systemd user
units on Linux and a launchd user agent on macOS, chosen by the activation platform.

**Rationale**: `sb resources` and `sb workspace reap` ship their probe over SSH and need no
`sb` runtime on the host (CLAUDE.md gotcha 23), so putting the timer on the host would add a
host-side install requirement that the rest of the feature deliberately avoids. The existing
`sandbox/recovery/scheduler.py` already sets the precedent of rendering units locally with a
`--remote` target baked into the `ExecStart` command. The operator's machine here is macOS,
so systemd-only rendering would have produced a plan that cannot be activated anywhere it is
read; launchd rendering is therefore not gold-plating but the only path to a usable default.

**Alternatives considered**: installing on the remote via `ssh systemctl` (rejected: adds a
host-side `sb` install and a second update path); cron (rejected: no per-unit timeout,
no randomized delay, no lock semantics, and no structured status); an in-process daemon
(rejected: Sandbox has no supervised long-lived process and one would need its own recovery).

## R2 — How is overlap prevented?

**Decision**: systemd `ExecStart` wraps the command in `flock -n`, exactly as
`render_systemd_units` already does for recovery; launchd, which has no equivalent, gets the
same guarantee from an O_EXCL lock file taken by the monitor runner itself
(`$SANDBOX_HOME/runtime/resources/monitor/<digest>.lock`), released in a `finally`.

**Rationale**: the runner-side lock is the portable guarantee and also protects a human run
that races a scheduled one. `flock` is kept on Linux because it prevents even starting the
Python process. A stale lock is detected by age and its owning PID and is broken with a
recorded reason rather than deadlocking the schedule forever.

**Alternatives considered**: relying on systemd's `Type=oneshot` non-overlap (rejected: it
only covers the same unit, not a concurrent human run); a lock in the remote's state
(rejected: two different controlling machines are not the case this feature protects, and a
remote lock costs a round trip before we know whether we need one).

## R3 — Where do the configuration keys live?

**Decision**: machine defaults under a new top-level `resources: monitor:` block in
`sandbox.yml`; per-machine override of the same block in `$SANDBOX_HOME/sandbox.local.yml`;
per-target override under `remotes.<name>.storage_monitor` in `$SANDBOX_HOME/sandbox.local.yml`.
Resolution is `built-in defaults → sandbox.yml → sandbox.local.yml → remotes.<name>.storage_monitor`.

**Rationale**: this is exactly the machine-state table in CLAUDE.md — machine/global defaults
in `sandbox.yml`, per-machine override in `$SANDBOX_HOME/sandbox.local.yml` — and
`remotes.<name>` is already the established per-target home (`sandbox/core/_remote.py`
docstring). Nothing about this belongs to a project checkout, so `sandbox.config.json` is
wrong: a storage monitor describes a machine an operator owns, not a plugin repository.

**Alternatives considered**: a separate `monitor.yml` (rejected: a fourth config file for six
keys); per-project `sandbox.config.json` (rejected: wrong ownership, and a project checkout
would silently change a host's deletion policy).

## R4 — How does the configuration register without bypassing module boundaries?

**Decision**: a pure `normalize_storage_monitor(raw)` in `sandbox/config/storage_monitor.py`,
registered in a new explicit `MACHINE_CONFIG_PROVIDERS` tuple in `sandbox/config/manifest.py`
alongside the existing project-scoped `COMMON_CONFIG_PROVIDERS`, with the same
`(key, provider, owner, order)` shape.

**Rationale**: CLAUDE.md requires new config schemas to register through explicit
manifests/contracts. `COMMON_CONFIG_PROVIDERS` is project-descriptor scoped and normalizing a
machine block through it would put machine policy into every project descriptor. A sibling
tuple in the same manifest module keeps one registration point and one file to read to learn
what config exists, without conflating the two scopes.

**Alternatives considered**: extending `COMMON_CONFIG_PROVIDERS` (rejected: scope
conflation); reading the YAML directly in `monitor.py` (rejected: that is the ad-hoc read the
boundary rule exists to prevent).

## R5 — How does `sb doctor` warn without paying for an SSH round trip?

**Decision**: `sb doctor` reads the durable last-run record per target and reports the
recorded level, numbers, and record age. It never contacts a target. A missing record, or one
older than `record_max_age` (default 6h), is reported as *unknown with a stated age*, with the
command that would refresh it — never as healthy.

**Rationale**: `doctor` is run constantly and must stay fast and offline-safe; a probe per
configured remote would add seconds and a failure mode per remote. Recording is what the
schedule is for, so reading the record is also the honest signal about whether the schedule is
actually running: a stale record is itself a finding.

**Alternatives considered**: probing each remote with a short budget (rejected: slow, and a
network error would read as a doctor failure unrelated to storage); showing nothing unless a
probe was already run this session (rejected: silence is indistinguishable from health).

## R6 — Should the automatic path reuse `disk_capacity_pressure()` or re-derive?

**Decision**: reuse it unchanged. `disk_capacity_pressure()` already raises
`ReclaimPolicyError("invalid_auto_tier")` for any tier other than `safe`; the new policy layer
validates the same rule a second time at configuration-resolution time so a bad configuration
is refused before any host contact, and the classifier remains the single place that decides
eligibility.

**Rationale**: two independent refusals of the same unsafe input is the pattern 042 already
uses for volume protection (policy refuses, probe refuses again host-side). Re-deriving the
classification would create a second definition of "warning" that could drift from the one
`sb resources status` prints.

**Alternatives considered**: moving the gate into the runner (rejected: the runner has I/O and
could not be unit-tested without a host).

## R7 — What does a scheduled reap do by default?

**Decision**: dry run. `reap_enabled` defaults to `false`, and a scheduled reap with it false
calls the existing `ReclaimService.reap(dry_run=True)` and reports candidates. With it true,
the reap runs with `confirm=True` and the existing 7-day default window, honouring leases,
explicit releases, and in-use protection unchanged.

**Rationale**: the brief and FR-021 require the retention reaper on the same schedule, but an
operator who accepts a monitor must not thereby accept a deleter. `reap_enabled` is a separate
key from `auto_enabled` because they authorise different deletions: the safe tier versus
expired workspaces. Note that reap is *not* subject to the safe-tier restriction — it is the
retention path, whose own guards are leases, releases, and activity, and it is the same code
path a human gets from `sb workspace reap --confirm`.

**Alternatives considered**: one combined `auto_enabled` switch (rejected: it would make
enabling capacity reclamation silently enable workspace deletion); reaping always (rejected:
off-by-default is the whole safety posture).
