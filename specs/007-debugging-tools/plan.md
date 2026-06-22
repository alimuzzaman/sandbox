# Implementation Plan: Headless Debugging Tools — Query Monitor, dump/dd, Xdebug

**Branch**: `feat/agent-tooling-specs` | **Date**: 2026-06-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/007-debugging-tools/spec.md`

## Summary

Three headless debugging surfaces for agents: (1) a `00-sandbox-dump.php` mu-plugin
defining global `dump()`/`dd()` that write VarDumper `CliDumper` output to a dedicated
`wp-content/debug-dump.log`, read via `tail_log(file="dump")`; (2) a
`00-sandbox-qm.php` mu-plugin that, on `shutdown`, serializes Query Monitor's
collectors to `wp-content/qm.jsonl`, captured via a new `qm_capture(url)` tool that
fires an HTTP request then reads the last line (QM provisioned installed-inactive,
auto-activated on first capture); (3) extend the existing `./sb xdebug` to herd + an
`xdebug` MCP toggle.

## Technical Context

**Language/Version**: PHP (two mu-plugins, target WP PHP) + Python 3 (`sandbox/` + `mcp/wp-server/`).

**Primary Dependencies**: the existing `_write_*_muplugin` provisioning pattern; `tail_log` + `http_fetch` tool internals in `mcp/wp-server/tools/`; `cmd_xdebug` in `sandbox/commands/debug.py`; vendored `symfony/var-dumper` (in the dump mu-plugin payload); Query Monitor (installed inactive per instance).

**Storage**: `wp-content/debug-dump.log` (dump) + `wp-content/qm.jsonl` (QM) — both runtime, gitignored, truncatable.

**Testing**: live-stack verification (constitution IV) — `dump()` from code read via tail; `qm_capture` of a slow page returns queries/timing; xdebug status on Docker + herd.

**Target Platform**: macOS/Linux; Docker + Laravel Herd.

**Project Type**: WordPress mu-plugins + host CLI/MCP extension.

**Performance Goals**: zero QM overhead on normal requests (QM inactive until first capture); dump/QM writes are append-only.

**Constraints**: dev-only (gated on `WP_DEBUG`/`WP_ENVIRONMENT_TYPE=local`); QM data only from a real web request (CLI SAPI short-circuits QM); `function_exists` guards on `dump`/`dd`; never define `QM_DISABLED`, do define `QM_HIDE_SELF`.

**Scale/Scope**: 2 mu-plugins; 1 new MCP tool (`qm_capture`) + `tail_log` `file` selector + `xdebug` MCP tool; CLI `./sb dump`, `./sb qm`, herd `cmd_xdebug` extension.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Per-Project Only** — PASS. mu-plugins + logs are per-instance; tools resolve instance via registry.
- **II. Registry SoT** — PASS. `qm_capture`/`xdebug`/`tail_log` resolve the instance the standard way.
- **III. Single Entry, Modular** — PASS. New `sandbox/commands/{dump,qm}.py` + `debug.py` extension; mu-plugin writers in the provisioning module; `sb` single-entry.
- **IV. Live-Stack Verification** — PASS. quickstart exercises real dump/capture/xdebug.
- **V. Idempotency & Docs-With-Code** — PASS. mu-plugin writers idempotent; CLAUDE.md + wp-debug skill land with code.
- **VI. Parity Before Removal** — PASS. Additive; existing `./sb xdebug` behavior unchanged for Docker.
- **Boundaries / Secrets** — PASS. mu-plugins + logs in writable bind-mounts; vendored var-dumper isolated to the dump payload; no secrets.

No violations — proceed.

## Project Structure

### Documentation (this feature)

```text
specs/007-debugging-tools/
├── plan.md
├── research.md          # QM headless extraction, dump engine, xdebug-on-herd
├── data-model.md        # dump log, qm.jsonl record, xdebug toggle
├── quickstart.md        # live dump/QM/xdebug verification
├── contracts/
│   └── cli-contract.md  # tail_log(file), qm_capture, xdebug + ./sb dump/qm
└── tasks.md
```

### Source Code (repository root)

```text
mcp/wp-server/tools/
├── fs.py                # tail_log — add `file` selector (debug|dump|qm)
├── net.py              # qm_capture (reuse the http_fetch function in net.py)
└── (xdebug tool)        # wraps a shared xdebug_set(instance,state) core helper
sandbox/core/
├── _provision.py        # _write_dump_muplugin + _write_qm_muplugin (NOT herd-gated)
└── (xdebug core)        # xdebug_set(instance, state) shared by CLI + MCP
sandbox/commands/
├── debug.py             # cmd_xdebug → calls xdebug_set; herd = status+message
├── dump.py              # NEW: ./sb dump [--follow|--clear]
└── qm.py                # NEW: ./sb qm [<url>] [--collectors] [--clear|off]
sandbox/assets/dump-muplugin/   # committed self-contained var-dumper bundle (NOT repo vendor/)
runtime/wp-<instance>/wp-content/
├── mu-plugins/
│   ├── 00-sandbox-dump.php   # dump()/dd() + bundled var-dumper
│   └── 00-sandbox-qm.php     # shutdown → qm.jsonl
├── debug-dump.log           # already gitignored via runtime/wp-*/
└── qm.jsonl
```

**Structure Decision**: Two provisioned mu-plugins (dump, QM) + host CLI/MCP
extensions; reuses the mu-plugin writer pattern, `tail_log`, and `http_fetch`.

## Complexity Tracking

No constitution violations — none.

## Phase 0 — Research

See [research.md](./research.md): QM's collector architecture + the shutdown-read
extraction (vs the partial REST-envelope/header surfaces), the VarDumper `CliDumper`
choice, the QM activation model, and xdebug-on-herd.

## Phase 1 — Design & Contracts

- [data-model.md](./data-model.md): dump-log entry, `qm.jsonl` record, xdebug toggle.
- [contracts/cli-contract.md](./contracts/cli-contract.md): tool + CLI signatures.
- [quickstart.md](./quickstart.md): live dump/QM/xdebug verification.
- Agent context: SPECKIT block points at this plan.

## Phase 2 — Tasks

Generated by `/speckit-tasks`.
