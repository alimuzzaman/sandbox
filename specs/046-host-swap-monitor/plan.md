# Implementation Plan: Remote Host Swap and Memory Monitor Commands

**Branch**: `codex/feature-046-specification` | **Date**: 2026-08-30 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/046-host-swap-monitor/spec.md`

## Summary

Add a remote-only swap lifecycle and aggregate host-memory monitor to the existing
`resources` command family. A shared host-memory service will obtain strict, bounded
observations through the authenticated remote control endpoint, create immutable
target/revision/state-bound plans, and apply only an explicitly confirmed current plan.
The co-located remote service will expose fixed protocol actions backed by an
ownership-scoped Linux provider with a root-owned lock, operation journal, receipt,
system swap unit, sysctl policy, monitor unit/timer, and bounded aggregate JSONL history.
Every phase revalidates evidence; ambiguous delivery reconciles the same intent, and an
unverified restoration remains `rollback_incomplete` and blocks unrelated mutation.

## Technical Context

**Language/Version**: Python 3.9+ compatible standard-library CLI and remote control
service; fixed Linux systemd units and root-owned host artifacts rendered by Python

**Primary Dependencies**: existing `sandbox.resources` service/context/envelope patterns;
registered-remote lookup and authenticated control HTTP transport in
`sandbox.core._remote`; remote MCP service marker and runtime-revision contract; Linux
`systemd`, `util-linux` swap tools, `/proc/meminfo`, `/proc/swaps`, `/proc/pressure/memory`,
`/proc/vmstat`, `sysctl`, and `logrotate`

**Storage**: owner-only local immutable plans under
`$SANDBOX_HOME/runtime/resources/host-memory/plans/`; fixed root-owned remote operation
journal, ownership receipt, swap file, systemd units, sysctl drop-in, current aggregate
JSONL history, and at most eight owned weekly history files totaling at most 32 MiB

**Testing**: Python `unittest`; pure policy/model/store tests; fake authenticated transport
and fixed host-runner tests; CLI and remote-control contract tests; privacy/size/timeout,
drift, interruption, replay, rollback, and manifest-registration tests; separately
authorized disposable Linux remote acceptance before release

**Target Platform**: macOS or Linux controller using a registered remote; Linux remote
with a maintained systemd system manager, swap facilities, PSI or explicitly partial
pressure evidence, non-interactive narrowly scoped privilege, and the current authenticated
Sandbox remote service

**Project Type**: modular Python CLI plus authenticated remote control service; no new
general host-command API and no new MCP tool in the first version

**Performance Goals**: read-only status and bounded history complete within the existing
remote diagnostic budget; a normal monitor sample completes within five seconds; history
responses are capped at 1 MiB and 1,000 samples; protected phases are finite and report
their last durable phase rather than streaming indefinitely

**Constraints**: remote-only and registered-target-only; no SSH fallback; no secret,
process, argv, environment, private-path, or per-container identity output; one fixed owned
swap file; 1-8 GiB and all capacity bounds enforced at plan and apply; 15-minute plans plus
exact observation/revision digests; root-owned fixed paths with no symlink or foreign-state
adoption; no automatic enable/resize/reboot; exact-intent replay only; no inherited
environment dumps in tests or diagnostics

**Scale/Scope**: one registered remote and one protected intent per request; one owned swap
file; a small fixed host artifact set; five-minute samples; current history plus eight
weekly files and 32 MiB total; at most 1,000 returned samples per read

## Constitution Check

*GATE: PASS before Phase 0 research. Re-checked after Phase 1 design: PASS.*

| Principle or boundary | Assessment |
|---|---|
| I. Per-project is the only instance model | PASS. This is an explicit global remote-host operation. It never resolves, boots, or falls back to a project instance. |
| II. Registry is the source of truth | PASS. Named target resolution uses the registered-remote API. Host-memory code does not read registry JSON or invent a local/default target. |
| III. Single entry file, modular package | PASS. Policy, models, stores, remote adapter, and host provider live in `sandbox/resources/host_memory/`; the existing `resources` command owns CLI registration. `sb` remains unchanged. |
| IV. Live-stack verification | PASS BY PLAN, NOT YET PROVEN. Local planning and contract tests are necessary only. Release remains blocked on human-reviewed, separately authorized disposable Linux remote acceptance, including rollback and privacy evidence. |
| V. Idempotency and docs-with-code | PASS. Plan identity, host journal, lock, receipt, phase verification, same-intent reconciliation, and `already_current` define re-runs. README, operator docs, CLI skill, contracts, and tests must land with implementation. |
| VI. Feature parity before removal | PASS. Existing resource status, Spec 043 storage monitoring/scheduling, cleanup, jobs, workspaces, container limits, and remote-service lifecycle remain unchanged. Nothing is removed or bypassed. |
| Module and protocol boundaries | PASS. The command surface stays in the explicit `resources` `CommandSpec`; the fixed control protocol is extended in its owning server/adapter; no consumer of `sandbox_core.py`, raw registries, Hermes facades, or MCP app helpers is added. Capability/revision checks precede host mutation. |
| Secrets and privileged host safety | PASS BY DESIGN. The remote bearer stays in the existing in-memory transport. The protocol accepts typed fields only; the provider renders fixed owned artifacts and fixed argv, and returns strict allowlisted aggregate evidence. Consequential code requires human review before release. |
| Dev-tool packaging | PASS. Spec Kit artifacts and the managed context pointer stay outside the shipped package. |

Post-design re-check: the data model, CLI/control contracts, and quickstart preserve all
gates. No constitution exception is required. Planning does not claim live host behavior,
reboot persistence, deployment, or release readiness.

## Project Structure

### Documentation (this feature)

```text
specs/046-host-swap-monitor/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli.md
│   └── control-protocol.md
└── checklists/
    └── requirements.md
