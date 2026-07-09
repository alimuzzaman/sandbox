# Implementation Plan: Remote VPS hosting for sandbox instances

**Branch**: `014-remote-vps-hosting` | **Date**: 2026-07-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/014-remote-vps-hosting/spec.md`

## Summary

Let a developer register an already-running VPS they manage themselves, provision it
with one command, deploy their current local project state to it on demand, and run a
full sandbox instance there — using the exact same CLI/MCP surface as a local instance,
with zero behavior change for anyone who never opts in. The technical approach (fully
resolved in `docs/remote-hosting-prd.md` §0 before this plan) is **co-location, not a
network-transparent daemon**: the MCP server, `sb`, `$SANDBOX_HOME`, Docker, and all
containers move onto the VPS together, reached over a Tailscale mesh, so every existing
filesystem/localhost-assuming tool (`fs_read`, `visit`, `db_query`, bind mounts,
snapshots) keeps working with **zero code change**, because on the VPS they're all local
again. Local↔VPS code transfer is a one-way, on-demand `sb deploy` (git push + a
replaced-not-stacked uncommitted-diff apply) — never a continuous sync daemon.

## Technical Context

**Language/Version**: Python 3.9+ (matches the rest of `sandbox/`)

**Primary Dependencies**: none new. Shells out to system `ssh`/`git`/`scp` (already a
project assumption — git is required to use this repo at all), exactly like the existing
pattern of shelling to `docker`/`wp` rather than adding a Docker/WP-CLI SDK dependency.
The MCP server's HTTP transport is provided by the `mcp` package already in use
(`FastMCP` supports `transport="streamable-http"` out of the box — confirmed in the PRD's
research against the MCP spec) — no new package.

**Storage**: two additions, both following existing precedent exactly — a `remotes:`
block in `sandbox.local.yml` (mirrors `sandbox/core/_licensing.py`'s
`_licensing_block()`/`_write_licensing_block()` read-modify-write pattern: gitignored,
`chmod 0o600`, secrets never echoed), and, on the VPS side, its OWN entirely separate
`$SANDBOX_HOME/runtime/registry.json` — see "Key architectural decision" below for why
this is a second, independent registry rather than a shared one.

**Testing**: stdlib `unittest`, mock-based (no docker, no real VPS) for config
read/write, SSH command construction, and deploy-mechanism logic — mirrors
`tests/test_ci.py`'s pattern. Per Constitution Principle IV, this feature is NOT
considered done on unit tests alone: a live-verification pass against a REAL VPS is
required (see quickstart.md), specifically the Phase 0 spike already scoped in the PRD
(§8) — prove `fs_read`/`visit`/`wp_cli` genuinely work through a Tailscale-reached,
VPS-hosted MCP server before trusting the rest of the design.

**Target Platform**: the local side is unchanged (macOS/Linux, wherever `sb` already
runs); the new remote side targets a Linux VPS (Ubuntu/Debian assumed for the
provisioning script, matching `scripts/install-ubuntu.sh`'s existing precedent — other
distros are not blocked, just not given a dedicated one-shot script in this phase, same
posture as this session's earlier Fedora/openSUSE support).

**Project Type**: CLI command group + MCP transport addition to the existing
single-package tool (matches `sandbox/commands/ci.py` + `mcp/wp-server/tools/ci.py`'s
existing shape for the CLI/MCP split; the MCP transport change touches
`mcp/wp-server/server.py` itself, which the constitution's Development Workflow section
already calls out as needing staged, live-smoke-tested commits when changed).

**Performance Goals**: N/A — on-demand developer commands, not a throughput-bound
service. `sb deploy`'s speed depends on the user's own network/VPS and is not a target
this feature optimizes beyond "only transfer new git objects, never the whole tree."

**Constraints**: the MCP server's HTTP transport MUST bind only to the VPS's Tailscale
interface, never `0.0.0.0` (FR-014 — exposing an unauthenticated Docker-adjacent
management surface to the public internet is a non-negotiable never-do); existing
local-only command behavior MUST be provably unchanged when no remote is configured
(FR-015, the release gate — same discipline already used for the `--label` axis and the
cross-platform work this session).

**Scale/Scope**: Phase 1 only, per the spec's Assumptions — one persistent, user-managed
VPS, one developer, no multi-tenant isolation, no on-demand VPS power management, no
continuous sync daemon. Multi-tenant/shared-VPS is explicitly out of scope (a separate,
much larger future effort per the PRD).

## Key architectural decision (resolved during this plan, refines the PRD)

The PRD's §4.2 originally sketched a shared registry with a per-instance `runtime` field
(`{"kind": "local"}` / `{"kind": "remote", "host": ...}`) so one registry could describe
both local and remote instances. Working through Model B's actual co-location premise
during this plan surfaced that **this isn't needed, and adding it would be**. Because the
remote MCP server is the exact same `mcp/wp-server` codebase running ON the VPS with its
OWN local `$SANDBOX_HOME`, a remote instance is *already* fully described by the VPS's own
independent `registry.json` — there is no second registry entry to reconcile, no
`runtime` field to add, no v2→v3 migration. The two registries (local, and each remote's)
are simply never merged; a user targets one or the other by which of the TWO registered
MCP servers they call (`sandbox` vs `sandbox-<remote-name>`), not by a field on a shared
record. This directly satisfies spec FR-012 (a local and a remote instance for the same
project can never collide or be silently conflated) *by construction* — they physically
live in different files on different machines — rather than needing code to enforce it.

The only genuinely new piece of "which VPS is this" bookkeeping lives entirely on the
**local** side, in `sandbox.local.yml`'s new `remotes:` block, and it only needs to answer
"how do I reach this VPS and is it provisioned" — never "what instances does it have,"
which `sb remote list`/status commands answer by asking the VPS's own MCP server
directly (the same way any MCP client would), not by reading local state about it.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Per-Project Is the Only Instance Model** — PASS. A remote instance is still owned
  by a project root recorded in a registry — just the VPS's own registry instead of the
  local one. No implicit/global/fallback instance is introduced on either side.
- **II. The Registry Is the Single Source of Truth** — PASS, with the refinement above:
  each machine's registry remains singularly authoritative FOR THAT MACHINE'S instances;
  resolution precedence (explicit `--instance` > `$SANDBOX_INSTANCE` > registry-for-cwd >
  error) is unchanged and applies identically on whichever machine's `sb`/MCP server is
  handling the call. No cross-machine registry merge is introduced (see decision above).
- **III. Single Entry File, Modular Package** — PASS. New logic lives in
  `sandbox/commands/remote.py` (new), `sandbox/commands/deploy.py` (new),
  `sandbox/core/_remote.py` (new — config block + SSH/git helpers, mirrors
  `_licensing.py`'s shape), and `mcp/wp-server/tools/remote.py` (new, thin). `sb` itself
  gains only subparser wiring; the only structural touch to an existing "core" file is
  `mcp/wp-server/server.py` gaining a transport-selection branch (stdio, unchanged, vs.
  streamable-http) — everything else in `mcp/wp-server/tools/*.py` is untouched, because
  those tools are already machine-local by construction and stay that way on the VPS.
- **IV. Live-Stack Verification Is the Only Proof of Done** — PASS, tracked explicitly.
  Unit tests cover config/SSH-construction/deploy-mechanism logic without a real VPS, but
  this feature is NOT done until live-verified against a real VPS (quickstart.md's Phase
  0 spike: prove `fs_read`/`visit`/`wp_cli` work through the Tailscale-reached, VPS-hosted
  MCP server end-to-end).
- **V. Idempotency and Docs-With-Code** — PASS. `sb remote provision` MUST be safe to
  re-run (spec FR-005); `sb deploy` is idempotent by construction (git push + reset-then-
  apply-fresh-diff, never an incremental stack). Docs land with code:
  `docs/remote-hosting-prd.md` is already updated (§0) as the grounded technical
  reference; this plan/spec/tasks set is the feature's design record; a
  `docs/remote-hosting.md` quick-reference (matching this session's
  `docs/ci-e2e-runner-spec.md`/`docs/cross-platform-support.md`/`docs/plugin-check.md`
  pattern) plus a README mention land in the same change as the implementation.
- **VI. Feature Parity Before Removal** — N/A. No old-model code is being removed; this
  is a net-new, opt-in capability.

**No violations. Complexity Tracking section is not needed.**

## Project Structure

### Documentation (this feature)

```text
specs/014-remote-vps-hosting/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md         # Phase 1 output
├── quickstart.md         # Phase 1 output
├── contracts/
│   └── cli-and-mcp.md   # Phase 1 output — CLI flag surface + MCP tool signatures
└── tasks.md              # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
sandbox/
├── commands/
│   ├── remote.py               # NEW — cmd_remote_add/list/provision/up/down/remove
│   └── deploy.py               # NEW — cmd_deploy (git push + diff-apply orchestration)
├── core/
│   └── _remote.py              # NEW — remotes: config block (mirrors _licensing.py),
│                                #       SSH/git command construction, reachability checks
└── cli.py                      # MODIFIED — new `remote` subparser group + `deploy` subparser

mcp/wp-server/
├── server.py                    # MODIFIED — transport selection (stdio unchanged default;
│                                 #            streamable-http + bearer auth when run in
│                                 #            "remote server" mode)
└── tools/
    └── remote.py                 # NEW — thin, local-side-only MCP tool(s) if any are needed
                                   #        beyond CLI parity (see contracts/cli-and-mcp.md)

scripts/
└── install-remote.sh            # NEW — VPS-side provisioning script (Tailscale join,
                                  #       Docker CE + compose plugin, sb runtime, tools venv,
                                  #       per-project deploy-target git repo setup helper),
                                  #       run over SSH by `sb remote provision`, mirroring
                                  #       install-macos.sh/install-ubuntu.sh's existing shape

tests/
└── test_remote.py               # NEW — mock-based: config read/write, SSH/git command
                                  #       construction, deploy diff-apply logic, mirrors
                                  #       tests/test_ci.py's shape

docs/
└── remote-hosting.md            # NEW — quick-reference design doc companion (matches
                                  #       docs/plugin-check.md's pattern); NOT a replacement
                                  #       for docs/remote-hosting-prd.md, which stays as the
                                  #       deeper grounded research/rationale document
```

**Structure Decision**: Single-project structure (sandbox is one Python package). New
code slots into the two already-established extension points
(`sandbox/commands/*.py` for CLI, `mcp/wp-server/tools/*.py` for MCP) exactly as
`ci.py`/`e2e.py`/`plugin_check.py` did earlier this session — no new top-level
directories except `scripts/install-remote.sh`, which follows the existing
`scripts/install-*.sh` convention exactly.

## Complexity Tracking

*(Not applicable — no Constitution Check violations.)*
