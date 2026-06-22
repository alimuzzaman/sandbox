# Feature Specification: Headless Debugging Tools — Query Monitor, dump/dd, Xdebug

**Feature Branch**: `feat/agent-tooling-specs`

**Created**: 2026-06-22

**Status**: Draft

**Input**: User description: "Integrate Query Monitor with CLI/MCP tools; add dump/dd
functions for quick-and-dirty debugging; cover Xdebug."

## Context

An agent debugs by reading files and JSON, not by looking at an admin-bar panel.
This feature gives it three headless debugging surfaces in increasing weight: (1)
`dump()`/`dd()` globals that write structured output to a clean, tailable file; (2)
Query Monitor's collected data (queries, errors, hooks, timing, HTTP, assets,
request) captured as JSON from a real page request without a browser; (3) Xdebug,
which already exists for container instances and is extended to host-served (herd)
instances plus a toggle on the agent surface. All three are development-only and
their output is runtime/gitignored.

Implementation detail (the var-dumper engine, the shutdown-collector capture
mechanism, exact tool/CLI flags) is deferred to `plan.md`.

## Clarifications

### Session 2026-06-22

- Q: How is Query Monitor activated on an instance? → A: Provision QM **installed-but-inactive** at instance-create time, and **auto-activate on first capture** (idempotent). The capture mechanism is always present regardless of QM's active state, so normal requests carry no QM overhead until a capture is requested.
- Q: How does the agent read dump()/dd() output? → A: Add a **file selector to the existing log-tail surface** (read the dedicated dump log); no new dedicated tool.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — dump/dd to a tailable file (Priority: P1)

A dev or agent drops `dump($thing)` in plugin code and reads the result from a clean,
dedicated file — not buried in the general debug log.

**Why this priority**: Quick variable inspection is the most common debugging need and
nothing provides it today.

**Independent Test**: Call `dump()` from plugin code and read the rendered value from
the dump log via the agent's log-tail surface.

**Acceptance Scenarios**:

1. **Given** a development instance, **When** plugin code calls `dump($var)`, **Then**
   a faithful, plain-text rendering (handling nesting/recursion) is appended to a
   dedicated dump log with a timestamp and caller location.
2. **When** code calls `dd($var)`, **Then** it writes the dump and halts with a pointer
   to the file.
3. **Given** a non-development environment, **When** the support loads, **Then** it
   no-ops and defines nothing.
4. **When** the agent reads the dump log, **Then** it can do so through the existing
   log-tail surface (file selector) and the CLI.

### User Story 2 — Capture Query Monitor data as JSON (Priority: P1)

An agent profiles a page or REST request and gets Query Monitor's data structured,
without a browser.

**Why this priority**: QM is the richest WP diagnostic; making it headless unlocks
query/error/timing analysis for agents.

**Independent Test**: Capture a given URL and confirm structured QM data (queries,
errors, timing, etc.) is returned for that request.

**Acceptance Scenarios**:

1. **Given** an instance, **When** the agent captures a URL, **Then** the stack issues
   a real request and returns parsed JSON of the collected data (selectable subset;
   the largest collector trimmed by default).
2. **Given** QM is not yet active, **When** the first capture runs, **Then** QM is
   auto-activated transparently.
3. **Given** capture works for anonymous requests, **When** captured, **Then** no
   admin login / capability is required to read the data.
4. **Given** only REST-scoped data is needed, **When** the agent uses the
   zero-config REST path, **Then** it gets the available collectors with no extra
   setup.

### User Story 3 — Xdebug on host-served instances and via the agent surface (Priority: P2)

A dev toggles Xdebug on a host-served (herd) instance and from the agent tool surface,
not only the container CLI.

**Why this priority**: Closes the existing gap (container-only) and adds an agent
toggle; step-debugging is the heavyweight escalation tier, hence P2.

**Independent Test**: Toggle Xdebug status on a herd instance and via the agent
surface and confirm the reported state.

**Acceptance Scenarios**:

1. **Given** a host-served instance, **When** the dev toggles Xdebug, **Then** it
   works or fails with a clear, actionable message (no opaque abort).
2. **Given** the agent surface, **When** it toggles/queries Xdebug, **Then** it
   succeeds without shelling out manually.
3. **Given** Xdebug is enabled, **When** documented, **Then** the trigger requirement
   is stated so requests actually break.

### Edge Cases

- Query Monitor data cannot be captured from a CLI-only execution path (it only
  populates on a real web request) — capture MUST go through an actual request.
- A request that errors still yields whatever QM collected up to that point.
- Dump and QM output files are truncatable on demand and never committed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide global `dump()` and `dd()` available to plugin
  code that write a faithful, plain-text, recursion-safe rendering — with timestamp and
  caller location — to a dedicated dump log separate from the general debug log; `dd()`
  also halts.
- **FR-002**: The dump support MUST be active only in development and MUST no-op
  (defining nothing, colliding with nothing) otherwise.
- **FR-003**: The agent MUST be able to read the dump log through the existing log-tail
  surface (via a file selector) and via the CLI.
- **FR-004**: The system MUST capture Query Monitor's collected data for a real page or
  REST request and return it as JSON, with a selectable subset and the largest
  collector trimmed by default.
- **FR-005**: QM capture MUST work for anonymous requests (no admin/capability gate)
  and MUST provision QM installed-but-inactive, auto-activating it on first capture.
- **FR-006**: The system MUST document a zero-config REST path for REST-scoped QM data.
- **FR-007**: Xdebug MUST be toggleable on host-served (herd) instances (or fail with a
  clear, actionable message) and from the agent tool surface, with the trigger
  requirement documented.
- **FR-008**: Dump and QM output MUST be runtime/gitignored and truncatable on demand.

### Key Entities

- **Dump log**: the dedicated, tailable file receiving `dump()`/`dd()` output.
- **QM capture**: a structured JSON snapshot of Query Monitor collectors for one
  request.
- **Xdebug toggle**: per-instance on/off/status, spanning container and host-served
  instances.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A `dump()` call from plugin code is readable by the agent from the
  dedicated dump log within one tail, with caller location and timestamp present.
- **SC-002**: Capturing a URL returns structured QM data (queries, errors, timing) for
  that request with no browser and no manual QM activation.
- **SC-003**: QM capture succeeds on an anonymous request (no login).
- **SC-004**: Normal (non-capture) requests incur no QM overhead because QM stays
  inactive until first capture.
- **SC-005**: Xdebug status/toggle works on both a container-backed and a host-served
  instance (or returns an actionable message on host-served).

## Assumptions

- Query Monitor data must come from a real web request (the CLI execution path
  short-circuits QM), so capture issues an actual request and reads the result.
- "Development" gating uses the Sandbox's local-environment configuration.
- Xdebug already exists for container instances; this feature extends reach and adds a
  toggle, it does not rebuild it.
- Dump/QM output files live under the instance's runtime area and are gitignored.
