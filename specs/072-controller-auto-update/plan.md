# Implementation Plan: Controller Auto-Update

**Branch**: `feat/controller-auto-update` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/072-controller-auto-update/spec.md`

## Summary

When a remote-capable `sb` command starts, the controller compares the newest
Sandbox source revision recorded for any registered remote with its own `HEAD`.
If a recorded revision might be newer, it fetches the checkout's own GitHub
`origin`, requires the revision to be on the followed upstream branch and to
descend from `HEAD`, fast-forwards to exactly that revision under a per-checkout
lock, refreshes the CLI venv through the new code, and re-executes the original
command once with a guard variable. Revisions are learned with no extra round
trip: an install or migrate records what it installed in machine-local state,
and leaves a small receipt on the remote that the existing service status probe
reads inside its existing ssh call.

## Technical Context

**Language/Version**: Python 3.12+ (stdlib only in the new package)

**Primary Dependencies**: `git` CLI (subprocess, bounded); existing
`sandbox.core._config._ensure_cli_requirements` for the venv refresh;
existing `sandbox.core._paths` (`ROOT`, `ENTRY`, `RUNTIME_DIR`, `CLI_VENV`)

**Storage**: one JSON file, `$SANDBOX_HOME/runtime/controller-update/state.json`
(hints and refusal memo), written atomically under a sidecar `flock`; one
lock file per checkout under `$SANDBOX_HOME/runtime/locks/`; one receipt on
each remote at `$HOME/sandbox/runtime/source-revision.json`

**Testing**: stdlib `unittest`, temporary git repositories only (a bare
"origin" plus clones; GitHub URLs mapped to the bare repo with git's
`url.<base>.insteadOf`). Run by Haiku helpers:
`.cli-venv/bin/python -m unittest discover -s tests -p 'test_controller_update*.py' -v`

**Target Platform**: macOS and Linux controllers

**Project Type**: CLI

**Performance Goals**: no fetch and no remote call when no recorded revision
differs from `HEAD`; at most one local `git rev-parse` per remote-capable
command when hints exist

**Constraints**: fetch bounded at 15 s, other git calls at 10 s, refresh at
300 s; machine-readable runs byte-identical; never fetch from a remote server;
fast-forward only; no `git stash`/`reset`

**Scale/Scope**: one checkout, a handful of remotes

## Constitution Check

| Principle | Status |
|---|---|
| I. Per-project instances | Not affected: no instance resolution changes. |
| II. Registry is source of truth | Pass: hints live in their own state file; the registry and `mcp_service` record keep their exact key sets. |
| III. Single entry file, modular package | Pass: `sb` is untouched; logic lives in `sandbox/controller_update/`, wired from `sandbox/cli.py`. |
| IV. Live-stack verification | Adapted: the feature acts on the controller checkout, not a WordPress stack. Proof is a real `./sb` invocation from a temporary clone against a temporary bare origin (quickstart), because the owner forbids running it against real remotes. Recorded under Complexity Tracking. |
| V. Idempotency and docs with code | Pass: lock released on exit, ref moves are fast-forward or compare-and-swap, the state file is replaced atomically; docs and both skill copies land in the same change. |
| VI. Parity before removal | Not affected: nothing removed or disabled. |

Module boundaries (repo CLAUDE.md): no new CLI command or MCP group; no new
consumer of `sandbox_core.py`, `sandbox.registry.COMMANDS` or the MCP helper
namespace; no direct registry JSON reads. Capability checks (opt-out, machine
output, job child, guard, checkout state) all happen before any side effect.

Remote protocol (gotcha 28): `sandbox/core/_remote.py` is not in the
protocol-fingerprinted module list, and no controller-to-runtime transport
payload changes, so `CONTROL_PROTOCOL_SPOKEN` is not bumped. The status
envelope gains one additive field, `installed_source_revision`.

Post-design re-check: unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/072-controller-auto-update/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli-behavior.md
│   ├── state-file.md
│   └── remote-receipt.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/controller_update/
├── __init__.py
├── models.py        # types: Hint, CheckoutState, Decision, SkipReason, Policy
├── gitops.py        # bounded git calls with a clean environment
├── state.py         # hint store + refusal memo (atomic, locked)
├── engine.py        # evaluate() -> Decision; apply() -> fetch/ff/refresh/rollback
├── hook.py          # maybe_auto_update(args, argv): gating, lock, notice, re-exec
└── refresh.py       # `python -m sandbox.controller_update.refresh` (runs in new code)

sandbox/cli.py                 # one call right after parse_args
sandbox/core/_remote.py        # receipt write in the install command; probe read; hint record

tests/test_controller_update_engine.py   # temp repos: trust, skips, ff, rollback
tests/test_controller_update_hook.py     # gating, notices, re-exec guard, opt-outs
tests/test_controller_update_state.py    # hint store, refusal memo
tests/test_controller_update_remote.py   # receipt + probe parsing (no ssh)

docs/controller-auto-update.md            # new reference
docs/remote-service-cli.md, docs/sandbox-config-reference.md  # cross-links
skills/sandbox-cli/SKILL.md, .agents/skills/sandbox-cli/SKILL.md
```

**Structure Decision**: a self-contained package under `sandbox/`, with the
only edits to existing code being the CLI call site and the three small
`_remote.py` touch points.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Live-stack proof is a temp-clone `./sb` run, not a WordPress stack | The feature changes the controller checkout, and the owner forbids running it against real remotes or the installed checkout | Running against `sandbox-main` or a real remote would move real code or touch production servers |
| A remote-side receipt is added to migrate | No existing probe reports the git source revision (only a 24-hex content digest) | Adding a unit env line would change the exact-key `mcp_service` record and the unit renderer; listing the image-stage helper bundles is ambiguous because several coexist |
