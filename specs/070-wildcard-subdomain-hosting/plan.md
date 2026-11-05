# Implementation Plan: Wildcard Subdomain Hosting

**Branch**: `latest` | **Date**: 2026-11-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/070-wildcard-subdomain-hosting/spec.md`

## Summary

Add shared DNS coverage for one zone on one nginx-fronted remote. A new package owns one remote coverage document per zone, one error-only fallback route with its own wildcard origin certificate, and the shared wildcard record. Hosted targets under an active zone default to shared coverage: they make no DNS write, and verification reaches the public edge without depending on the target name's DNS answer. Existing exact routes keep working, and only the registered fallback gets an exemption from the nginx overlap check.

## Technical Context

**Language/Version**: Python 3.12, standard library only in the remote program

**Primary Dependencies**: existing `sandbox/core/_cloudflare.py` client, `sandbox/hosting/front_door/nginx.py`, `sandbox/commands/hosting.py`, `sandbox/hosting/destroy.py`

**Storage**: one JSON document per zone at `<remote SANDBOX_HOME>/runtime/zone-coverage/zones/<zone>.json`, under a flock, written by a fixed program sent over the authenticated SSH transport, as in `sandbox/hosting/coordination/program.py`

**Testing**: `python -m unittest`, with fake Cloudflare and SSH doubles as in `tests/test_front_door_nginx.py`. Live proof follows [quickstart.md](quickstart.md)

**Target Platform**: nginx front door on Linux. Caddy remotes refuse with `front_door_capability_unavailable`

**Project Type**: CLI

**Performance Goals**: every store call within the 15 s bound coordination uses. A covered apply adds at most one provider listing and three store calls (read, pending, bound). A target in a zone with no coverage record adds one store read and no provider call

**Constraints**: no `default_server`, no `nginx.conf` edit, no foreign file edit, no secret values in any output, no change for a zone without a coverage document

**Scale/Scope**: one zone on one remote in the first release, up to 512 dependents per zone

## Constitution Check

| Principle | Result |
|-----------|--------|
| I. Per-project instance model | Pass. Hosted targets only; no instance change |
| II. Registry is the source of truth | Pass. The registry stays untouched. Coverage state is shared between controllers, so it lives on the remote, the same way coordination does |
| III. Single entry file, modular package | Pass. New package `sandbox/hosting/zone_coverage/`, no new consumers of `sandbox_core.py` or `COMMANDS` |
| IV. Live-stack verification | Required before enabling, with a read-only smoke test after each stage. See quickstart. Unit tests are not proof. Cases that cannot run live are listed there with the reason |
| V. Idempotency and docs with code | Pass. Every step observes before writing. Each stage in tasks.md carries its own docs |
| VI. Feature parity before removal | Pass. Nothing is removed or disabled. The exact-record path is unchanged outside covered zones |

Module boundaries: a capability check (`ZONE_COVERAGE`) runs before side effects, through `front_door.require_capability`. No new protocol-fingerprinted payload, so no control-protocol bump, and no migrate is needed for this feature to work. See research R7.

Human review before release: production DNS path, fallback install, enable and disable, overwrite authorization, and the takeover rule for interrupted operations.

## Project Structure

### Documentation (this feature)

```text
specs/070-wildcard-subdomain-hosting/
├── prd.md
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli.md
│   └── zone-store.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/hosting/zone_coverage/
├── __init__.py     # errors and fixed codes
├── models.py       # states, transitions, document validation, name eligibility
├── program.py      # the fixed remote store program
├── client.py       # controller side of the store
├── fallback.py     # fallback rendering, install, origin probe, certificate
├── service.py      # plan, enable, disable, repair, renew, remove-fallback, status
└── targets.py      # per-target decision, dependent binding, public edge probe

sandbox/hosting/front_door/__init__.py   # ZONE_COVERAGE capability
sandbox/hosting/front_door/nginx.py      # exemption for the registered fallback only
sandbox/core/_cloudflare.py              # paginated zone listing
sandbox/core/_hosting.py                 # cloudflare.dns validation, desired_plan
sandbox/commands/hosting.py              # plan, apply, status, recovery, verification
sandbox/commands/remote.py, sandbox/cli.py   # sb remote coverage
sandbox/hosting/destroy.py               # release the dependent, shared-retained report

tests/test_zone_coverage_models.py
tests/test_zone_coverage_program.py
tests/test_zone_coverage_fallback.py
tests/test_zone_coverage_service.py
tests/test_zone_coverage_targets.py
tests/test_front_door_nginx.py           # extended
```

**Structure Decision**: one new package under `sandbox/hosting/`, because the lifecycle is independent of any target (FR-037). `sandbox/commands/hosting.py` already has 7555 lines, so it gets only call sites.

## Complexity Tracking

| Addition | Why needed | Simpler alternative rejected because |
|----------|------------|--------------------------------------|
| Remote store document | Dependents must be visible across controllers (FR-041) | Controller-local state cannot see another controller |
| Fallback exemption in the overlap check | Exact routes must coexist with one wildcard (FR-029) | A general wildcard exemption would let application wildcards take unknown names |
