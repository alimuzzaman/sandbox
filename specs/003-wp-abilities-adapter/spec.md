# Feature Specification: In-Instance WordPress Abilities + MCP Adapter Layer

**Feature Branch**: `feat/agent-tooling-specs`

**Created**: 2026-06-22

**Status**: Draft

**Input**: User description: "Steal from Novamira #1 — ride the official WP Abilities API +
`wordpress/mcp-adapter` so abilities are discoverable WP-natively and work with any MCP
client; plus the crash-recovery sandbox-loader pattern."

## Context

The Sandbox exposes WordPress to agents through a host-side Python MCP server
(`mcp__sandbox__*`) whose ~23 tools are hand-rolled and reachable only by clients
the Sandbox wires up. Agents using other MCP clients (Cursor, Windsurf, Cline,
Claude Desktop) can't reach a Sandbox instance, and the most powerful capability —
running code inside the live WordPress runtime — isn't offered at all. This feature
adds a **second, optional MCP surface that lives inside each provisioned instance**,
built on the WordPress-native Abilities API so any standards-compliant client can
connect directly. It does not replace the Python MCP, which keeps owning
provisioning, lifecycle, snapshots, and multi-instance routing.

Implementation detail (mu-plugin layout, adapter vendoring, CLI wiring, the AGPL
boundary) is deferred to `plan.md` per the spec-kit split.

## Clarifications

### Session 2026-06-22

- Q: What should the in-instance Abilities layer expose in v1, and how is it reached? → A: Hybrid — code-execution **plus** file-CRUD abilities, exposed on **both** surfaces (the direct instance endpoint + a host-side proxy). External clients connecting straight to the instance lack the Python MCP's file tools, so the endpoint must be self-sufficient; the proxy gives in-session convenience for existing `mcp__sandbox__*` users.
- Q: Default state of the Abilities layer on a newly provisioned instance? → A: On by default (instances are disposable/local), with a "dev/staging only" banner; toggleable off.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Run code in the live WordPress runtime (Priority: P1)

An agent executes arbitrary PHP against a running instance and gets back the return
value, echoed output, captured warnings/notices, and timing — without writing a
file or rebuilding anything.

**Why this priority**: Live-runtime execution is the irreplaceable capability this
layer exists to add; everything else (file abilities, the editor engine in spec
005) builds on it, and it is the single most-used agent affordance.

**Independent Test**: With one instance running and the layer enabled, call the
code-execution ability and confirm it returns a structured result for a simple
expression — fully testable on its own.

**Acceptance Scenarios**:

1. **Given** a running instance with the layer enabled, **When** the agent runs
   `return get_option('siteurl');`, **Then** it gets back a structured result with
   the value, empty output, no errors, and an execution-time measurement.
2. **Given** code that emits a notice, **When** it runs, **Then** the notice is
   captured in a structured errors list (type, message, file, line) and the request
   still completes.
3. **Given** code that throws, **When** it runs, **Then** the result reports failure
   with the error message and class, and the request survives (no white screen).
4. **Given** code that would run too long, **When** it runs, **Then** it is cut by a
   hard execution-time limit.

### User Story 2 — Any MCP client connects directly (Priority: P1)

A developer points any standards-compliant MCP client at an instance's endpoint and
the abilities appear as usable tools, with no Sandbox-specific glue.

**Why this priority**: Client portability is the core reason to adopt the WP-native
standard over the bespoke Python tool list; without it this layer adds little over
what exists.

**Independent Test**: Run the connection helper, paste the emitted config into a
fresh MCP client, and confirm the client lists the abilities and can invoke
code-execution.

**Acceptance Scenarios**:

1. **Given** a running instance, **When** the developer runs the connection helper
   (or opens the dashboard "connect" block), **Then** they get the endpoint URL, an
   application password, and a ready-to-paste client config.
2. **Given** a connected client, **When** it requests ability discovery, **Then** it
   receives the ability list **plus** Sandbox environment guidance (focused plugin,
   instance URL, snapshot reminder).

### User Story 3 — Self-sufficient file access for external clients (Priority: P2)

An agent on an external client (without the Sandbox Python tools) reads, writes, and
edits files under the instance through abilities on the same endpoint.

**Why this priority**: Makes the direct endpoint useful on its own for external
clients; lower than code-execution because Sandbox-native users already have file
tools.

**Independent Test**: From a directly-connected external client, list a directory,
write a file, read it back, and confirm path-jailing rejects an out-of-bounds path.

**Acceptance Scenarios**:

1. **Given** a connected client, **When** it writes then reads a file under the WP
   install, **Then** the round-trip succeeds.
2. **Given** a path that escapes the install root (including via symlink), **When**
   a file ability is called, **Then** it is rejected.

### User Story 4 — Persistent AI-written code with crash recovery (Priority: P2)

An agent saves persistent PHP into a dedicated sandbox folder; if that code fatals,
the site auto-recovers into safe mode instead of white-screening.

**Why this priority**: Makes persistent experimentation safe on a real stack; without
it one bad file bricks the instance, but it's secondary to the read/execute path.

**Independent Test**: Write a deliberately fatal file via the ability, load a page,
and confirm the site stays up in safe mode with an admin notice naming the file.

**Acceptance Scenarios**:

1. **Given** the write ability, **When** new PHP is written, **Then** it lands only
   in the dedicated sandbox-code folder (path-jailed).
2. **Given** a sandbox file that fatals, **When** any request runs, **Then** all
   sandbox files are skipped (safe mode), an admin notice names the offending file,
   and a manual safe-mode override is available.

### User Story 5 — Off-switch and per-call authorization (Priority: P1)

The layer can be disabled per instance, and every ability independently enforces
authentication and capability.

