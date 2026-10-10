# Product Requirements Draft: Remote Server Health Monitoring

**Status**: Discovery

**Created**: 2026-10-10

**Last Refined**: 2026-10-10

**Input**: "check if we can improve our remote server monitoring by learning from xcloud? i attached a result from xcloud dashboard. you can look for it's script on the server, xcloud-london. check what script they are using. hook into it while we are hosting on xcloud and for other host review all the xcloud script on the server and learn from them and create a detailed plan/prd and save it. commit it. we will do implementations later."

**Drafting Configuration**: Claude Opus 5.5 root (speckit-refine, first draft). Evidence came from read-only inspection of xcloud-london on 2026-10-10: the xCloud provisioning run log, the monitor and cleanup sources, cron entries, sshd, modprobe, nginx and apt drop-ins, and the sysstat state. Three Haiku 5.5 helpers made read-only line-range reviews of the provisioning script. A user-supplied xCloud dashboard export and screenshot were also used. Technical findings are recorded in `docs/research/xcloud-server-monitoring-review.md`. No secret, callback token or key material was copied.

**Final Validation**: `PENDING`. The independent readiness review has not run yet.

**Validated On**: N/A

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Sandbox runs production on remotes: Lenzora, xspeed-hub and alimuzzaman.me
on xcloud-london, and development workloads on the same hosts. The operator
can see disk pressure (spec 043 storage monitor and 057 scheduled routine),
host memory and swap (spec 046), and one hosted target's service health
(`host status`). Nothing answers the questions an operator asks first when a
site is slow or down:

- Is the host itself busy? This covers CPU, load average, memory and swap.
- Which process or container is causing it, and which hosted target or
  development workspace owns it?
- How did the host behave over the last day? Was this a spike, or a trend?
- Is anything about to break? This covers a disk filling, a service down,
  a reboot pending, or security updates waiting.

The 2026-10-10 incident (feedback `6bba550f`) shows the cost. The root
filesystem of xcloud-london filled with about 21 development workspaces
(61 GB). Production Lenzora Postgres went into a PANIC/restart loop on
ENOSPC, and nobody was told until the site failed. The storage monitor
existed, but it was not scheduled on that remote and pushed no alert. It
also could not say which workspaces grew, or when.

xCloud, the panel provisioning xcloud-london, already ships a host monitor
whose dashboard shows exactly that first-look view. The user supplied an
export of it. The view covers:

- live CPU, memory, disk and uptime;
- top CPU and top memory processes;
- per-container CPU and memory;
- per-site disk, CPU and memory;
- state of critical services;
- upgradable packages and reboot-required;
- a largest files and folders scan;
- a 24-hour statistics graph.

Reading its scripts shows the collection techniques are simple and portable.
Several are better than what Sandbox does today. Examples are CPU measured
before the collector's own load, and per-site disk that includes the site's
Compose named volumes. It also shows practices Sandbox must not copy: an
obfuscated root binary, a TLS-unverified callback carrying a per-server
secret in the URL, and hourly-only sampling.

Sandbox hosts on xCloud today and will host elsewhere. It therefore needs
one health view that has two properties:

- on an xCloud-managed host, it coexists with xCloud's agent and reuses what
  that host already records, instead of duplicating it or interfering with it;
- on any other host, it provides the same signals itself.

## Users and Desired Outcomes

- **Operator (owner) of hosted production**: The operator wants one bounded
  command, or one MCP call, that shows a remote's current health. The view
  should attribute each heavy process, container and disk consumer to the
  hosted target or development workspace that owns it. The operator should
  learn about a dangerous condition before the site fails, not after.
- **Coding agent working on a remote**: The agent wants to check whether the
  host has headroom before submitting heavy work, and to explain a slow or
  failed job with host evidence rather than guesses.
- **Operator on an xCloud-managed host**: The operator wants Sandbox and
  xCloud to agree on the numbers. Sandbox must never break, slow down, or
  reveal anything from xCloud's own monitoring.

## Goals

