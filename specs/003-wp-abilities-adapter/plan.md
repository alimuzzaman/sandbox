# Implementation Plan: In-Instance WordPress Abilities + MCP Adapter Layer

**Branch**: `feat/agent-tooling-specs` | **Date**: 2026-06-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/003-wp-abilities-adapter/spec.md`

## Summary

Ship a per-instance WordPress mu-plugin that registers WP-native **Abilities**
(code-execution + file-CRUD) and exposes them over MCP via the bundled
`wordpress/mcp-adapter`, so any MCP client can connect directly to an instance's
`/wp-json` endpoint. Add `./sb abilities on|off|status` and `./sb connect`, a
host-side Python-MCP proxy (`wp_eval_live` + file proxies), and a crash-recovery
loader for persistent AI-written PHP. The layer is provisioned idempotently
alongside the existing mail/dl-cache/autologin mu-plugins, on by default, and works
on apache/nginx/litespeed (Docker) and herd (host).

## Technical Context

**Language/Version**: PHP 8.x (mu-plugin, target WP's supported PHP) + Python 3
(the MCP server `mcp/wp-server/` + `sb` package) + minimal vanilla JS (none required v1).

**Primary Dependencies**: `wordpress/mcp-adapter` (^0.5.x, vendored into the
mu-plugin payload) which requires the WP-core **Abilities API** (`wp_register_ability`,
WP 6.9+/7.0); the existing `_write_*_muplugin` provisioning pattern; the on-disk
registry + `sandbox.local.yml` `instances:` block (app passwords, tokens); the
FastMCP server in `mcp/wp-server/`.

**Storage**: a per-instance enable flag (option, mirrored in `sandbox.local.yml`);
AI-written persistent PHP under `wp-content/sandbox-code/`; a `.crashed` safe-mode
marker file. No new DB tables.

**Testing**: live-stack verification (constitution IV) — connect a real MCP client +
`run_tests`/manual MCP calls against a running instance; PHPUnit for the ability
callbacks where practical (host harness).

**Target Platform**: macOS/Linux dev machines; Docker Desktop/Engine and Laravel Herd.

**Project Type**: WordPress mu-plugin + host CLI/MCP extension (single-entry `sb` +
`sandbox/` package + `mcp/wp-server/`).

**Performance Goals**: ability discovery + a trivial `execute-php` round-trip in
well under 1s on a warm instance; provisioning adds no perceptible time to
`up`/`install`.

**Constraints**: dev/staging only (never production); secrets never persisted to a
tracked file/commit/memory; mu-plugin lives only in writable bind-mounts; vendored
adapter isolated to the mu-plugin payload (not the user's plugin, not `vendor/`).

**Scale/Scope**: one mu-plugin payload per instance; ~6 abilities
(execute-php, read/write/edit/list-file, discover override); 2 new CLI commands;
~3 proxy MCP tools.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Per-Project Is the Only Instance Model** — PASS. The layer is per-instance:
  mu-plugin written into each instance's bind-mount, enable flag is instance-scoped,
  `abilities`/`connect` resolve the instance via the registry (cwd/`--instance`/env).
  No global state.
- **II. Registry Is the Single Source of Truth** — PASS. `connect`/`abilities` and
  the proxy resolve the target instance + endpoint URL + app password through the
  registry + `sandbox.local.yml`, the same path as existing tools.
- **III. Single Entry File, Modular Package** — PASS. New logic lands in
  `sandbox/commands/abilities.py` + `sandbox/commands/connect.py` (self-registered),
  plus a mu-plugin writer in the provisioning module; `sb` stays a single entry file.
- **IV. Live-Stack Verification Is the Only Proof of Done** — PASS. quickstart.md
  defines real-client + MCP-call verification incl. a fatal→safe-mode recovery probe.
- **V. Idempotency and Docs-With-Code** — PASS. The mu-plugin writer is idempotent
  (re-written on every up/install/apply like the other mu-plugins); CLAUDE.md +
  MCP-surface table + config reference land in the same change.
- **VI. Feature Parity Before Removal** — PASS. Purely additive; removes nothing.
- **Boundaries** — PASS. mu-plugin + vendored adapter live under
  `wp-content/mu-plugins/` (writable bind-mount); nothing touches `runtime/wp*/`
  core or `vendor/`.
- **Secrets** — PASS w/ note. `./sb connect` displays the instance app password to
  the operator interactively (the credential they need to wire a client), exactly as
  the sandbox already surfaces app passwords for MCP setup; it is never written to a
  tracked file, commit, comment, or memory. See Complexity Tracking.

- **Auth model note (analysis CN1)**: the constitution's "nonce + capability on
  every handler" is satisfied here by **Application Password + `permission_callback`
  capability check** — nonces do not apply to external-client REST/MCP calls (there
  is no browser session/CSRF surface). This is the WP-sanctioned REST auth model, not
  a gap.

No unjustified violations — proceed.

## Project Structure

### Documentation (this feature)

```text
specs/003-wp-abilities-adapter/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions (adapter vendoring, AGPL boundary, enable model)
├── data-model.md        # Phase 1 — abilities, enable flag, sandbox-code/safe-mode entities
├── quickstart.md        # Phase 1 — live verification scenarios
├── contracts/
│   ├── abilities.md     # ability tool contracts (execute-php, file CRUD, discover)
│   └── cli-contract.md  # ./sb abilities, ./sb connect, proxy MCP tools
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
sandbox/
├── commands/
│   ├── abilities.py     # NEW: ./sb abilities on|off|status (instance-scoped)
│   └── connect.py       # NEW: ./sb connect — endpoint + app-pw + per-client config
├── core/                # provisioning helpers; add _write_abilities_muplugin
│   └── muplugins.py     # (or existing module) idempotent writer, hooked into up/install/apply
mcp/wp-server/
└── tools/
    └── abilities.py     # NEW: wp_eval_live + file-ability proxy tools