**Why this priority**: This is powerful, destructive capability on a real stack;
gating is non-negotiable for safe operation.

**Independent Test**: Disable the layer and confirm the endpoint exposes nothing and
abilities are refused; re-enable and confirm an unauthenticated/under-privileged
caller is still refused.

**Acceptance Scenarios**:

1. **Given** the layer disabled, **When** the endpoint is queried, **Then** it
   exposes no abilities and calls are refused.
2. **Given** the layer enabled, **When** an ability is called without a valid
   application password or without the required capability, **Then** it is refused.

### Edge Cases

- WordPress version below the Abilities-API minimum → the layer no-ops with a logged
  notice rather than erroring.
- Destructive abilities (code-execution, write/edit/delete) are flagged as such so a
  client/agent can require confirmation; read/list abilities are flagged read-only.
- A stale `.crashed` marker → safe mode persists until the marker is cleared, with
  the admin notice explaining how to resume.
- Herd (host-served) instances → the layer works unchanged because it is host-file
  based; the endpoint is the herd URL.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Each provisioned instance MUST host an MCP endpoint that exposes
  WordPress abilities registered via the WP-native Abilities API, reachable by any
  standards-compliant MCP client.
- **FR-002**: The layer MUST provide a **code-execution** ability that runs PHP in
  the live WordPress runtime and returns a structured result: success flag, return
  value, captured output, captured non-fatal diagnostics, error message/class on
  failure, and execution time. It MUST capture warnings, notices, and deprecations
  (E_WARNING/E_NOTICE/E_DEPRECATED and their user-triggered equivalents) without
  fataling, and survive thrown `Throwable`s. (Genuine fatals — parse errors,
  `exit()`/`die()`, OOM — are not catchable; the ability instructs against them.)
- **FR-003**: Code execution MUST be bounded by a hard execution-time limit,
  **default 30 seconds**.
- **FR-004**: The layer MUST provide **file abilities** (read, write, edit, list)
  jailed to the WordPress install root, rejecting path escapes including via
  symlink, so the direct endpoint is self-sufficient for clients lacking the Python
  MCP's file tools.
- **FR-005**: Ability discovery MUST return the ability list **plus** Sandbox
  environment guidance (focused plugin, instance URL, snapshot reminder).
- **FR-006**: The layer MUST be toggleable per instance and MUST default to **on**
  for Sandbox instances, surfacing a "development/staging only" indication.
- **FR-007**: New persistent PHP written through the layer MUST be confined to a
  dedicated sandbox-code folder, loaded with crash recovery: a fatal MUST trigger a
  safe mode that skips all sandbox files, with an admin notice naming the file and a
  manual safe-mode override. The sandbox-code **loader runs independently of the
  enable flag** (existing sandbox files keep loading even when the layer is disabled);
  only the write ability — which the enable flag gates — can create them.
- **FR-008**: Every ability MUST enforce authentication (application password) **and**
  a capability check per call; **all v1 abilities require `manage_options`**.
  Destructive abilities MUST be flagged destructive and read-only abilities flagged
  read-only.
- **FR-009**: A connection helper MUST emit, for any instance, the endpoint URL, an
  application password, and a ready-to-paste per-client configuration; the dashboard
  MUST surface the same.
- **FR-010**: The same abilities MUST also be reachable in-session through the
  existing Sandbox tool namespace via a host-side proxy, so current users get them
  without switching clients.
- **FR-011**: On WordPress below the Abilities-API minimum the layer MUST no-op with
  a logged notice rather than failing provisioning.
- **FR-012**: The layer MUST be (re)provisioned idempotently as part of normal
  instance bring-up/refresh, and MUST work on both container-backed and host-served
  (herd) instances.

### Key Entities

- **Ability**: a named, discoverable WP capability with an input/output contract,
  authorization rule, and destructive/read-only annotation (e.g. code-execution,
  file read/write/edit/list).
- **Instance MCP endpoint**: the per-instance WP-native MCP server URL exposing the
  enabled abilities.
- **Sandbox-code folder**: the jailed location for persistent AI-written PHP,
  governed by the crash-recovery loader and its safe-mode marker.
- **Enable flag**: per-instance state controlling whether the layer is active.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From a freshly provisioned instance, an agent can run code in the live
  runtime and receive a structured result with no manual setup steps.
- **SC-002**: A developer can connect a previously-unconfigured external MCP client
  to an instance and invoke an ability in under 5 minutes using only the helper's
  output.
- **SC-003**: A deliberately fatal sandbox file never takes the site down — the next
  request returns a working page in safe mode 100% of the time.
- **SC-004**: With the layer disabled, zero abilities are reachable; with it enabled,
  zero abilities succeed without both a valid credential and the required capability.
- **SC-005**: File abilities reject 100% of attempted path escapes (including
  symlink) in test.
- **SC-006**: The layer adds no manual step to instance provisioning and re-running
  provisioning never duplicates or corrupts it (idempotent).

## Assumptions

- Instances are disposable, local development/staging stacks — hence on-by-default
  and the "dev/staging only" posture; this layer is never intended for production.
- The host-side Python MCP remains the owner of provisioning, lifecycle, snapshots,
  and multi-instance routing; this layer is additive.
- The WordPress-native Abilities API + MCP adapter are available on supported WP
  versions; older versions degrade to a no-op.
- Spec 005 (editor authoring) depends on this layer and is a primary consumer of it.
- Application-password REST auth is available per the Sandbox's local-environment
  configuration.
- The enable flag is a site-scoped option; multisite network-wide option scope is
  out of scope for v1 (single-site assumption). [analysis U3]
- The dashboard "connect" surface (FR-009) reuses the existing web-dashboard "Use
  with Claude" block; no new dashboard page is built in v1. [analysis U2]
