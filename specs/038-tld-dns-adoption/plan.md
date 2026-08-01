# Implementation Plan: TLD and DNS Adoption

**Branch**: `latest` | **Date**: 2026-08-01 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/038-tld-dns-adoption/spec.md`

## Summary

Replace the legacy DNS-takeover branch with a resolver service that observes the active
owner, selects a manifest-registered adapter, plans an attributable exact-name or zone
binding, applies it transactionally after interactive consent, and verifies a fresh lookup
against an ingress-supplied address. Routed resolvers use a Sandbox-owned, non-forwarding
dnsmasq authority on a collision-checked unprivileged loopback endpoint. Existing `.tst`
identities remain unchanged; new unpinned identities use `.test`; `.local` is rejected.

The existing `_domains.py` entry points remain as compatibility facades while lifecycle
callers move to the new application service. An adapter is advertised as adoptable only
when its manifest entry has the required live evidence; otherwise detection reports the
implemented-but-unproven or detect-only tier and preserves the per-port URL.

## Technical Context

**Language/Version**: Python 3.10+ (current development host: Python 3.12); POSIX shell only
for the narrow privileged helper

**Primary Dependencies**: Python standard library; existing PyYAML dependency;
`BoundedProcessRunner` and `HttpProbe` shared mechanisms; host `dnsmasq`; documented
`resolvectl`, `nmcli`, macOS resolver-file, Herd, and Valet control surfaces selected
through adapters

**Storage**: Existing project/override configuration plus an atomic, locked repository at
`$SANDBOX_HOME/runtime/network/resolver-state.json`; generated authority configuration,
PID, logs, and recovery records below `$SANDBOX_HOME/runtime/network/`

**Testing**: `unittest` unit/contract/integration suites; isolated fake host-command
fixtures; resolver conformance runner; live `./sb domains` lookup plus HTTP verification

**Target Platform**: Linux and macOS local development hosts; WSL2 detection without
Windows-side mutation

**Project Type**: Single Python CLI/MCP product with host-service adapters

**Performance Goals**: Read-only status returns within 2 seconds on a healthy host; apply
and cleanup remain bounded by 30 seconds excluding an explicit interactive privilege step;
fresh-answer verification uses a bounded retry window

**Constraints**: No global resolver takeover; no raw state-file consumers; no non-TTY
prompt; exact ownership/drift checks before mutation; unrelated DNS behavior preserved;
public names never shadowed; every advertised adapter live-proven

**Scale/Scope**: Tens of local instances, exact records by default, a small number of
shared local zones, one authority process per machine, one resolver owner active at a time

## Constitution Check

*GATE: Passed before research and re-checked after design.*

- **I — Per-project model**: Every binding owner is the canonical project root plus label
  from the registry. Global authority state exists only as reference-counted shared
  mechanism; it cannot create an implicit instance.
- **II — Registry source of truth**: Instance identity is resolved by the registry service.
  Resolver state stores attributable integration evidence, never a second project map.
- **III — Modular package**: New behavior lives in `sandbox/network/` and
  `sandbox/application/domain_service.py`, registered through explicit adapter/config/MCP
  manifests. No new consumer imports `sandbox_core.py` or registry JSON directly.
- **IV — Live proof**: The quickstart and adapter evidence contract require a fresh DNS
  lookup followed by an HTTP request through A. Unit tests are not the done gate.
- **V — Idempotency/docs**: Plan/apply/status/cleanup compare desired, last-applied, and
  observed state; README and config reference changes are in scope.
- **VI — Parity before removal**: `_domains.py`, current commands, and the current Caddy
  path remain compatibility facades until live parity is recorded. No legacy removal is
  planned here.

Post-design re-check: **PASS**. The only privileged component is a narrow validated helper;
all policy and ownership decisions remain in the unprivileged service and all failure paths
retain the working per-port URL.

## Project Structure

### Documentation (this feature)

```text
specs/038-tld-dns-adoption/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── domain-service.md
│   └── cli-mcp.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/
├── application/
│   └── domain_service.py          # orchestration and A/B handoff
├── config/
│   ├── domains.py                 # normalized project policy
│   └── manifest.py                # explicit schema registration
├── network/
│   ├── models.py                  # immutable observations/plans/results
│   ├── registry.py                # adapter contracts and registry
│   ├── manifest.py                # deterministic built-in adapters/proof tiers
│   ├── detection.py               # read-only resolver ownership evidence
│   ├── authority.py               # scoped dnsmasq config/process lifecycle
│   ├── repository.py              # locked atomic owned-state repository
│   ├── verification.py            # fresh DNS and HTTP checks
│   └── adapters/
│       ├── resolved.py
│       ├── networkmanager.py
│       ├── macos.py
│       ├── dnsmasq.py
│       ├── incumbent.py
│       ├── hosts.py
│       └── external.py
├── commands/
│   ├── domains.py                 # feature-owned CommandSpec and handlers
│   └── net.py                     # remaining unrelated legacy networking commands
└── core/
    └── _domains.py                # retained compatibility facade

tools/
└── resolver-helper.sh             # fixed, validated privileged mutations only

mcp/wp-server/tools/
├── instances.py                   # compatibility setup result delegation
└── domains.py                     # explicit import-safe status/plan/apply group

tests/
├── test_domain_models.py
├── test_domain_detection.py
├── test_domain_service.py
├── test_domain_adapters.py
├── test_domain_authority.py
├── test_domain_cli.py
├── test_domain_mcp.py
└── host_fixtures/resolvers/       # isolated command/filesystem observations
```

**Structure Decision**: Use a new bounded `sandbox.network` domain package rather than
expanding `core/_domains.py`. The application service owns sequencing; adapters own only
resolver-specific policy; the authority and repository own shared mechanisms. Legacy
functions delegate through a compatibility facade for rollback safety. The config provider
must preserve omitted-versus-explicit hostname/TLD provenance for both WordPress and
generic Compose descriptors; changing the legacy scalar default alone is forbidden.

## Complexity Tracking

No constitution violations require justification.
