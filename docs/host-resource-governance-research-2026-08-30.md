# Shared-host production resource governance research

**Date:** 2026-08-30
**Repository revision inspected:** `995e94465ebbe3da6781eafdde573c25f1856b1d`
**Scope:** Generic Sandbox product research for protecting hosted production
while lending spare capacity to development, tests, CI, previews, and builds.
No implementation, test, deployment, remote mutation, or secret access was
performed.

## Product conclusion

Sandbox has useful pieces but no complete host-wide production resource
governor. The required model is:

```text
static kernel-enforced safety envelope
                 +
dynamic pressure-aware admission and preemption
                 +
durable leases, verification, and audit evidence
```

Per-container limits alone are insufficient. Twenty valid test containers can
exhaust a host while each remains under its own ceiling. Production and
opportunistic workloads require aggregate parent pools whose enforcement
survives the Sandbox controller.

## Existing reusable mechanisms

### Generic Compose instance limits

Sandbox already validates `cpus`, `memoryMB`, and `pids` for generic Compose
instances and generates an override containing `cpus`, `mem_limit`, and
`pids_limit`. This demonstrates per-instance enforcement but currently targets
the selected web service rather than an aggregate hosted environment.

Relevant source:

- `sandbox/config/compose.py`
- `sandbox/runtimes/compose.py`
- `tests/test_generic_compose.py`

### Native isolation compiler

The managed/native resource compiler already models CPU, memory, PIDs, runtime,
disk, inodes, file descriptors, connections, and I/O weight. Its systemd output
includes CPU quota, `MemoryHigh`, `MemoryMax`, disabled swap, `TasksMax`, file
descriptor limits, and I/O weight. This is a useful policy vocabulary but is
not yet the generic hosted-Compose authority.

Relevant source:

- `sandbox/isolation/resources.py`
- native runtime verification and acceptance tests.

### Durable jobs and capacity leases

The job scheduler already provides:

- bounded parallel slots;
- disk and available-memory admission floors;
- workspace and host-capacity leases;
- TTL/heartbeat renewal;
- stale and terminal lease reconciliation;
- queue position and blocker evidence.

Its current capacity model is job-count based. It does not reserve declared CPU,
memory, I/O, PIDs, bytes, or inodes, and does not protect a production pool from
aggregate non-production work.

Relevant source:

- `sandbox/jobs/scheduler.py`
- job repository lease tables and tests.

### Remote diagnostics

Sandbox remote diagnostics already return bounded, redacted host memory, load,
free disk, job state, point-in-time process observations, and Docker container
CPU/memory/PID rows. The output includes explicit limitations and avoids
environment or credential inspection.

This is a strong evidence surface, but the current scheduler does not consume a
complete pressure and cgroup event model for admission/preemption.

Relevant source:

- `sandbox/core/_remote.py`
- `sandbox/services/container_stats.py`
- `docs/cli-first-operation.md`.

### Storage-pressure and swap work

Existing specifications cover ownership-safe storage reclamation, storage
pressure scheduling, resource observation, and a host swap monitor. They must
remain separate authorities:

- resource admission cannot authorize broad deletion;
- swap observation cannot be treated as proof of memory safety;
- failed or partial measurement means unavailable, not zero usage;
- cleanup needs explicit ownership and non-use evidence.

Relevant artifacts:

- `specs/035-resource-monitoring-cleanup/`
- `specs/036-deep-disk-attribution/`
- `specs/042-host-storage-reclamation/`
- `specs/043-storage-pressure-scheduler/`
- `specs/046-host-swap-monitor/prd.md`.

### Hosted Compose lifecycle

Sandbox hosting validates a manifest, composes declared files plus a generated
runtime override, runs targeted services, records bounded receipts, and can
disable on-host builds with `compose.build: false`. It does not currently own a
generic resource-intent schema, environment aggregate pools, preemption policy,
or post-start cgroup enforcement proof.

Relevant source:

- `sandbox/core/_hosting.py`
- `sandbox/commands/hosting.py`
- `docs/remote-hosting.md`.

