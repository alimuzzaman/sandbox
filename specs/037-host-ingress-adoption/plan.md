# Implementation Plan: Host Ingress Adoption

**Branch**: `latest` | **Date**: 2026-08-01 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/037-host-ingress-adoption/spec.md`

## Summary

Introduce a manifest-driven ingress service that observes kernel TCP listeners and
product evidence before any proxy action, selects one ingress capable of every promised
protocol, gives spec B its acceptable listener addresses, and transactionally adds an
attributable route only after B returns verified naming state. Sandbox Caddy is the DEFAULT
ingress on every platform and for every runtime: the service selects it whenever its exact
bind endpoints are free or already Sandbox-owned, without requiring any adapter to reach an
adoptable tier. Incumbent adoption is an opt-in alternative, selectable at setup and
switchable on demand at project or machine-local scope.

Initial mutation adapters cover Herd/Valet, system nginx, Apache, system Caddy, and a
Traefik file provider. Nginx Proxy Manager, DDEV router, Local, XAMPP, Laragon/WAMP, and
unidentified owners are classified without private-state mutation. Live-proof gating applies
to incumbent adoption only; it MUST NOT gate the default Sandbox Caddy path. The existing
`_domains.py`/proxy path keeps working unchanged and is not disabled, bypassed, or removed
until the new path has live parity and explicit removal approval.

## Technical Context

**Language/Version**: Python 3.10+ (current development host: Python 3.12); narrow POSIX
shell helper for validated privileged file/reload operations

**Primary Dependencies**: Python standard library; Linux `/proc` and `ss` evidence;
macOS `lsof`/service evidence; documented incumbent CLIs/config validation/reload surfaces;
the 038 domain service contract

**Storage**: Atomic locked ingress repository at
`$SANDBOX_HOME/runtime/network/ingress-state.json`; generated candidate/backup/recovery
artifacts below `$SANDBOX_HOME/runtime/network/ingress/`; credentials only in existing
machine-local secret storage

**Testing**: `unittest` unit/contract/integration suites; listener topology fixtures;
incumbent filesystem/command fakes; host conformance runner; live `./sb` route and HTTP
checks on each advertised product

**Target Platform**: Linux and macOS local hosts; WSL2 Linux-side detection/adoption;
Windows-side products report outside-platform

**Project Type**: Single Python CLI/MCP application with host ingress adapters

**Performance Goals**: Read-only detection/status within 2 seconds; plan within 3 seconds;
transactional apply/cleanup within 30 seconds excluding explicit interactive privilege;
bounded HTTP health retry

**Constraints**: No port stealing or split ingress; exact bind-scope semantics including
IPv4/IPv6 wildcard overlap; no non-TTY prompting; full-config validation before and after
candidate changes; rollback and previous-route health proof; per-port URL always preserved

**Scale/Scope**: Tens of routes across one selected ingress per hostname; two protocol
endpoints (HTTP/HTTPS) plus wildcard hostname capability; one host may expose multiple
candidate products but an explicit pin has precedence

## Constitution Check

*GATE: Passed before research and re-checked after design.*

- **I — Per-project model**: Route owners are canonical registry project root plus label;
  no global implicit instance is created by listener detection.
- **II — Registry source of truth**: The shared project registry resolves ownership;
  ingress state records route evidence only and is accessed through its repository.
- **III — Modular package**: New adapters and configuration register through explicit
  manifests/contracts under `sandbox/ingress/`; the application service owns sequencing.
  No new consumer reads registry/state JSON or imports compatibility facades directly.
- **IV — Live proof**: Every advertised adoption adapter requires a live
  add/request/update/remove evidence pack with incumbent-route preservation. The current
  host's active system Caddy is the first conformance target. Proof gating scopes adoption
  only; the default Sandbox Caddy ingress stays selectable and serving while adapters are
  unproven.
- **V — Idempotency/docs**: Desired/last-applied/observed comparison drives route updates
  and cleanup. README/config/support documentation lands with code.
- **VI — Parity before removal**: Existing Sandbox Caddy and Valet behavior remain the
  working default; compatibility facades stay live and functional, including the privileged
  clean-URL bootstrap, until live parity evidence and explicit removal approval. Disabling
  the legacy path in place counts as removal and is not permitted under this gate.

Post-design re-check: **PASS**. Privilege is isolated in a fixed helper and adapters never
reload before complete current/candidate validation and a rollback snapshot.

## Project Structure

### Documentation (this feature)

```text
specs/037-host-ingress-adoption/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── ingress-service.md
│   └── adapter.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/
├── application/
│   ├── ingress_service.py          # observe/select/plan/apply/cleanup
│   └── clean_url_service.py        # C → A offer → B naming → A activation
├── config/
│   └── domains.py                  # shared hostname/resolver/ingress policy
├── ingress/
│   ├── models.py
│   ├── registry.py                 # adapter protocol and registry
│   ├── manifest.py                 # deterministic products, tiers, proof gates
│   ├── listeners.py                # kernel endpoint/bind overlap observation
│   ├── detection.py                # product evidence and selection
│   ├── repository.py               # locked atomic route/consent/recovery state
│   ├── transaction.py              # validate/stage/activate/health/rollback
│   ├── verification.py
│   └── adapters/
│       ├── sandbox_caddy.py
│       ├── herd_valet.py
│       ├── nginx.py
│       ├── apache.py
│       ├── caddy.py
│       ├── traefik.py
│       └── detect_only.py
├── commands/
│   ├── domains.py                  # feature-owned CommandSpec and clean-URL handlers
│   └── net.py                      # remaining unrelated legacy networking commands
└── core/
    └── _domains.py                 # retained compatibility facade

tools/
└── ingress-helper.sh               # validated owned-fragment and reload verbs

mcp/wp-server/tools/
└── domains.py                      # same explicit clean-URL service group as 038

tests/
├── test_ingress_listeners.py
├── test_ingress_detection.py
├── test_ingress_service.py
├── test_ingress_transactions.py
├── test_ingress_adapters.py
├── test_clean_url_service.py
├── test_ingress_cli.py
├── test_ingress_mcp.py
└── host_fixtures/ingress/
```

**Structure Decision**: Keep ingress policy separate from resolver policy while composing
both in `clean_url_service.py`. Adapter implementations own product behavior, the shared
transaction runner owns rollback mechanics, and compatibility entry points delegate into
the application service.

## Complexity Tracking

No constitution violations require justification.
