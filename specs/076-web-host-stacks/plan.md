# Implementation Plan: Stacks and Hosted Sites View

**Branch**: `feat/web-host-stacks` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/076-web-host-stacks/spec.md`

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
specs/076-web-host-stacks/
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

## Follow-up 2026-11-10: lifetimes and owner screenshots (User Stories 6-8, FR-017 to FR-028)

Branch `feat/panel-lifetime`. Same read-only panel, three additions:

- **Lifetimes (US6).** Each stack and hosted target carries a `lifetime` view built from spec 077's record. On a server the panel reads the records through a new lock-free, bounded `Store.peek(now)` in `hoststore.py`. On a controller it runs `sb host stack list --remote R --json` per remote in a background cache. Both sources go through `hoststore.parse_record` and `hoststore.view`, so validation and eligibility stay in one place (CLAUDE.md gotcha 35). A new `panel_lifetime.py` only projects the parsed record onto the panel's allow-list and orders the rows. `GET /api/hosting` gains a top-level `lifetimes` list in that order. Host inventory rows gain the same `lifetime` view, read by the remote itself, so host pages show it on a server and on a controller.
- **Landing page and sidebar (US7).** These are TypeScript only. `localHostView` leads with hosted apps when the host reports them, and its sections are labelled "Sandbox dev instances" and "Hosted apps (sb host apply / stacks)". The empty-state "New instance" prompt and the footer "Start all"/"Stop all" are hidden when there are no dev instances. The sidebar adds a hosted app count. The decisions sit in an import-free `src/web/src/ui/hostlabels.ts`, so `node --test` covers them.
- **Hosted targets table (US8).**
  - `SummaryRecorder` takes the outcome from the delivery record the apply returns. `LastApply` gains the status `unverified` and a `reason`.
  - Rows without a summary read "applied before tracking".
  - The inventory's `stack_slug` falls back to the lifetime record's slug.
  - The remote page's instance table is retitled "Sandbox dev instances".
  - CPU is labelled a point-in-time sample.

### Technical context (delta)

- No new dependency.
- The controller's lifetime fetch is a subprocess of the checkout's own `sb` entry, with a 90 s timeout, a 60 s cache, one fetch per remote in flight, at most 16 remotes and 2 workers. The page never waits for it.
- The server read is bounded: 500 files, 256 KiB each, no symlinks, no lock, no write.

### Constitution check (delta)

| Principle | Gate | Status |
|---|---|---|
| II. Registry is the source of truth | The lifetime records are read through the store module that owns them. The panel never reads registry JSON directly. | Pass |
| III. Modular package | New logic is in `sandbox/hosting/panel_lifetime.py`. `ui_dash.py` only wires the cache. | Pass |
| IV. Live-stack proof | Unit tests plus the panel against a fixture `SANDBOX_HOME`, with screenshots. The xspeed-dev deploy is the orchestrator's. | Pass, with the same stated limit |
| V. Docs with code | `docs/remote-hosting.md` (panel section) and README change in the same commit. | Pass |
| VI. Parity before removal | Nothing removed. The "Hosted instances" table is retitled, not removed. | Pass |

### Source code (delta)

```text
sandbox/hosting/lifetime/hoststore.py   # Store.peek(now): read-only bounded listing (new method)
sandbox/hosting/panel_lifetime.py       # LifetimeView, projection, ordering, server read, controller cache (new)
sandbox/hosting/panel_records.py        # LastApply status "unverified" + reason; SummaryRecorder outcome from delivery
sandbox/hosting/panel_view.py           # lifetime per stack/target, top-level lifetimes, stack fallback
sandbox/remote_runtime/host_inventory.py # hosted rows: lifetime, stack_slug fallback, last_apply_reason
sandbox/commands/ui_dash.py             # wire server lifetimes and the controller cache into _hosting_view
sandbox/core/_paths.py, src/web/index.html # sidebar hosted count slot
src/web/src/{types,render,main}.ts, pages/{hosting,hosts,remote}.ts, ui/hostlabels.ts
config/sandbox-web.js, config/sandbox-web.css (rebuilt)
tests/test_panel_lifetime.py (new), tests/test_panel_records.py, tests/test_panel_view.py,
tests/test_host_inventory_hosted.py, tests/test_web_hosting_api.py, tests/test_web_poller.py,
src/web/tests/hostlabels.test.ts (new)
```

### Files this branch stays off

These are owned by parallel threads: `sandbox/commands/host_stack.py`, the stack and hosting job paths (`sandbox/commands/hosting.py` included), `sandbox/resources/*`, `tests/test_lifecycle.py`, `tests/test_remote_hosting_images.py` and `tests/test_server_transport.py`. That is why the apply outcome is derived inside `SummaryRecorder.run()` from the result the apply already returns, and the call at `hosting.py` that passes a bool is left alone. `host_stack.py` is only invoked as the `sb host stack list` command, never imported for this.