## Gaps

1. No normalized resource intent shared by hosting, generic Compose, durable
   jobs, builds, and native runtimes.
2. No explicit system/production/opportunistic/build aggregate pool model.
3. No production protected memory floor.
4. No work-conserving CPU/I/O priority between production and tests.
5. No aggregate non-production hard maximum.
6. No final Compose-model validation preventing project configuration from
   escaping policy.
7. No create-before-start inspection and post-start proof of effective cgroup
   placement and limits.
8. No PSI-aware green/yellow/red admission state with hysteresis and cooldown.
9. No resource-request accounting across active leases.
10. No generic priority/preemptibility declaration or victim policy.
11. No graceful drain/checkpoint/restart sequence with bounded forced-stop
    escalation.
12. No dedicated constrained BuildKit pool; builds can escape service limits
    through the shared daemon.
13. No multi-resource fairness between owners and projects.
14. No complete terminal reason taxonomy for pressure, OOM, PID, disk, manual
    cancellation, and supersession.
15. No generic emergency pause/drain/resume control with dry-run explanation.
16. No generic reboot ordering that restores production before opportunistic
    admission.
17. No provider capability profile that fails closed when required controllers
    or quota mechanisms are absent.

## Required generic resource intent

Every hosted environment, service, build, instance, and durable job must resolve
to a versioned intent containing:

- workload class and owner;
- environment class;
- CPU request, weight, and hard maximum;
- memory request/protection, throttle target, and hard maximum;
- PID hard maximum;
- I/O weight and optional verified device caps;
- disk bytes, inodes, writable-layer, artifact, cache, and log policy;
- runtime deadline;
- priority and preemption mode;
- stop signal and grace period;
- durable lease and fencing identity;
- provider capability requirements;
- evidence and retention classification.

Repository input is untrusted policy input. Users may request only allowed
classes and bounded values. They cannot assign host-control or production-
critical priority without an operator-owned policy granting it.

## Enforcement hierarchy

On a supported systemd/cgroup-v2 host:

```text
-.slice
├── system.slice
└── sandbox.slice
    ├── sandbox-production.slice
    └── sandbox-opportunistic.slice
        ├── sandbox-development.slice
        ├── sandbox-test.slice
        ├── sandbox-preview.slice
        └── sandbox-build.slice
```

Systemd owns top-level slice properties. Docker Compose services receive the
correct `cgroup_parent` plus per-service ceilings. Sandbox verifies both the
rendered Compose model and the effective Docker/cgroup state.

### Hard controls

- parent `MemoryMax` and per-service `mem_limit`;
- parent and per-service PID/task maximum;
- aggregate pool CPU quota only where a hard ceiling is required;
- verified disk/inode admission reserve;
- bounded runtime/deadline;
- immutable owner and preemption policy.

### Flexible controls

- CPU weight/shares so tests can use idle cores but production wins contention;
- I/O weight when the host device/controller supports it;
- aggregate `MemoryHigh` to throttle/reclaim opportunistic work;
- dynamic admission and concurrency;
- cooperative release, drain, checkpoint, or restart;
- queue fairness and backfill.

Compose `mem_reservation` is not a verified mapping to cgroup-v2
`memory.high`. Sandbox must use its aggregate slice authority for the throttle
boundary and probe effective cgroup state.

## Host capability gate

Before production protection can be enabled, Sandbox must prove:

- detected CPU and usable memory;
- cgroup v2 and delegated controllers;
- systemd version and cgroup driver;
- Docker/Compose versions and supported resource fields;
- effective CPU, memory, PID, I/O, and swap behavior;
- block device, filesystem, mount, storage driver, and quota capabilities;
- BuildKit driver and parent/resource control;
- bytes and inode measurement;
- log driver/rotation;
- PSI availability and cgroup event files;
- controller and observation freshness.

If mandatory production enforcement cannot be verified, production apply fails
closed. If optional I/O or filesystem quota support is absent, the capability
profile must state the fallback and preserve stricter admission reserves.

