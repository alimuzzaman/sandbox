<!--
Sync Impact Report
- Version change: 1.0.0 → 1.0.1   (PATCH: registry path is base-relative,
    `$SANDBOX_HOME/runtime/registry.json` — spec 009 moved all machine-state
    under a per-user base; the registry remains authoritative, location only)
- Modified principles: n/a (first version; template placeholders replaced)
- Added sections: Core Principles (6), Additional Constraints, Development Workflow, Governance
- Removed sections: none
- Templates requiring updates:
  - .specify/templates/plan-template.md ✅ reviewed — generic "Constitution Check"
    gate is compatible; principles surface there at /speckit-plan time
  - .specify/templates/spec-template.md ✅ reviewed — no mandatory-section conflict
  - .specify/templates/tasks-template.md ✅ reviewed — live-stack verification maps
    to existing validation/polish task phases
- Follow-up TODOs: none
-->

# WPDeveloper Sandbox Constitution

The Sandbox is the `sb` CLI, `sandbox_core.py`, and the MCP `wp-server` that together
give developers a per-project WordPress dev/test stack. This constitution governs how
that tooling is built and changed.

## Core Principles

### I. Per-Project Is the Only Instance Model
Every WordPress instance MUST be owned by a project root recorded in
`$SANDBOX_HOME/runtime/registry.json`. There is NO implicit, global, or `main` instance. A command
run outside a registered project MUST fail with actionable guidance ("cd into a
registered project, or run `sb init` / `sb ensure`") and MUST NOT silently boot or
target a fallback instance. Rationale: the phantom `main` caused commands to act on
the wrong stack; one project ↔ one instance is the entire mental model.

### II. The Registry Is the Single Source of Truth
The on-disk registry (`$SANDBOX_HOME/runtime/registry.json`) is authoritative for project → instance
mapping. Per-instance config lives in `sandbox.local.yml` under `instances:`, written
only by `ensure_instance` / `apply_config`. Instance resolution precedence MUST be:
explicit `--instance` > `$SANDBOX_INSTANCE` > the registry instance for the current
working directory's project > error. Rationale: a single resolution path, shared by
the CLI and the MCP server, prevents the two from disagreeing about which stack is live.

### III. Single Entry File, Modular Package (NON-NEGOTIABLE)
`sb` MUST remain a single polyglot ENTRY file (shell→python bootstrap; `ROOT =
Path(__file__).resolve().parent`). All feature logic MUST live in an importable
`sandbox/` Python package (`core/` + `commands/`), with every feature a self-contained
module registered through a command registry. `sb` MUST NEVER become a directory —
doing so breaks the global symlink (`sb global`), the npm `bin` shim, and the release
tarball. Rationale: modularity for maintainability, single-file entry for the
distribution model the installer and symlink depend on.

### IV. Live-Stack Verification Is the Only Proof of Done
A change is "done" only when an MCP or CLI call against the running instance produces
the expected result, captured as evidence. Type-checking, linting, and code reading
are necessary but are NOT proof. Rationale: this tooling exists to drive a real WP
stack; only behavior on that stack confirms correctness.

### V. Idempotency and Docs-With-Code
Anything that mutates disk or Docker/Herd state MUST be safe to re-run. Code changes
MUST land together with their matching `README.md` / `CLAUDE.md` / `SKILL.md` /
`WORKFLOW.md` updates in the same change — never deferred. Rationale: re-runnability
makes recovery cheap; co-located docs stay true because they can't drift between commits.

### VI. Feature Parity Before Removal
No old-model code (the `main` instance, `DEFAULT_INSTANCE`, legacy keys, or migrations)
may be deleted until the per-project replacement for that feature is proven on the live
stack. Removals MUST be staged behind verified parity. Rationale: this is critical,
shared tooling; a half-removed feature bricks every developer who pulls it.

## Additional Constraints

- **Boundaries:** `runtime/wp*/` core files and `vendor/` are off-limits (clobbered on
  pull / `composer install`). Only plugin sources and `uploads/` are writable.
- **Secrets:** never echo a password/token to stdout, a commit, a comment, or a memory
  file. Per-instance secrets live in `sandbox.local.yml` (gitignored).
- **WP-touching work uses the MCP tools / `sb`**, not raw `docker`/`curl`/`mysql`.
- **Plugin code** follows WP best practice: auth (nonce + capability) on every handler,
  sanitize-in/escape-out, prefix everything, WP APIs over raw PHP.
- **Dev-process tooling** (e.g. spec-kit `.specify/` and its `speckit-*` skills) MUST be
  kept out of the shipped product (release prune + excluded from `package.json` files).

## Development Workflow

- Changes proceed spec-first via spec-kit: constitution → specify → clarify → plan →
  tasks → implement, with `/speckit-analyze` before implementation for cross-artifact
  consistency.
- Risky or sweeping changes to `sb` / `sandbox_core.py` / `server.py` happen on a branch
  and are staged into separately verified commits; each stage is smoke-tested live
  (`sb status` / `sb doctor` / `sb wp …` from a real project dir) before the next.
- Git actions (commit, push, tag, PR) require explicit per-action user approval; approval
  for one action is never approval for the next. No emojis in code or commit messages.

## Governance

This constitution supersedes other process conventions for the Sandbox tooling. Amendments
require: a written rationale, a version bump per the policy below, and propagation to any
dependent spec-kit templates and runtime guidance (`CLAUDE.md`). Every plan and review MUST
verify compliance with these principles; deviations MUST be justified in the plan's
Complexity/Constitution-Check section or the change is rejected.

Versioning policy (semantic): MAJOR = backward-incompatible governance/principle removal or
redefinition; MINOR = a new principle/section or materially expanded guidance; PATCH =
clarifications and non-semantic refinements.

**Version**: 1.0.1 | **Ratified**: 2026-06-21 | **Last Amended**: 2026-06-23
