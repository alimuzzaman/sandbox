# Implementation Plan: Secrets unset and remote copy

**Branch**: `feat/secrets-unset-copy` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/073-secrets-unset-copy/spec.md`

## Summary

Add two broker operations next to `secrets set`:

- `secrets unset` removes one dotenv key through the existing
  continuity-checked `writer.remove_keys`, inside the audit-first
  `SecretService._operate("unset", ...)` wrapper, with the same provider refusal,
  key validation and `--if-revision` precondition `set` uses.
- `secrets copy` reads one local value inside `_operate("copy", ...)` and hands it
  to a `RemoteSecretSink`. The production sink runs one SSH session through
  `sandbox.core._remote.ssh_run(..., input_data=...)` that `exec`s the remote's
  staged `<SANDBOX_HOME>/sb-src/sb secrets set ... --stdin --skip-unchanged --json`.
  The value is only on that session's stdin. The remote compares the incoming value
  to its stored one and answers `already_current` without writing.
- `secrets set` gains opt-in `--skip-unchanged`; without it, `set` is unchanged.
- The audit journal gains one optional, non-secret `destination` field used only by
  `copy`.

## Technical Context

**Language/Version**: Python 3 (stdlib only in the broker), as the rest of `sandbox/secrets`.

**Primary Dependencies**: none new. Uses `sandbox.secrets.writer`, `sandbox.secrets.ownership.continuity.capture`, `sandbox.core._remote` (SSH).

**Storage**: owner-only dotenv files; append-only audit JSONL under `$SANDBOX_HOME/runtime/secrets/`.

**Testing**: stdlib `unittest` (`.cli-venv/bin/python -m unittest tests.test_secret_service tests.test_secret_commands tests.test_secret_unset_copy -v`), run only in Haiku helpers.

**Target Platform**: macOS and Linux controllers; Linux remotes over SSH.

**Project Type**: CLI (`sb`).

**Performance Goals**: unset on a local source completes in well under a second; copy is one SSH session (60 s timeout).

**Constraints**: value never in argv, env, temp files, output, logs or audit; refusals before side effects; no automatic retry after an SSH timeout.

**Scale/Scope**: one key per invocation.

## Constitution Check

| Principle | Status |
|---|---|
| I. Per-project instance model | Not affected: secrets commands are global-scope and do not boot instances. |
| II. Registry single source of truth | Remote lookup uses the existing `get_remote` registration reader; no registry JSON is read directly. |
| III. Single entry file, modular package | New code lives in `sandbox/secrets/remote_copy.py` and existing modules; `sb` untouched. |
| IV. Live verification | Unit and CLI tests against temp sources; a CLI smoke run of `unset` against a temp registered source in a temp `SANDBOX_HOME`. Real-remote `copy` is deferred to the owner after merge (brief forbids real remotes); recorded as an open limit. |
| V. Idempotency, docs with code | Unset rerun refuses `key_missing` without change; copy rerun converges to `already_current`. `docs/secret-inspection.md`, `skills/secret-inspection/SKILL.md` and the `sandbox-cli` skill land in the same change. |
| VI. Parity before removal | Nothing removed; `set` default behaviour unchanged. |
| CLAUDE.md module boundaries | No new consumers of `sandbox_core.py`, the registry, the Hermes facade or MCP `app.py`. `sandbox.core._remote` is already imported by `_hermes`, `_proplugins` and transports. Capability checks (provider refusal, key and remote validation, provisioned check) run before audit intent or any read. |
| Remote readiness gate (gotcha 30) | Not applicable: gate covers job submissions and deploys, not a one-shot SSH broker call. Recorded in research R4. |

Result: PASS, no violations.

## Project Structure

### Documentation (this feature)

```text
specs/073-secrets-unset-copy/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/cli.md
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/secrets/
├── models.py         # + CopyAction / RemoteSetRequest / RemoteSetResult types
├── audit.py          # + optional "destination" field
├── writer.py         # + update_source(skip_unchanged=...)
├── service.py        # + unset(), copy_to_remote(); set/copy_reference pass skip_unchanged
└── remote_copy.py    # NEW: RemoteSecretSink protocol, SSH sink, response parser
sandbox/commands/secrets.py   # + unset / copy parsers and dispatch; set --skip-unchanged
tests/test_secret_unset_copy.py   # NEW
docs/secret-inspection.md
skills/secret-inspection/SKILL.md
skills/sandbox-cli/SKILL.md (+ .agents mirror if it is a copy)
```

**Structure Decision**: extend the existing broker package; the only new module holds
the transport so `service.py` stays transport-neutral (its docstring says so).

## Complexity Tracking

None.