## Admission state machine

### Green

- telemetry is fresh;
- effective limits match desired policy;
- production health and latency are within target;
- host and pool reserves are above recovery thresholds;
- declared requests fit all resource budgets;
- configured opportunistic concurrency is allowed.

### Yellow

- stop new heavy tests, builds, and previews;
- allow only separately reserved interactive work;
- reduce browser/build/test concurrency;
- cancel superseded replaceable runs;
- request cooperative cache release and idle-worker shutdown;
- do not evict until the grace period or a hard threshold.

### Red

- admit no opportunistic work;
- select only eligible non-production victims;
- drain/checkpoint/restart before forced stop;
- perform only scoped ownership-proven reclamation;
- retain protected production and host control;
- emit pressure-specific terminal evidence.

### Recovery

- use lower exit thresholds than entry thresholds;
- require sustained healthy observations;
- use a cooldown;
- increase concurrency one step per interval;
- do not immediately replay every preempted job.

Load average is advisory only. Admission needs PSI, cgroup events, current and
reserved resources, disk/inodes, production health, and evidence freshness.

## Fairness and priority

Priority order:

1. host control and recovery;
2. production database, queue, ingress, web, and durable storage authority;
3. production workers;
4. production deploy, rollback, migration, and backup finalization;
5. interactive development smoke/debug work;
6. standard CI;
7. exhaustive CI, bulk browser/comparison work, and builds;
8. previews and cache warming.

Within an opportunistic priority class, use weighted fair sharing, per-owner
concurrency caps, aging, and dominant-resource accounting rather than strict
FIFO or job count. Small-job backfill is permitted only when it cannot delay a
higher-priority reserved workload.

## Preemption and terminal evidence

Supported policy classes:

- `never`: production data services, migrations, releases, backup finalization;
- `drain`: stop after current request/work item;
- `checkpoint`: persist bounded progress and resume later;
- `restart`: disposable tests, CI, previews, and builds.

Required terminal distinctions include:

- succeeded;
- failed;
- cancelled by user;
- superseded;
- preempted for production;
- pressure-evicted;
- memory-throttled;
- OOM-killed;
- PID-limited;
- disk-rejected;
- deadline-exceeded;
- interrupted/ambiguous.

An expired lease never proves termination. State-mutating work must be fenced
before any replacement attempt. Disposable jobs may be stopped and requeued
only after runtime reconciliation.

## Build and disk policy

Builds require a dedicated BuildKit pool with central concurrency, bounded
parallelism, cache accounting, and garbage-collection floors. Production
deploys should consume prebuilt immutable images and use `compose.build: false`.

There is no portable Compose-wide disk quota. Writable-layer size controls are
storage-driver/filesystem dependent and do not constrain arbitrary named
volumes. Sandbox must combine:

- provider capability detection;
- host bytes/inode reserves;
- separate quota-capable directories/filesystems where supported;
- BuildKit `reservedSpace`, `maxUsedSpace`, and `minFreeSpace` policy;
- log rotation;
- artifact/cache budgets and TTLs;
- scoped cleanup with ownership/non-use evidence;
- emergency admission stop before the reserve is consumed.

Broad `docker system prune` or automatic volume deletion is not an acceptable
resource-governance mechanism.

## Controller safety

- Use a host-scoped admission lock and durable controller lease.
- Stop admission if the controller cannot renew ownership.
- Hard limits remain when the controller is unavailable.
- Reconcile durable desired state, runtime containers, cgroup placement, and
  leases on restart.
- Do not shrink `MemoryMax` below current usage plus margin; stop admission,
  apply pressure, drain, wait, then lower.
- Treat repository policy, telemetry, and runtime output as untrusted input.
- Record fixed-field, bounded, secret-free evidence for every decision.
- Manual emergency controls require narrow scope, a reason, and durable receipt.

## Acceptance matrix