runtime/
└── wp-<instance>/wp-content/
    ├── mu-plugins/
    │   ├── 00-sandbox-abilities.php     # loader: register abilities + MCP server + discover override
    │   └── sandbox-abilities/           # payload: ability callbacks + vendored mcp-adapter
    └── sandbox-code/                    # jailed AI-written persistent PHP (+ .crashed marker)
```

**Structure Decision**: Follows the established split — host orchestration in the
`sandbox/` package + `mcp/wp-server/tools/`, the in-WP surface as a provisioned
mu-plugin under the instance's writable bind-mount. Mirrors how mail/dl-cache/
autologin mu-plugins are written today.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `connect` displays the app password to the operator | A client can't connect without the credential; this is the feature's purpose | Not showing it makes the connect helper useless; it is shown interactively only, never persisted to a tracked file/commit/memory, consistent with existing app-password surfacing for MCP setup |

## Phase 0 — Research

See [research.md](./research.md): adapter vendoring vs requiring core, the AGPL
boundary (re-implement callbacks, don't copy Novamira), enable-default model, the
WP-version gate + no-op, herd reachability, and the proxy-vs-direct decision (ship
both).

## Phase 1 — Design & Contracts

- [data-model.md](./data-model.md): Ability, Instance MCP endpoint, enable flag,
  sandbox-code folder + safe-mode marker.
- [contracts/abilities.md](./contracts/abilities.md): input/output + annotations for
  `sandbox/execute-php`, `read/write/edit/list-file`, and the discover override.
- [contracts/cli-contract.md](./contracts/cli-contract.md): `./sb abilities`,
  `./sb connect`, and the proxy MCP tools.
- [quickstart.md](./quickstart.md): connect a client, run execute-php, trip a fatal
  → safe-mode recovery, verify gating on/off.
- Agent context: the managed `<!-- SPECKIT START -->` block in `CLAUDE.md` points at
  this plan.

## Phase 2 — Tasks

Generated by `/speckit-tasks` into tasks.md (not created here).