- G1. One read-only health snapshot per remote. It covers:
  - CPU utilisation and load averages relative to the number of cores;
  - memory and swap;
  - each filesystem's used and free space, including the usable space of the
    volume that holds hosted data;
  - uptime;
  - the top processes by CPU and by memory;
  - per-container CPU and memory;
  - the state of critical host services;
  - pending package updates, including security updates, and whether a
    reboot is required.
- G2. Attribution. Each container, process group and disk consumer in the
  snapshot is labelled as one of the following:
  - a hosted target (remote/project/environment);
  - a development workspace or job;
  - Sandbox infrastructure;
  - another tenant or the system, such as xCloud-managed sites;
  - unattributed.
  Per-target disk includes the target's named volumes, not only its source
  directory.
- G3. Bounded history. Sandbox shows at least the last 24 hours of host CPU,
  load, memory, swap and per-filesystem free space at 10-minute or finer
  resolution. It keeps coarser history for at least 7 days. On a host that
  already records system statistics (xcloud-london does: sysstat, 10-minute
  samples, 7 days), Sandbox reuses that history rather than sampling a
  second time.
- G4. Alerts before failure. Some conditions are detected on a schedule on
  the remote, without the controller being on, and reported where the
  operator will see them:
  - disk pressure, together with the growth that caused it;
  - sustained CPU, memory or swap saturation;
  - a critical service down;
  - a hosted target's containers unhealthy;
  - a pending reboot;
  - security updates older than a threshold.
  Disk alerts name the top growers and the exact safe-cleanup command.
- G5. Largest consumers on demand. An explicit, bounded scan names the
  largest directories and files under the paths Sandbox owns. On request it
  also covers the whole host, where the probe can read. Each entry carries
  its attribution.
- G6. xCloud coexistence. On an xCloud-managed host, Sandbox detects the
  xCloud agent and reports it as present. It reuses the system history the
  host keeps. It never edits, disables, runs or reads the secrets of xCloud's
  monitoring, cleanup or provisioning artifacts.
- G7. Low overhead. A snapshot completes within a fixed time budget and adds
  no sustained load. It never runs as a resident daemon on hosts that do not
  opt into scheduled collection.

## Non-Goals

- Replacing xCloud's dashboard, or sending data to xCloud. Sandbox does not
  post to xCloud's callback, reuse its token, or change its schedule.
- A general-purpose metrics stack, such as Prometheus, Grafana, Netdata or a
  hosted APM. That means no long-term time-series store, no per-request
  application metrics and no log aggregation.
- Automatic remediation beyond what already exists. The monitor reports and
  recommends. The existing 057 safe-tier routine stays the only unattended
  deleter. Auto-restarting services, auto-upgrading packages and
  auto-rebooting are out of scope. xCloud's monitor restarts ssh on its own;
  Sandbox will not.
- Monitoring the hosted application's internals, such as WordPress or
  Postgres query performance. Container health and resource use are in
  scope; the application itself is not.
- Local-machine (developer laptop) monitoring beyond what `sb resources` and
  `sb doctor` already do.
- Host hardening changes, such as sshd policy, kernel module blacklists or
  firewall rules. Findings from xCloud's hardening are recorded in the
  research note as a separate future candidate.
- A web dashboard UI. CLI, JSON and MCP come first. A rendered view is a
  later product decision.

## Product Scenarios

### Scenario 1: First look at a slow site

- **Starting state**: Lenzora on xcloud-london responds slowly.
- **User action**: The operator asks for the remote's health snapshot.
- **Expected outcome**: One response within the time budget shows:
  - CPU at 92% of 4 cores and a 5-minute load of 7.8;
  - memory at 81% with swap in use;
  - the top CPU process, a development job's `node` build, labelled with its
    workspace and job id;
  - the Lenzora containers at normal use;
  - root filesystem free space;
  - all critical services active.
  The operator can see the slowness comes from development work on the same
  host, not from production.

### Scenario 2: Disk filling, caught early