```

`tasks.md` is intentionally absent; it belongs to `/speckit-tasks`, not this planning
phase.

### Source Code (repository root)

```text
sandbox/
├── commands/
│   └── resources.py                 # add flat swap lifecycle/history actions and rendering
├── core/
│   └── _remote.py                   # strict authenticated host-memory control envelope
└── resources/
    ├── context.py                   # compose a remote-only HostMemoryService
    └── host_memory/
        ├── __init__.py              # public typed service/projection exports
        ├── models.py                # observations, plans, operations, samples, receipts
        ├── policy.py                # pure eligibility, freshness, warning, and transition rules
        ├── repository.py            # atomic local plans and root-owned remote journal/receipt stores
        ├── remote.py                # fixed authenticated protocol adapter; no SSH fallback
        ├── provider.py              # fixed Linux probes/artifacts/argv and rollback mechanics
        └── service.py               # plan/apply/replay/reconciliation orchestration

mcp/wp-server/
└── server.py                        # fixed `/resources` host-memory action dispatch and schema

docs/
└── resource-monitoring.md           # swap lifecycle, history, recovery, and release gates

skills/sandbox-cli/
└── SKILL.md                         # CLI-first routing and mutation boundary

tests/
├── test_host_memory_models.py
├── test_host_memory_policy.py
├── test_host_memory_repository.py
├── test_host_memory_provider.py
├── test_host_memory_service.py
├── test_host_memory_remote.py
├── test_host_memory_interfaces.py
├── test_resource_interfaces.py
├── test_resource_remote.py
└── test_remote.py
```

**Structure Decision**: use a feature-owned `sandbox.resources.host_memory` package because
the lifecycle has a separate state machine, plan/replay store, privileged host provider,
and privacy contract. CLI remains a thin extension of the already registered global
`resources` command. The authenticated remote endpoint owns the co-located host provider;
the controller never sends executable source, argv, paths, or shell text. No first-version
MCP tool is added: automation receives the stable `--json` envelope, while future MCP
exposure can adapt the same service without creating a second policy implementation.

## Research

See [research.md](research.md). All technical choices and integration unknowns are
resolved; no open clarification remains.

## Design Artifacts

- [data-model.md](data-model.md)
- [contracts/cli.md](contracts/cli.md)
- [contracts/control-protocol.md](contracts/control-protocol.md)
- [quickstart.md](quickstart.md)

## Composition Boundaries

- **Spec 043** owns controller-side *disk-storage* pressure classification, local schedule
  installation, safe-tier storage reclamation, and its last-run record. Feature 046 owns
  remote-host *RAM/swap* observations, swap lifecycle, remote system monitor, and bounded
  memory history. The two share only the top-level `resources` command and common envelope;
  neither reads, writes, schedules, or interprets the other's state.
- **Feature 047 host governance** may consume Feature 046's typed read-only host-memory
  status projection when deciding admission or policy. It does not own swap planning,
  confirmation, provider calls, operation locks, receipts, history, rollback, or mutation.
  Feature 046 exposes evidence; Feature 047 remains a consumer and must treat unknown,
  partial, drifted, or rollback-incomplete evidence as non-authorizing.
- Existing remote service lifecycle remains the authority for registration,
  authentication, ownership marker, and runtime revision. Feature 046 extends the fixed
  co-located resource protocol only after those facts match; it never installs, migrates,
  restarts, stops, or repairs the remote service itself.

## Complexity Tracking

No constitution violations require justification.
