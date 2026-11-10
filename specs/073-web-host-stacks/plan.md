# Implementation Plan: Stacks and Hosted Sites View

**Branch**: `feat/web-host-stacks` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/073-web-host-stacks/spec.md`

## Summary

Add a read-only "Stacks and hosted sites" page to `sb web`. A new `GET /api/hosting` returns one normalized view of stacks, plain hosted targets and their server rows. Two sources feed it:

- **This server** (`sb web` on a Sandbox remote): secret-free target summaries that `sb host apply` now writes to `runtime/hosts/<project>/<env>/summary.json` on the remote, plus site links that `sb deploy --stack` writes to `runtime/stack-sites/<slug>/<hostname>.json`. Both are under the remote's own `$SANDBOX_HOME`.
- **The controller** (`sb web` on a Mac): the controller's stack registry (`runtime/stacks/registry.json`, now with a `sites` list) and hosting state (`hosts.json`).

Health comes from an in-process cache. A single background worker refreshes it, one bounded HTTPS request per hostname per 60 seconds, and the endpoint never waits for it.

## Technical Context

**Language/Version**: Python 3.11+ (stdlib only in new modules); TypeScript 5 for the panel, built by Vite into the vendored `config/sandbox-web.js`.

**Primary Dependencies**: none new. `http.client`, `ssl`, `threading`, `concurrent.futures`, `json`.

**Storage**: JSON files under `$SANDBOX_HOME/runtime/` (remote: `hosts/<p>/<e>/summary.json`, `stack-sites/<slug>/<host>.json`; controller: the existing `stacks/registry.json`).

**Testing**: `unittest` (`tests/test_panel_*.py`, `tests/test_deploy_stack_link.py`), run in Haiku helpers on xspeed-dev through `job-start`; `npm run typecheck` and `npm run build` for `src/web`.

**Target Platform**: Linux remote (panel as a user service) and macOS controller.

**Project Type**: CLI with an embedded web panel.

**Performance Goals**: `/api/hosting` answers in under 1 second with 20 targets (it only reads JSON files); at most one probe per hostname per 60 seconds.

**Constraints**: read-only panel; no secret values or key names; probes only to hostnames from Sandbox state; summary and link writes are best-effort and never fail an apply. A missing `--stack` refuses a deploy before any effect.

**Scale/Scope**: tens of targets per remote; health cap of 64 hostnames per refresh round.

## Constitution Check

| Principle | Gate | Status |
|---|---|---|
| I. Per-project instances | No new instance model; site links point to existing registry instances. | Pass |
| II. Registry is the source of truth | Instance existence on the server is read through the existing `registry_all()` call the panel already makes; no direct JSON reads of the registry. | Pass |
| III. Single entry, modular package | New logic in `sandbox/hosting/panel_records.py`, `panel_view.py` and `panel_health.py`; `ui_dash.py` only routes. | Pass |
| IV. Live-stack proof | The panel runs locally against a fixture `SANDBOX_HOME`, with screenshots. The remote summary write is proven by unit tests with a recorded command transport. A live `host apply` is out of bounds for this task (owner's hard limit), so the next real apply proves it; the handoff says so. | Pass, with a stated limit |
| V. Idempotent, docs with code | Summary and link writes are atomic overwrites (temp file, then `mv`) and safe to repeat. README and `docs/remote-hosting.md` change in the same commit. | Pass |
| VI. Parity before removal | Nothing removed or disabled. | Pass |

Module-boundary rule (CLAUDE.md): no new consumers of `sandbox_core.py`. The view builder takes registry rows from `ui_dash`, which already calls `registry_all()`.

## Project Structure

### Documentation (this feature)

```text
specs/073-web-host-stacks/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── api-hosting.md
│   └── remote-records.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/hosting/panel_records.py   # types, summary/link builders, remote writers, server readers (new)
sandbox/hosting/panel_view.py      # one view model from server or controller records (new)
sandbox/hosting/panel_health.py    # bounded, cached HTTPS health checks (new)
sandbox/commands/ui_dash.py        # GET /api/hosting route only
sandbox/commands/hosting.py        # 3 small hooks in _apply_host and its caller
sandbox/commands/deploy.py         # --stack validation (before effects) and link recording (after success)
sandbox/cli.py                     # deploy --stack flag
sandbox/hosting/stack_registry.py  # StackRecord gains optional sites (one line)
sandbox/commands/host_stack.py     # carry sites across up (one line)
src/web/src/{types,api,router,render,main}.ts, src/web/src/pages/hosting.ts
sandbox/core/_paths.py             # sidebar link in _WEB_PAGE
config/sandbox-web.js, config/sandbox-web.css (rebuilt)
tests/test_panel_records.py, tests/test_panel_view.py, tests/test_panel_health.py,
tests/test_web_hosting_api.py, tests/test_deploy_stack_link.py
```

**Structure Decision**: The new pure modules live in `sandbox/hosting/` beside `stack_registry.py`, because they share its records. `ui_dash.py` stays a thin router, as it is for `/api/remote/`.

## Shared files with other branches

`feat/host-exec` also edits `sandbox/commands/hosting.py` and `host_stack.py`. This branch's edits there are limited to:

- `hosting.py`: `_apply_host` gains a `summary_recorder=None` keyword. It keeps the durable context it already computes, and makes one `started()` call after source publish and one `finished(True)` call before it returns. The caller builds the recorder and wraps the call in `recorder.run(lambda: ...)`.
- `host_stack.py`: the record built in `_up` keeps `"sites": (previous or {}).get("sites", [])`.
- `stack_registry.py`: `StackRecord` gains `sites: list[SiteLinkRef]` and the `SiteLinkRef` TypedDict.

## Complexity Tracking

None. No principle is violated.
