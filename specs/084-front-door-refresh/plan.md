# Implementation Plan: Front-door-only refresh for hosted targets

**Branch**: `feat/front-door-refresh` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/084-front-door-refresh/spec.md`

## Summary

Add `sb host refresh-front-door` (CLI) and `host_front_door_refresh` (MCP). It re-renders
one hosted target's front-door route (Caddy or nginx, gate names included) with the
current renderer, bound to what the target's last successful delivery recorded, reloads
it, verifies it with the same checks an apply uses, and restores the previous route on
failure through spec 081's fail-closed restore. It creates no delivery attempt and touches
no container, Compose project, initializer, DNS record or certificate. It holds the target
effect lock and the remote target lease for its whole run, refuses on an active or
uncertain operation, and writes an audit line before and after its single remote change.

Side scope: `delivery inspect` and `host retire-delivery` accept `--name`/`--target` and
resolve a stack target's attempt through the delivery scope its hosting record names.

## Technical Context

**Language/Version**: Python 3.11+ (controller), stdlib only for new modules

**Primary Dependencies**: existing `sandbox.commands.hosting` edge adapters
(`_CaddyHostEdge`, `_NginxHostEdge`), `sandbox.core._hosting` renderers,
`sandbox.hosting.front_door.nginx`, `sandbox.hosting.target_lease`,
`sandbox.hosting.recovery.repository.RecoveryRepository`, `sandbox.delivery`

**Storage**: controller `hosts.json` (via `RecoveryRepository`), a new controller audit log
`$SANDBOX_HOME/runtime/front-door-refresh/audit.jsonl`

**Testing**: `python3 -m unittest` on xspeed-dev durable jobs (Haiku helpers, one job at a
time, `--cancel-on-stall`)

**Target Platform**: controller on macOS/Linux; remotes with a Caddy or nginx front door

**Project Type**: CLI tool + MCP server

**Performance Goals**: one strict route read, at most one reload, one verification

**Constraints**: no new consumers of `sandbox_core.py` / registry internals; nothing in the
modules fingerprinted by `sandbox/remote_runtime/shapes.py`; stay off `ui_dash.py`,
`sandbox/hosting/panel_*`, `src/web`; unforced Caddy and nginx transaction commands unchanged

**Scale/Scope**: one target per call

## Constitution Check

| Principle | Status |
|---|---|
| I/II Registry and per-project model | Not affected: hosted targets resolve through the hosting state and stack registry, as `host status` does. |
| III Single entry, modular package | New logic in `sandbox/hosting/front_door/refresh.py`; command wiring in the existing `host` command. |
| IV Live-stack proof | **Deviation, recorded below.** Unit and integration tests run here; the live refresh is the orchestrator's post-merge step, because the brief forbids applying to any live target from this branch. |
| V Idempotency, docs with code | A refresh re-run converges; an identical route is not reloaded. `docs/remote-hosting.md`, both `sandbox-cli` skill copies and `specs/README.md` land in the same change. |
| VI Parity before removal | Nothing removed; apply and recovery paths keep their behaviour (two additive record fields: `front_door_declaration`, `front_door_refresh`). |

Gate: PASS with the Principle IV deviation below. The refresh selects its edge adapter through `_host_edge()`, which calls `front_door.require_capability(entry, HOST_ROUTES)` before any remote effect (repo gotcha 27).

## Project Structure

### Documentation (this feature)

```text
specs/084-front-door-refresh/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/cli.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/hosting/front_door/refresh.py   # new: codes, declaration digest, record binding, route features, audit
sandbox/hosting/front_door/nginx.py     # + origin_certificate_present (check only)
sandbox/commands/hosting.py             # + refresh orchestration, edge refresh_certificate, CLI dispatch,
                                        #   declaration digest recorded on apply/continuation success,
                                        #   retire-delivery recorded-scope fallback and selectors
sandbox/delivery/hosting.py             # + recorded_request_scope (shared with require_previous_snapshot)
sandbox/delivery/context.py             # + recorded scope in the inspect resolver
sandbox/delivery/service.py             # + recorded_scope pass-through
sandbox/commands/delivery.py            # + --name/--target for inspect
sandbox/cli.py                          # + action choice and help
mcp/wp-server/tools/share.py            # + host_front_door_refresh tool
mcp/wp-server/tools/manifest.py         # + tool name in the share group
tests/test_front_door_refresh.py        # new
tests/test_delivery_stack_selector.py   # new
docs/remote-hosting.md, skills/sandbox-cli/SKILL.md (+ .agents mirror), specs/README.md
```

**Structure Decision**: pure, testable rules in a new stdlib module; the orchestration sits
beside `_continue_host_edge_only` in `hosting.py`, because it reuses that file's private edge
adapters, verification and fail-closed restore rather than copying them.

## Complexity Tracking

| Item | Why | Simpler alternative rejected |
|---|---|---|
| Principle IV live proof deferred to the orchestrator | The brief forbids applying to live targets from this branch; the orchestrator runs the refresh after merge and migrate (quickstart.md, Live). | A throwaway hosted target on xspeed-dev is still a live apply. |
| Orchestration in `sandbox/commands/hosting.py` | It reuses that file's private edge adapters, verification and fail-closed restore; moving them would be a refactor outside scope. | A new command module importing those private helpers would add consumers of private names. |