| Scenario | Required result |
|---|---|
| Test CPU saturation | Tests use idle CPU; production latency stays within target under contention. |
| Test crosses memory high | Test throttles; global OOM does not occur. |
| Test crosses memory max | Only the test workload is killed and classified. |
| Fork bomb | PID cap contains it; Docker and production remain responsive. |
| I/O pressure | Heavy admission stops before production DB objective fails. |
| Disk/log storm | Work stops before byte/inode reserve; only scoped disposable data is reclaimed. |
| Parallel BuildKit work | Aggregate builder remains in build pool and cache reserve. |
| Scheduler crash | Limits persist; no new opportunistic work starts. |
| Scheduler restart | Existing work is adopted, fenced, stopped, or requeued without duplication. |
| Rapid threshold crossings | Hysteresis prevents repeated start/stop oscillation. |
| Production surge | Opportunistic capacity is reclaimed within the target deadline. |
| Ignored graceful stop | Work is force-terminated after the declared grace period. |
| Multiple owners | Fair sharing prevents starvation and burst gaming. |
| Host reboot | Host control and production recover before non-production admission. |
| Missing cgroup capability | Production apply fails closed instead of silently ignoring policy. |
| Resource-profile update | Effective settings are verified and unsafe shrink is refused. |

## Delivery stages

1. **Intent and capability:** normalized schema, host probes, profiles, plan,
   and read-only effective-state evidence.
2. **Static pools:** systemd slices, generated Compose enforcement overrides,
   per-service ceilings, and post-create/post-start verification.
3. **Multi-resource scheduler:** requests, pool accounting, PSI states,
   hysteresis, leases, preemption, and terminal reasons.
4. **Build/disk governance:** dedicated builder, cache GC, logs, artifact
   budgets, inode/byte floors, and quota-capability fallbacks.
5. **Fairness and operator controls:** owner quotas, aging, backfill, dry-run,
   emergency pause/drain/resume, and dashboards.
6. **Adversarial acceptance:** live pressure, OOM, PID, I/O, disk, crash,
   restart, reboot, and recovery evidence on a non-production host.
7. **Lenzora adoption:** policy profile, off-host immutable build, R2 migration,
   measured service limits, and production-first recovery proof.

## Primary sources

- Docker resource constraints: https://docs.docker.com/engine/containers/resource_constraints/
- Docker Compose services: https://docs.docker.com/reference/compose-file/services/
- Compose deploy resources: https://docs.docker.com/reference/compose-file/deploy/
- Docker runtime metrics: https://docs.docker.com/engine/containers/runmetrics/
- Docker logging: https://docs.docker.com/engine/logging/configure/
- BuildKit configuration: https://docs.docker.com/build/buildkit/configure/
- BuildKit garbage collection: https://docs.docker.com/build/cache/garbage-collection/
- Linux cgroup v2: https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html
- Linux pressure-stall information: https://docs.kernel.org/accounting/psi.html
- systemd cgroup interface: https://systemd.io/CONTROL_GROUP_INTERFACE/
- systemd pressure handling: https://systemd.io/PRESSURE/
- systemd resource control: https://github.com/systemd/systemd/blob/main/man/systemd.resource-control.xml
- Kubernetes requests/limits: https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/
- Kubernetes node pressure: https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/
- Kubernetes priority/preemption: https://kubernetes.io/docs/concepts/scheduling-eviction/pod-priority-preemption/
- Kubernetes leases: https://kubernetes.io/docs/concepts/architecture/leases/
- Kueue fair sharing: https://kueue.sigs.k8s.io/docs/concepts/fair_sharing/
- GitHub Actions concurrency: https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency
- GitHub Actions cancellation: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-cancellation

## Research conclusion

Sandbox can become a credible single-host production protector without adopting
Kubernetes immediately. The necessary product boundary is not another static
Compose limit. It is a portable resource-intent model, verified aggregate
cgroup pools, pressure-aware durable admission, and safe preemption whose hard
limits survive controller failure. Kubernetes patterns are useful policy
references, but a single-node Kubernetes installation would add complexity
without removing the Contabo host failure domain.
