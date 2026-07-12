# Research: Generic Project Instances

## Decision 1 — Require explicit opt-in

**Decision**: A non-WordPress repository must declare `kind: "compose"` or run an explicit initialization command before `sb ensure` executes project code.

**Rationale**: Compose files, Dockerfiles, and package scripts are executable input. Automatic fallback would turn an innocent status/ensure attempt in an untrusted checkout into code execution and would conflict with the existing no-phantom-instance rule.

**Alternatives considered**: Auto-detect and run `compose.yaml`; infer from `package.json`; restore a generic global fallback. All were rejected because detection is ambiguous and unsafe.

## Decision 2 — Use project-owned Compose as the MVP

**Decision**: Support an explicit Compose file and public service first. Sandbox supplies only a generated overlay for port exposure, labels, and routing metadata.

**Rationale**: Compose already models images, builds, dependencies, environment, and volumes. Reimplementing these would make Sandbox a second container orchestrator and create ongoing framework maintenance.

**Alternatives considered**: A universal Dockerfile runner; a Node-only container schema; Dev Container compatibility; native host processes. These can be evaluated later if evidence shows Compose is insufficient.

## Decision 3 — Detect kind before applying defaults

**Decision**: Split common defaults from WordPress defaults and choose the runtime schema before normalization.

**Rationale**: The current loader applies WordPress plugins, versions, database, mail, and slug rules unconditionally. Adding a late `kind` branch would still contaminate generic descriptors and reproduce the reported dot-slug failure.

**Alternatives considered**: Strip WordPress keys after current normalization. Rejected because it is fragile and makes validation order-dependent.

## Decision 4 — Keep the current registry identity

**Decision**: Continue using canonical root plus label as identity, with additive kind/adapter metadata and a separate runtime-safe ID.

**Rationale**: This preserves the strongest existing invariant and supports worktrees/multiple labels. Directory names are presentation input, not identity.

**Alternatives considered**: Key by name, Compose project, or domain. All can collide and break relocation semantics.

## Decision 5 — Derive capabilities from adapters

**Decision**: Each adapter exposes a fixed set of capabilities; shared commands require generic capability names and WordPress tools require namespaced WordPress capabilities.

**Rationale**: A project-kind switch in every tool would spread conditional logic across 67 CLI commands and 51 MCP tools. Capability checks also produce useful early errors.

**Alternatives considered**: Let incompatible subprocess calls fail; duplicate generic and WordPress CLIs; persist a mutable capability list. These produce weaker errors, divergent UX, or stale state.

## Decision 6 — Preserve project volumes by default

**Decision**: Generic destroy omits Compose volume deletion and removes only Sandbox-owned state.

**Rationale**: Sandbox cannot safely infer whether project-defined volumes contain disposable caches or valuable data. Safe cleanup is reversible enough for a side project.

**Alternatives considered**: Match WordPress destroy semantics; inspect volume labels; prompt interactively. A separate explicit purge can be specified later.

## Decision 7 — Keep Astro as a preset

**Decision**: Astro initialization emits the same Compose descriptor used by the MVP and may generate a reviewable project Compose file.

**Rationale**: This solves the immediate case without creating framework-specific lifecycle code. The preset can be maintained or removed independently.

**Alternatives considered**: An Astro adapter or hard-coded Astro fallback. Both would make the first framework a permanent special case.

## Decision 8 — Use additive compatibility fields

**Decision**: Introduce common `http_port` and `kind` fields while retaining `wordpress_port` and all current WordPress registry/config fields.

**Rationale**: Existing CLI, MCP, proxy, tests, and local state read WordPress-specific fields. Additive reads enable staged parity and rollback.

**Alternatives considered**: Rename all port fields in one migration. Rejected as unnecessary cross-cutting risk.

## Decision 9 — Improve modularity only on the touched path

**Decision**: New runtime logic uses explicit modules/imports. Touched command parsers move beside their handlers, and MCP group loading stops growing the server bootstrap. Existing unrelated monoliths remain documented debt.

**Rationale**: The audit found real coupling, but a broad rewrite would dominate the user-facing feature and violate the side-project constraint.

**Alternatives considered**: Full core rewrite; file-size limits; no modularity work. The chosen middle path creates a reusable seam without dedicating the project to cleanup.

## Decision 10 — Validate with two live stacks

**Decision**: A live generic/Astro scenario proves the new behavior, and a live WordPress scenario proves compatibility.

**Rationale**: Unit tests cannot prove Compose routing, proxy reachability, or compatibility with the current WordPress stack. Both sides of the adapter boundary need evidence.

**Alternatives considered**: Mock-only tests or generic-only live tests. Both leave critical regressions unobserved.

## Evidence Summary

- Current HEAD: `014d5e1`; audit performed 2026-07-12.
- `sandbox_core.py` contains 980 lines of project config and registry logic; WordPress plugin-slug validation occurs before a project-kind concept exists.
- CLI: 67 registered commands across 25 command modules, with all parser definitions and routing sets centralized in the 756-line `sandbox/cli.py`.
- Core: 24 underscore modules, but `sandbox/core/__init__.py` imports/back-fills a shared namespace; 40 production files use wildcard imports from `sandbox.core` or MCP `app`.
- MCP: 51 tools across 17 tool groups; the 607-line `app.py` owns shared helpers and tool modules mostly wildcard-import it.
- Largest concentrated features include `sandbox/core/_hermes.py` (2,193 lines), `_provision.py` (994), `_domains.py` (846), `_instances.py` (836), `_remote.py` (756), `_docker.py` (744), and `_dash.py` (673).

See [modularity-audit.md](modularity-audit.md) for the full surface classification and bounded recommendations.