- **Starting state**: Development workspaces grow the root filesystem from
  70% to 88% over six hours.
- **User action**: None. The scheduled check runs on the remote.
- **Expected outcome**: Before free space crosses the critical reserve,
  Sandbox records a warning alert. The alert names the filesystem, the free
  space, the growth rate, the top three growing owners (workspace ids and
  sizes), and the exact safe-cleanup command. The operator sees the alert
  the next time they use Sandbox, through doctor, status or MCP. If a push
  channel is configured, the alert is also sent there.

### Scenario 3: The last 24 hours

- **Starting state**: The operator heard of an outage around 03:00.
- **User action**: The operator asks for the remote's history over the last
  24 hours.
- **Expected outcome**: The response holds the series that were requested:
  CPU, load, memory, swap and per-filesystem free space, at 10-minute
  resolution. Gaps are marked as gaps. The response states where the data
  came from: the host's existing system statistics, or Sandbox's own
  samples. Nothing is interpolated.

### Scenario 4: xCloud-managed host

- **Starting state**: xcloud-london has xCloud's hourly monitor cron, its
  daily cleanup cron and active sysstat collection.
- **User action**: The operator takes a snapshot and asks for history.
- **Expected outcome**:
  - The snapshot reports the xCloud agent as present. The agent's
    configuration and secret are never read; presence is inferred from the
    cron entry and the agent's directory.
  - History comes from the host's sysstat data, and Sandbox adds no sampler
    of its own for those series.
  - Running the snapshot does not change any xCloud file, cron entry,
    process or callback.

### Scenario 5: A host without xCloud

- **Starting state**: A plain Ubuntu remote with Docker and no sysstat.
- **User action**: The operator enables scheduled collection for the remote,
  which is a protected operation with explicit confirmation.
- **Expected outcome**:
  - Sandbox records its own bounded samples on the remote.
  - The 24-hour history is available after collection has run for 24 hours.
  - The retention ceiling is enforced on the remote.
  - Disabling collection removes the schedule. Dropping the history needs an
    explicit, confirmed request.

### Scenario 6: Largest consumers

- **Starting state**: Disk pressure is reported, and the operator wants
  detail.
- **User action**: The operator requests a largest-consumers scan.
- **Expected outcome**:
  - A bounded scan returns the top directories and files under the
    Sandbox-owned roots, each with its size and attribution.
  - The response says whether the scan was complete or stopped at its time
    budget.
  - A host-wide scan is a separate, explicit request. Paths the probe cannot
    read are reported as unmeasured, not skipped silently.

### Negative scenarios

- **Remote unreachable or control service down**: The snapshot reports the
  remote as unavailable, with the reason and the age of the last good
  snapshot. It never shows stale numbers as current.
- **Partial collection**: For example, Docker is not responding, or the
  package list is locked by a running apt. Each signal that failed reports
  `unavailable` with a reason. Every other signal is still returned, and
  the snapshot is marked partial.
- **Disk full on the remote**: The snapshot and the alert still work when
  the remote's filesystem has no free space. Collection never needs to write
  to the full filesystem in order to read, and it never writes a log once
  free space is below a floor. xCloud's monitor skips its log when less than
  10 MB is free; Sandbox does the same.
- **Container with a long or hostile name or command line**: The output is
  escaped and bounded. A process command line is truncated to a fixed length
  and cannot break the JSON or leak argument secrets. Command lines are
  redacted with the existing redaction rules before they leave the host.
- **Process of another tenant**: On a shared host, processes and directories
  that Sandbox does not own are shown only as aggregates, labelled
  other/system. On request, they can be shown by process name and user,
  never with full command lines or file paths. The full list is available
  only to an elevated host-wide request.
- **xCloud agent changes or disappears**: Detection degrades to "not
  detected". History falls back to Sandbox's own samples if scheduled
  collection is enabled; otherwise it is reported as unavailable.
- **Collector overrun**: A snapshot that exceeds its budget returns what it
  has, marked partial, and leaves no orphaned child process.

## Proposed Product Behavior

