# Implementation Plan: Supervised Long-Running Secret Session

**Branch**: `latest` (feature 059) | **Date**: 2026-10-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/059-supervised-secret-session/spec.md`

## Summary

Add `sb secrets run --session [--lifetime-seconds N]`: a local, foreground,
terminal-only mode that keeps one direct-argv child alive with brokered secrets
for up to 12 hours (default 8). The broker enforces the lifetime on a
wall-clock-and-monotonic deadline, ends the child's process group on lifetime
expiry, interrupt, hangup, terminal loss or the child's own exit, and streams
redacted output live without retaining it. A new `sandbox/secrets/session.py`
holds the loop and signal ownership; `SecretService.run_session` reuses
`run_many`'s binding validation and the audit-first `_operate` wrapper; the CLI
adds the flags, the terminal/foreground check, start/end lines and exit mapping.
Ordinary `secrets run`, `RunResult`, use profiles and MCP are unchanged.

## Technical Context

**Language/Version**: Python 3.11+ (CI runs 3.12); the `sandbox/` package

**Primary Dependencies**: stdlib only (`signal`, `selectors`, `subprocess`, `os`, `codecs`, `time`, `pty` in tests); existing `sandbox.services.redaction.StreamingRedactor`

**Storage**: none new; the existing owner-only secret audit journal (`SecretAudit`)

**Testing**: `python3 -m unittest`; child fixtures via `sys.executable -c` (pattern from `tests/test_secret_service.py`); CLI via `SimpleNamespace` args and patched `_service` (pattern from `tests/test_secret_commands.py`); one real CLI run under `pty.openpty()` with a temporary `SANDBOX_HOME` (pattern from `test_isolated_live_cli_flow_never_prints_fixture_value`)

**Target Platform**: operator workstation, macOS and Linux, interactive terminal

**Project Type**: CLI

**Performance Goals**: newline-terminated output displayed within 1 s; end detected within 0.25 s of the deadline while awake; process group gone within 5 s of any end

**Constraints**: every refusal before any secret read (except `key_missing`, as `run`); no value in argv, output, result, audit, errors or Sandbox-written files; no new audit fields; ordinary `run` limits unchanged (spec 041 FR-036)

**Scale/Scope**: one child per session, up to 100 bindings (existing `MAX_SELECTED_KEYS`), sessions up to 12 h with unbounded streamed output and zero retained output

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Note |
|-----------|--------|------|
| I. Per-project instance model | Pass | No instance is created or targeted; `--project-dir` only resolves registered sources, as `run` does. |
| II. Registry is the source of truth | Pass | Registry untouched. Secret sources resolve through `SourceRegistry`. |
| III. Single entry file, modular package | Pass | New logic in `sandbox/secrets/session.py`; the existing `secrets` command spec gains flags. `sb` unchanged. |
| IV. Live verification | Pass | The live surface is the real CLI under a real terminal: pty end-to-end test plus the quickstart evidence run past 30 minutes (T030). No WP stack is involved. |
| V. Idempotency and docs with code | Pass | Session mode mutates nothing on disk except audit appends. Skill, operator doc, README/contract and a spec 041 amendment note land with the code (Phase 6). |
| VI. Parity before removal | Pass | Nothing removed or disabled; `run` defaults are preserved by substituting 300 when `--timeout-seconds` is omitted. |
| Secrets constraint | Pass | Spec 041 FR-030 to FR-035 and FR-041 hold; nothing retained. |
| Module boundaries (CLAUDE.md) | Pass | No new consumers of `sandbox_core.py`, `sandbox.registry.COMMANDS`, `sandbox.hermes.facade` or the MCP helper namespace; no MCP group change. |

Post-design re-check: Pass. No complexity exceptions.

## Project Structure

### Documentation (this feature)

```text
specs/059-supervised-secret-session/
├── prd.md               # input (not modified)
├── spec.md
├── plan.md              # this file
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/cli.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
sandbox/secrets/
├── models.py        # + DEFAULT_SESSION_SECONDS, MAX_SESSION_SECONDS, END_REASONS, SessionResult
├── runner.py        # extract shared argv/secret validation and the _CONTROL filter for reuse
├── session.py       # NEW: SessionSignals, run_session loop, group termination, live redacted display
└── service.py       # + _normalize_bindings (from run_many), run_session, success reason_code in _operate

sandbox/commands/secrets.py   # --session, --lifetime-seconds, --timeout-seconds default None,
                              # terminal/foreground check, start/end lines, exit mapping

tests/
├── test_secret_session.py    # NEW: loop, lifetime, signals, group end, redaction, sleep clock, pty e2e
├── test_secret_commands.py   # parsing, conflicts, tty check, exit mapping, ordinary run unchanged
├── test_secret_service.py    # run_session ordering, audit pair, surface refusal, key_missing
└── test_secret_mcp.py        # tool list unchanged; no session parameter on secret_use_profile

skills/secret-inspection/SKILL.md            # operator-only session section, agents stay on bounded run
docs/secret-inspection.md                    # "5b. Operator session" section, limits and risks
specs/041-safe-secret-inspection/spec.md     # amendment note pointing at spec 059 (FR-036, FR-037 display)
specs/041-safe-secret-inspection/contracts/cli.md  # cross-reference to 059 contract
```

**Structure Decision**: Single Python package, matching spec 041's layout. The
session loop is its own module because it owns signal handlers and a
long-running loop that `runner.py`'s bounded, retaining runner must not acquire.

## Design Notes

- **Order of checks** (FR-009): argparse, lifetime (`lifetime_invalid`), option
  conflicts (`option_conflict`), terminal and foreground (`tty_required`), enter
  `SessionSignals`, then `service.run_session`: bindings/destinations/source
  (`selection_invalid`, `destination_denied`, `source_unknown`), surface
  (`command_denied`), audit intent, read, key lookup, launch. See research R1 to
  R4 and R9.
- **Loop** (R5 to R8): selector wait at most 0.25 s; per iteration check signal
  reason, deadline, child exit; feed chunks through `StreamingRedactor`,
  incremental UTF-8 decode, `_CONTROL` filter, write and flush; write failure is
  `hangup`.
- **End** (R6): `SIGTERM` to the group, 3 s grace, `SIGKILL`, up to 1.5 s; drain
  pipe; `redactor.finish()`; return `SessionResult`.
- **Audit** (R9): operation `use_session`; success outcome carries
  `reason_code=<end_reason>`.
- **Exit** (R10): `child_exited` reuses `run`'s mapping; `lifetime_expired` 0,
  `interrupted` 130, `hangup` 129 (provisional).

## Pending Decisions

Three provisional answers are in force (research "Pending decisions"):
`SIGTERM`/`SIGQUIT` map to `interrupted`; exit 0/130/129 for non-child ends;
control characters stripped from the live stream. Each is isolated to one
constant or mapping so a different answer changes one place and its tests.

## Complexity Tracking

No constitution violations.