- Snapshot, history, largest-consumers and alert listing are read-only
  operations. They are available from the CLI with `--json` and from MCP.
  They reach a remote through the authenticated control service, like the
  046 and 057 surfaces, and never fall back to SSH.
- Enabling or disabling scheduled collection and alert evaluation on a
  remote is protected and needs explicit confirmation, like
  `resources routine`.
- CPU utilisation is measured over a short fixed window, before the
  collector's other work starts, so the collector's own load is excluded.
  Utilisation counts I/O wait as busy. Top-process CPU is normalised to the
  host's core count so that 100% means the whole host.
- Attribution reuses Sandbox's existing ownership knowledge: the hosted
  target registry, the workspace and job registry, the Compose project
  labels, and the 043 attribution index. It must not introduce a second
  source of truth. On an xCloud host, sites owned by xCloud users are
  labelled "other tenant" (xCloud site).
- History sources, in order of preference:
  1. existing host system statistics (sysstat) when present and readable;
  2. Sandbox's own scheduled samples when collection is enabled;
  3. otherwise none, reported as unavailable.
  The response always names the source.
- Alert thresholds and the evaluation cadence are policy. They are resolved
  like the 043 storage-monitor policy: built-in defaults, then machine
  config, then a per-remote override. The disk thresholds reuse the 043
  `warn_ratio` and `critical_ratio` rather than adding a second set. Alerts
  are de-duplicated and carry the state they describe: when first raised,
  last confirmed, and cleared.
- Alerts are always visible in `sb doctor`, in remote status and in MCP.
  Pushing an alert to an external channel is optional and off by default.
  The channel is an open question.
- Update and reboot information is reported only. Security updates are
  counted separately from other updates where the package manager can tell
  them apart. Packages that the host intentionally holds back are reported
  as held, not as pending. One example is a nginx pinned to nginx.org and
  blacklisted from unattended upgrades, as xCloud does.

## Constraints and Dependencies

- Builds on existing features: 043 storage monitor and attribution index;
  046 host memory and its history; 057 remote scheduled routine; the control
  service and readiness model (061/063). Spec 060's per-target state must be
  respected: target labels come from the hosting registry.
- Remote work runs through the authenticated control service. A fix to
  remote code takes effect only after `remote service migrate`, which needs
  owner approval for production remotes. Any new controller-to-runtime
  payload bumps the control protocol (CLAUDE.md gotcha 28).
- The probe runs as the Sandbox remote user. On xcloud-london that user is
  in the docker group and has passwordless sudo. Other hosts may have
  neither, so each signal must degrade to `unavailable` without privilege.
  Elevation is used only where a signal needs it, and only with a bounded
  timeout inside sudo (gotcha 21).
- Hard rule (user instruction): no heavy work on xcloud-london without the
  owner's say-so. Scheduled collection must stay within a small CPU and I/O
  budget, and the largest-consumers host-wide scan is opt-in.
- Secrets: xCloud's callback URL embeds a per-server token. It must never be
  read, logged, stored or transmitted by Sandbox. Detection relies only on
  file and cron presence.
- Data stays on the remote and the operator's controller. Nothing is sent to
  a third party unless the operator configures a push channel.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| How to "hook into" xCloud | Coexist and reuse the host's recorded statistics. Do not run, decode or call xCloud's monitor binary | The monitor is an obfuscated root binary that sleeps 10 s and posts to xCloud with a secret. Running it would need root, double-sample CPU and risk an extra callback; its `nocallback` mode only prints the same signals Sandbox can read directly | Proposed (open question Q1) |
| Collection technique | Learn from xCloud's script (CPU delta first, core-normalised top, Compose-label volume attribution, bounded apt lock wait, low-disk log guard), implemented in Sandbox's own probe | Same numbers as the xCloud dashboard, without its anti-patterns | Proposed |
| Remediation | Report and recommend only; the 057 routine stays the only unattended deleter | Keeps deletion authority where it is reviewed | Existing policy |
| Disk thresholds | Reuse 043 `warn_ratio` / `critical_ratio` | One policy for disk pressure | Existing policy |
| Transport | Authenticated control service only, no SSH fallback | Same as 046/057 | Existing policy |

## Open Questions

- **Q1 (blocking)**: Confirm the meaning of "hook into it while we are
  hosting on xcloud". Option A (recommended) is to reuse what the host
  records, mainly sysstat, and to detect the xCloud agent. Sandbox would
  never execute it. Option B is to also run xCloud's monitor in its
  print-only mode, under sudo with a timeout. That option gives a
  byte-for-byte match with the xCloud dashboard, at the cost of root
  execution of an opaque binary and about 12 seconds of collection.
- **Q2 (blocking)**: Choose the push channel for alerts, if any. Options:
  none (doctor, status and MCP only); email through the operator's mail; a
  webhook; or T3/Claude notification.
- **Q3**: Retention of Sandbox's own samples on hosts without sysstat. The
  proposal is 10-minute samples for 48 hours plus hourly roll-ups for
  30 days, with a fixed size ceiling.
- **Q4**: Should a scheduled check on a host that lacks sysstat offer to
  install it? That is a host change, which is out of scope by default.

## Acceptance Outcomes

- AO1. On xcloud-london, one snapshot returns every G1 signal within
  20 seconds. Its CPU, memory, disk and per-container values agree with
  xCloud's dashboard taken at the same time, within 5 percentage points.
- AO2. In a snapshot of a host running at least one hosted target and one
  development workspace, at least 95% of container CPU and memory, and of
  disk use under Sandbox-owned roots, is attributed to a named owner. The
  rest is reported as unattributed, with its size.
- AO3. On xcloud-london, a 24-hour history request returns 10-minute series
  with gaps marked. It is sourced from the host's existing statistics, and
  no Sandbox sampler runs on that host.
- AO4. In a replay of the 2026-10-10 growth pattern on a disposable host,
  the warning alert is recorded before free space reaches the critical
  ratio. The alert names the top growers and the safe-cleanup command.
- AO5. Before and after a snapshot, history request and enabled schedule on
  xcloud-london, every xCloud file, cron entry and process is unchanged.
  xCloud's callback token never appears in any Sandbox output, record or
  log.
- AO6. When the remote's filesystem is full, the snapshot still returns and
  the alert still raises. No Sandbox write to the full filesystem is
  attempted.
- AO7. Scheduled collection, when enabled, uses less than 1% of one core
  averaged over an hour and stays inside its storage ceiling.
- AO8. Every signal that fails in a snapshot is reported individually as
  `unavailable` with a reason, while the other signals are still returned.

## Risks and Assumptions

- **Risk**: Attribution may be wrong on shared hosts where xCloud sites and
  Sandbox containers share Docker. A wrong owner label could steer an
  operator to the wrong cleanup. Mitigation: labels come only from
  registries and Compose labels, and anything else is "unattributed".
- **Risk**: sysstat output formats vary by version. History parsing must
  fail per series, not as a whole.
- **Risk**: xCloud could change its agent layout or stop sysstat. Detection
  and history must degrade cleanly (Negative scenarios).
- **Risk**: The cost of top-process sampling on a busy host. The sampling
  window must be short and fixed.
- **Assumption**: xcloud-london keeps sysstat active with world-readable
  data files. Observed 2026-10-10: 10-minute samples, 7-day history.
- **Assumption**: The Sandbox remote user keeps docker-group access on
  hosted remotes. Without it, per-container signals are `unavailable`.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [ ] Consequential choices are confirmed rather than inferred (Q1, Q2).
- [x] Acceptance outcomes are measurable and implementation-independent.
- [ ] No blocking open questions remain (Q1, Q2).
- [x] No implementation plan, task list, contracts, or code changes are included.
- [ ] The latest independent readiness review verdict is `PASS`.

**Readiness**: `NOT READY`. Waiting on owner decisions Q1 and Q2, then one independent readiness review.

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
